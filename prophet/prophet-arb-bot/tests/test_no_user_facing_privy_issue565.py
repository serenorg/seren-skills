"""Issue #565: strip user-facing "Privy" mentions.

Two pins:
  1. SKILL.md has zero matches for "Privy" (case-insensitive). Claude reads
     SKILL.md to phrase prompts to operators; if Privy appears here, Jill
     sees "Privy email" copy that confuses her about which service she
     needs an account at.
  2. The OTP-timeout error message raised by token_acquirer does not
     surface "Privy" in user-visible English. The exception class name
     itself stays internal (out of scope).
"""

from __future__ import annotations

import re
from pathlib import Path


ARB_BOT_ROOT = Path(__file__).resolve().parent.parent


def test_skill_md_has_no_user_facing_privy_mentions() -> None:
    """SKILL.md is operator-facing — Claude renders it back to Jill."""
    skill_md = (ARB_BOT_ROOT / "SKILL.md").read_text(encoding="utf-8")
    matches = re.findall(r"privy", skill_md, flags=re.IGNORECASE)
    assert matches == [], (
        f"SKILL.md still mentions Privy {len(matches)} time(s). "
        f"Users do not know who Privy is — this leaks into operator prompts "
        f"like 'Privy email address you use to log into Prophet'. See issue #565."
    )


def test_otp_timeout_message_does_not_say_privy() -> None:
    """The OTP-timeout error bubbles into operator-visible JSON envelopes."""
    acquirer = (
        ARB_BOT_ROOT / "scripts" / "otp_worker" / "token_acquirer.py"
    ).read_text(encoding="utf-8")
    # Locate the OtpEmailTimeout raise block and pin its message.
    match = re.search(
        r"raise OtpEmailTimeout\(\s*([^)]*)\)",
        acquirer,
        flags=re.DOTALL,
    )
    assert match is not None, "OtpEmailTimeout raise site moved; update this test"
    raise_block = match.group(1)
    assert "Privy" not in raise_block, (
        "OtpEmailTimeout message still says 'Privy'. This string bubbles "
        "into the operator-visible JSON envelope. See issue #565."
    )
