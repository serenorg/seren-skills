"""Critical contract for the Claude ultrasonic-angle generator.

Replaces the generic Hypothesis prompt. The new prompt asks Claude
for three HU-specific ultrasonic-welding angles per Lead — the
selling-thesis input for the assigned SAL's pitch.

Tests pin:
- The dataclass shape (`UltrasonicAngles.angles: list[str]`).
- The parser pulls bullet-prefixed lines (`•` / `-` / `1)`) out of the
  Claude response.
- An empty / malformed response degrades to an empty angle list — the
  Note renderer surfaces "(not surfaced)" rather than hallucinating.
"""

from __future__ import annotations

import json
from typing import Any

from scripts.research import claude_angles


def _stub_fetcher(response_payload: dict[str, Any]):
    """Build a Fetcher stub matching the `seren_client.Fetcher` signature."""

    encoded = json.dumps(response_payload).encode("utf-8")

    def fetcher(method: str, url: str, headers: dict, body: bytes | None):
        return (200, encoded)

    return fetcher


def test_generate_parses_three_bullet_angles() -> None:
    """`• …` bullets in the Claude response land as `angles`."""

    response = (
        "• Lidding-film sealing on fresh-cut clamshells — ultrasonic seals "
        "through moisture and juice.\n"
        "• Cold-chain compatibility: no sustained heat at the seal line.\n"
        "• Mono-material recyclable lidding: ultrasonic enables fiber-based "
        "lidstock that doesn't tolerate heat coatings."
    )
    fetcher = _stub_fetcher(
        {"choices": [{"message": {"content": response}}]}
    )

    result = claude_angles.generate(
        lead_name="Jose Zamudio",
        company_name="Fresh Pak, Inc.",
        perplexity_summary="Fresh-cut produce processor.",
        fetcher=fetcher,
        api_key="sk-test",
    )

    assert isinstance(result, claude_angles.UltrasonicAngles)
    assert len(result.angles) == 3
    assert "Lidding-film" in result.angles[0]


def test_generate_handles_numbered_list() -> None:
    """`1) … 2) … 3) …` also parses as angles — Claude is inconsistent."""

    response = (
        "1) Lidding-film sealing on clamshells.\n"
        "2) Cold-chain compatibility.\n"
        "3) Mono-material recyclable lidding."
    )
    fetcher = _stub_fetcher(
        {"choices": [{"message": {"content": response}}]}
    )

    result = claude_angles.generate(
        lead_name="Jose Zamudio",
        company_name="Fresh Pak, Inc.",
        perplexity_summary="",
        fetcher=fetcher,
        api_key="sk-test",
    )

    assert len(result.angles) == 3


def test_generate_caps_at_three_angles() -> None:
    """Claude returns 5 bullets -> we keep the first 3. Nathan's spec is 3."""

    response = "\n".join(f"• Angle {i}" for i in range(1, 6))
    fetcher = _stub_fetcher(
        {"choices": [{"message": {"content": response}}]}
    )

    result = claude_angles.generate(
        lead_name="X",
        company_name="Y",
        perplexity_summary="",
        fetcher=fetcher,
        api_key="sk-test",
    )

    assert len(result.angles) == 3


def test_generate_empty_response_yields_empty_angles() -> None:
    """Empty Claude response -> empty list. No hallucinated angle text."""

    fetcher = _stub_fetcher({"choices": []})

    result = claude_angles.generate(
        lead_name="X",
        company_name="Y",
        perplexity_summary="",
        fetcher=fetcher,
        api_key="sk-test",
    )

    assert result.angles == []
