"""Microsoft SSO + TOTP flow driver via Playwright.

Drives the Salesforce login page through Microsoft Entra (Azure AD) and
the rolling 6-digit TOTP step the org enforces. Persists the resulting
authenticated browser storage to disk so subsequent runs reuse the
session and skip the full OTP dance — `playwright_storage.json` is the
session token, equivalent to a cookie jar plus localStorage.

On the first run, the Microsoft tenant URL is unknown. We discover it
at runtime by following the Salesforce SSO redirect, and persist what
we find to `sso_discovery.json` so the operator can inspect it later.
The skill does not depend on the captured URL for correctness — every
run rediscovers it — but the cache makes the failure mode obvious if
Microsoft ever rotates the tenant host.

This module owns one public function: `authenticate`. It returns the
authenticated Playwright `Page` so the caller can immediately navigate
to a Salesforce record without spinning up a second context.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scripts.auth.op_service_account import SalesforceCredentials
from scripts.sf import selectors


# --------------------------------------------------------------------- #
# State paths                                                           #
# --------------------------------------------------------------------- #

# Both paths are inside `state/`, which is gitignored. They are
# rewritten in place on every successful run.
DEFAULT_STORAGE_PATH = Path("state/playwright_storage.json")
DEFAULT_SSO_DISCOVERY_PATH = Path("state/sso_discovery.json")

# Generous timeouts to absorb a slow Microsoft tenant and the Lightning
# bootstrap, which routinely takes 8–12 seconds even on a fast network.
DEFAULT_NAVIGATION_TIMEOUT_MS = 30_000
DEFAULT_SELECTOR_TIMEOUT_MS = 15_000


# --------------------------------------------------------------------- #
# Protocols                                                             #
# --------------------------------------------------------------------- #


class _Page(Protocol):
    """Subset of `playwright.sync_api.Page` that this module touches.

    Declared as a Protocol so the unit tests can pass a hand-rolled
    fake without importing Playwright at test time.
    """

    url: str

    def goto(self, url: str, *, timeout: int = ...) -> object: ...
    def click(self, selector: str, *, timeout: int = ...) -> None: ...
    def fill(self, selector: str, value: str, *, timeout: int = ...) -> None: ...
    def wait_for_url(self, url: object, *, timeout: int = ...) -> None: ...
    def wait_for_selector(self, selector: str, *, timeout: int = ...) -> object: ...
    def is_visible(self, selector: str) -> bool: ...


class _Context(Protocol):
    """Subset of `playwright.sync_api.BrowserContext` we touch."""

    def new_page(self) -> _Page: ...
    def storage_state(self, *, path: str) -> object: ...
    def cookies(self) -> list[dict]: ...


# --------------------------------------------------------------------- #
# Result type                                                           #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class AuthenticationResult:
    """Outcome of one `authenticate` call.

    `microsoft_tenant_url` is the URL of the Microsoft sign-in screen
    we landed on after the Salesforce SSO redirect. Captured on every
    run, even when storage_state reuse short-circuits the flow before
    it — in the reuse path the field is `None`.
    """

    page: _Page
    reused_storage: bool
    microsoft_tenant_url: str | None


# --------------------------------------------------------------------- #
# Reuse check                                                           #
# --------------------------------------------------------------------- #


def _is_authenticated(page: _Page) -> bool:
    """Heuristic: are we landed inside Lightning?

    The App Launcher selector is present on every authenticated
    Lightning page; if we see it after `goto(salesforce_org_url)`,
    the persisted storage_state was good and we can skip the SSO
    dance entirely.
    """

    return page.is_visible(selectors.SF_LIGHTNING_AUTHENTICATED_SENTINEL)


# --------------------------------------------------------------------- #
# SSO discovery                                                         #
# --------------------------------------------------------------------- #


def _persist_sso_discovery(
    *,
    discovery_path: Path,
    microsoft_tenant_url: str,
) -> None:
    """Write the discovered Microsoft tenant URL to disk for inspection.

    Not load-bearing for correctness — every run rediscovers — but
    a stable record on disk makes debugging dramatically easier when
    Microsoft rotates a tenant host or breaks a flow.
    """

    discovery_path.parent.mkdir(parents=True, exist_ok=True)
    discovery_path.write_text(
        json.dumps({"microsoft_tenant_url": microsoft_tenant_url}, indent=2)
    )


# --------------------------------------------------------------------- #
# Fresh-login flow                                                      #
# --------------------------------------------------------------------- #


def _drive_fresh_login(
    *,
    page: _Page,
    salesforce_org_url: str,
    creds: SalesforceCredentials,
    discovery_path: Path,
) -> str:
    """Walk Salesforce → Microsoft → Lightning.

    Returns the Microsoft tenant URL the SSO redirect landed us on,
    so the caller can persist it alongside the browser storage.
    """

    # Step 1. Salesforce login page. Click the Microsoft SSO button.
    page.goto(salesforce_org_url, timeout=DEFAULT_NAVIGATION_TIMEOUT_MS)
    page.click(
        selectors.SF_LOGIN_MICROSOFT_SSO_BUTTON,
        timeout=DEFAULT_SELECTOR_TIMEOUT_MS,
    )

    # Step 2. Wait for the redirect to land on the Microsoft sign-in
    # host, then capture the URL. We match the host by substring
    # (`microsoftonline.com`) rather than a fixed tenant value
    # because the tenant is per-customer and not known in advance.
    page.wait_for_url(
        lambda url: "microsoftonline.com" in str(url),
        timeout=DEFAULT_NAVIGATION_TIMEOUT_MS,
    )
    microsoft_tenant_url = page.url
    _persist_sso_discovery(
        discovery_path=discovery_path,
        microsoft_tenant_url=microsoft_tenant_url,
    )

    # Step 3. Race between the Microsoft email screen and the Salesforce
    # Lightning sentinel. Some tenants silently resume the SSO session
    # through Microsoft straight back to Lightning when the upstream IdP
    # cookie is still valid — Microsoft never renders the email form,
    # the page lands on Lightning, and a bare wait on `MS_EMAIL_INPUT`
    # times out at 15s with no useful diagnostic. CSS-grouping the two
    # selectors makes `wait_for_selector` return as soon as either one
    # is visible; `is_visible` then disambiguates the winner. Issue #759.
    page.wait_for_selector(
        f"{selectors.MS_EMAIL_INPUT}, "
        f"{selectors.SF_LIGHTNING_AUTHENTICATED_SENTINEL}",
        timeout=DEFAULT_SELECTOR_TIMEOUT_MS,
    )
    if page.is_visible(selectors.SF_LIGHTNING_AUTHENTICATED_SENTINEL):
        # Silent session resume — the Microsoft form was never rendered.
        # Persist the storage as a Lightning session and skip the
        # email/password/TOTP/KMSI screens entirely.
        return microsoft_tenant_url

    page.fill(selectors.MS_EMAIL_INPUT, creds.username)
    page.click(selectors.MS_EMAIL_SUBMIT)

    # Step 4. Password screen.
    page.wait_for_selector(
        selectors.MS_PASSWORD_INPUT, timeout=DEFAULT_SELECTOR_TIMEOUT_MS
    )
    page.fill(selectors.MS_PASSWORD_INPUT, creds.password)
    page.click(selectors.MS_PASSWORD_SUBMIT)

    # Step 5. TOTP screen.
    page.wait_for_selector(
        selectors.MS_TOTP_INPUT, timeout=DEFAULT_SELECTOR_TIMEOUT_MS
    )
    page.fill(selectors.MS_TOTP_INPUT, creds.totp_code)
    page.click(selectors.MS_TOTP_SUBMIT)

    # Step 6. "Stay signed in?" (KMSI) interstitial. Microsoft
    # renders this on a redirect AFTER the TOTP POST settles, so we
    # have to wait for it — a bare `is_visible` check races the
    # network and silently no-ops. We click "No" to keep Playwright
    # storage_state as the single session anchor.
    #
    # The KMSI page is not guaranteed: some tenants suppress it for
    # corporate-managed devices, in which case we redirect straight
    # back to Salesforce. We race both possibilities with a single
    # wait_for_url.
    page.wait_for_url(
        lambda url: (
            "/kmsi" in str(url).lower()
            or "ProcessAuth" in str(url)
            or "salesforce.com" in str(url)
            or "force.com" in str(url)
        ),
        timeout=DEFAULT_NAVIGATION_TIMEOUT_MS,
    )
    if page.is_visible(selectors.MS_STAY_SIGNED_IN_NO):
        page.click(selectors.MS_STAY_SIGNED_IN_NO)

    # Step 7. Wait for the round-trip back to Salesforce. The host
    # leaving microsoftonline.com is the load-bearing signal — only
    # then can `sid` be issued. The DOM sentinel is belt-and-suspenders.
    page.wait_for_url(
        lambda url: (
            "lightning.force.com" in str(url)
            or "my.salesforce.com" in str(url)
        ),
        timeout=DEFAULT_NAVIGATION_TIMEOUT_MS,
    )
    page.wait_for_selector(
        selectors.SF_LIGHTNING_AUTHENTICATED_SENTINEL,
        timeout=DEFAULT_NAVIGATION_TIMEOUT_MS,
    )

    return microsoft_tenant_url


# --------------------------------------------------------------------- #
# Public entry point                                                    #
# --------------------------------------------------------------------- #


def authenticate(
    *,
    context: _Context,
    salesforce_org_url: str,
    creds: SalesforceCredentials,
    storage_path: Path = DEFAULT_STORAGE_PATH,
    discovery_path: Path = DEFAULT_SSO_DISCOVERY_PATH,
) -> AuthenticationResult:
    """Drive Microsoft SSO end-to-end and return an authenticated Page.

    The caller owns the Playwright lifecycle (browser + context). This
    function takes a fresh `BrowserContext`, navigates inside it, and
    on success calls `context.storage_state(path=storage_path)` so the
    next run can short-circuit the OTP flow by reusing the same file.

    Reuse is attempted automatically: if the storage file exists,
    the caller is expected to have already passed `storage_state=...`
    when constructing the context. We verify reuse worked by checking
    for the Lightning App Launcher on the initial page; if it is not
    visible, we fall back to the full SSO flow.

    Raises whatever Playwright raises if a selector times out, a
    navigation fails, or the credentials are wrong. Callers should
    let that surface — the failure modes are operator-visible and
    each one points at a different remediation (Microsoft rotated
    selectors, TOTP drifted, network blocked Salesforce, etc.).
    """

    page = context.new_page()
    page.goto(salesforce_org_url, timeout=DEFAULT_NAVIGATION_TIMEOUT_MS)

    # Reuse path. If we are already inside Lightning, the persisted
    # storage_state is still valid and we are done.
    if _is_authenticated(page):
        return AuthenticationResult(
            page=page,
            reused_storage=True,
            microsoft_tenant_url=None,
        )

    # Fresh login path.
    microsoft_tenant_url = _drive_fresh_login(
        page=page,
        salesforce_org_url=salesforce_org_url,
        creds=creds,
        discovery_path=discovery_path,
    )

    # Verify we actually hold a Salesforce session before persisting
    # storage. Without a `sid` cookie on a *.salesforce.com or
    # *.force.com domain, every subsequent Lightning navigation will
    # redirect to login (?ec=302) — silently saving a sessionless
    # storage_state would make the next dry-run fail with a useless
    # selector timeout instead of a clear auth failure here.
    sf_sids = [
        c for c in context.cookies()
        if c.get("name") == "sid"
        and ("salesforce.com" in c.get("domain", "")
             or "force.com" in c.get("domain", ""))
    ]
    if not sf_sids:
        raise RuntimeError(
            "Microsoft SSO completed but no Salesforce `sid` cookie was "
            "issued. The driver landed on a Lightning page but the "
            "session cookie is missing — likely an unhandled Microsoft "
            "interstitial (KMSI variant, MFA challenge, consent prompt). "
            "Inspect `state/sso_discovery.json` and re-run with "
            "`--headless` removed to see what Microsoft is showing."
        )

    # Persist the authenticated storage so the next run can reuse.
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(storage_path))

    return AuthenticationResult(
        page=page,
        reused_storage=False,
        microsoft_tenant_url=microsoft_tenant_url,
    )
