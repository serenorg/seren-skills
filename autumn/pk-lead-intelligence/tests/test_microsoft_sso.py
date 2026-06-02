"""Unit tests for scripts/auth/microsoft_sso.py.

Playwright itself is not exercised here. We pass hand-rolled fakes
that satisfy the `_Page` / `_Context` protocols and assert against
the call log the driver produces. The selectors live in
`scripts/sf/selectors.py` and are imported here only so changes to
their values do not require parallel test updates — the assertions
match against the constants, not literal strings.

The live SSO path (real Chromium against the real Microsoft tenant)
is exercised by the Phase 1 dry-run checkpoint with the operator
watching. That path will surface any selector that drifted, which
this test suite cannot catch by design.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scripts.auth import microsoft_sso
from scripts.auth.op_service_account import SalesforceCredentials
from scripts.sf import selectors


# --------------------------------------------------------------------- #
# Regression: issue #833 — F821 Optional on _drive_fresh_login          #
# --------------------------------------------------------------------- #


def test_drive_fresh_login_annotations_resolve():
    """Return annotation must resolve at runtime.

    `_drive_fresh_login` is annotated `-> Optional[str]` under
    `from __future__ import annotations`. The annotation is stored as
    a string and only evaluated when something (Pydantic, dataclasses,
    `typing.get_type_hints`, a downstream tool) asks for the resolved
    hints. If `Optional` is not in the module namespace, that
    resolution raises `NameError` and the production SSO path
    silently carries a latent F821.
    """
    hints = typing.get_type_hints(microsoft_sso._drive_fresh_login)
    assert hints["return"] == typing.Optional[str]


# --------------------------------------------------------------------- #
# Fakes                                                                 #
# --------------------------------------------------------------------- #


@dataclass
class FakePage:
    """Records every Page interaction and lets tests script outcomes.

    The driver expects a small surface — goto, click, fill,
    wait_for_url, wait_for_selector, is_visible — and a mutable
    `url` attribute. Each method records its call so tests can
    assert on call order.
    """

    url: str = ""
    visible_selectors: set[str] = field(default_factory=set)
    call_log: list[tuple[str, tuple, dict]] = field(default_factory=list)
    # Successive `url` values applied each time `wait_for_url` is
    # called, so a test can simulate the redirect to Microsoft.
    wait_for_url_returns: list[str] = field(default_factory=list)
    # Parallel queue of selector sets that become visible after each
    # `wait_for_url`. Lets tests model network-driven state transitions
    # — e.g. Microsoft silently bouncing the session back to Lightning
    # so the sentinel renders without ever exposing the email form.
    # See #759 (SSO session-resume race).
    wait_for_url_visibility_after: list[set[str]] = field(default_factory=list)

    def goto(self, url, *, timeout: int = 0):
        self.call_log.append(("goto", (url,), {"timeout": timeout}))
        self.url = url

    def click(self, selector, *, timeout: int = 0):
        self.call_log.append(("click", (selector,), {"timeout": timeout}))

    def fill(self, selector, value, *, timeout: int = 0):
        self.call_log.append(("fill", (selector, value), {"timeout": timeout}))

    def wait_for_url(self, url, *, timeout: int = 0):
        self.call_log.append(("wait_for_url", (url,), {"timeout": timeout}))
        if self.wait_for_url_returns:
            self.url = self.wait_for_url_returns.pop(0)
        if self.wait_for_url_visibility_after:
            self.visible_selectors |= self.wait_for_url_visibility_after.pop(0)

    def wait_for_selector(self, selector, *, timeout: int = 0):
        self.call_log.append(
            ("wait_for_selector", (selector,), {"timeout": timeout})
        )

    def is_visible(self, selector):
        self.call_log.append(("is_visible", (selector,), {}))
        return selector in self.visible_selectors


@dataclass
class FakeContext:
    """Returns a single pre-built FakePage and records storage_state.

    `cookies_to_return` is exposed because the SSO driver verifies a
    Salesforce `sid` cookie exists in the jar before persisting
    storage_state — see the post-login check in `authenticate`. The
    default fixture provides a valid SF sid so the happy-path tests
    stay green; the no-sid failure path has its own test.
    """

    page: FakePage
    storage_state_calls: list[str] = field(default_factory=list)
    cookies_to_return: list[dict] = field(
        default_factory=lambda: [
            {"name": "sid", "domain": "acme.my.salesforce.com", "value": "x"}
        ]
    )

    def new_page(self) -> FakePage:
        return self.page

    def storage_state(self, *, path: str):
        self.storage_state_calls.append(path)

    def cookies(self) -> list[dict]:
        return list(self.cookies_to_return)


# --------------------------------------------------------------------- #
# Fixtures                                                              #
# --------------------------------------------------------------------- #


@pytest.fixture
def creds() -> SalesforceCredentials:
    return SalesforceCredentials(
        username="owner@example.com",
        password="hunter2",
        totp_code="123456",
    )


@pytest.fixture
def storage_path(tmp_path: Path) -> Path:
    return tmp_path / "playwright_storage.json"


@pytest.fixture
def discovery_path(tmp_path: Path) -> Path:
    return tmp_path / "sso_discovery.json"


# --------------------------------------------------------------------- #
# Reuse path                                                            #
# --------------------------------------------------------------------- #


def test_reuse_returns_early_when_lightning_sentinel_visible(
    creds, storage_path, discovery_path
):
    """If the App Launcher renders on the initial goto, we are done.

    The driver must not fill any form fields, must not write the
    storage_state file again, and must report reused_storage=True.
    """

    page = FakePage(
        visible_selectors={selectors.SF_LIGHTNING_AUTHENTICATED_SENTINEL},
    )
    context = FakeContext(page=page)

    result = microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    assert result.reused_storage is True
    assert result.microsoft_tenant_url is None
    assert result.page is page

    # No fills happened — reuse must not enter the SSO flow.
    assert not any(call[0] == "fill" for call in page.call_log)
    # storage_state is not re-saved when reusing.
    assert context.storage_state_calls == []
    # discovery file is not written.
    assert not discovery_path.exists()


# --------------------------------------------------------------------- #
# Fresh-login path                                                      #
# --------------------------------------------------------------------- #


def test_fresh_login_fills_email_password_and_totp_in_order(
    creds, storage_path, discovery_path
):
    """The three fills must happen in email → password → TOTP order.

    Misordering breaks the Microsoft flow and is a class of bug a
    casual refactor can introduce. We assert the relative order of
    the three fills, not exact indices.
    """

    page = FakePage(
        wait_for_url_returns=["https://login.microsoftonline.com/tenant/oauth2/authorize"],
    )
    context = FakeContext(page=page)

    microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    fills = [call for call in page.call_log if call[0] == "fill"]
    selectors_filled = [call[1][0] for call in fills]
    values_filled = [call[1][1] for call in fills]

    assert selectors_filled == [
        selectors.MS_EMAIL_INPUT,
        selectors.MS_PASSWORD_INPUT,
        selectors.MS_TOTP_INPUT,
    ]
    assert values_filled == [
        creds.username,
        creds.password,
        creds.totp_code,
    ]


def test_fresh_login_resolves_lazy_credential_provider(
    creds, storage_path, discovery_path
):
    """Fresh login still reads credentials when storage reuse fails."""

    calls = 0

    def creds_provider() -> SalesforceCredentials:
        nonlocal calls
        calls += 1
        return creds

    page = FakePage(
        wait_for_url_returns=["https://login.microsoftonline.com/tenant/oauth2/authorize"],
    )
    context = FakeContext(page=page)

    microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds_provider,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    assert calls == 1
    fills = [call for call in page.call_log if call[0] == "fill"]
    assert [call[1][1] for call in fills] == [
        creds.username,
        creds.password,
        creds.totp_code,
    ]


def test_fresh_login_captures_microsoft_tenant_url(
    creds, storage_path, discovery_path
):
    """The URL we land on after the SF→MS redirect is persisted."""

    tenant_url = (
        "https://login.microsoftonline.com/abc-tenant-guid/oauth2/v2.0/authorize"
    )
    page = FakePage(wait_for_url_returns=[tenant_url])
    context = FakeContext(page=page)

    result = microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    assert result.microsoft_tenant_url == tenant_url
    assert discovery_path.exists()
    contents = discovery_path.read_text()
    assert tenant_url in contents


def test_fresh_login_persists_storage_state(creds, storage_path, discovery_path):
    """On a successful fresh login, storage_state must be written."""

    page = FakePage(
        wait_for_url_returns=["https://login.microsoftonline.com/tenant/oauth2/authorize"],
    )
    context = FakeContext(page=page)

    microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    assert context.storage_state_calls == [str(storage_path)]


def test_fresh_login_clicks_stay_signed_in_no_when_visible(
    creds, storage_path, discovery_path
):
    """If Microsoft shows the 'Stay signed in?' prompt, click No.

    The prompt is intermittent — Microsoft hides it on subsequent
    sign-ins from the same IP. The driver must handle both cases
    without raising.
    """

    page = FakePage(
        wait_for_url_returns=["https://login.microsoftonline.com/tenant/oauth2/authorize"],
        visible_selectors={selectors.MS_STAY_SIGNED_IN_NO},
    )
    context = FakeContext(page=page)

    microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    clicks = [call[1][0] for call in page.call_log if call[0] == "click"]
    assert selectors.MS_STAY_SIGNED_IN_NO in clicks


def test_fresh_login_skips_stay_signed_in_when_absent(
    creds, storage_path, discovery_path
):
    """If the prompt does not appear, we must not call click on it.

    Microsoft removes the prompt on repeat sign-ins. Clicking a
    selector that is not visible raises in Playwright.
    """

    page = FakePage(
        wait_for_url_returns=["https://login.microsoftonline.com/tenant/oauth2/authorize"],
        visible_selectors=set(),  # prompt is not visible
    )
    context = FakeContext(page=page)

    microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    clicks = [call[1][0] for call in page.call_log if call[0] == "click"]
    assert selectors.MS_STAY_SIGNED_IN_NO not in clicks


def test_fresh_login_raises_when_no_salesforce_sid_cookie(
    creds, storage_path, discovery_path
):
    """If SSO completes but no `sid` cookie is in the jar, fail loud.

    Regression guard for the silent-failure mode where Microsoft
    keeps the browser on its own domain (KMSI variant, unhandled MFA
    challenge) but the Lightning DOM sentinel still matches by
    accident. Without this check, the driver would persist a
    sessionless storage_state and every downstream Lightning
    navigation would 302-redirect to the login page — a useless
    selector timeout instead of an honest auth failure here.

    See also: probe_sso_cookies.py and probe_ms_interstitial.py.
    """

    page = FakePage(
        wait_for_url_returns=[
            "https://login.microsoftonline.com/tenant/oauth2/authorize"
        ],
    )
    # Simulate the cookie-jar state we actually observed in the
    # silent-failure run: Microsoft cookies present, but no
    # Salesforce sid.
    context = FakeContext(
        page=page,
        cookies_to_return=[
            {"name": "ESTSAUTH", "domain": ".login.microsoftonline.com",
             "value": "ms-session"},
            {"name": "BrowserId", "domain": ".salesforce.com", "value": "z"},
        ],
    )

    with pytest.raises(RuntimeError, match="no Salesforce `sid` cookie"):
        microsoft_sso.authenticate(
            context=context,
            salesforce_org_url="https://acme.lightning.force.com",
            creds=creds,
            storage_path=storage_path,
            discovery_path=discovery_path,
        )

    # Critical: we MUST NOT persist a sessionless storage_state.
    assert context.storage_state_calls == []


# --------------------------------------------------------------------- #
# SSO session-resume race (#759)                                        #
# --------------------------------------------------------------------- #


def test_fresh_login_returns_early_when_microsoft_silently_resumes_to_lightning(
    creds, storage_path, discovery_path
):
    """Regression for #759 SSO session-resume race.

    When the Microsoft IdP cookie is still valid, clicking the SF SSO
    button transits microsoftonline.com briefly and then bounces back
    to Lightning without ever rendering the email form. Previously the
    driver waited 15s for `MS_EMAIL_INPUT`, timed out, and crashed.

    The fix races the email input against the Lightning sentinel and
    returns early on the sentinel. This test models the bounce by
    making the Lightning sentinel visible after the Microsoft URL
    transit settles, and asserts that no credential fills happen.
    """

    sentinel = selectors.SF_LIGHTNING_AUTHENTICATED_SENTINEL
    page = FakePage(
        # Microsoft URL transits, then immediate bounce-back: the
        # Lightning sentinel becomes visible. Email form never shows.
        wait_for_url_returns=[
            "https://login.microsoftonline.com/tenant/oauth2/authorize",
        ],
        wait_for_url_visibility_after=[{sentinel}],
    )
    context = FakeContext(page=page)

    result = microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    # No credential fills happened — we bypassed the Microsoft flow.
    assert not any(call[0] == "fill" for call in page.call_log), (
        "silent-resume path must not fill email/password/TOTP"
    )

    # The microsoft_tenant_url is still captured (it transited
    # microsoftonline.com) so the discovery file is informative.
    assert result.microsoft_tenant_url is not None
    assert "microsoftonline.com" in result.microsoft_tenant_url

    # storage_state IS persisted — we landed on Lightning with a
    # valid session and want the next run to reuse it.
    assert context.storage_state_calls == [str(storage_path)]
    assert result.reused_storage is False


def test_fresh_login_returns_early_when_sf_resumes_session_without_microsoft_transit(
    creds, storage_path, discovery_path
):
    """Regression for #762 SSO race B.

    When both SF and Microsoft sessions are cached, the SF SSO button
    click jumps **straight** to Lightning — `microsoftonline.com` is
    never visited. Previously step 2's `wait_for_url(microsoftonline)`
    deadlocked for 30s waiting for a URL that never appeared.

    The fix widens the URL predicate to also accept Lightning hosts
    and probes `_is_authenticated` after the race wins. This test
    models the race-B path by making the Lightning sentinel visible
    after step 2's `wait_for_url` and asserts that:

    - no Microsoft form is filled,
    - no discovery file is written (no tenant URL to record),
    - storage state IS persisted (we landed on a valid session),
    - the returned `microsoft_tenant_url` is `None`.
    """

    sentinel = selectors.SF_LIGHTNING_AUTHENTICATED_SENTINEL
    page = FakePage(
        # SF jumps straight to Lightning; URL never visits Microsoft.
        wait_for_url_returns=[
            "https://acme.lightning.force.com/lightning/page/home",
        ],
        wait_for_url_visibility_after=[{sentinel}],
    )
    context = FakeContext(page=page)

    result = microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    # No credential fills happened — Microsoft flow was bypassed entirely.
    assert not any(call[0] == "fill" for call in page.call_log), (
        "race-B path must not fill any Microsoft form fields"
    )

    # discovery file is NOT written — there is no Microsoft URL to
    # record, and writing a Lightning URL into sso_discovery.json
    # would be misleading.
    assert not discovery_path.exists()

    # The wider URL predicate ran on exactly one wait_for_url call —
    # the race-B early-return at step 2 skips every later flow step
    # (Microsoft form-fills, KMSI, final SF redirect). The pre-fix
    # behavior would have made TWO wait_for_url calls: one that
    # deadlocked on `microsoftonline.com` and a second one we never
    # reached.
    url_waits = [c for c in page.call_log if c[0] == "wait_for_url"]
    assert len(url_waits) == 1

    # storage IS persisted — we landed on Lightning with a valid sid.
    assert context.storage_state_calls == [str(storage_path)]
    assert result.reused_storage is False
    # No Microsoft tenant URL to surface — race B bypassed Microsoft.
    assert result.microsoft_tenant_url is None


# --------------------------------------------------------------------- #
# Microsoft passkey-default verification-method routing (#857)          #
# --------------------------------------------------------------------- #


def test_fresh_login_navigates_passkey_default_to_totp(
    creds, storage_path, discovery_path
):
    """Regression for #857. Tenant defaults to passkey after password.

    Microsoft now renders the "Face, fingerprint, PIN or security key"
    prompt after the password step on many tenants. Playwright cannot
    complete a WebAuthn ceremony in a remote-controlled browser. The
    driver must click "Sign in another way" → "Use a verification code"
    to reach the existing OTC input. The full click order between
    password submit and TOTP submit must be exactly:

        Sign in another way → Use a verification code
    """

    page = FakePage(
        wait_for_url_returns=[
            "https://login.microsoftonline.com/tenant/oauth2/authorize",
        ],
        visible_selectors={selectors.MS_SIGN_IN_ANOTHER_WAY_LINK},
    )
    context = FakeContext(page=page)

    microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    # Anchor on the password and TOTP fills (unique per step). The
    # submit-button selectors are NOT unique — Microsoft reuses
    # `input#idSIButton9` for the email and password submits — so
    # `clicks.index(MS_PASSWORD_SUBMIT)` would match the email submit.
    def call_index(call_type, target):
        return next(
            i for i, c in enumerate(page.call_log)
            if c[0] == call_type and c[1][0] == target
        )

    pwd_fill = call_index("fill", selectors.MS_PASSWORD_INPUT)
    totp_fill = call_index("fill", selectors.MS_TOTP_INPUT)
    saw_click = call_index("click", selectors.MS_SIGN_IN_ANOTHER_WAY_LINK)
    uvc_click = call_index("click", selectors.MS_VERIFICATION_USE_CODE_OPTION)

    # Passkey flow strictly requires sign-in-another-way before
    # use-verification-code; both must sit between password fill and
    # TOTP fill.
    assert pwd_fill < saw_click < uvc_click < totp_fill


def test_fresh_login_clicks_use_code_when_picker_shown_directly(
    creds, storage_path, discovery_path
):
    """Regression for #857. Tenant skips passkey, shows picker directly.

    Some tenants render the "Verify your identity" method picker
    immediately after password submit, without the passkey prompt
    intermediate. In that case the driver must NOT click
    "Sign in another way" (it does not exist on the picker page;
    clicking a missing selector raises in real Playwright) and MUST
    click "Use a verification code" exactly once.
    """

    page = FakePage(
        wait_for_url_returns=[
            "https://login.microsoftonline.com/tenant/oauth2/authorize",
        ],
        visible_selectors={selectors.MS_VERIFICATION_USE_CODE_OPTION},
    )
    context = FakeContext(page=page)

    microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    clicks = [call[1][0] for call in page.call_log if call[0] == "click"]
    assert selectors.MS_SIGN_IN_ANOTHER_WAY_LINK not in clicks
    assert clicks.count(selectors.MS_VERIFICATION_USE_CODE_OPTION) == 1

    # Anchor on the password and TOTP fills (unique per step). See the
    # comment in test_fresh_login_navigates_passkey_default_to_totp for
    # why submit-button selectors are not safe anchors.
    def call_index(call_type, target):
        return next(
            i for i, c in enumerate(page.call_log)
            if c[0] == call_type and c[1][0] == target
        )

    pwd_fill = call_index("fill", selectors.MS_PASSWORD_INPUT)
    totp_fill = call_index("fill", selectors.MS_TOTP_INPUT)
    uvc_click = call_index("click", selectors.MS_VERIFICATION_USE_CODE_OPTION)

    assert pwd_fill < uvc_click < totp_fill


def test_fresh_login_skips_method_picker_on_legacy_totp_default(
    creds, storage_path, discovery_path
):
    """Regression for #857. Tenants on the old TOTP default still work.

    When the OTC input is the first selector visible after password
    submit, the driver must skip the method-picker code path entirely
    — no "Sign in another way" click, no "Use a verification code"
    click. This protects tenants that haven't been rolled onto the
    passkey default from incurring extra clicks they don't need.
    """

    page = FakePage(
        wait_for_url_returns=[
            "https://login.microsoftonline.com/tenant/oauth2/authorize",
        ],
        visible_selectors={selectors.MS_TOTP_INPUT},
    )
    context = FakeContext(page=page)

    microsoft_sso.authenticate(
        context=context,
        salesforce_org_url="https://acme.lightning.force.com",
        creds=creds,
        storage_path=storage_path,
        discovery_path=discovery_path,
    )

    clicks = [call[1][0] for call in page.call_log if call[0] == "click"]

    assert selectors.MS_SIGN_IN_ANOTHER_WAY_LINK not in clicks
    assert selectors.MS_VERIFICATION_USE_CODE_OPTION not in clicks
