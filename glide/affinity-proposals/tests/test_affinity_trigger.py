from __future__ import annotations

import pytest

from scripts.affinity import Note, should_generate_proposal


LONG_MEETING_NOTE = (
    "Met with the Acme Capital team to review their fund launch goals. "
    "They discussed investor mix, advisor responsibilities, timing, "
    "operating constraints, service-provider expectations, and next steps "
    "for a proposal package after the introductory call."
)


@pytest.mark.parametrize(
    ("status", "notes", "expected"),
    [
        (
            "Engaged - 25%",
            [Note(content=LONG_MEETING_NOTE.replace("proposal", "deck"))],
            True,
        ),
        ("Engaged - 25%", [Note(content="email sent")], False),
        ("Engaged - 25%", [Note(content=LONG_MEETING_NOTE)], False),
        (
            "Prospect - 0%",
            [Note(content=LONG_MEETING_NOTE.replace("proposal", "deck"))],
            False,
        ),
    ],
)
def test_trigger_filter(status, notes, expected):
    assert should_generate_proposal(status=status, notes=notes) is expected
