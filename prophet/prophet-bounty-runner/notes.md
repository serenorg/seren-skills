# prophet-bounty-runner — implementation notes

Tracks decisions that the plan asks the implementer to leave a breadcrumb
for, so the DRY pass after both skills package cleanly can find them.

## Phase 9 — duplicated functions from prophet-market-seeder

Per plan §15.1 (small-copy fallback), the score heuristic was copied
rather than imported. The seeder's `generate_market_candidates` is
template-driven and incompatible with the bounty runner's
PolymarketSource-driven flow, so a slim adapter was written instead of
a full sibling import.

**Duplicated (copy of the score formula):**

- `prophet/prophet-bounty-runner/scripts/candidates.py::score_candidates`
- mirrors `prophet/prophet-market-seeder/scripts/agent.py::score_market_candidates`
- same clarity / has_date / category-diversity weights (0.3 / 0.3 / 0.4)

**Bounty-runner-specific (no source-skill equivalent):**

- `candidates.generate_candidates(polymarket_sources, n)` — maps
  PolymarketSource → Candidate
- `candidates.filter_candidates(scored, submit_limit)` — score-threshold
  + submit_limit cap (the seeder filter dedups by recent titles via
  SerenDB; the bounty runner does Prophet-side dedup separately in
  Phase 10's `dedup_against_prophet` step)

**Follow-up (after Phase 11):** package `prophet-market-seeder` as an
editable package via `pyproject.toml` so siblings can `from
prophet.prophet_market_seeder.scripts.agent import
score_market_candidates`. Then collapse the duplicate. Do NOT do this
inside the bounty-runner PR — it touches the source skill.
