"""AuthFacade: single entrypoint that hides cache-vs-OTP from callers.

Plan §11.4: callers (agent.py, the cron runner) only call
AuthFacade.get_fresh_jwt(email). They do not know whether the JWT came
from the cache, a silent refresh, or a fresh cold-start OTP.

Decision tree:
  - cache.state == fresh AND jwt has > 60s of life → return cached jwt.
  - cache.state == needs_refresh → run one refresh; if it goes fresh,
    return; if it flips to needs_otp, fall through to cold-start.
  - cache.state == needs_otp OR cache empty → cold-start TokenAcquirer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .playwright_client import BrowserSession
from .session_cache import SessionCache
from .token_acquirer import AcquiredSession, acquire_token
from .token_refresher import RefreshResult, refresh_once


@dataclass
class FreshJwt:
    jwt: str
    prophet_viewer_id: str
    source: str  # 'cache' | 'refresh' | 'otp'


class AuthFacade:
    def __init__(
        self,
        *,
        cache: SessionCache | None = None,
        acquirer: Callable[..., AcquiredSession] = acquire_token,
        refresher: Callable[..., RefreshResult] = refresh_once,
    ) -> None:
        self.cache = cache or SessionCache()
        self._acquirer = acquirer
        self._refresher = refresher

    def get_fresh_jwt(
        self,
        *,
        email: str,
        provider: str,
        seren_user_id: str,
        bounty_id: str,
        browser_session: BrowserSession,
        gateway: Any,
        transport: Any = None,
    ) -> FreshJwt:
        """`gateway` is used for the email-OTP inbox publisher
        (gmail/outlook). `transport` is the direct-to-Prophet HTTP
        client used for viewer-bind + affiliate bind after the JWT
        lands. Issue #493.
        """
        entry = self.cache.read()

        if entry.is_fresh():
            return FreshJwt(
                jwt=entry.jwt,
                prophet_viewer_id=entry.prophet_viewer_id,
                source="cache",
            )

        if entry.state == "needs_refresh" and entry.refresh_token:
            self._refresher(cache=self.cache)
            entry = self.cache.read()
            if entry.is_fresh():
                return FreshJwt(
                    jwt=entry.jwt,
                    prophet_viewer_id=entry.prophet_viewer_id,
                    source="refresh",
                )
            # Refresh failed → fall through to cold-start.

        # Cold-start path: cache is needs_otp, missing, or refresh just failed.
        if transport is None:
            from prophet.transport import ProphetDirectTransport

            transport = ProphetDirectTransport()
        acquired = self._acquirer(
            email=email,
            provider=provider,
            seren_user_id=seren_user_id,
            bounty_id=bounty_id,
            browser_session=browser_session,
            gateway=gateway,
            transport=transport,
            cache=self.cache,
        )
        return FreshJwt(
            jwt=acquired.jwt,
            prophet_viewer_id=acquired.prophet_viewer_id,
            source="otp",
        )
