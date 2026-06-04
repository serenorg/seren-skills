from __future__ import annotations

from scripts.affinity import AffinityProspectSource


LONG_MEETING_NOTE = (
    "Met with the team to review their fund launch goals. They discussed "
    "investor mix, advisor responsibilities, timing, operating constraints, "
    "service-provider expectations, and next steps for a proposal package "
    "after the introductory call."
).replace("proposal", "deck")


class FakeAffinityClient:
    """Stub Affinity client backing AffinityProspectSource for filter tests.

    Two engaged orgs in one list: org-1 owned by Cristin, org-2 owned by
    someone else. Both carry a qualifying meeting summary.
    """

    def __init__(self) -> None:
        self.notes_calls: list[str] = []

    def find_list(self, name):
        return {"id": 1, "name": name}

    def list_fields(self, list_id):
        return {"fields": []}

    def list_entries(self, list_id):
        return [
            {"id": "entry-1", "entity": {"id": "org-1", "name": "Cristin Co"}},
            {"id": "entry-2", "entity": {"id": "org-2", "name": "Other Co"}},
        ]

    def field_values(self, list_entry_id):
        owner = (
            "Cristin.Carter@glideinvest.com"
            if list_entry_id == "entry-1"
            else "someone.else@glideinvest.com"
        )
        return [
            {"id": f"fv-status-{list_entry_id}", "field_name": "Status", "value": "Engaged - 25%"},
            {"field_name": "Owner", "value": [{"email": owner}]},
        ]

    def notes(self, org_id):
        self.notes_calls.append(str(org_id))
        return [{"content": LONG_MEETING_NOTE, "created_at": "2026-06-01"}]


def _source(client, owner_emails):
    return AffinityProspectSource(
        client,
        list_name="Glide Prospects",
        owner_emails=owner_emails,
    )


def test_owner_filter_keeps_only_allowed_owner():
    client = FakeAffinityClient()
    source = _source(client, ["cristin.carter@glideinvest.com"])

    prospects = source.qualified_prospects()

    assert [p.org_id for p in prospects] == ["org-1"]
    assert prospects[0].owner_email == "Cristin.Carter@glideinvest.com"
    # Non-matching owner is skipped before its notes are ever fetched.
    assert client.notes_calls == ["org-1"]


def test_owner_filter_is_case_insensitive():
    source = _source(FakeAffinityClient(), ["CRISTIN.CARTER@GLIDEINVEST.COM"])
    assert [p.org_id for p in source.qualified_prospects()] == ["org-1"]


def test_no_owner_filter_keeps_all_owners():
    for owner_emails in (None, []):
        prospects = _source(FakeAffinityClient(), owner_emails).qualified_prospects()
        assert sorted(p.org_id for p in prospects) == ["org-1", "org-2"]


def test_owner_filter_accepts_single_string():
    source = _source(FakeAffinityClient(), "cristin.carter@glideinvest.com")
    assert [p.org_id for p in source.qualified_prospects()] == ["org-1"]
