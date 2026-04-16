"""No skill doc may reference the archived serenorg/seren-desktop-issues repo.

Fixes #370: the archived repo is read-only, so agents reading a skill doc
that advertises it as the issue tracker will fail to file bugs there. The
correct target is serenorg/seren-desktop/issues.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ARCHIVED = "seren-desktop-issues"

# rerere cache mirrors past merge conflicts verbatim; it's git metadata,
# not a source file, so skip it.
SKIP_PREFIXES = (".git/",)


def test_no_skill_doc_references_archived_desktop_issues_repo() -> None:
    offenders: list[str] = []
    for path in REPO_ROOT.rglob("*.md"):
        rel = str(path.relative_to(REPO_ROOT))
        if any(rel.startswith(p) for p in SKIP_PREFIXES):
            continue
        if ARCHIVED in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(rel)
    assert not offenders, (
        f"{len(offenders)} docs still reference the archived "
        f"serenorg/{ARCHIVED} repo:\n"
        + "\n".join(f"  - {p}" for p in sorted(offenders))
    )
