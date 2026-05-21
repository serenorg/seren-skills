"""Per-Lead enrichment recency ledger.

Replaces the legacy `Last_Enrichment_At__c` Salesforce custom field
that the original Phase 4 spec called for. Issue #563 collapsed the
field into a skill-owned SerenDB table because Nathan's operator
permission set in HU's Salesforce does not allow custom-field
creation, and the recency semantics are entirely internal to this
skill — Salesforce does not need to know when the cron last touched a
Lead.

The ledger is the single source of truth for the 24h "skip
recently-enriched Leads" gate that `write_note.write_note_to_lead`
enforces. Two methods:

* `read_last_enrichment(lead_id) -> datetime | None`
* `record_enrichment(lead_id, when, note_title, agent_run_id) -> None`

The Protocol exists so unit tests can inject a fake without standing
up `psycopg2` or a Postgres connection. Production wiring in
`scripts/agent.py` constructs the psycopg2-backed
`PsycopgEnrichmentLedger`, calls `ensure_schema` once at startup,
then passes it down to the Note-write path.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional, Protocol


# --------------------------------------------------------------------- #
# Protocol — the surface write_note_to_lead consumes                     #
# --------------------------------------------------------------------- #


class EnrichmentLedger(Protocol):
    """The read+write contract the Phase 4 recency gate depends on.

    Both methods are keyed by the Salesforce record id. `lead_id`
    is required to be unique per Lead — the underlying table uses
    it as PRIMARY KEY so duplicate writes UPSERT in place.
    """

    def read_last_enrichment(self, lead_id: str) -> Optional[datetime]: ...

    def record_enrichment(
        self,
        *,
        lead_id: str,
        when: datetime,
        note_title: str,
        agent_run_id: Optional[str] = None,
    ) -> None: ...


# --------------------------------------------------------------------- #
# psycopg2-backed production implementation                              #
# --------------------------------------------------------------------- #


# The DDL the ledger expects. Kept inline here (in addition to
# `serendb_schema.sql`) so `ensure_schema` is self-contained — a
# fresh deployment does not need to find and apply the schema file
# separately.
_DDL = """
CREATE TABLE IF NOT EXISTS pk_lead_enrichment_log (
    lead_id        TEXT PRIMARY KEY,
    enriched_at    TIMESTAMPTZ NOT NULL,
    note_title     TEXT,
    agent_run_id   TEXT
);
"""

_SELECT_LAST = """
SELECT enriched_at FROM pk_lead_enrichment_log WHERE lead_id = %s
"""

_UPSERT = """
INSERT INTO pk_lead_enrichment_log (lead_id, enriched_at, note_title, agent_run_id)
VALUES (%s, %s, %s, %s)
ON CONFLICT (lead_id) DO UPDATE
SET enriched_at = EXCLUDED.enriched_at,
    note_title = EXCLUDED.note_title,
    agent_run_id = EXCLUDED.agent_run_id
"""


class PsycopgEnrichmentLedger:
    """Production ledger backed by `psycopg2` against a SerenDB Postgres.

    Construction is cheap — it does not open a connection. The first
    read/write call opens a connection lazily (a new one per call)
    and lets the caller handle pooling concerns elsewhere. Phase 4's
    cron tick is one read + at most one write per Lead, so the
    short-lived-connection pattern is fine; if Phase 5 cron volume
    grows, swap in a pool here.
    """

    def __init__(self, connection_uri: str) -> None:
        if not connection_uri:
            raise ValueError(
                "PsycopgEnrichmentLedger requires a non-empty "
                "connection_uri. Call bootstrap_serendb() first to "
                "obtain one."
            )
        self._uri = connection_uri

    def ensure_schema(self) -> None:
        """Apply the `pk_lead_enrichment_log` DDL idempotently.

        Safe to call on every cron tick. The DDL uses `CREATE TABLE
        IF NOT EXISTS` so existing data is preserved.
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_DDL)
            conn.commit()

    def read_last_enrichment(self, lead_id: str) -> Optional[datetime]:
        """Return the most recent enrichment timestamp for `lead_id`.

        Returns `None` when no row exists for this Lead — the cron
        treats that as "never enriched" and proceeds to write.
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_SELECT_LAST, (lead_id,))
                row = cur.fetchone()
        return row[0] if row else None

    def record_enrichment(
        self,
        *,
        lead_id: str,
        when: datetime,
        note_title: str,
        agent_run_id: Optional[str] = None,
    ) -> None:
        """UPSERT the enrichment row for `lead_id`.

        Called after a successful Note write — the write must precede
        this call so the timestamp never lies about a Note that does
        not exist. `write_note.write_note_to_lead` enforces that
        order.
        """

        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(_UPSERT, (lead_id, when, note_title, agent_run_id))
            conn.commit()

    def _connect(self):
        """Open a fresh psycopg2 connection.

        Import is lazy so the unit tests (which inject the Protocol
        fake) do not pull `psycopg2` into the test environment.
        """

        import psycopg2  # noqa: PLC0415

        return psycopg2.connect(self._uri)
