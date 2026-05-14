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


# --------------------------------------------------------------------- #
# Setup → Object Manager → Lead (Phase 3 field provisioning)            #
# --------------------------------------------------------------------- #

# Setup is served from `/lightning/setup/...` and renders inside an
# iframe in some orgs. The deep link below jumps straight to the
# Lead object's Fields & Relationships list — bypassing the App
# Launcher → Setup → Object Manager → Lead navigation chain.
# `FieldsAndRelationships` is the stable subpath the Object Manager
# uses to anchor the field list.
SF_SETUP_LEAD_FIELDS_PATH = (
    "/lightning/setup/ObjectManager/Lead/FieldsAndRelationships/view"
)

# The Object Manager fields table renders one row per existing
# field. Each row's Field Name cell carries an anchor whose visible
# text is the API name (with the `__c` suffix for custom fields).
# We collect every visible API-name anchor to build the
# existing-fields set for idempotency.
SF_SETUP_FIELDS_API_NAME_CELL = 'th[data-label="Field Name"] a'

# The "New" button on the fields table opens the New Custom Field
# wizard. Lightning renders it as a `<button>` with the visible
# label "New" inside the Object Manager toolbar.
SF_SETUP_FIELDS_NEW_BUTTON = 'button:has-text("New")'

# New Custom Field wizard. Step-2 inputs share names across types
# (`MasterLabel`, `DeveloperName`, `Description`). The data-type
# radio is selected on step 1 via a label-matching CSS selector
# built from the field-type string.
SF_NEW_FIELD_NEXT_BUTTON = 'button:has-text("Next")'
SF_NEW_FIELD_SAVE_BUTTON = 'button:has-text("Save")'
SF_NEW_FIELD_LABEL_INPUT = 'input[name="MasterLabel"]'
SF_NEW_FIELD_API_NAME_INPUT = 'input[name="DeveloperName"]'
SF_NEW_FIELD_DESCRIPTION_TEXTAREA = 'textarea[name="Description"]'

# Type-specific step-2 fields.
SF_NEW_FIELD_NUMBER_LENGTH_INPUT = 'input[name="Length"]'
SF_NEW_FIELD_NUMBER_DECIMALS_INPUT = 'input[name="Precision"]'


def sf_new_field_type_radio(field_type: str) -> str:
    """CSS selector for the data-type radio on New Custom Field step 1.

    Lightning labels each radio with the visible type string
    ("Checkbox", "Date/Time", "Number", etc.). Phase 3 needs the
    three the LEAD_FIELD_SPECS list references.
    """

    return f'label:has-text("{field_type}") input[type="radio"]'


# --------------------------------------------------------------------- #
# Reports + Dashboards (Phase 3 reporting surface)                       #
# --------------------------------------------------------------------- #

# Lightning Reports app deep link. `/lightning/o/Report/home` lands
# on the report list and exposes the "New Report" button on the
# toolbar.
SF_REPORTS_HOME_PATH = "/lightning/o/Report/home"
SF_REPORTS_NEW_BUTTON = 'button:has-text("New Report")'

# Report list search box. Used to find an existing report by title
# for idempotency.
SF_REPORTS_SEARCH_INPUT = 'input[placeholder*="Search"]'

# A matched report row in the search results. Lightning emits anchors
# whose href contains `/lightning/r/Report/<id>/view`.
SF_REPORT_ROW_LINK = 'a[href*="/lightning/r/Report/"][href$="/view"]'

# Lightning Dashboards app deep link.
SF_DASHBOARDS_HOME_PATH = "/lightning/o/Dashboard/home"
SF_DASHBOARDS_NEW_BUTTON = 'button:has-text("New Dashboard")'
SF_DASHBOARD_ROW_LINK = 'a[href*="/lightning/r/Dashboard/"][href$="/view"]'
