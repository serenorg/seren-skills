"""Critical tests for scripts/research/linkedin_scraper.py.

The scraper has two halves:

  * `extract_profile_from_html` — pure, takes a raw HTML string and
    returns Optional[LinkedInProfile]. This is the only piece worth
    unit-testing; selector drift is the dominant failure mode and the
    fixtures pin exactly what we expect to extract.
  * `scrape_profile` — thin Playwright wrapper around the extractor.
    Not unit-tested (selector drift detected via operator-driven
    end-to-end runs against real LinkedIn).

Three fixtures cover the three operator-visible states:

  * full profile (every field populated)
  * partial profile (headline + experience only — common when a person
    has not filled out education or skills)
  * signed-out view (LinkedIn's public gate that hides everything
    except the name)

Plus one negative test for the soft-fail contract: malformed HTML
must return None rather than raising.
"""

from __future__ import annotations

import pytest

from scripts.research import linkedin_scraper


# --------------------------------------------------------------------- #
# HTML fixtures                                                          #
# --------------------------------------------------------------------- #


# Minimal, realistic LinkedIn DOM. LinkedIn changes class names
# regularly; the scraper reads the locked-class-name set declared in
# `linkedin_scraper._SELECTORS`. When LinkedIn rotates, this fixture
# moves to match and the test catches the drift.
_FULL_PROFILE_HTML = """
<html>
<head><title>Jane Doe | LinkedIn</title></head>
<body>
  <main>
    <h1 class="text-heading-xlarge">Jane Doe</h1>
    <div class="text-body-medium break-words">
      Director of Procurement at Acme Packaging
    </div>
    <div class="text-body-small inline t-black--light break-words">
      San Francisco Bay Area
    </div>
    <section id="experience" data-section="experience">
      <ul>
        <li data-test-id="experience-item">
          <div class="t-bold">Director of Procurement</div>
          <div class="t-14 t-normal">Acme Packaging</div>
          <div class="t-14 t-normal t-black--light">Jan 2023 - Present · 2 yrs 4 mos</div>
        </li>
        <li data-test-id="experience-item">
          <div class="t-bold">Senior Buyer</div>
          <div class="t-14 t-normal">BrightFoods Inc</div>
          <div class="t-14 t-normal t-black--light">Mar 2018 - Dec 2022 · 4 yrs 10 mos</div>
        </li>
        <li data-test-id="experience-item">
          <div class="t-bold">Procurement Analyst</div>
          <div class="t-14 t-normal">GlobalCo</div>
          <div class="t-14 t-normal t-black--light">Aug 2014 - Feb 2018 · 3 yrs 7 mos</div>
        </li>
      </ul>
    </section>
    <section id="education" data-section="education">
      <ul>
        <li data-test-id="education-item">
          <div class="t-bold">Stanford University</div>
          <div class="t-14 t-normal">MBA, Supply Chain Management</div>
          <div class="t-14 t-normal t-black--light">2012 - 2014</div>
        </li>
        <li data-test-id="education-item">
          <div class="t-bold">UC Berkeley</div>
          <div class="t-14 t-normal">BS, Industrial Engineering</div>
          <div class="t-14 t-normal t-black--light">2008 - 2012</div>
        </li>
      </ul>
    </section>
    <section id="skills" data-section="skills">
      <ul>
        <li data-test-id="skill-item"><span>Procurement</span></li>
        <li data-test-id="skill-item"><span>Supply Chain Management</span></li>
        <li data-test-id="skill-item"><span>Negotiation</span></li>
        <li data-test-id="skill-item"><span>Packaging</span></li>
        <li data-test-id="skill-item"><span>Vendor Management</span></li>
        <li data-test-id="skill-item"><span>Cost Reduction</span></li>
      </ul>
    </section>
    <section data-test-id="activity-section">
      <ul>
        <li data-test-id="activity-item">
          <span data-test-id="activity-kind">post</span>
          <p data-test-id="activity-snippet">Excited to share our new sustainable packaging line with food-safe ultrasonic-sealed pouches. Real progress on cutting glue and heat from our process.</p>
          <span data-test-id="activity-posted-at">3d</span>
        </li>
        <li data-test-id="activity-item">
          <span data-test-id="activity-kind">comment</span>
          <p data-test-id="activity-snippet">Great point on supplier diversification.</p>
          <span data-test-id="activity-posted-at">1w</span>
        </li>
      </ul>
    </section>
  </main>
</body>
</html>
"""


_PARTIAL_PROFILE_HTML = """
<html>
<body>
  <main>
    <h1 class="text-heading-xlarge">Bob Smith</h1>
    <div class="text-body-medium break-words">
      Plant Manager at SmallCo
    </div>
    <section id="experience" data-section="experience">
      <ul>
        <li data-test-id="experience-item">
          <div class="t-bold">Plant Manager</div>
          <div class="t-14 t-normal">SmallCo</div>
          <div class="t-14 t-normal t-black--light">Jun 2020 - Present · 4 yrs 11 mos</div>
        </li>
      </ul>
    </section>
  </main>
</body>
</html>
"""


# LinkedIn's signed-out public gate shows the name + a "Sign in to see"
# call to action but hides every section. The scraper detects this and
# returns None so the renderer falls through to not-surfaced markers.
_SIGNED_OUT_HTML = """
<html>
<body>
  <main>
    <h1 class="text-heading-xlarge">Carol Lee</h1>
    <div class="join-form-page">
      <h2>Sign in to see Carol's full profile</h2>
      <a href="/login">Sign in</a>
    </div>
  </main>
</body>
</html>
"""


# --------------------------------------------------------------------- #
# Tests                                                                  #
# --------------------------------------------------------------------- #


def test_full_profile_extracts_all_fields() -> None:
    """Lock the extraction contract against the canonical DOM shape.

    If LinkedIn rotates a class name the fixture moves to match and
    the assertion list moves with it.
    """

    profile = linkedin_scraper.extract_profile_from_html(
        html=_FULL_PROFILE_HTML,
        url="https://www.linkedin.com/in/jane-doe-test/",
    )

    assert profile is not None
    assert profile.url == "https://www.linkedin.com/in/jane-doe-test/"
    assert profile.headline == "Director of Procurement at Acme Packaging"
    assert profile.current_title == "Director of Procurement"
    assert profile.current_company == "Acme Packaging"
    assert profile.current_tenure_months == 28  # 2 yrs 4 mos
    assert profile.location == "San Francisco Bay Area"

    # Prior roles capped at 3, most-recent first.
    assert len(profile.prior_roles) == 3
    assert profile.prior_roles[0].title == "Director of Procurement"
    assert profile.prior_roles[0].company == "Acme Packaging"
    assert profile.prior_roles[1].company == "BrightFoods Inc"

    # Education capped at 2, most-recent first.
    assert len(profile.education) == 2
    assert profile.education[0].school == "Stanford University"
    assert profile.education[0].degree == "MBA"
    assert profile.education[0].field == "Supply Chain Management"

    # Skills capped at 5.
    assert len(profile.skills) == 5
    assert "Procurement" in profile.skills

    # Recent activity capped at 3.
    assert len(profile.recent_activity) == 2
    assert profile.recent_activity[0].kind == "post"
    assert "ultrasonic" in profile.recent_activity[0].snippet.lower()
    assert profile.recent_activity[0].posted_at_label == "3d"

    # Audit timestamp present and looks like ISO-8601 UTC.
    assert profile.fetched_at_utc.endswith("Z")
    assert "T" in profile.fetched_at_utc


def test_partial_profile_returns_what_is_present() -> None:
    """Missing sections render as None / empty lists, not exceptions.

    Many real LinkedIn profiles are sparse — the scraper must accept
    them without false-fails or the operator will turn the flag off.
    """

    profile = linkedin_scraper.extract_profile_from_html(
        html=_PARTIAL_PROFILE_HTML,
        url="https://www.linkedin.com/in/bob-smith-test/",
    )

    assert profile is not None
    assert profile.headline == "Plant Manager at SmallCo"
    assert profile.current_title == "Plant Manager"
    assert profile.current_company == "SmallCo"
    assert profile.location is None
    assert len(profile.prior_roles) == 1
    assert profile.education == []
    assert profile.skills == []
    assert profile.recent_activity == []


def test_signed_out_view_returns_none() -> None:
    """The signed-out gate is the dominant failure mode in production.

    When LinkedIn shows "Sign in to see <name>'s full profile" the
    scraper must return None — never a half-populated profile that the
    renderer then prints into the Note as if it were authoritative.
    """

    profile = linkedin_scraper.extract_profile_from_html(
        html=_SIGNED_OUT_HTML,
        url="https://www.linkedin.com/in/carol-lee-test/",
    )

    assert profile is None


def test_malformed_html_soft_fails_to_none() -> None:
    """Soft-fail contract: a parse error returns None, not raises.

    A single bad page must never abort the enrichment batch.
    """

    profile = linkedin_scraper.extract_profile_from_html(
        html="<<<not html<<<",
        url="https://www.linkedin.com/in/garbage/",
    )

    assert profile is None


def test_tenure_parser_handles_variants() -> None:
    """`current_tenure_months` is the load-bearing structured field.

    LinkedIn renders tenure in a handful of canonical shapes. This
    pins each one so future changes do not silently degrade the
    months value into garbage.
    """

    parse = linkedin_scraper._parse_tenure_months

    assert parse("Jan 2023 - Present · 2 yrs 4 mos") == 28
    assert parse("Mar 2018 - Dec 2022 · 4 yrs 10 mos") == 58
    assert parse("Jun 2024 - Present · 6 mos") == 6
    assert parse("2020 - Present · 5 yrs") == 60
    # Unparseable shape returns None rather than 0 — None is the
    # signal "we do not know"; 0 would be the signal "they just
    # started", which is a different claim.
    assert parse("on and off since college") is None
    assert parse("") is None
