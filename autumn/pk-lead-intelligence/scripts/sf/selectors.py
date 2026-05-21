"""CSS / role selectors for Salesforce Lightning + Microsoft SSO.

Kept in one module so selector drift produces one-line diffs. Updated
whenever Salesforce or Microsoft rotates a label or markup.

The Lightning selectors below were captured live against
`herrmannultraschall.lightning.force.com` on 2026-05-21 (issue #563
selector verification pass). The Microsoft selectors were last
captured 2026-05-14.
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
# Lead detail page — Project Business Unit read (cross-division gate)    #
# --------------------------------------------------------------------- #

# HU's `Lead.Project_Business_Unit__c` field renders in the Lightning
# Details tab as a label/value pair inside a `slds-form-element`. The
# label text "Project Business Unit" is anchored on the label span;
# the value renders in a sibling `test-id__field-value` span. We pick
# the form-element wrapper by label text via Playwright's `:has()`
# extension, then drop into the value span. Verified live on
# 2026-05-21 (issue #563): Dan Coats Lead returned "PACKAGING".
SF_LEAD_DETAIL_PROJECT_BUSINESS_UNIT_VALUE = (
    'div.slds-form-element:has('
    'span.test-id__field-label:has-text("Project Business Unit")'
    ') span.test-id__field-value'
)

# Build a Lead detail URL relative to the org root. HU's modern
# Lightning emits both shapes; `/lightning/r/Lead/<id>/view` is the
# stable canonical that always resolves regardless of org routing.
SF_LEAD_DETAIL_PATH_TEMPLATE = "/lightning/r/Lead/{record_id}/view"

# The "PACKAGING" string is what the Lightning Details tab renders
# for the PK division. Match must be exact (case-sensitive) so a
# stray "Packaging" value (different division code) does not pass
# the gate.
SF_PROJECT_BUSINESS_UNIT_PK_VALUE = "PACKAGING"


# --------------------------------------------------------------------- #
# Lead detail page — Note write flow (#563 verified)                     #
# --------------------------------------------------------------------- #

# The Related tab on the Lead detail page. Lightning renders it
# inside `lightning-tab-bar`'s shadow root, but the `a[role="tab"]`
# is reachable through Playwright's auto-pierce CSS engine. Match
# by visible text "Related" + the SLDS tab-link class so the tab
# strip on Marketing Engagements (also a tab) does not collide.
SF_LEAD_RELATED_TAB = (
    'a[role="tab"].slds-tabs_default__link:has-text("Related")'
)

# Within the Related tab, the Notes card. We scope by the
# AttachedContentNotes related-list URL embedded on the card's
# title anchor — that internal name is the modern ContentNote join
# object and is the only `Attached…` related list on the Lead.
SF_LEAD_NOTES_CARD = (
    'article.forceRelatedListCardDesktop:has('
    'a[href*="AttachedContentNotes"]'
    ')'
)

# The "New" button on the Notes card opens the Note modal. Lightning
# renders it as an `<a class="forceActionLink" title="New">`.
SF_LEAD_NOTES_NEW_BUTTON = (
    'article.forceRelatedListCardDesktop:has('
    'a[href*="AttachedContentNotes"]'
    ') a.forceActionLink[title="New"]'
)

# The Note modal — Lightning's `[role="dialog"]` container.
SF_NOTE_MODAL = '[role="dialog"]'

# Title input. Lightning's Note title is an `input.slds-input` with
# placeholder "Untitled Note" — the placeholder is the stable handle
# (the id is auto-generated, e.g. `input-585`).
SF_NOTE_MODAL_TITLE_INPUT = (
    'div[role="dialog"] input.slds-input[placeholder="Untitled Note"]'
)

# Body editor. Modern Lightning Notes uses Quill, so the body is a
# contenteditable div with class `ql-editor`, role `textbox`.
# Cannot be filled via Playwright `fill()`; must focus + type.
SF_NOTE_MODAL_BODY_EDITOR = (
    'div[role="dialog"] div.ql-editor[role="textbox"]'
)

# "Done" button finalizes the note. Note that ContentNote auto-saves
# on type, so by the time Done is clicked the note already exists
# server-side; Done simply closes the editor. Class `hideDoneButton`
# is brittle (the name suggests display logic), so we also match
# by visible text as a more semantic fallback.
SF_NOTE_MODAL_DONE_BUTTON = (
    'div[role="dialog"] button.hideDoneButton, '
    'div[role="dialog"] button:has-text("Done")'
)

# Close button (X icon). Used by the dry-run path to dismiss the
# modal without persisting.
SF_NOTE_MODAL_CLOSE_BUTTON = 'div[role="dialog"] button.closeButton'
