#!/usr/bin/env python3
"""Agent runtime for the Capital Call Notification Workflow skill.

Placeholder runtime — workflow-shape skill scaffolded as part of the TOS
family-office VOC upgrade (PR #465). The full event-driven implementation
(parse incoming GP cap call notice, log to ledger, route to bookkeeping
sink, reconcile against commitment register) is not in this PR.

The PILLAR constant below is the source of truth for catalog branding —
the family-office branding test (cpa-tax-package-checklist/tests/
test_branding.py::test_skill_branded_correctly) reads this value and
asserts the skill's `pillar:<x>` frontmatter tag matches it.
"""
from __future__ import annotations

import sys


PILLAR = "complexity-management"


def main(argv: list[str] | None = None) -> int:
    """Stub entrypoint.

    The capital-call workflow is event-triggered (an incoming GP notice),
    not interview-driven. The runtime that handles parse → fund → ledger →
    reconcile lives in a follow-up PR. This stub exists so the catalog
    branding test can read PILLAR.
    """
    print(
        "cap-call-notification-workflow: runtime not yet implemented. "
        "See SKILL.md for the workflow contract.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
