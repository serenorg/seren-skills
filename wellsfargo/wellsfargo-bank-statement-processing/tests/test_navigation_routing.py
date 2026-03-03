from __future__ import annotations

import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import wf_download  # noqa: E402


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def evaluate(self, _script: str):
        return self.payload


class NavigationRoutingTests(unittest.TestCase):
    def test_extract_wf_navigation_candidates_ignores_invalid_payload(self) -> None:
        client = _FakeClient({"urls": [" ", None, "/documents/default", "https://wellsfargo.com/"]})
        urls = wf_download._extract_wf_navigation_candidates(client)
        self.assertEqual(urls, ["/documents/default", "https://wellsfargo.com/"])

    def test_extract_wf_navigation_candidates_handles_non_dict(self) -> None:
        client = _FakeClient(["not", "a", "dict"])
        self.assertEqual(wf_download._extract_wf_navigation_candidates(client), [])

    def test_score_prefers_statement_list_before_default(self) -> None:
        ranked = sorted(
            [
                "https://example.com/edocs/documents/default",
                "https://example.com/edocs/documents/statement/list",
                "https://example.com/edocs/exit/saml;identifier=accounts",
            ],
            key=wf_download._score_wf_navigation_candidate,
        )
        self.assertEqual(ranked[0], "https://example.com/edocs/documents/statement/list")
        self.assertEqual(ranked[1], "https://example.com/edocs/documents/default")

    def test_resolve_selector_profile_for_chrome_and_firefox(self) -> None:
        base_profile = {
            "nav_accounts": ["base"],
            "browser_overrides": {
                "firefox": {"nav_accounts": ["fx"]},
                "chrome": {"nav_accounts": ["ch"]},
            },
        }
        firefox_profile, firefox_family, firefox_override = (
            wf_download._resolve_selector_profile_for_browser(base_profile, "moz-firefox")
        )
        chrome_profile, chrome_family, chrome_override = (
            wf_download._resolve_selector_profile_for_browser(base_profile, "chrome")
        )

        self.assertEqual(firefox_family, "firefox")
        self.assertTrue(firefox_override)
        self.assertEqual(firefox_profile["nav_accounts"], ["fx"])

        self.assertEqual(chrome_family, "chrome")
        self.assertTrue(chrome_override)
        self.assertEqual(chrome_profile["nav_accounts"], ["ch"])


if __name__ == "__main__":
    unittest.main()
