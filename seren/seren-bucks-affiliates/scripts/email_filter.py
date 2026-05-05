from __future__ import annotations

import re

BUSINESS_PREFIXES = frozenset([
    "info",
    "hello",
    "contact",
    "support",
    "sales",
    "partnerships",
    "team",
    "admin",
    "affiliates",
    "press",
    "media",
    "hr",
    "careers",
    "jobs",
    "billing",
    "legal",
    "marketing",
    "engineering",
    "product",
    "design",
    "ops",
    "finance",
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "help",
    "service",
    "services",
    "feedback",
    "enquiries",
    "inquiries",
    "office",
    "general",
    "business",
    "corporate",
    "investor",
    "investors",
    "relations",
    "compliance",
    "security",
    "privacy",
    "abuse",
    "postmaster",
    "webmaster",
    "newsletter",
    "notifications",
    "alerts",
    "updates",
    "subscribe",
    "unsubscribe",
])

TRANSACTIONAL_KEYWORDS = frozenset([
    "invoice",
    "receipt",
    "order confirmation",
    "shipping confirmation",
    "tracking number",
    "payment received",
    "subscription",
    "renewal",
    "ticket",
    "case number",
    "reference number",
    "automated message",
    "do not reply",
])

B2B_KEYWORDS = frozenset([
    "partnership proposal",
    "business inquiry",
    "rfp",
    "request for proposal",
    "vendor",
    "procurement",
    "contract",
    "agreement",
    "nda",
    "sow",
    "statement of work",
    "purchase order",
    "po number",
])


def is_business_email(email: str) -> bool:
    if not email or "@" not in email:
        return True

    local_part = email.lower().split("@")[0]
    local_normalized = re.sub(r"[.\-_+]", "", local_part)

    for prefix in BUSINESS_PREFIXES:
        normalized_prefix = re.sub(r"[.\-_+]", "", prefix)
        if local_normalized == normalized_prefix:
            return True
        if local_normalized.startswith(normalized_prefix) and len(local_normalized) == len(normalized_prefix):
            return True

    if re.match(r"^[a-z]+\d{2,}$", local_normalized):
        return True

    return False


def is_transactional_content(content: str) -> bool:
    if not content:
        return False

    content_lower = content.lower()
    matches = sum(1 for kw in TRANSACTIONAL_KEYWORDS if kw in content_lower)
    return matches >= 2


def is_b2b_content(content: str) -> bool:
    if not content:
        return False

    content_lower = content.lower()
    matches = sum(1 for kw in B2B_KEYWORDS if kw in content_lower)
    return matches >= 1


def is_personal_relationship(email: str, context: dict | None = None) -> bool:
    if is_business_email(email):
        return False

    if context:
        content = context.get("thread_content", "") or context.get("content", "")
        if is_transactional_content(content):
            return False
        if is_b2b_content(content):
            return False

    return True


def compute_personal_score_penalty(email: str, context: dict | None = None) -> int:
    penalty = 0

    if is_business_email(email):
        penalty += 100

    if context:
        content = context.get("thread_content", "") or context.get("content", "")
        if is_transactional_content(content):
            penalty += 30
        if is_b2b_content(content):
            penalty += 50

    return penalty
