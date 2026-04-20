"""Shared runtime for the family-office skill catalog.

Public API is re-exported here so leaf skills import from a single module:

    from family_office_base import (
        run_schema_guard, audit_query, AuditQueryError,
        CONFIDENTIALITY_LABELS, confidentiality_check, ConfidentialityError,
        memory_read, memory_write, MemoryRecord,
    )

Single-family-office tenancy. No client_id. audit_query is the only permitted
read/write path to the family-office database. Schema guard is the only permitted
DDL path. See the family-office design doc, §5 for the canonical object model.
"""

from .audit_query import AuditQueryError, Runner, audit_query
from .confidentiality import (
    CONFIDENTIALITY_LABELS,
    ConfidentialityError,
    confidentiality_check,
    visible_labels_for_role,
)
from .memory import MemoryRecord, memory_read, memory_write
from .schema_guard import SchemaGuardError, run_schema_guard

__all__ = [
    "AuditQueryError",
    "CONFIDENTIALITY_LABELS",
    "ConfidentialityError",
    "MemoryRecord",
    "Runner",
    "SchemaGuardError",
    "audit_query",
    "confidentiality_check",
    "memory_read",
    "memory_write",
    "run_schema_guard",
    "visible_labels_for_role",
]

__version__ = "0.1.0"
