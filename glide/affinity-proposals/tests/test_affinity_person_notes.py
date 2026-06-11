from __future__ import annotations

import logging

from scripts.affinity import AffinityProspectSource


LONG_MEETING_NOTE = (
    "Met with the Acme team to review fund launch goals, target investors, "
    "advisor responsibilities, launch timing, service-provider expectations, "
    "operating constraints, and next steps for a detailed deck package after "
    "the introductory call."
)


class FakeAffinityClient:
    def __init__(self, *, org_notes=None, person_notes=None) -> None:
        self.org_notes = list(org_notes or [])
        self.person_note_map = dict(person_notes or {})
        self.person_notes_calls: list[str] = []

    def find_list(self, name):
        return {"id": 1, "name": name}

    def list_fields(self, list_id):
        return {
            "fields": [
                {"id": 10, "name": "Status", "dropdown_options": []},
                {"id": 11, "name": "Owners"},
            ]
        }

    def list_entries(self, list_id):
        return [{"id": "entry-1", "entity": {"id": "org-1", "name": "Acme"}}]

    def field_values(self, list_entry_id):
        return [
            {"id": 20, "field_id": 10, "value": {"text": "Engaged - 25%"}},
            {"id": 21, "field_id": 11, "value": "owner@example.com"},
        ]

    def notes(self, org_id):
        return self.org_notes

    def organization(self, org_id):
        return {"id": org_id, "person_ids": ["person-1"]}

    def person_notes(self, person_id):
        self.person_notes_calls.append(str(person_id))
        return self.person_note_map.get(str(person_id), [])


def _source(client: FakeAffinityClient) -> AffinityProspectSource:
    return AffinityProspectSource(
        client,
        list_name="Glide Prospects",
        owner_emails=["owner@example.com"],
    )


def test_person_attached_meeting_note_qualifies_when_org_notes_are_empty() -> None:
    client = FakeAffinityClient(
        org_notes=[],
        person_notes={
            "person-1": [
                {"id": "note-1", "content": LONG_MEETING_NOTE, "created_at": "2026-06-01"}
            ]
        },
    )

    prospects = _source(client).qualified_prospects()

    assert [p.org_id for p in prospects] == ["org-1"]
    assert prospects[0].notes[0].content == LONG_MEETING_NOTE
    assert client.person_notes_calls == ["person-1"]


def test_notes_reachable_from_org_and_person_are_deduped_by_id() -> None:
    note = {"id": "note-1", "content": LONG_MEETING_NOTE, "created_at": "2026-06-01"}
    client = FakeAffinityClient(
        org_notes=[note],
        person_notes={"person-1": [dict(note)]},
    )

    prospects = _source(client).qualified_prospects()

    assert len(prospects) == 1
    assert len(prospects[0].notes) == 1


def test_empty_org_and_person_note_pool_warns_and_counts_no_notes(caplog) -> None:
    client = FakeAffinityClient(org_notes=[], person_notes={"person-1": []})
    source = _source(client)

    with caplog.at_level(logging.WARNING, logger="scripts.affinity"):
        prospects = source.qualified_prospects()

    assert prospects == []
    assert source.scan_summary.skipped["no_notes_via_api"] == 1
    assert "prospect_skipped_no_notes_via_api" in caplog.text
    assert "likely_cause" in caplog.text
    assert "https://api.affinity.co/companies/org-1/notes" in caplog.text
