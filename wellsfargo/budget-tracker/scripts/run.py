#!/usr/bin/env python3
"""Wells Fargo Budget Tracker.

Reads categorized transaction data from SerenDB (populated by bank-statement-processing)
and compares actual spending against budget targets by category.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from budget_builder import (
    aggregate_actuals,
    compare_budget,
    load_budget_targets,
    render_markdown,
)

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MONTHS = 1


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def dump_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def append_jsonl(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True, default=str) + "\n")


class RunLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path

    def emit(self, step: str, message: str, **data: Any) -> None:
        payload = {"ts": utc_now_iso(), "step": step, "message": message, "data": data}
        append_jsonl(self.log_path, payload)
        suffix = f" | {json.dumps(data, sort_keys=True, default=str)}" if data else ""
        print(f"[{payload['ts']}] {step}: {message}{suffix}")


# ---------------------------------------------------------------------------
# SerenDB resolution (mirrors bank-statement-processing logic)
# ---------------------------------------------------------------------------

def _run_seren_json(seren_bin: str, args: list[str]) -> tuple[int, Any, str]:
    cmd = [seren_bin, *args, "-o", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    payload: Any = None
    if result.stdout.strip():
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
    return result.returncode, payload, result.stderr.strip()


def _extract_database_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("databases", "data", "items", "results"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


def _parse_dotenv_value(env_path: Path, key: str) -> str:
    if not env_path.exists():
        return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line[len(f"{key}="):].strip().strip("\"'")
    return ""


def resolve_serendb_database_url(config: dict[str, Any], logger: RunLogger) -> tuple[str, str]:
    serendb_cfg = config.get("serendb", {})
    env_key = str(serendb_cfg.get("database_url_env", "WF_SERENDB_URL")).strip() or "WF_SERENDB_URL"

    from_env = os.getenv(env_key, "").strip()
    if from_env:
        return from_env, f"env:{env_key}"

    if not bool(serendb_cfg.get("auto_resolve_via_seren_cli", True)):
        raise RuntimeError(f"SerenDB is enabled but {env_key} is empty and auto-resolve is disabled.")

    seren_bin = shutil.which("seren")
    if not seren_bin:
        raise RuntimeError(f"SerenDB is enabled but {env_key} is empty and `seren` CLI was not found in PATH.")

    with tempfile.TemporaryDirectory(prefix="wf-budget-env-") as temp_dir:
        env_path = Path(temp_dir) / ".env"
        base_cmd = [seren_bin, "env", "init", "--env", str(env_path), "--key", env_key, "--yes", "-o", "json"]
        if bool(serendb_cfg.get("pooled_connection", True)):
            base_cmd.append("--pooled")

        rc, payload, _ = _run_seren_json(seren_bin, ["list-all-databases"])
        rows = _extract_database_rows(payload) if rc == 0 else []

        desired_project = str(serendb_cfg.get("project_name", "")).strip().lower()
        desired_database = str(serendb_cfg.get("database_name", "serendb")).strip().lower()
        candidates: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()

        explicit_pid = str(serendb_cfg.get("project_id", "")).strip()
        explicit_bid = str(serendb_cfg.get("branch_id", "")).strip()
        if explicit_pid and explicit_bid:
            candidates.append((explicit_pid, explicit_bid, "explicit"))
            seen.add((explicit_pid, explicit_bid))

        for row in rows:
            pid = row.get("project_id", "").strip()
            bid = row.get("branch_id", "").strip()
            if not pid or not bid or (pid, bid) in seen:
                continue
            rp = row.get("project_name", "").strip().lower()
            rd = row.get("database_name", "").strip().lower()
            if desired_project and rp != desired_project:
                continue
            if desired_database and rd != desired_database:
                continue
            seen.add((pid, bid))
            candidates.append((pid, bid, f"catalog:{rp}/{row.get('branch_name', '')}/{rd}"))

        if not candidates:
            raise RuntimeError(f"Failed to resolve SerenDB URL for {env_key}.")

        attempt_errors: list[str] = []
        for project_id, branch_id, source in candidates:
            cmd = [*base_cmd, "--project-id", project_id, "--branch-id", branch_id]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                resolved = _parse_dotenv_value(env_path, env_key).strip()
                if resolved:
                    os.environ[env_key] = resolved
                    logger.emit("serendb_url_resolved", "Resolved SerenDB URL", env_key=env_key, source=source)
                    return resolved, f"seren_cli_context:{source}"
                attempt_errors.append(f"{source}: empty dotenv write")
                continue
            attempt_errors.append(f"{source}: {(result.stderr or result.stdout or 'unknown error').strip()}")

        raise RuntimeError(f"Failed to resolve SerenDB URL. Tried {len(candidates)} candidates. Errors: {'; '.join(attempt_errors[:5])}")


QUERY_CATEGORIZED_TRANSACTIONS = """
SELECT
  t.row_hash, t.account_masked, t.txn_date, t.description_raw,
  t.amount, t.currency,
  COALESCE(c.category, 'uncategorized') AS category,
  COALESCE(c.category_source, 'none') AS category_source,
  c.confidence
FROM wf_transactions t
LEFT JOIN wf_txn_categories c ON c.row_hash = t.row_hash
WHERE t.txn_date >= %(start_date)s AND t.txn_date <= %(end_date)s
ORDER BY t.txn_date, t.row_hash
"""


def fetch_transactions(database_url: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY_CATEGORIZED_TRANSACTIONS, {"start_date": start_date.isoformat(), "end_date": end_date.isoformat()})
            columns = [desc.name for desc in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]


def _read_sql(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"SQL file not found: {path}")
    return path.read_text(encoding="utf-8")


def persist_budget_snapshot(database_url: str, schema_path: Path, run_record: dict[str, Any], comparison: dict[str, Any]) -> None:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(_read_sql(schema_path))
            cur.execute(
                """INSERT INTO wf_budget_runs (run_id, started_at, ended_at, status, period_start, period_end, total_budget, total_actual, total_variance, categories_over, txn_count, artifact_root)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET ended_at=EXCLUDED.ended_at, status=EXCLUDED.status, total_budget=EXCLUDED.total_budget, total_actual=EXCLUDED.total_actual, total_variance=EXCLUDED.total_variance, categories_over=EXCLUDED.categories_over, txn_count=EXCLUDED.txn_count""",
                (run_record["run_id"], run_record["started_at"], run_record["ended_at"], run_record["status"], run_record["period_start"], run_record["period_end"], comparison["total_budget"], comparison["total_actual"], comparison["total_variance"], comparison["categories_over"], run_record["txn_count"], run_record["artifact_root"]),
            )
            for cat in comparison["categories"]:
                cur.execute(
                    """INSERT INTO wf_budget_categories (run_id, category, label, budget_amount, actual_amount, variance, utilization_pct, txn_count, is_over_budget)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (run_id, category) DO UPDATE SET label=EXCLUDED.label, budget_amount=EXCLUDED.budget_amount, actual_amount=EXCLUDED.actual_amount, variance=EXCLUDED.variance, utilization_pct=EXCLUDED.utilization_pct, txn_count=EXCLUDED.txn_count, is_over_budget=EXCLUDED.is_over_budget""",
                    (run_record["run_id"], cat["category"], cat["label"], cat["budget_amount"], cat["actual_amount"], cat["variance"], cat["utilization_pct"], cat["txn_count"], cat["is_over_budget"]),
                )
            cur.execute(
                """INSERT INTO wf_budget_snapshots (run_id, period_start, period_end, total_budget, total_actual, total_variance, categories_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (run_id) DO UPDATE SET total_budget=EXCLUDED.total_budget, total_actual=EXCLUDED.total_actual, total_variance=EXCLUDED.total_variance, categories_json=EXCLUDED.categories_json""",
                (run_record["run_id"], run_record["period_start"], run_record["period_end"], comparison["total_budget"], comparison["total_actual"], comparison["total_variance"], json.dumps(comparison["categories"], default=str)),
            )
        conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track Wells Fargo budget vs. actual spending")
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    parser.add_argument("--start", type=str, default="")
    parser.add_argument("--end", type=str, default="")
    parser.add_argument("--out", type=str, default="artifacts/budget-tracker")
    parser.add_argument("--skip-persist", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Config file not found: {config_path}", file=sys.stderr)
        sys.exit(1)
    config = json.loads(config_path.read_text(encoding="utf-8"))

    today = date.today()
    if args.start:
        period_start = date.fromisoformat(args.start)
        period_end = date.fromisoformat(args.end) if args.end else today
    else:
        from dateutil.relativedelta import relativedelta
        period_start = today - relativedelta(months=args.months)
        period_end = today

    num_months = max(1.0, (period_end - period_start).days / 30.44)
    out_dir = Path(args.out)
    report_dir = ensure_dir(out_dir / "reports")
    export_dir = ensure_dir(out_dir / "exports")
    log_dir = ensure_dir(out_dir / "logs")

    run_id = f"budget-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    logger = RunLogger(log_dir / f"{run_id}.jsonl")
    logger.emit("start", "Budget tracking started", run_id=run_id)

    run_record: dict[str, Any] = {
        "run_id": run_id, "started_at": utc_now_iso(), "ended_at": None,
        "status": "running", "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(), "txn_count": 0,
        "artifact_root": str(out_dir.resolve()),
    }

    try:
        db_url, _ = resolve_serendb_database_url(config, logger)
        transactions = fetch_transactions(db_url, period_start, period_end)
        if not transactions:
            run_record["status"] = "empty"
            run_record["ended_at"] = utc_now_iso()
            print("No transactions found.")
            sys.exit(0)

        run_record["txn_count"] = len(transactions)
        targets_path = Path(config.get("budget_targets_path", "config/budget_targets.json"))
        if not targets_path.is_absolute():
            targets_path = config_path.parent / targets_path
        budget_targets = load_budget_targets(targets_path)

        actuals = aggregate_actuals(transactions)
        comparison = compare_budget(actuals, budget_targets, num_months=num_months)

        md_path = report_dir / f"{run_id}.md"
        md_path.write_text(render_markdown(comparison, period_start, period_end, run_id, len(transactions)), encoding="utf-8")
        dump_json(report_dir / f"{run_id}.json", {"run_id": run_id, "period_start": period_start.isoformat(), "period_end": period_end.isoformat(), "txn_count": len(transactions), "comparison": comparison})

        export_path = export_dir / f"{run_id}.categories.jsonl"
        for cat in comparison["categories"]:
            append_jsonl(export_path, cat)

        if not args.skip_persist and bool(config.get("serendb", {}).get("enabled", True)):
            schema_path = Path(config.get("serendb", {}).get("schema_path", "sql/schema.sql"))
            if not schema_path.is_absolute():
                schema_path = config_path.parent / schema_path
            run_record["status"] = "success"
            run_record["ended_at"] = utc_now_iso()
            persist_budget_snapshot(db_url, schema_path, run_record, comparison)
        else:
            run_record["status"] = "success"
            run_record["ended_at"] = utc_now_iso()

        print(f"\nBudget Tracker completed!")
        print(f"  Budget: ${comparison['total_budget']:,.2f}  Actual: ${comparison['total_actual']:,.2f}  Over: {comparison['categories_over']} categories")

    except Exception as exc:
        run_record["status"] = "error"
        run_record["ended_at"] = utc_now_iso()
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
