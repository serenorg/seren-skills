from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


SKILL_ROOT = Path(__file__).resolve().parents[1]


def test_runner_keeps_fuzzy_matches_out_of_existing_and_blocks_missing_oauth() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SKILL_ROOT / "scripts" / "agent.py"),
            "--target",
            "Calendly",
            "--catalog-fixture",
            str(SKILL_ROOT / "tests" / "fixtures" / "calendly_fuzzy_catalog.json"),
            "--asana-fixture",
            str(SKILL_ROOT / "tests" / "fixtures" / "asana_publisher.json"),
            "--organizations-fixture",
            str(SKILL_ROOT / "tests" / "fixtures" / "organizations.json"),
            "--oauth-providers-fixture",
            str(SKILL_ROOT / "tests" / "fixtures" / "oauth_providers.json"),
        ],
        check=True,
        text=True,
        capture_output=True,
    )
    payload = json.loads(result.stdout)

    assert payload["status"] == "blocked"
    assert payload["target"]["slug"] == "calendly"
    assert payload["catalog_guard"]["queried_all"] is True
    assert payload["exact_match"] is None
    assert payload["fuzzy_matches"][0]["slug"] == "google-calendar"
    assert payload["existing"] == []
    assert payload["blocked"][0]["slug"] == "calendly"
    assert payload["blocked"][0]["reason"] == "missing_target_oauth_provider"
    assert payload["organization"]["id"] == "org_personal"
    assert payload["template"]["slug"] == "asana"
