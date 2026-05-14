"""CSS / role selectors for Salesforce Lightning + Microsoft SSO.

Kept in one module so selector drift produces one-line diffs. Updated
whenever Salesforce or Microsoft rotates a label or markup.

The values below are best-guess defaults that match the standard
Microsoft Entra (Azure AD) sign-in flow and the stock Salesforce
Lightning experience. The first live dry-run with the operator
watching will surface any selector that needs tuning — patch the
constant in place and the rest of the codebase picks it up.
"""

from __future__ import annotations

# --------------------------------------------------------------------- #
# Salesforce login page (pre-SSO redirect)                              #
# --------------------------------------------------------------------- #

# The Salesforce login page renders one button per configured SSO
# provider. Labels are customer-specific — HU's button reads
# "Log in with HU Azure"; other orgs use "Log in with Microsoft" or
# a generic "Single Sign-On". We match by visible text with a
# permissive alternation that absorbs the labels we have seen in
# the field. Add new customer labels here as they surface.
SF_LOGIN_MICROSOFT_SSO_BUTTON = "text=/Microsoft|Azure|Single Sign-On/i"


# --------------------------------------------------------------------- #
# Microsoft Entra sign-in pages                                         #
# --------------------------------------------------------------------- #

# The email-or-username field. `loginfmt` is the stable form-field
# name Microsoft has used since the AAD/Entra rebrand.
MS_EMAIL_INPUT = 'input[name="loginfmt"]'

# Next/Submit on the email screen. The button is `input[type=submit]`
# with id `idSIButton9` — both selectors target the same element; we
# prefer the type selector so a label rotation does not break us.
MS_EMAIL_SUBMIT = 'input[type="submit"]'

# Password field. `passwd` is the stable form-field name.
MS_PASSWORD_INPUT = 'input[name="passwd"]'
MS_PASSWORD_SUBMIT = 'input[type="submit"]'

# TOTP code field for "Authenticator app or hardware token" flow.
# Microsoft renders this as a single 6-digit input with name `otc`.
MS_TOTP_INPUT = 'input[name="otc"]'
MS_TOTP_SUBMIT = 'input[type="submit"]'

# The "Stay signed in?" interstitial that Microsoft shows after a
# successful sign-in. Clicking "No" keeps the session ephemeral; "Yes"
# extends it. We click "No" to keep Playwright storage_state as the
# single source of truth for session persistence.
MS_STAY_SIGNED_IN_NO = 'input[id="idBtn_Back"]'


# --------------------------------------------------------------------- #
# Salesforce Lightning (post-auth)                                      #
# --------------------------------------------------------------------- #

# Sentinel that indicates we have landed in Lightning after SSO.
# `[role="main"]` is rendered by Lightning on every authenticated
# page before the per-app chrome shows up — earlier than the App
# Launcher button, which in HU's org is only present on certain
# layouts. Live audit (2026-05-14) confirmed this in HU's Lightning.
SF_LIGHTNING_AUTHENTICATED_SENTINEL = '[role="main"]'

# The Lead list view. `/lightning/o/Lead/list` is the stock relative
# URL; we append it to the configured org URL at runtime. The
# `__Recent` default filter can be empty for an account that has not
# viewed leads in this session, so we force the populated
# `AllOpenLeads` filter to guarantee at least one row hydrates.
SF_LEAD_LIST_PATH = "/lightning/o/Lead/list?filterName=AllOpenLeads"

# First data row inside the Lightning list-view DataTable. Live
# audit showed HU's Lightning datatable annotates each row with
# `data-row-key-value=<recordId>`; the `HEADER` row carries the
# literal string `HEADER`, so a `tr[data-row-key-value]:not([data-row-key-value="HEADER"])`
# match isolates the first real data row regardless of the
# Salesforce table role (`grid` vs missing on some pages).
SF_LEAD_LIST_FIRST_ROW = (
    'tr[data-row-key-value]:not([data-row-key-value="HEADER"])'
)

# Within a Lead row, the link cell that exposes the Lead Name and its
# record id. HU's Lightning emits hrefs of the form
# `/lightning/r/<recordId>/view` (no entity slug) and does not set
# `data-refid="recordId"` on the anchor. The first anchor whose href
# starts with `/lightning/r/` and ends with `/view` is the Name
# link; columns like Email render `mailto:` anchors that we skip via
# the href prefix filter.
SF_LEAD_ROW_NAME_LINK = 'a[href^="/lightning/r/"][href$="/view"]'
