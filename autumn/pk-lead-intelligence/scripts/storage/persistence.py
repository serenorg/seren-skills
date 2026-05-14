"""SerenDB persistence layer.

Wraps `psycopg2` connections that the skill uses for the canonical
`enriched_leads` ledger, run history, and weekly status records.
Implemented in phase 1 (schema guard) and extended through phase 4.
"""
