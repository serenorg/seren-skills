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

# Next/Submit on the email screen. Microsoft uses the same id
# `idSIButton9` for "Next" and "Sign in" across the email, password,
# and KMSI screens — pinning to the id avoids matching unrelated
# submit inputs (hidden form posts, layered modals).
MS_EMAIL_SUBMIT = 'input#idSIButton9'

# Password field. `passwd` is the stable form-field name.
MS_PASSWORD_INPUT = 'input[name="passwd"]'
MS_PASSWORD_SUBMIT = 'input#idSIButton9'

# TOTP code field for "Authenticator app or hardware token" flow.
# Microsoft renders this as a single 6-digit input with name `otc`
# and the verify button as `idSubmit_SAOTCC_Continue` (distinct from
# the password/email `idSIButton9`).
MS_TOTP_INPUT = 'input[name="otc"]'
MS_TOTP_SUBMIT = 'input#idSubmit_SAOTCC_Continue'

# Microsoft's passkey / WebAuthn-default prompt ("Face, fingerprint,
# PIN or security key") that many tenants now render BEFORE the TOTP
# step. Playwright cannot complete a WebAuthn ceremony in a remote-
# controlled browser, so the driver clicks "Sign in another way" to
# escape to the method picker. The id `signInAnotherWay` is the
# stable handle; the text-based fallbacks absorb tenant-language and
# minor markup churn. Issue #857.
MS_SIGN_IN_ANOTHER_WAY_LINK = (
    'a#signInAnotherWay, '
    'a:has-text("Sign in another way"), '
    'button:has-text("Sign in another way")'
)

# "Verify your identity" method-picker tile for the TOTP option.
# Microsoft renders each method as a separately clickable element
# (anchor/div/button depending on the tenant); match by visible text
# to absorb the markup variation. The label has been stable at
# "Use a verification code" across tenant rollouts; the
# `data-value="PhoneAppOTP"` attribute is the legacy hook still
# present on some tenants. Issue #857.
MS_VERIFICATION_USE_CODE_OPTION = (
    'div[role="button"]:has-text("Use a verification code"), '
    'button:has-text("Use a verification code"), '
    'a:has-text("Use a verification code"), '
    '[data-value="PhoneAppOTP"]'
)

# The "Stay signed in?" (KMSI) interstitial Microsoft shows after a
# successful sign-in. Body says "Stay signed in?"; "Yes" is
# `idSIButton9`, "No" is `idBtn_Back`. We click "No" to keep
# Playwright storage_state as the single source of truth for session
# persistence. The page must be WAITED FOR (not just probed for
# visibility) — Microsoft renders it on a redirect after the TOTP
# submit, not synchronously.
MS_STAY_SIGNED_IN_NO = 'input#idBtn_Back'
MS_STAY_SIGNED_IN_YES = 'input#idSIButton9'


# --------------------------------------------------------------------- #
# Salesforce Lightning (post-auth)                                      #
# --------------------------------------------------------------------- #

# Sentinel that indicates we have landed in Lightning after SSO.
# The previous `[role="main"]` selector was a P0 false-positive —
# every well-formed page (including Microsoft's sign-in interstitials)
# has a `[role="main"]` landmark, so the driver declared SSO success
# while still stuck on `login.microsoftonline.com` and never minted a
# Salesforce `sid` cookie.
#
# The correct sentinel must be Lightning-specific. `one-appnav` is
# the Aura custom element Lightning renders for the top navigation
# bar; it appears on every authenticated Lightning page and on no
# Microsoft page. Pair this with a URL-host check in the auth driver
# so we fail loudly if we're still on microsoftonline.com.
SF_LIGHTNING_AUTHENTICATED_SENTINEL = 'one-appnav'

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


# Report viewer links to the record detail pages for cells such as Lead
# Name and Owner. Client code filters the matched anchors down to Lead
# ObjectPrefix `00Q`; the selector deliberately remains broad enough to
# tolerate both `/lightning/r/Lead/<id>/view` and HU's modern
# `/lightning/r/<id>/view` shape.
SF_REPORT_RECORD_LINK = 'a[href^="/lightning/r/"][href$="/view"]'

# Lightning report data hydrates inside this report-app iframe in HU.
# We wait for the frame before scanning both top-page and frame scopes.
SF_REPORT_VIEWER_IFRAME = 'iframe[src*="lightningReportApp.app"]'


# --------------------------------------------------------------------- #
# Record detail page — Business Unit checkbox read (PK gate)             #
# --------------------------------------------------------------------- #

# Build a record detail URL relative to the org root. HU report links
# usually start as Lead records (`00Q`) but converted records can land
# on Contact detail pages (`003`) after Lightning resolves redirects.
SF_RECORD_DETAIL_PATH_TEMPLATE = "/lightning/r/{object_name}/{record_id}/view"

# Note writes remain Lead-oriented. Converted Lead redirects are handled
# by Lightning after navigation, but the write path keeps the Lead URL
# shape because the skill only sources Lead report rows.
SF_LEAD_DETAIL_PATH_TEMPLATE = "/lightning/r/Lead/{record_id}/view"

SF_BUSINESS_UNIT_PACKAGING_LABEL = "PACKAGING"

# HU renders a "Business Unit" section with checkbox-style boolean
# fields (`PLASTICS`, `PACKAGING`, `NONWOVENS`, `METALS`). The PK gate
# is the PACKAGING field inside that section. Keep the selector variants
# narrow to record-layout sections so a stray "PACKAGING" elsewhere on
# the Details tab cannot pass the gate.
SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD_SELECTORS = (
    'records-record-layout-section:has-text("Business Unit") '
    'div.slds-form-element:has('
    'span.test-id__field-label:has-text("PACKAGING"))',
    'div.slds-section:has(.slds-section__title:has-text("Business Unit")) '
    'div.slds-form-element:has('
    'span.test-id__field-label:has-text("PACKAGING"))',
    'div.slds-section:has(button:has-text("Business Unit")) '
    'div.slds-form-element:has('
    'span.test-id__field-label:has-text("PACKAGING"))',
)
SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD = ", ".join(
    SF_RECORD_DETAIL_BUSINESS_UNIT_PACKAGING_FIELD_SELECTORS
)

SF_RECORD_DETAIL_BUSINESS_UNIT_SECTION_LABEL = (
    'div.slds-section .slds-section__title:has-text("Business Unit"), '
    'records-record-layout-section:has-text("Business Unit")'
)

# Boolean display has varied across Salesforce renderers. Prefer native
# checkbox states, then Lightning's checked icon/image affordances.
SF_RECORD_DETAIL_BOOLEAN_TRUE_MARKERS = (
    'input[type="checkbox"]:checked',
    'input[type="checkbox"][checked]',
    '[aria-checked="true"]',
    'lightning-input[checked]',
    'lightning-icon[icon-name="utility:check"]',
    'lightning-primitive-icon[data-key="check"]',
    'svg[data-key="check"]',
    'img[alt="Checked"]',
    'img[title="Checked"]',
    '[title="Checked"]',
)


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
