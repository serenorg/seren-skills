# Seren Bucks V1 Provider Mapping

| Capability | Provider | Role in v1 |
| --- | --- | --- |
| Affiliate attribution and performance | `seren-affiliates` | Source of truth for program metrics |
| Gmail sent mail history | `gmail` | Candidate discovery from sent emails |
| Gmail address books | `google-contacts` | Candidate discovery from contacts (People API) |
| Outlook sent mail history | `outlook` | Candidate discovery from sent emails |
| Outlook address books | `outlook-contacts` | Candidate discovery from contacts |
| Skill-owned CRM and run memory | `serendb` | Source of truth after persistence |
| Draft generation support | `seren-models` | Optional drafting and rewrite support |

## Publisher Scope Boundaries

- `gmail` — scoped to email operations only: `/messages`, `/labels`, `/threads`, `/drafts`
- `google-contacts` — scoped to People API: `/otherContacts`, `/people:searchContacts`
- `outlook` — scoped to email operations only
- `outlook-contacts` — scoped to contacts API

## Degradation behavior

- `seren-affiliates` failure before bootstrap completion blocks the run.
- Gmail or Outlook failure after bootstrap degrades the run but does not block if at least one candidate source remains available.
