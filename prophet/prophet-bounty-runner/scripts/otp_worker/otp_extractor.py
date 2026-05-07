"""Extract a 6-digit Privy OTP from an email body.

Privy's OTP emails put the code as a standalone line of 6 digits, often
inside a styled container. We accept any 6-digit run that is *not* part
of a longer numeric token (so we don't pick up tracking IDs, order
numbers, or year-prefixed dates).

This module is provider-agnostic: gmail and outlook both feed plain-text
or stripped HTML through the same extractor.
"""

from __future__ import annotations

import re

from . import OtpCodeNotFound

_CODE_RE = re.compile(r"(?<![0-9])([0-9]{6})(?![0-9])")


def extract_otp_code(body: str) -> str:
    """Return the first standalone 6-digit code in the email body.

    Raises OtpCodeNotFound if no code is present. Privy emails reliably
    put the code near the top, so we take the first match — using "last
    match" would be wrong if the footer happens to contain a 6-digit
    tracking ID.
    """
    if not body:
        raise OtpCodeNotFound("empty email body")

    match = _CODE_RE.search(body)
    if match is None:
        raise OtpCodeNotFound("no 6-digit code found in email body")
    return match.group(1)
