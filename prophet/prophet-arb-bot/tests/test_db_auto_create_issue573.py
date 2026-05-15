"""Regression test for issue #573.

`resolve_target` must auto-create the SerenDB database when it is missing,
instead of failing closed with `database '...' not found`. Operator was
blocked on a manual `seren__create_database` MCP call before setup could
proceed; the setup path should provision the database itself.

The auto-create flow:
  GET /projects                              -> resolve project
  GET /projects/{id}/branches/{bid}/databases -> list databases (miss)
  POST /projects/{id}/branches/{bid}/databases -> create database
  GET /projects/{id}/connection_uri          -> fetch URI

This test patches `db._http_get` and `db._http_post` so it never touches
the live publisher.
"""

from __future__ import annotations

from typing import Any

import pytest

import db


def test_resolve_target_auto_creates_missing_database(monkeypatch) -> None:
    """If the named database does not exist, setup creates it instead of
    failing closed."""

    project_id = "462d9dea-b2ef-4b21-afb1-1fdb710c49c6"
    branch_id = "f0133f36-bbe0-4dcf-a2d3-970b0384c2ee"

    get_calls: list[str] = []
    post_calls: list[dict[str, Any]] = []

    # First /databases call returns empty (miss); second returns the new db.
    databases_response_sequence = [
        [],  # first GET — empty
        [{"id": "c1b1952a", "name": "prophet"}],  # post-create GET — populated
    ]

    def fake_get(path: str, *, api_key: str) -> Any:
        get_calls.append(path)
        if path == "/projects":
            return [
                {
                    "id": project_id,
                    "name": "prophet",
                    "default_branch_id": branch_id,
                }
            ]
        if path == f"/projects/{project_id}/branches/{branch_id}/databases":
            return databases_response_sequence.pop(0)
        if path == f"/projects/{project_id}/connection_uri":
            return {
                "uri": (
                    f"postgres://owner:pw@ep.example.us-east-1.aws.neon.tech"
                    f"/serendb?sslmode=require"
                )
            }
        raise AssertionError(f"unexpected GET path: {path}")

    def fake_post(path: str, *, api_key: str, body: dict[str, Any]) -> Any:
        post_calls.append({"path": path, "body": body})
        return {"id": "c1b1952a", "name": body.get("name")}

    monkeypatch.setattr(db, "_http_get", fake_get)
    monkeypatch.setattr(db, "_http_post", fake_post)
    # Bust the module-level URI cache so the test is hermetic.
    monkeypatch.setattr(db, "_TARGET_CACHE", {}, raising=True)

    target = db.resolve_target(
        api_key="sk_test_fake_key",
        project_name="prophet",
        database_name="prophet",
    )

    # Auto-create POST fired exactly once with the requested name + branch.
    assert len(post_calls) == 1
    assert post_calls[0]["path"] == (
        f"/projects/{project_id}/branches/{branch_id}/databases"
    )
    assert post_calls[0]["body"]["name"] == "prophet"

    # And the URI is rewritten to the requested database name.
    assert target.database_name == "prophet"
    assert target.connection_uri.endswith("/prophet?sslmode=require")
