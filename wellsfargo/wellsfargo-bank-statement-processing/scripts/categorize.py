from __future__ import annotations

import os
from typing import Any

import requests

RULES: dict[str, list[str]] = {
    "income": ["payroll", "salary", "direct deposit", "refund", "interest paid"],
    "housing": ["rent", "mortgage", "hoa", "property tax"],
    "groceries": ["grocery", "whole foods", "trader joe", "costco", "kroger"],
    "dining": ["restaurant", "cafe", "coffee", "doordash", "uber eats"],
    "transport": ["uber", "lyft", "shell", "chevron", "fuel", "parking", "transit"],
    "utilities": ["electric", "water", "gas bill", "internet", "utility"],
    "subscriptions": ["netflix", "spotify", "apple.com/bill", "hulu", "subscription"],
    "healthcare": ["pharmacy", "cvs", "walgreens", "doctor", "hospital"],
    "travel": ["airlines", "hotel", "airbnb", "booking", "expedia"],
    "fees": ["fee", "service charge", "overdraft", "atm charge"],
    "transfers": ["zelle", "venmo", "paypal", "transfer", "wire"],
    "cash": ["atm withdrawal", "cash withdrawal"],
}


def _rule_classify(description: str) -> str | None:
    desc = description.lower()
    for category, keywords in RULES.items():
        if any(keyword in desc for keyword in keywords):
            return category
    return None


def _heuristic_llm(description: str) -> tuple[str, float, str]:
    desc = description.lower()
    if "amazon" in desc or "target" in desc:
        return "shopping", 0.66, "heuristic_llm_keyword"
    if "insurance" in desc:
        return "insurance", 0.64, "heuristic_llm_keyword"
    if "tax" in desc:
        return "taxes", 0.62, "heuristic_llm_keyword"
    return "uncategorized", 0.0, "heuristic_llm_fallback"


def _remote_llm_classify(
    description: str,
    amount: float,
    endpoint: str,
    api_key: str | None,
) -> tuple[str, float, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload = {
        "description": description,
        "amount": amount,
        "taxonomy": list(RULES.keys()) + ["shopping", "insurance", "taxes", "uncategorized"],
    }
    response = requests.post(endpoint, json=payload, headers=headers, timeout=25)
    response.raise_for_status()
    body = response.json()
    category = str(body.get("category", "uncategorized"))
    confidence = float(body.get("confidence", 0.0))
    rationale = str(body.get("rationale", "remote_llm"))
    return category, confidence, rationale


def categorize_transactions(
    transactions: list[dict[str, Any]],
    llm_mode: str = "heuristic",
    llm_endpoint: str = "",
    llm_api_key: str | None = None,
) -> list[dict[str, Any]]:
    categories: list[dict[str, Any]] = []
    llm_api_key = llm_api_key or os.getenv("WF_LLM_API_KEY")

    for txn in transactions:
        description = str(txn.get("description_raw", "")).strip()
        amount = float(txn.get("amount", 0.0))
        row_hash = str(txn.get("row_hash"))

        rule_category = _rule_classify(description)
        if rule_category:
            categories.append(
                {
                    "row_hash": row_hash,
                    "category_source": "rule",
                    "category": rule_category,
                    "confidence": 1.0,
                    "rationale_short": "keyword_rule_match",
                }
            )
            continue

        if llm_mode == "disabled":
            categories.append(
                {
                    "row_hash": row_hash,
                    "category_source": "none",
                    "category": "uncategorized",
                    "confidence": 0.0,
                    "rationale_short": "llm_disabled",
                }
            )
            continue

        if llm_endpoint:
            try:
                category, confidence, rationale = _remote_llm_classify(
                    description=description,
                    amount=amount,
                    endpoint=llm_endpoint,
                    api_key=llm_api_key,
                )
                categories.append(
                    {
                        "row_hash": row_hash,
                        "category_source": "llm",
                        "category": category,
                        "confidence": confidence,
                        "rationale_short": rationale,
                    }
                )
                continue
            except Exception:
                pass

        category, confidence, rationale = _heuristic_llm(description)
        categories.append(
            {
                "row_hash": row_hash,
                "category_source": "llm_heuristic",
                "category": category,
                "confidence": confidence,
                "rationale_short": rationale,
            }
        )

    return categories
