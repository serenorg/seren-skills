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

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scripts.auth import microsoft_sso
from scripts.auth.op_service_account import SalesforceCredentials
from scripts.sf import selectors


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

    def wait_for_selector(self, selector, *, timeout: int = 0):
        self.call_log.append(
            ("wait_for_selector", (selector,), {"timeout": timeout})
        )

    def is_visible(self, selector):
        self.call_log.append(("is_visible", (selector,), {}))
        return selector in self.visible_selectors


@dataclass
class FakeContext:
    """Returns a single pre-built FakePage and records storage_state."""

    page: FakePage
    storage_state_calls: list[str] = field(default_factory=list)

    def new_page(self) -> FakePage:
        return self.page

    def storage_state(self, *, path: str):
        self.storage_state_calls.append(path)


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
