#!/usr/bin/env python3
"""Wells Fargo Vendor Analysis."""
from __future__ import annotations

import argparse, json, os, shutil, subprocess, sys, tempfile, uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from vendor_builder import aggregate_vendors, render_markdown

SCRIPT_DIR = Path(__file__).resolve().parent

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

def _run_seren_json(seren_bin: str, args: list[str]) -> tuple[int, Any, str]:
    cmd = [seren_bin, *args, "-o", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    payload = None
    if result.stdout.strip():
        try: payload = json.loads(result.stdout)
        except json.JSONDecodeError: pass
    return result.returncode, payload, result.stderr.strip()

def _extract_database_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list): return payload
    if isinstance(payload, dict):
        for key in ("databases", "data", "items", "results"):
            if isinstance(payload.get(key), list): return payload[key]
    return []

def _parse_dotenv_value(env_path: Path, key: str) -> str:
    if not env_path.exists(): return ""
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line[len(f"{key}="):].strip().strip("\"'")
    return ""

def resolve_serendb_database_url(config: dict[str, Any], logger: RunLogger) -> tuple[str, str]:
    serendb_cfg = config.get("serendb", {})
    env_key = str(serendb_cfg.get("database_url_env", "WF_SERENDB_URL")).strip() or "WF_SERENDB_URL"
    from_env = os.getenv(env_key, "").strip()
    if from_env: return from_env, f"env:{env_key}"
    if not bool(serendb_cfg.get("auto_resolve_via_seren_cli", True)):
        raise RuntimeError(f"{env_key} is empty and auto-resolve is disabled.")
    seren_bin = shutil.which("seren")
    if not seren_bin:
        raise RuntimeError(f"{env_key} is empty and `seren` CLI not found.")
    with tempfile.TemporaryDirectory(prefix="wf-vendor-env-") as temp_dir:
        env_path = Path(temp_dir) / ".env"
        base_cmd = [seren_bin, "env", "init", "--env", str(env_path), "--key", env_key, "--yes", "-o", "json"]
        if bool(serendb_cfg.get("pooled_connection", True)): base_cmd.append("--pooled")
        rc, payload, _ = _run_seren_json(seren_bin, ["list-all-databases"])
        rows = _extract_database_rows(payload) if rc == 0 else []
        desired_project = str(serendb_cfg.get("project_name", "")).strip().lower()
        desired_database = str(serendb_cfg.get("database_name", "serendb")).strip().lower()
        candidates: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        pid_e = str(serendb_cfg.get("project_id", "")).strip()
        bid_e = str(serendb_cfg.get("branch_id", "")).strip()
        if pid_e and bid_e: candidates.append((pid_e, bid_e, "explicit")); seen.add((pid_e, bid_e))
        for row in rows:
            pid = row.get("project_id", "").strip(); bid = row.get("branch_id", "").strip()
            if not pid or not bid or (pid, bid) in seen: continue
            rp = row.get("project_name", "").strip().lower(); rd = row.get("database_name", "").strip().lower()
            if desired_project and rp != desired_project: continue
            if desired_database and rd != desired_database: continue
            seen.add((pid, bid)); candidates.append((pid, bid, f"catalog:{rp}/{row.get('branch_name','')}/{rd}"))
        if not candidates: raise RuntimeError(f"No candidates for {env_key}.")
        errors: list[str] = []
        for project_id, branch_id, source in candidates:
            cmd = [*base_cmd, "--project-id", project_id, "--branch-id", branch_id]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                resolved = _parse_dotenv_value(env_path, env_key).strip()
                if resolved: os.environ[env_key] = resolved; return resolved, f"seren_cli_context:{source}"
                errors.append(f"{source}: empty"); continue
            errors.append(f"{source}: {(result.stderr or result.stdout or '?').strip()}")
        raise RuntimeError(f"Failed. Errors: {'; '.join(errors[:5])}")

QUERY = """
SELECT t.row_hash, t.account_masked, t.txn_date, t.description_raw,
  t.amount, t.currency, COALESCE(c.category,'uncategorized') AS category,
  COALESCE(c.category_source,'none') AS category_source, c.confidence
FROM wf_transactions t LEFT JOIN wf_txn_categories c ON c.row_hash = t.row_hash
WHERE t.txn_date >= %(start_date)s AND t.txn_date <= %(end_date)s
ORDER BY t.txn_date, t.row_hash
"""

def fetch_transactions(url: str, s: date, e: date) -> list[dict[str, Any]]:
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(QUERY, {"start_date": s.isoformat(), "end_date": e.isoformat()})
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

def persist_vendor_analysis(url: str, schema_path: Path, rec: dict, analysis: dict) -> None:
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute(schema_path.read_text(encoding="utf-8"))
            cur.execute("INSERT INTO wf_vendor_runs (run_id,started_at,ended_at,status,period_start,period_end,unique_vendors,total_spend,txn_count,artifact_root) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO UPDATE SET ended_at=EXCLUDED.ended_at,status=EXCLUDED.status,unique_vendors=EXCLUDED.unique_vendors,total_spend=EXCLUDED.total_spend,txn_count=EXCLUDED.txn_count",
                (rec["run_id"],rec["started_at"],rec["ended_at"],rec["status"],rec["period_start"],rec["period_end"],analysis["unique_vendors"],analysis["total_spend"],rec["txn_count"],rec["artifact_root"]))
            for v in analysis["vendors"]:
                cur.execute("INSERT INTO wf_vendor_merchants (run_id,vendor_normalized,category,total_spend,txn_count,avg_amount,first_seen,last_seen,spend_rank) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (run_id,vendor_normalized) DO UPDATE SET category=EXCLUDED.category,total_spend=EXCLUDED.total_spend,txn_count=EXCLUDED.txn_count,avg_amount=EXCLUDED.avg_amount,spend_rank=EXCLUDED.spend_rank",
                    (rec["run_id"],v["vendor_normalized"],v["category"],v["total_spend"],v["txn_count"],v["avg_amount"],v["first_seen"],v["last_seen"],v["spend_rank"]))
            cur.execute("INSERT INTO wf_vendor_snapshots (run_id,period_start,period_end,unique_vendors,total_spend,top_vendors_json) VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (run_id) DO UPDATE SET unique_vendors=EXCLUDED.unique_vendors,total_spend=EXCLUDED.total_spend,top_vendors_json=EXCLUDED.top_vendors_json",
                (rec["run_id"],rec["period_start"],rec["period_end"],analysis["unique_vendors"],analysis["total_spend"],json.dumps(analysis["vendors"],default=str)))
        conn.commit()

def main() -> None:
    p = argparse.ArgumentParser(description="Analyze Wells Fargo vendor spending")
    p.add_argument("--config", default="config.json"); p.add_argument("--months", type=int, default=12)
    p.add_argument("--start", default=""); p.add_argument("--end", default="")
    p.add_argument("--top", type=int, default=50); p.add_argument("--out", default="artifacts/vendor-analysis")
    p.add_argument("--skip-persist", action="store_true")
    args = p.parse_args()
    cp = Path(args.config)
    if not cp.exists(): print(f"Config not found: {cp}", file=sys.stderr); sys.exit(1)
    config = json.loads(cp.read_text(encoding="utf-8"))
    today = date.today()
    if args.start:
        ps = date.fromisoformat(args.start); pe = date.fromisoformat(args.end) if args.end else today
    else:
        from dateutil.relativedelta import relativedelta
        ps = today - relativedelta(months=args.months); pe = today
    out = Path(args.out); log_dir = ensure_dir(out / "logs")
    rid = f"vendor-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:8]}"
    logger = RunLogger(log_dir / f"{rid}.jsonl")
    rec = {"run_id": rid, "started_at": utc_now_iso(), "ended_at": None, "status": "running", "period_start": ps.isoformat(), "period_end": pe.isoformat(), "txn_count": 0, "artifact_root": str(out.resolve())}
    try:
        url, _ = resolve_serendb_database_url(config, logger)
        txns = fetch_transactions(url, ps, pe)
        if not txns: rec["status"]="empty"; rec["ended_at"]=utc_now_iso(); print("No transactions."); sys.exit(0)
        rec["txn_count"] = len(txns)
        analysis = aggregate_vendors(txns, top_n=args.top)
        rd = ensure_dir(out / "reports")
        rd_path = rd / f"{rid}.md"
        rd_path.write_text(render_markdown(analysis, ps, pe, rid, len(txns)), encoding="utf-8")
        dump_json(rd / f"{rid}.json", {"run_id": rid, "period_start": ps.isoformat(), "period_end": pe.isoformat(), "txn_count": len(txns), "analysis": analysis})
        ed = ensure_dir(out / "exports")
        for v in analysis["vendors"]: append_jsonl(ed / f"{rid}.vendors.jsonl", v)
        if not args.skip_persist and config.get("serendb",{}).get("enabled",True):
            sp = Path(config.get("serendb",{}).get("schema_path","sql/schema.sql"))
            if not sp.is_absolute(): sp = cp.parent / sp
            rec["status"]="success"; rec["ended_at"]=utc_now_iso()
            persist_vendor_analysis(url, sp, rec, analysis)
        else: rec["status"]="success"; rec["ended_at"]=utc_now_iso()
        print(f"\nVendor Analysis done! {analysis['unique_vendors']} vendors, ${analysis['total_spend']:,.2f} total")
    except Exception as e:
        rec["status"]="error"; rec["ended_at"]=utc_now_iso(); print(f"ERROR: {e}", file=sys.stderr); sys.exit(1)

if __name__ == "__main__": main()
