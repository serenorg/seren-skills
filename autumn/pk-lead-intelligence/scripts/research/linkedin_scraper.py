"""LinkedIn profile scraper (issue #781).

Two halves:

* `extract_profile_from_html` — pure, takes a raw HTML string and
  returns Optional[LinkedInProfile]. This is the load-bearing surface
  and the only piece worth unit-testing; selector drift is the
  dominant failure mode and the test fixtures pin exactly what we
  expect to extract.

* `scrape_profile` — thin Playwright wrapper. Navigates to the URL on
  the operator's already-authenticated browser context (the same
  Page the Salesforce flow uses), waits for the headline anchor, and
  hands the DOM to the extractor. Not unit-tested — selector drift is
  detected via the operator-driven end-to-end run against real
  LinkedIn.

Soft-fail contract: every error path returns None instead of raising,
so a single bad profile cannot abort the enrichment batch. The agent
counts None-when-attempted as `linkedin_signed_out` on the run
summary so the operator sees scraper health from the cron line.

Auth model: this module never signs in to LinkedIn. The operator does
a one-time `--headful` sign-in (the same Playwright context already
used for Salesforce); cookies persist in `state/playwright_storage.json`
and ride forward across runs. If the cookie has expired LinkedIn
serves the signed-out gate, the extractor detects it, and we return
None — which the operator sees as a `linkedin_signed_out=N` blip on
the next cron line and re-runs `--headful` once to refresh.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from bs4 import BeautifulSoup  # type: ignore[import-untyped]


# --------------------------------------------------------------------- #
# Result types                                                           #
# --------------------------------------------------------------------- #


@dataclass(frozen=True)
class PriorRole:
    """One role pulled from the experience section."""

    title: str
    company: str
    duration_label: str


@dataclass(frozen=True)
class Education:
    """One entry from the education section."""

    school: str
    degree: Optional[str]
    field: Optional[str]
    duration_label: Optional[str]


@dataclass(frozen=True)
class ActivityItem:
    """One post / comment / reaction surfaced on the activity feed."""

    kind: str
    snippet: str
    posted_at_label: str


@dataclass(frozen=True)
class LinkedInProfile:
    """Structured snapshot of a single LinkedIn profile.

    Fields default to None / [] when the corresponding section is
    absent or unparseable — the renderer reads each field
    independently and falls back to its existing `(not surfaced)`
    marker for any None.
    """

    url: str
    headline: Optional[str]
    current_title: Optional[str]
    current_company: Optional[str]
    current_tenure_months: Optional[int]
    location: Optional[str]
    prior_roles: list[PriorRole] = field(default_factory=list)
    education: list[Education] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    recent_activity: list[ActivityItem] = field(default_factory=list)
    fetched_at_utc: str = ""


# --------------------------------------------------------------------- #
# Selectors                                                              #
# --------------------------------------------------------------------- #


# Operator-tunable selector set. LinkedIn rotates class names a few
# times a year; a one-line edit here is the supported drift response.
# When a selector misses, the corresponding field defaults to None /
# [] and the renderer falls through to its not-surfaced marker — the
# scraper never raises on a missing selector.
_SELECTORS = {
    "headline": "div.text-body-medium.break-words",
    "name_header": "h1.text-heading-xlarge",
    "location": "div.text-body-small.inline.t-black--light.break-words",
    "experience_section": "section#experience",
    "experience_item": '[data-test-id="experience-item"]',
    "experience_title": "div.t-bold",
    "experience_company": "div.t-14.t-normal",
    "experience_duration": "div.t-14.t-normal.t-black--light",
    "education_section": "section#education",
    "education_item": '[data-test-id="education-item"]',
    "skills_section": "section#skills",
    "skills_item": '[data-test-id="skill-item"]',
    "activity_section": '[data-test-id="activity-section"]',
    "activity_item": '[data-test-id="activity-item"]',
    "activity_kind": '[data-test-id="activity-kind"]',
    "activity_snippet": '[data-test-id="activity-snippet"]',
    "activity_posted_at": '[data-test-id="activity-posted-at"]',
    "signed_out_marker": ".join-form-page",
}


# Caps — each section truncates to this size so a single profile cannot
# blow out the Note's rendered length.
_MAX_PRIOR_ROLES = 3
_MAX_EDUCATION = 2
_MAX_SKILLS = 5
_MAX_ACTIVITY = 3


# --------------------------------------------------------------------- #
# Tenure parsing                                                         #
# --------------------------------------------------------------------- #


# Matches "2 yrs", "5 yr", "10 yrs"
_YEARS_RE = re.compile(r"(\d+)\s*yrs?\b", re.IGNORECASE)
# Matches "4 mos", "1 mo"
_MONTHS_RE = re.compile(r"(\d+)\s*mos?\b", re.IGNORECASE)


def _parse_tenure_months(duration_label: str) -> Optional[int]:
    """Convert a LinkedIn duration label to integer months.

    LinkedIn renders durations as `"Jan 2023 - Present · 2 yrs 4 mos"`
    with a tail describing total elapsed time. We pull the trailing
    `N yrs M mos` and convert.

    Returns None when neither years nor months can be parsed — the
    caller should not coerce None to 0, since 0 means "they just
    started" and None means "we do not know."
    """

    if not duration_label:
        return None

    years_match = _YEARS_RE.search(duration_label)
    months_match = _MONTHS_RE.search(duration_label)

    if years_match is None and months_match is None:
        return None

    total = 0
    if years_match is not None:
        total += int(years_match.group(1)) * 12
    if months_match is not None:
        total += int(months_match.group(1))
    return total


# --------------------------------------------------------------------- #
# Section extractors (pure)                                              #
# --------------------------------------------------------------------- #


def _text(node: Optional[Any]) -> Optional[str]:
    if node is None:
        return None
    text = node.get_text(strip=True)
    return text or None


def _looks_signed_out(soup: BeautifulSoup) -> bool:
    """Detect LinkedIn's signed-out public gate.

    The gate uses a `.join-form-page` class on a container element and
    typically renders a "Sign in to see <name>'s full profile" prompt.
    When this marker is present we refuse to extract anything — a
    half-populated profile is worse than no profile, because the
    renderer would then print "(not surfaced)" for the fields we
    failed on while presenting the rest as authoritative.
    """

    if soup.select_one(_SELECTORS["signed_out_marker"]) is not None:
        return True
    # Defensive secondary check — if the explicit class drifts, fall
    # back to a body-text scan for the canonical CTA.
    text = soup.get_text(" ", strip=True).lower()
    return "sign in to see" in text and "full profile" in text


def _extract_headline(soup: BeautifulSoup) -> Optional[str]:
    return _text(soup.select_one(_SELECTORS["headline"]))


def _extract_location(soup: BeautifulSoup) -> Optional[str]:
    return _text(soup.select_one(_SELECTORS["location"]))


def _parse_experience_item(item: Any) -> Optional[PriorRole]:
    title = _text(item.select_one(_SELECTORS["experience_title"]))
    # The first `.t-14.t-normal` is the company; the
    # `.t-14.t-normal.t-black--light` (more specific) is the duration.
    # Select all `.t-14.t-normal`, take the first that is NOT the
    # duration node.
    duration_node = item.select_one(_SELECTORS["experience_duration"])
    duration_label = _text(duration_node) or ""

    company = None
    for candidate in item.select(_SELECTORS["experience_company"]):
        if duration_node is not None and candidate is duration_node:
            continue
        company = _text(candidate)
        if company:
            break

    if not title or not company:
        return None
    return PriorRole(
        title=title,
        company=company,
        duration_label=duration_label,
    )


def _extract_prior_roles(soup: BeautifulSoup) -> list[PriorRole]:
    section = soup.select_one(_SELECTORS["experience_section"])
    if section is None:
        return []
    items = section.select(_SELECTORS["experience_item"])
    roles: list[PriorRole] = []
    for item in items:
        role = _parse_experience_item(item)
        if role is not None:
            roles.append(role)
        if len(roles) >= _MAX_PRIOR_ROLES:
            break
    return roles


# Degree shapes like "MBA, Supply Chain Management" or "BS, Industrial
# Engineering" — split on the first comma. When no comma is present
# the whole string is the degree and field is None.
def _split_degree_field(raw: str) -> tuple[Optional[str], Optional[str]]:
    if not raw:
        return None, None
    if "," in raw:
        head, tail = raw.split(",", 1)
        return head.strip() or None, tail.strip() or None
    return raw.strip() or None, None


def _parse_education_item(item: Any) -> Optional[Education]:
    school = _text(item.select_one(_SELECTORS["experience_title"]))
    if not school:
        return None
    # School + degree/field share the same `.t-14.t-normal` slot
    # pattern. Same trick as experience: filter the duration node.
    duration_node = item.select_one(_SELECTORS["experience_duration"])
    duration_label = _text(duration_node)

    degree_raw = ""
    for candidate in item.select(_SELECTORS["experience_company"]):
        if duration_node is not None and candidate is duration_node:
            continue
        degree_raw = _text(candidate) or ""
        if degree_raw:
            break

    degree, field_str = _split_degree_field(degree_raw)
    return Education(
        school=school,
        degree=degree,
        field=field_str,
        duration_label=duration_label,
    )


def _extract_education(soup: BeautifulSoup) -> list[Education]:
    section = soup.select_one(_SELECTORS["education_section"])
    if section is None:
        return []
    items = section.select(_SELECTORS["education_item"])
    out: list[Education] = []
    for item in items:
        edu = _parse_education_item(item)
        if edu is not None:
            out.append(edu)
        if len(out) >= _MAX_EDUCATION:
            break
    return out


def _extract_skills(soup: BeautifulSoup) -> list[str]:
    section = soup.select_one(_SELECTORS["skills_section"])
    if section is None:
        return []
    skills: list[str] = []
    for item in section.select(_SELECTORS["skills_item"]):
        text = _text(item)
        if text:
            skills.append(text)
        if len(skills) >= _MAX_SKILLS:
            break
    return skills


def _parse_activity_item(item: Any) -> Optional[ActivityItem]:
    kind = _text(item.select_one(_SELECTORS["activity_kind"]))
    snippet = _text(item.select_one(_SELECTORS["activity_snippet"]))
    posted_at = _text(item.select_one(_SELECTORS["activity_posted_at"]))
    if not snippet:
        return None
    return ActivityItem(
        kind=kind or "post",
        snippet=snippet,
        posted_at_label=posted_at or "",
    )


def _extract_recent_activity(soup: BeautifulSoup) -> list[ActivityItem]:
    section = soup.select_one(_SELECTORS["activity_section"])
    if section is None:
        return []
    items = section.select(_SELECTORS["activity_item"])
    out: list[ActivityItem] = []
    for item in items:
        activity = _parse_activity_item(item)
        if activity is not None:
            out.append(activity)
        if len(out) >= _MAX_ACTIVITY:
            break
    return out


def _split_headline(headline: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """LinkedIn headlines often render as `"<title> at <company>"`. Split
    on the canonical separator. Headlines that do not fit the pattern
    return (headline, None) so the title is still available.
    """

    if not headline:
        return None, None
    parts = re.split(r"\s+at\s+", headline, maxsplit=1, flags=re.IGNORECASE)
    if len(parts) == 2:
        return parts[0].strip() or None, parts[1].strip() or None
    return headline.strip() or None, None


# --------------------------------------------------------------------- #
# Public extractor                                                       #
# --------------------------------------------------------------------- #


def extract_profile_from_html(*, html: str, url: str) -> Optional[LinkedInProfile]:
    """Pure extraction over a LinkedIn profile HTML string.

    Returns None for the signed-out gate, malformed HTML, or any
    unexpected parsing failure. Callers should treat None as "we
    don't know" and let the renderer fall back to not-surfaced markers.
    """

    if not html or not html.strip():
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception:
        # BeautifulSoup with the stdlib parser does not raise on
        # malformed input in practice, but catch defensively so a
        # future parser swap cannot break the soft-fail contract.
        return None

    if _looks_signed_out(soup):
        return None

    try:
        headline = _extract_headline(soup)
        # Be conservative: if there is no headline AND no experience
        # AND no education AND no skills, this is not a usable profile.
        prior_roles = _extract_prior_roles(soup)
        education = _extract_education(soup)
        skills = _extract_skills(soup)
        activity = _extract_recent_activity(soup)

        if (
            headline is None
            and not prior_roles
            and not education
            and not skills
            and not activity
        ):
            return None

        current_title, headline_company = _split_headline(headline)
        # When the headline yields a company, trust it for the
        # `current_company` field; otherwise fall back to the most
        # recent experience entry.
        current_company = headline_company
        current_tenure_months: Optional[int] = None
        if prior_roles:
            most_recent = prior_roles[0]
            if current_company is None:
                current_company = most_recent.company
            # Tenure is the most-recent role's months.
            current_tenure_months = _parse_tenure_months(most_recent.duration_label)
            # When the headline did not give us a title, take the most
            # recent role's title.
            if current_title is None:
                current_title = most_recent.title

        location = _extract_location(soup)
        fetched_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return LinkedInProfile(
            url=url,
            headline=headline,
            current_title=current_title,
            current_company=current_company,
            current_tenure_months=current_tenure_months,
            location=location,
            prior_roles=prior_roles,
            education=education,
            skills=skills,
            recent_activity=activity,
            fetched_at_utc=fetched_at,
        )
    except Exception:
        # Soft-fail on any unexpected parse error — a single bad page
        # must never abort the enrichment batch.
        return None


# --------------------------------------------------------------------- #
# Playwright-driven entry (not unit-tested — operator dry-run)           #
# --------------------------------------------------------------------- #


# Sentinel timeout matched to LinkedIn's median TTFB + render. Set
# slightly tighter than Salesforce navigation (which has internal
# Lightning waits) so a stalled LinkedIn load does not block the rest
# of the enrichment cycle.
_NAV_TIMEOUT_MS = 30_000
_HEADLINE_WAIT_MS = 10_000


def scrape_profile(
    *,
    profile_url: str,
    page: Any,
    timeout_ms: int = _NAV_TIMEOUT_MS,
) -> Optional[LinkedInProfile]:  # pragma: no cover
    """Drive the Playwright Page to `profile_url` and extract.

    Reuses the same `Page` the Salesforce flow already opened so the
    LinkedIn session cookies in `state/playwright_storage.json` ride
    across the request without a re-auth.

    Marked `pragma: no cover` because the live behavior requires
    Playwright + a real LinkedIn profile + an authenticated session.
    The pure extractor is unit-tested via fixtures.

    Returns None on any of: navigation failure, headline timeout,
    signed-out gate, parse failure. Soft-fail end to end.
    """

    try:
        page.goto(profile_url, timeout=timeout_ms)
    except Exception:
        return None
    try:
        page.wait_for_selector(
            _SELECTORS["headline"], timeout=_HEADLINE_WAIT_MS
        )
    except Exception:
        # No headline within budget — could be signed-out, could be a
        # selector rotation. Either way, extract from whatever DOM we
        # have; the extractor will return None if nothing usable is
        # present.
        pass

    try:
        html = page.content()
    except Exception:
        return None
    return extract_profile_from_html(html=html, url=profile_url)
