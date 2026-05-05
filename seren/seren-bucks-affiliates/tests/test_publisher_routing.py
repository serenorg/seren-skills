from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from candidate_sync import PUBLISHER_ROUTING


def test_gmail_contacts_uses_google_contacts_publisher() -> None:
    """Gmail address books must use google-contacts publisher, not gmail."""
    assert PUBLISHER_ROUTING["gmail_contacts"] == "google-contacts"


def test_gmail_sent_uses_gmail_publisher() -> None:
    """Gmail sent mail uses gmail publisher."""
    assert PUBLISHER_ROUTING["gmail_sent"] == "gmail"


def test_outlook_contacts_uses_outlook_contacts_publisher() -> None:
    """Outlook address books use outlook-contacts publisher."""
    assert PUBLISHER_ROUTING["outlook_contacts"] == "outlook-contacts"


def test_outlook_sent_uses_outlook_publisher() -> None:
    """Outlook sent mail uses outlook publisher."""
    assert PUBLISHER_ROUTING["outlook_sent"] == "outlook"
