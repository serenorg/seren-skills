"""Market quality filter for auto-discover (#633).

The auto-discover funnel pulls every active Polymarket market that
passes the volume + resolution-window gates. Pre-#633 that was the
entire filter — any market with $10k+ 24h volume and a resolution date
in the campaign window was seeded with operator capital.

The cost of that was real. Tweet-count buckets, subjective political
events ("will X insult Y"), and date-bounded geopolitical questions
("airspace closes by Friday") resolve ambiguously often enough that a
single mismatch between Prophet and Polymarket wipes out weeks of
small arb wins. At the operator's typical $1-per-seed scale,
default-deny on unrecognized question patterns is the economically
correct policy: a false positive costs an opportunity, a false negative
costs real money.

This module is pure — no I/O, no publisher calls. The auto-discover
loop in ``polymarket.discovery.discover_arb_candidates_with_stats``
calls ``is_high_quality_resolution`` at the same gate level as the
volume and date filters, BEFORE incrementing
``markets_passing_gates`` so the run summary reflects only markets
the bot would actually want to pair.
"""

from __future__ import annotations

# Hard-reject patterns. Each entry is matched as a case-insensitive
# substring against the question text. Order doesn't matter — any
# match drops the candidate.
#
# These came from the 2026-05-17 live candidate sheet. Each represents
# a class of markets that resolved ambiguously enough to cost real money
# under the arb-bot's delta-neutral execution model, where Prophet and
# Polymarket each settle independently and may disagree.
_REJECT_PATTERNS: tuple[str, ...] = (
    # Tweet-count buckets — sampling-window ambiguity, resolutions
    # disputed when the count lands near a bucket boundary.
    "tweets from",
    "tweet count",
    "of tweets",
    # Subjective political events — "insult", "announce", "say" all
    # depend on the resolver's interpretation of news coverage.
    "will donald trump announce",
    "will trump insult",
    "will trump say",
    "will biden announce",
    "will biden say",
    "will the president announce",
    "will the white house",
    # Date-bounded geopolitical events — "closes its airspace by Friday"
    # depends on what "closes" means and whose newswire counts as the
    # resolution source.
    "closes its airspace",
    "blockade of",
    "lifted by",
)


# Hard-accept patterns. Same matching rule (case-insensitive substring),
# but matching ANY of these returns True. Default behavior on
# zero-match is False (deny).
#
# Sports vs-match: " vs. " or " vs " in the question consistently
# indicates a team-vs-team market that resolves on the official final
# score. The bot already saw MLB, NBA, EPL, NCAA, CBA in the live
# candidate sheet — all of them parse cleanly into this pattern.
#
# Binary financial: explicit threshold language ("above $", "greater
# than 25m", "to close above") indicates an objective numeric
# resolution.
#
# Named election: "republican nominee for" / "democratic nominee for"
# / specific primary patterns resolve on official tallies.
_ACCEPT_PATTERNS: tuple[str, ...] = (
    # Sports — team-vs-team. The trailing space and period variants
    # cover both " vs. " (most common) and " vs " (used in some leagues).
    " vs. ",
    " vs ",
    # Single-team win/loss with a date — "Will Arsenal win on 2026-05-18?"
    # The full phrase is too specific to keyword-match; we rely on
    # category=Sports being set in the upstream filter or operator
    # whitelist. (Today this falls through to default-deny; revisit
    # if operators want to relax.)
    # Binary financial thresholds — covers "above $", "below $",
    # "greater than", "less than" with explicit numeric or dollar
    # amounts. "Box office" is a special-case because the question
    # phrasing varies ("4th Weekend Box Office be greater than 25m").
    "box office",
    "to close above",
    "to close below",
    "above $",
    "below $",
    "greater than $",
    "less than $",
    "exceed $",
    "reach $",
    # Named election results — both major-party patterns.
    "republican nominee for",
    "democratic nominee for",
    "win the primary",
    "win the general",
)


def is_high_quality_resolution(
    *, question: str, category: str | None = None, slug: str | None = None
) -> bool:
    """Return True iff this market resolves on objective criteria.

    Hard-reject patterns take precedence — if a market matches any
    reject pattern it is rejected regardless of accept matches. Default
    on no-accept-match is False (deny), so the operator's seed capital
    is only ever exposed to markets the filter explicitly recognizes
    as objective.

    Parameters
    ----------
    question
        The Polymarket market question text.
    category
        Polymarket's category field. Currently advisory only — most
        live rows return ``None`` or ``"Other"``, so the filter can't
        rely on it. Reserved for a future enhancement that combines
        category with question-pattern matching.
    slug
        Polymarket slug. Currently advisory only — reserved for
        the same future enhancement.
    """
    q = (question or "").strip().lower()
    if not q:
        return False

    # Phase 1 — hard reject. Each reject pattern represents a class
    # of markets that has previously cost real operator money.
    for bad in _REJECT_PATTERNS:
        if bad in q:
            return False

    # Phase 2 — category-based accept. Polymarket's "Sports" category
    # consistently resolves on official scores/standings; single-team
    # win questions ("Will Arsenal FC win on 2026-05-18?") that don't
    # match the vs-pattern still belong in this bucket.
    if (category or "").strip().lower() == "sports":
        return True

    # Phase 3 — hard accept by question pattern. Each accept pattern
    # represents a class of markets that resolves on objectively-
    # verifiable criteria (sports vs-match, binary financial threshold,
    # named election result).
    for good in _ACCEPT_PATTERNS:
        if good in q:
            return True

    # Phase 4 — default deny. Unrecognized patterns don't get seed
    # capital. The operator can extend _ACCEPT_PATTERNS as new
    # known-good market classes appear.
    return False
