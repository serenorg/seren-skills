"""Server-side viewer-binding helpers (post-OTP).

Issue #487: browser-driven OTP and onboarding moved to the agent
layer (Seren Desktop's Playwright MCP). This module is now strictly
post-OTP server-side glue — `token_acquirer._query_viewer` takes a
JWT the agent already captured and binds it to a Prophet
`viewer.user.id`.

Public surface (only these are imported by callers):

  - exceptions: `OtpWorkerError`, `PrivyAuthFailed`
"""

from __future__ import annotations


class OtpWorkerError(Exception):
    """Base for all OTP-worker exceptions."""


class PrivyAuthFailed(OtpWorkerError):
    """Privy / Prophet rejected the supplied JWT during viewer binding."""


__all__ = [
    "OtpWorkerError",
    "PrivyAuthFailed",
]
