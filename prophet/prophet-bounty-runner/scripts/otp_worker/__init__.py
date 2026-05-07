"""Phase 5: Email-OTP worker for Prophet Privy auth.

Public surface (only these are imported by callers):

  - AuthFacade.get_fresh_jwt(email, *, provider) -> JWT str
  - exceptions: OtpEmailTimeout, OtpCodeNotFound, PrivyAuthFailed,
                EmailPublisherUnavailable, IdentityMismatch

Everything else is internal. See plan §11.4 for the module layout.
"""

from __future__ import annotations


class OtpWorkerError(Exception):
    """Base for all OTP-worker exceptions."""


class OtpEmailTimeout(OtpWorkerError):
    """Privy OTP email did not land in the inbox within the timeout."""


class OtpCodeNotFound(OtpWorkerError):
    """Email arrived but no 6-digit code matched."""


class PrivyAuthFailed(OtpWorkerError):
    """Privy returned an invalid response after submitting the OTP."""


class EmailPublisherUnavailable(OtpWorkerError):
    """gmail/outlook publisher returned 401 or is not configured."""


class IdentityMismatch(OtpWorkerError):
    """viewer.email from Prophet does not match inputs.prophet_email.

    Per plan §11.1 step 10: this is a P0 fail-closed condition. The user
    logged in with a different account than they specified; do not proceed.
    """


__all__ = [
    "OtpWorkerError",
    "OtpEmailTimeout",
    "OtpCodeNotFound",
    "PrivyAuthFailed",
    "EmailPublisherUnavailable",
    "IdentityMismatch",
]
