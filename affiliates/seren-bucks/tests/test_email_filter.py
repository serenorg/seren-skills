from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from email_filter import (
    is_business_email,
    is_personal_relationship,
    is_transactional_content,
    is_b2b_content,
    compute_personal_score_penalty,
)


class TestIsBusinessEmail:
    def test_generic_prefixes_are_business(self):
        assert is_business_email("info@company.com") is True
        assert is_business_email("hello@startup.io") is True
        assert is_business_email("contact@brand.com") is True
        assert is_business_email("support@service.com") is True
        assert is_business_email("partnerships@vercel.com") is True
        assert is_business_email("affiliates@emergent.sh") is True

    def test_role_prefixes_are_business(self):
        assert is_business_email("marketing@company.com") is True
        assert is_business_email("engineering@startup.io") is True
        assert is_business_email("sales@brand.com") is True
        assert is_business_email("hr@corp.com") is True

    def test_noreply_is_business(self):
        assert is_business_email("noreply@company.com") is True
        assert is_business_email("no-reply@startup.io") is True
        assert is_business_email("donotreply@brand.com") is True

    def test_personal_names_are_not_business(self):
        assert is_business_email("john.doe@company.com") is False
        assert is_business_email("sarah@gmail.com") is False
        assert is_business_email("mike.smith@startup.io") is False
        assert is_business_email("alex@alpaca.markets") is False

    def test_empty_or_invalid_is_business(self):
        assert is_business_email("") is True
        assert is_business_email("not-an-email") is True


class TestIsPersonalRelationship:
    def test_business_email_is_not_personal(self):
        assert is_personal_relationship("partnerships@vercel.com") is False
        assert is_personal_relationship("hello@windsurf.com") is False

    def test_personal_email_with_no_context_is_personal(self):
        assert is_personal_relationship("john.doe@company.com") is True
        assert is_personal_relationship("sarah@gmail.com") is True

    def test_transactional_context_is_not_personal(self):
        context = {"thread_content": "Your invoice #12345 is attached. Payment received."}
        assert is_personal_relationship("john@company.com", context) is False

    def test_b2b_context_is_not_personal(self):
        context = {"thread_content": "Please find the partnership proposal attached."}
        assert is_personal_relationship("john@company.com", context) is False

    def test_friendly_context_is_personal(self):
        context = {"thread_content": "Hey! Great catching up at the conference last week."}
        assert is_personal_relationship("john@company.com", context) is True


class TestComputePersonalScorePenalty:
    def test_business_email_gets_max_penalty(self):
        penalty = compute_personal_score_penalty("info@company.com")
        assert penalty >= 100

    def test_personal_email_no_penalty(self):
        penalty = compute_personal_score_penalty("john@gmail.com")
        assert penalty == 0

    def test_transactional_content_adds_penalty(self):
        context = {"content": "Invoice #123 and receipt attached"}
        penalty = compute_personal_score_penalty("john@gmail.com", context)
        assert penalty == 30

    def test_b2b_content_adds_penalty(self):
        context = {"content": "Partnership proposal for your review"}
        penalty = compute_personal_score_penalty("john@gmail.com", context)
        assert penalty == 50
