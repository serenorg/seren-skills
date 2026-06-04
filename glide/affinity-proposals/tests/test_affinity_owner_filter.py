from __future__ import annotations

from scripts.affinity import AffinityProspectSource


LONG_MEETING_NOTE = (
    "Met with the team to review their fund launch goals. They discussed "
    "investor mix, advisor responsibilities, timing, operating constraints, "
    "service-provider expectations, and next steps for a deck package after "
    "the introductory call."
)

# Live Affinity v1 list-field ids (Glide Prospects, list 337186).
STATUS_FID = 5570062
OWNER_FID = 5570064

PEOPLE = {
    194941263: "Cristin.Carter@glideplatform.com",
    185544917: "david@glideplatform.com",
}


class FakeAffinityClient:
    """Stub mirroring the real Affinity v1 payload shapes.

    Field-values carry NO ``field_name`` (identity is ``field_id`` only),
    Status is a ``{"text": ...}`` dict, and the Owners value is a bare
    person-ID int that must be resolved via ``person()``.
    """

    def __init__(self, owners: dict[str, int]) -> None:
        self.owners = owners  # org_id -> owner person id
        self.notes_calls: list[str] = []
        self.person_calls: list[int] = []

    def find_list(self, name):
        return {"id": 337186, "name": name}

    def list_fields(self, list_id):
        return {
            "fields": [
                {
                    "id": STATUS_FID,
                    "name": "Status",
                    "dropdown_options": [{"id": 23418334, "text": "Proposal - 50%"}],
                },
                {"id": OWNER_FID, "name": "Owners"},
            ]
        }

    def list_entries(self, list_id):
        return [
            {"id": int(f"24244{i}"), "entity": {"id": org, "name": f"Co {org}"}}
            for i, org in enumerate(self.owners)
        ]

    def field_values(self, list_entry_id):
        # Map entry back to its org to find the owner person id.
        org = list(self.owners)[int(str(list_entry_id)[-1])]
        return [
            {"id": 1, "field_id": OWNER_FID, "value": self.owners[org]},
            {"id": 2, "field_id": STATUS_FID, "value": {"text": "Engaged - 25%"}},
        ]

    def notes(self, org_id):
        self.notes_calls.append(str(org_id))
        return [{"content": LONG_MEETING_NOTE, "created_at": "2026-06-01"}]

    def person(self, person_id):
        self.person_calls.append(int(person_id))
        return {"primary_email": PEOPLE[int(person_id)]}


def _source(client, owner_emails):
    return AffinityProspectSource(
        client, list_name="Glide Prospects", owner_emails=owner_emails
    )


def test_owner_filter_resolves_person_id_and_keeps_allowed_owner():
    client = FakeAffinityClient({"org-cristin": 194941263, "org-david": 185544917})
    source = _source(client, ["cristin.carter@glideplatform.com"])

    prospects = source.qualified_prospects()

    assert [p.org_id for p in prospects] == ["org-cristin"]
    assert prospects[0].owner_email == "Cristin.Carter@glideplatform.com"
    assert prospects[0].status == "Engaged - 25%"  # read via field_id, not field_name
    # David's row is filtered before its notes are fetched.
    assert client.notes_calls == ["org-cristin"]


def test_owner_resolution_is_cached_per_person():
    owners = {"a": 194941263, "b": 194941263, "c": 194941263}
    client = FakeAffinityClient(owners)
    _source(client, ["cristin.carter@glideplatform.com"]).qualified_prospects()
    # One /persons call for the single distinct owner id, not one per row.
    assert client.person_calls.count(194941263) == 1


def test_owner_filter_is_case_insensitive():
    client = FakeAffinityClient({"org-cristin": 194941263})
    source = _source(client, ["CRISTIN.CARTER@GLIDEPLATFORM.COM"])
    assert [p.org_id for p in source.qualified_prospects()] == ["org-cristin"]


def test_no_owner_filter_keeps_all_owners():
    for owner_emails in (None, []):
        client = FakeAffinityClient({"org-cristin": 194941263, "org-david": 185544917})
        prospects = _source(client, owner_emails).qualified_prospects()
        assert sorted(p.org_id for p in prospects) == ["org-cristin", "org-david"]


def test_owner_filter_accepts_single_string():
    client = FakeAffinityClient({"org-cristin": 194941263, "org-david": 185544917})
    source = _source(client, "cristin.carter@glideplatform.com")
    assert [p.org_id for p in source.qualified_prospects()] == ["org-cristin"]
