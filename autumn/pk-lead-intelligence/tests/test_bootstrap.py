"""Critical-path tests for the first-run bootstrap (issue #853).

Scope: only the behaviours that would silently break Jill if regressed.
- staging is idempotent (no operator data loss on re-run)
- auto-provision lands on the right config keys
- the missing-fields envelope omits auto-resolved keys (no Postgres
  URI prompts, per feedback_no_db_setup_prompts)
- `--set` merges without dropping sibling keys
- first-run locks `live_mode=False` regardless of the example value

Everything else (HTTP client wiring, JSON encoding) is thin and
covered indirectly by these five.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import bootstrap


@pytest.fixture()
def skill_root(tmp_path: Path) -> Path:
    """A fake skill root with the two example files the bootstrap stages."""

    root = tmp_path / "skill"
    root.mkdir()
    (root / "config.example.json").write_text(
        json.dumps(
            {
                "inputs": {
                    "salesforce_org_url": "https://<org>.lightning.force.com",
                    "salesforce_owner_email": "",
                    "live_mode": True,
                    "serendb_connection_uri": "<postgres://...>",
                    "google_drive_folder_id": "<paste-folder-id>",
                    "nathan_share_email": "",
                    "monthly_close_target_usd": 500000,
                },
                "schedule": {"timezone": "America/New_York"},
            }
        )
    )
    (root / ".env.example").write_text(
        "SEREN_API_KEY=\nSF_USERNAME=\nSF_PASSWORD=\nSF_TOTP_SECRET=\n"
    )
    return root


@pytest.fixture()
def stable_dir(tmp_path: Path) -> Path:
    return tmp_path / "stable"


def _fake_serendb_factory() -> bootstrap.SerenDBLike:
    class _Client:
        def list_projects(self) -> list[dict]:
            return []

        def create_project(self, name: str) -> dict:
            return {"id": "prj_x", "name": name}

        def list_databases(self, project_id: str) -> list[dict]:
            return []

        def create_database(self, project_id: str, name: str) -> dict:
            return {"id": "db_x", "name": name}

        def get_connection_uri(self, project_id: str, database_name: str) -> str:
            return f"postgresql://fake/{database_name}"

    return _Client()


def _fake_drive_call(_publisher: str, _path: str, body: dict) -> dict:
    assert body.get("mimeType") == "application/vnd.google-apps.folder"
    return {"id": "folder_abc", "name": body.get("name")}


def test_staging_is_idempotent_and_preserves_operator_edits(
    skill_root: Path, stable_dir: Path
) -> None:
    bootstrap.run_bootstrap(
        stable_dir=stable_dir,
        skill_root=skill_root,
        serendb_client_factory=_fake_serendb_factory,
        drive_publisher_call=_fake_drive_call,
    )
    config_path = stable_dir / "config.json"
    edited = json.loads(config_path.read_text())
    edited["inputs"]["salesforce_org_url"] = "https://jill.lightning.force.com"
    config_path.write_text(json.dumps(edited))

    bootstrap.run_bootstrap(
        stable_dir=stable_dir,
        skill_root=skill_root,
        serendb_client_factory=_fake_serendb_factory,
        drive_publisher_call=_fake_drive_call,
    )

    after = json.loads(config_path.read_text())
    assert (
        after["inputs"]["salesforce_org_url"]
        == "https://jill.lightning.force.com"
    )


def test_auto_provision_populates_serendb_uri_and_drive_folder_id(
    skill_root: Path, stable_dir: Path
) -> None:
    result = bootstrap.run_bootstrap(
        stable_dir=stable_dir,
        skill_root=skill_root,
        serendb_client_factory=_fake_serendb_factory,
        drive_publisher_call=_fake_drive_call,
    )

    config = json.loads((stable_dir / "config.json").read_text())
    assert (
        config["inputs"]["serendb_connection_uri"]
        == "postgresql://fake/pk_lead_enrichment"
    )
    assert config["inputs"]["google_drive_folder_id"] == "folder_abc"
    assert "serendb_connection_uri" in result.auto_resolved
    assert "google_drive_folder_id" in result.auto_resolved


def test_missing_fields_envelope_omits_auto_resolved_keys(
    skill_root: Path, stable_dir: Path
) -> None:
    result = bootstrap.run_bootstrap(
        stable_dir=stable_dir,
        skill_root=skill_root,
        serendb_client_factory=_fake_serendb_factory,
        drive_publisher_call=_fake_drive_call,
    )

    missing_keys = {m.key for m in result.missing}
    assert "serendb_connection_uri" not in missing_keys
    assert "google_drive_folder_id" not in missing_keys
    assert {
        "salesforce_org_url",
        "salesforce_owner_email",
        "nathan_share_email",
        "SF_USERNAME",
        "SF_PASSWORD",
        "SF_TOTP_SECRET",
    }.issubset(missing_keys)


def test_apply_set_merges_without_dropping_siblings(
    skill_root: Path, stable_dir: Path
) -> None:
    bootstrap.run_bootstrap(
        stable_dir=stable_dir,
        skill_root=skill_root,
        serendb_client_factory=_fake_serendb_factory,
        drive_publisher_call=_fake_drive_call,
    )

    bootstrap.apply_set(
        stable_dir=stable_dir,
        assignments=[
            ("salesforce_org_url", "https://acme.lightning.force.com"),
            ("SF_PASSWORD", "hunter2"),
        ],
    )

    config = json.loads((stable_dir / "config.json").read_text())
    assert (
        config["inputs"]["salesforce_org_url"]
        == "https://acme.lightning.force.com"
    )
    assert (
        config["inputs"]["serendb_connection_uri"]
        == "postgresql://fake/pk_lead_enrichment"
    ), "siblings populated by run_bootstrap must survive --set"
    assert config["inputs"]["monthly_close_target_usd"] == 500000
    assert config["schedule"]["timezone"] == "America/New_York"

    env_text = (stable_dir / ".env").read_text()
    assert "SF_PASSWORD=hunter2" in env_text


def test_first_run_forces_live_mode_false_even_when_example_is_true(
    skill_root: Path, stable_dir: Path
) -> None:
    bootstrap.run_bootstrap(
        stable_dir=stable_dir,
        skill_root=skill_root,
        serendb_client_factory=_fake_serendb_factory,
        drive_publisher_call=_fake_drive_call,
    )

    config = json.loads((stable_dir / "config.json").read_text())
    assert config["inputs"]["live_mode"] is False
