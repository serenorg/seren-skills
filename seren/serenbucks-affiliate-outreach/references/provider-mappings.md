# SerenBucks Affiliate Outreach V1 Provider Mapping

| Capability | Provider | Role in v1 |
| --- | --- | --- |
| Affiliate attribution and performance | `seren-affiliates` | Source of truth for campaign metrics |
| Gmail sent history and address books | `gmail` | Candidate discovery input |
| Outlook sent history and address books | `outlook` | Candidate discovery input |
| Skill-owned CRM and run memory | `serendb` | Source of truth after persistence |
| Draft generation support | `seren-models` | Optional drafting and rewrite support |

## Degradation behavior

- `seren-affiliates` failure before bootstrap completion blocks the run.
- Gmail or Outlook failure after bootstrap degrades the run but does not block if at least one candidate source remains available.
