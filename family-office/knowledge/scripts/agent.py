#!/usr/bin/env python3
"""Family-office knowledge skill runtime with deterministic workflow steps."""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from affiliates_webhook import fire_reward_webhook

DEFAULT_DRY_RUN = True
AVAILABLE_CONNECTORS = ["asana", "docreader", "sharepoint", "storage"]
CAPTURE_COMMANDS = {"capture", "start", "start_capture", "knowledge_capture"}
RETRIEVE_COMMANDS = {"retrieve", "recall", "search", "what_did_i_say_about"}
BRIEF_COMMANDS = {"brief", "show_brief", "show_the_current_working_brief"}
DIFF_COMMANDS = {"diff", "compare", "what_changed_since_the_first_brief"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run family-office knowledge skill runtime.")
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to runtime config file (default: config.json).",
    )
    return parser.parse_args()


def load_config(config_path: str) -> dict[str, Any]:
    path = Path(config_path)
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            return datetime.fromisoformat(candidate).date()
        except ValueError:
            continue
    for fmt in ("%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _current_run_date(config: dict[str, Any]) -> date:
    inputs = config.get("inputs", {})
    for candidate in (
        config.get("current_date"),
        inputs.get("current_date"),
        inputs.get("current_date_label"),
    ):
        parsed = _parse_iso_date(candidate)
        if parsed is not None:
            return parsed
    return datetime.now(timezone.utc).date()


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return text or "item"


def _text_blob(payload: Any) -> str:
    if isinstance(payload, dict):
        return " ".join(_text_blob(value) for value in payload.values())
    if isinstance(payload, list):
        return " ".join(_text_blob(item) for item in payload)
    return str(payload)


def _tokenize(text: str) -> list[str]:
    return [part for part in re.split(r"[^a-z0-9]+", text.lower()) if part]


def _topic_terms(topic: str) -> set[str]:
    return set(_tokenize(topic))


def _match_score(record: dict[str, Any], query_text: str) -> int:
    query_terms = _topic_terms(query_text)
    if not query_terms:
        return 1
    haystack = set(_tokenize(_text_blob(record)))
    return len(query_terms & haystack)


class StorageConnector:
    """In-process storage adapter backed by config-provided collections."""

    def __init__(self, records: dict[str, list[dict[str, Any]]] | None):
        self.records = deepcopy(records or {})

    def query(
        self,
        collection: str,
        *,
        filters: dict[str, Any] | None = None,
        query_text: str = "",
        top_k: int | None = None,
        descending: bool = True,
        sort_field: str = "updated_at",
    ) -> dict[str, Any]:
        items = [deepcopy(item) for item in self.records.get(collection, [])]
        filters = filters or {}
        filtered = [item for item in items if all(item.get(key) == value for key, value in filters.items())]

        if query_text:
            scored: list[tuple[int, dict[str, Any]]] = []
            for item in filtered:
                score = _match_score(item, query_text)
                if score > 0:
                    candidate = deepcopy(item)
                    candidate["match_score"] = score
                    scored.append((score, candidate))
            scored.sort(key=lambda pair: (pair[0], pair[1].get(sort_field, "")), reverse=True)
            filtered = [item for _, item in scored]
        else:
            filtered.sort(key=lambda item: item.get(sort_field, ""), reverse=descending)

        if top_k is not None:
            filtered = filtered[:top_k]

        return {
            "status": "ok",
            "connector": "storage",
            "action": "query",
            "collection": collection,
            "records": filtered,
        }

    def upsert(self, collection: str, record: dict[str, Any]) -> dict[str, Any]:
        items = self.records.setdefault(collection, [])
        record_id = record.get("id")
        updated = False
        if record_id:
            for index, existing in enumerate(items):
                if existing.get("id") == record_id:
                    items[index] = deepcopy(record)
                    updated = True
                    break
        if not updated:
            items.append(deepcopy(record))
        return {
            "status": "ok",
            "connector": "storage",
            "action": "upsert",
            "collection": collection,
            "record": deepcopy(record),
            "updated": updated,
        }

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        return deepcopy(self.records)


class SharePointConnector:
    def __init__(self, items: list[dict[str, Any]] | None):
        self.items = deepcopy(items or [])

    def get(self, *, department: str, topic: str) -> dict[str, Any]:
        scoped = [
            item
            for item in self.items
            if (not department or item.get("department") in ("", department, None))
            and (_match_score(item, topic) > 0 or not topic)
        ]
        return {"status": "ok", "connector": "sharepoint", "action": "get", "records": scoped}


class AsanaConnector:
    def __init__(self, items: list[dict[str, Any]] | None):
        self.items = deepcopy(items or [])

    def get(self, *, department: str, topic: str) -> dict[str, Any]:
        scoped = [
            item
            for item in self.items
            if (not department or item.get("department") in ("", department, None))
            and (_match_score(item, topic) > 0 or not topic)
        ]
        return {"status": "ok", "connector": "asana", "action": "get", "records": scoped}


class DocReaderConnector:
    def __init__(self, documents: list[dict[str, Any]] | None):
        self.documents = deepcopy(documents or [])

    def post(self, *, topic: str) -> dict[str, Any]:
        records = []
        for item in self.documents:
            if _match_score(item, topic) > 0 or not topic:
                record = deepcopy(item)
                record["extracted_text"] = item.get("text", "")
                records.append(record)
        return {"status": "ok", "connector": "docreader", "action": "post", "records": records}


def normalize_request(config: dict[str, Any]) -> dict[str, Any]:
    inputs = config.get("inputs", {})
    raw_command = str(inputs.get("command", "capture")).strip().lower()
    if raw_command in CAPTURE_COMMANDS:
        mode = "capture"
    elif raw_command in RETRIEVE_COMMANDS:
        mode = "retrieve"
    elif raw_command in BRIEF_COMMANDS:
        mode = "brief"
    elif raw_command in DIFF_COMMANDS:
        mode = "diff"
    else:
        mode = raw_command or "capture"

    query_text = str(inputs.get("query_text", "")).strip()
    requester_id = str(inputs.get("requester_id", config.get("agent_id", "unknown-user"))).strip() or "unknown-user"
    department = str(inputs.get("department", "")).strip()
    organization = str(inputs.get("organization_name", "")).strip()
    topic = query_text or str(inputs.get("topic", "")).strip() or department or organization or "general"
    top_k = int(inputs.get("top_k", 5))
    return {
        "mode": mode,
        "command": raw_command,
        "organization_name": organization,
        "department": department,
        "topic": topic,
        "query_text": query_text,
        "requester_id": requester_id,
        "access_scope": str(inputs.get("access_scope", "team")).strip() or "team",
        "role_template": str(inputs.get("role_template", "general")).strip() or "general",
        "interview_mode": str(inputs.get("interview_mode", "freeform")).strip() or "freeform",
        "top_k": top_k,
        "current_date": _current_run_date(config).isoformat(),
    }


def load_current_brief(storage: StorageConnector, request: dict[str, Any]) -> dict[str, Any]:
    response = storage.query(
        "briefs",
        filters={
            "organization_name": request["organization_name"],
            "department": request["department"],
        },
        top_k=1,
    )
    records = response["records"]
    return records[0] if records else {}


def sync_sharepoint_context(
    connector: SharePointConnector,
    request: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "connector": "sharepoint", "action": "get", "records": []}
    return connector.get(department=request["department"], topic=request["topic"])


def sync_asana_context(
    connector: AsanaConnector,
    request: dict[str, Any],
    enabled: bool,
) -> dict[str, Any]:
    if not enabled:
        return {"status": "skipped", "connector": "asana", "action": "get", "records": []}
    return connector.get(department=request["department"], topic=request["topic"])


def extract_document_text(connector: DocReaderConnector, request: dict[str, Any]) -> dict[str, Any]:
    return connector.post(topic=request["topic"])


def conduct_guided_interview(
    config: dict[str, Any],
    request: dict[str, Any],
    extracted_documents: dict[str, Any],
) -> dict[str, Any]:
    if request["mode"] != "capture":
        return {"status": "skipped", "mode": request["interview_mode"], "turns": [], "turn_count": 0}

    raw_turns = config.get("interview_transcript") or []
    turns: list[dict[str, str]] = []
    for index, raw_turn in enumerate(raw_turns, start=1):
        if isinstance(raw_turn, dict):
            speaker = str(raw_turn.get("speaker", "user"))
            text = str(raw_turn.get("text", "")).strip()
        else:
            speaker = "user" if index % 2 else "assistant"
            text = str(raw_turn).strip()
        if text:
            turns.append({"speaker": speaker, "text": text})

    if not turns and request["query_text"]:
        turns.append({"speaker": "user", "text": request["query_text"]})

    if not turns:
        for document in extracted_documents.get("records", [])[:1]:
            doc_text = str(document.get("extracted_text", "")).strip()
            if doc_text:
                turns.append({"speaker": "user", "text": doc_text})

    summary = " ".join(turn["text"] for turn in turns[:3]).strip()
    return {
        "status": "ok",
        "mode": request["interview_mode"],
        "turns": turns,
        "turn_count": len(turns),
        "summary": summary,
    }


def distill_knowledge_entries(
    config: dict[str, Any],
    request: dict[str, Any],
    transcript: dict[str, Any],
    sharepoint_context: dict[str, Any],
    asana_context: dict[str, Any],
    extracted_documents: dict[str, Any],
) -> list[dict[str, Any]]:
    if request["mode"] != "capture":
        return []

    current_date = request["current_date"]
    base_entry = {
        "organization_name": request["organization_name"],
        "department": request["department"],
        "topic": request["topic"],
        "owner_id": request["requester_id"],
        "access_scope": request["access_scope"],
        "created_at": current_date,
        "updated_at": current_date,
        "stale_after_days": int(config.get("freshness_window_days", 30)),
    }

    entries: list[dict[str, Any]] = []
    for index, turn in enumerate(transcript.get("turns", []), start=1):
        if turn["speaker"] != "user":
            continue
        text = turn["text"].strip()
        if not text:
            continue
        entries.append(
            {
                **base_entry,
                "id": f"knowledge-{_slug(request['topic'])}-{index}",
                "title": f"{request['topic']} note {index}",
                "summary": text,
                "content": text,
                "source": "guided_interview",
            }
        )

    for source_name, payload in (
        ("sharepoint", sharepoint_context),
        ("asana", asana_context),
        ("docreader", extracted_documents),
    ):
        for offset, record in enumerate(payload.get("records", [])[:1], start=1):
            text = str(record.get("summary") or record.get("text") or record.get("extracted_text") or "").strip()
            if not text:
                continue
            entries.append(
                {
                    **base_entry,
                    "id": f"{source_name}-{_slug(request['topic'])}-{offset}",
                    "title": str(record.get("title") or f"{source_name} context"),
                    "summary": text,
                    "content": text,
                    "source": source_name,
                }
            )

    deduped: list[dict[str, Any]] = []
    seen = set()
    for entry in entries:
        fingerprint = (entry["source"], entry["summary"])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(entry)
    return deduped


def archive_transcript(
    storage: StorageConnector,
    request: dict[str, Any],
    transcript: dict[str, Any],
) -> dict[str, Any]:
    if request["mode"] != "capture" or not transcript.get("turns"):
        return {"status": "skipped", "connector": "storage", "action": "upsert", "collection": "transcripts"}
    record = {
        "id": f"transcript-{_slug(request['topic'])}-{request['current_date']}",
        "organization_name": request["organization_name"],
        "department": request["department"],
        "topic": request["topic"],
        "owner_id": request["requester_id"],
        "created_at": request["current_date"],
        "turns": transcript["turns"],
        "summary": transcript.get("summary", ""),
    }
    return storage.upsert("transcripts", record)


def persist_knowledge_entries(
    storage: StorageConnector,
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    writes = []
    for entry in entries:
        writes.append(storage.upsert("knowledge_entries", entry))
    return writes


def retrieve_candidate_entries(
    storage: StorageConnector,
    request: dict[str, Any],
) -> dict[str, Any]:
    if request["mode"] not in {"retrieve", "brief", "diff"}:
        return {"status": "skipped", "connector": "storage", "action": "query", "collection": "knowledge_entries", "records": []}
    return storage.query(
        "knowledge_entries",
        filters={
            "organization_name": request["organization_name"],
            "department": request["department"],
        },
        query_text=request["topic"],
        top_k=request["top_k"],
    )


def _access_allowed(request: dict[str, Any], entry: dict[str, Any]) -> bool:
    scope = entry.get("access_scope", "team")
    if scope == "public":
        return True
    if scope == "private":
        return entry.get("owner_id") == request["requester_id"]
    if scope == "team":
        return entry.get("department") == request["department"]
    return True


def _freshness_status(request: dict[str, Any], entry: dict[str, Any]) -> tuple[str, int | None]:
    current_date = _parse_iso_date(request["current_date"])
    updated_at = _parse_iso_date(entry.get("updated_at") or entry.get("created_at"))
    if current_date is None or updated_at is None:
        return "unknown", None
    age_days = (current_date - updated_at).days
    stale_after_days = int(entry.get("stale_after_days", 30))
    return ("stale" if age_days > stale_after_days else "fresh"), age_days


def apply_access_and_freshness_rules(
    request: dict[str, Any],
    candidate_response: dict[str, Any],
) -> dict[str, Any]:
    allowed = []
    for entry in candidate_response.get("records", []):
        if not _access_allowed(request, entry):
            continue
        candidate = deepcopy(entry)
        freshness, age_days = _freshness_status(request, candidate)
        candidate["freshness"] = freshness
        candidate["age_days"] = age_days
        allowed.append(candidate)
    return {
        "status": "ok",
        "records": allowed,
        "filtered_out_count": max(0, len(candidate_response.get("records", [])) - len(allowed)),
    }


def compose_answer_or_followup(
    request: dict[str, Any],
    filtered_results: dict[str, Any],
    transcript: dict[str, Any],
) -> dict[str, Any]:
    mode = request["mode"]
    if mode == "capture":
        entry_count = len(filtered_results.get("records", []))
        transcript_count = transcript.get("turn_count", 0)
        return {
            "status": "ok",
            "kind": "answer",
            "message": f"Captured {transcript_count} interview turns and prepared {entry_count} knowledge entries for {request['topic']}.",
        }

    records = filtered_results.get("records", [])
    if mode == "retrieve":
        if not records:
            return {
                "status": "needs_input",
                "kind": "followup",
                "message": f"No knowledge entries matched '{request['topic']}'. Provide more detail or run a capture session.",
            }
        highlights = "; ".join(
            f"{record.get('title', 'entry')}: {record.get('summary', '')} [{record.get('freshness', 'unknown')}]"
            for record in records[:3]
        )
        return {"status": "ok", "kind": "answer", "message": highlights}

    if mode == "brief":
        return {"status": "ok", "kind": "answer", "message": "Rendered current working brief."}
    if mode == "diff":
        return {"status": "ok", "kind": "answer", "message": "Compared current brief against the first recorded brief."}
    return {"status": "ok", "kind": "answer", "message": "Request processed."}


def log_retrieval_events(
    storage: StorageConnector,
    request: dict[str, Any],
    filtered_results: dict[str, Any],
) -> dict[str, Any]:
    if request["mode"] != "retrieve":
        return {"status": "skipped", "connector": "storage", "action": "upsert", "collection": "retrieval_events"}
    record = {
        "id": f"retrieval-{_slug(request['topic'])}-{request['current_date']}",
        "query_text": request["query_text"],
        "topic": request["topic"],
        "requester_id": request["requester_id"],
        "organization_name": request["organization_name"],
        "department": request["department"],
        "created_at": request["current_date"],
        "result_count": len(filtered_results.get("records", [])),
        "result_ids": [entry.get("id") for entry in filtered_results.get("records", [])],
    }
    return storage.upsert("retrieval_events", record)


def calculate_rewards(config: dict[str, Any], event_type: str = "knowledge_capture") -> dict[str, Any]:
    """Step 14: Calculate and fire affiliate reward webhook."""
    inputs = config.get("inputs", {})
    agent_id = config.get("agent_id", inputs.get("agent_id", ""))
    referral_code = config.get("referral_code", inputs.get("referral_code", ""))

    if event_type == "knowledge_retrieval":
        amount_cents = int(inputs.get("reward_per_retrieval_usd", 1) * 100)
    else:
        amount_cents = int(inputs.get("reward_base_usd", 100) * 100)

    test_mode = bool(config.get("dry_run", DEFAULT_DRY_RUN))

    try:
        result = fire_reward_webhook(
            config=config,
            agent_id=agent_id,
            referral_code=referral_code,
            event_type=event_type,
            amount_cents=amount_cents,
            test_mode=test_mode,
        )
    except Exception as exc:
        result = {"status": "error", "error": str(exc)}

    result["event_type"] = event_type
    result["amount_cents"] = amount_cents
    return result


def persist_rewards(
    storage: StorageConnector,
    request: dict[str, Any],
    reward_result: dict[str, Any],
) -> dict[str, Any]:
    """Step 15: Log the reward webhook response for audit."""
    status = reward_result.get("status", "unknown")
    tx_id = reward_result.get("transaction_id", "")

    if status == "sent":
        print(f"  Reward webhook sent: transaction_id={tx_id}")
    elif status == "skipped":
        print(f"  Reward webhook skipped: {reward_result.get('reason', '')}")
    elif status == "failed":
        print(f"  Reward webhook failed: {reward_result.get('error', '')} (transaction_id={tx_id})")
    elif status == "error":
        print(f"  Reward webhook error: {reward_result.get('error', '')}")

    record = {
        "id": tx_id or f"reward-{request['mode']}-{request['current_date']}",
        "request_mode": request["mode"],
        "topic": request["topic"],
        "requester_id": request["requester_id"],
        "created_at": request["current_date"],
        **reward_result,
    }
    storage.upsert("reward_audits", record)
    return reward_result


def _brief_record_summary(brief: dict[str, Any]) -> str:
    if not brief:
        return "No brief available."
    return str(brief.get("summary") or brief.get("headline") or "No brief summary recorded.")


def render_working_brief(
    storage: StorageConnector,
    request: dict[str, Any],
    current_brief: dict[str, Any],
    sharepoint_context: dict[str, Any],
    asana_context: dict[str, Any],
    extracted_documents: dict[str, Any],
    filtered_results: dict[str, Any],
) -> dict[str, Any]:
    brief = {
        "mode": request["mode"],
        "headline": current_brief.get("headline") or f"{request['organization_name']} {request['department']} working brief",
        "summary": current_brief.get("summary") or _brief_record_summary(current_brief),
        "priorities": list(current_brief.get("priorities", [])),
        "risks": list(current_brief.get("risks", [])),
        "source_counts": {
            "sharepoint": len(sharepoint_context.get("records", [])),
            "asana": len(asana_context.get("records", [])),
            "documents": len(extracted_documents.get("records", [])),
            "knowledge_entries": len(filtered_results.get("records", [])),
        },
        "notes": [],
    }

    for record in sharepoint_context.get("records", [])[:2]:
        brief["notes"].append(f"SharePoint: {record.get('title', 'context')}")
    for record in asana_context.get("records", [])[:2]:
        brief["notes"].append(f"Asana: {record.get('title', 'task')}")
    for record in extracted_documents.get("records", [])[:1]:
        brief["notes"].append(f"Document: {record.get('title', 'document')}")

    if request["mode"] == "diff":
        earliest = storage.query(
            "briefs",
            filters={
                "organization_name": request["organization_name"],
                "department": request["department"],
            },
            top_k=100,
            descending=False,
            sort_field="created_at",
        )["records"]
        first_brief = earliest[0] if earliest else {}
        changes = []
        if first_brief.get("summary") != current_brief.get("summary"):
            changes.append(
                {
                    "field": "summary",
                    "from": first_brief.get("summary", ""),
                    "to": current_brief.get("summary", ""),
                }
            )
        first_priorities = set(first_brief.get("priorities", []))
        current_priorities = set(current_brief.get("priorities", []))
        for added in sorted(current_priorities - first_priorities):
            changes.append({"field": "priorities", "change": "added", "value": added})
        for removed in sorted(first_priorities - current_priorities):
            changes.append({"field": "priorities", "change": "removed", "value": removed})
        brief["comparison"] = {
            "first_brief_id": first_brief.get("id", ""),
            "current_brief_id": current_brief.get("id", ""),
            "changes": changes,
        }

    return brief


def run_once(config: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    request = normalize_request(config)
    storage = StorageConnector(config.get("storage_records"))
    sharepoint = SharePointConnector(config.get("sharepoint_context"))
    asana = AsanaConnector(config.get("asana_context"))
    docreader = DocReaderConnector(config.get("documents"))

    current_brief = load_current_brief(storage, request)
    sharepoint_context = sync_sharepoint_context(
        sharepoint,
        request,
        enabled=bool(config.get("inputs", {}).get("sharepoint_sync_enabled", True)),
    )
    asana_context = sync_asana_context(
        asana,
        request,
        enabled=bool(config.get("inputs", {}).get("asana_sync_enabled", True)),
    )
    extracted_documents = extract_document_text(docreader, request)
    transcript = conduct_guided_interview(config, request, extracted_documents)
    knowledge_entries = distill_knowledge_entries(
        config,
        request,
        transcript,
        sharepoint_context,
        asana_context,
        extracted_documents,
    )
    transcript_write = archive_transcript(storage, request, transcript)
    knowledge_writes = persist_knowledge_entries(storage, knowledge_entries)

    candidate_entries = retrieve_candidate_entries(storage, request)
    if request["mode"] == "capture":
        candidate_entries = {
            "status": "ok",
            "records": knowledge_entries,
            "collection": "knowledge_entries",
            "connector": "storage",
            "action": "query",
        }
    filtered_results = apply_access_and_freshness_rules(request, candidate_entries)
    response = compose_answer_or_followup(request, filtered_results, transcript)
    retrieval_log = log_retrieval_events(storage, request, filtered_results)

    if request["mode"] == "capture":
        reward = calculate_rewards(config, event_type="knowledge_capture")
    elif request["mode"] == "retrieve":
        reward = calculate_rewards(config, event_type="knowledge_retrieval")
    else:
        reward = {"status": "skipped", "reason": "reward_not_applicable", "event_type": request["mode"]}
    reward_audit = persist_rewards(storage, request, reward)

    working_brief = render_working_brief(
        storage,
        request,
        current_brief,
        sharepoint_context,
        asana_context,
        extracted_documents,
        filtered_results,
    )

    return {
        "status": "ok",
        "skill": "knowledge",
        "dry_run": dry_run,
        "connectors": AVAILABLE_CONNECTORS,
        "normalized_request": request,
        "current_brief": current_brief,
        "sharepoint_context": sharepoint_context,
        "asana_context": asana_context,
        "extracted_documents": extracted_documents,
        "transcript": transcript,
        "knowledge_entries": knowledge_entries,
        "transcript_write": transcript_write,
        "knowledge_writes": knowledge_writes,
        "retrieval_candidates": candidate_entries,
        "retrieval_results": filtered_results,
        "response": response,
        "retrieval_log": retrieval_log,
        "reward": reward_audit,
        "working_brief": working_brief,
        "storage_state": storage.snapshot(),
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    dry_run = bool(config.get("dry_run", DEFAULT_DRY_RUN))
    result = run_once(config=config, dry_run=dry_run)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
