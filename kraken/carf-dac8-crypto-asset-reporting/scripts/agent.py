#!/usr/bin/env python3
"""CARF/DAC8 reconciliation runtime."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge_1099da import merge_bridge_records, parse_1099da_csv
from carf_parser import parse_carf_xml
from csv_normalizer import parse_casp_csv, parse_user_csv
from currency_normalizer import normalize_fiat_values
from dac8_parser import parse_dac8_xml
from enrichment import enrich_tax_treatments, resolve_cost_basis
from jurisdiction_detector import detect_jurisdictions
from logger import AuditLogger
from notifications import build_notifications
from reconciliation_engine import ToleranceConfig, reconcile_transactions
from report_generator import generate_reconciliation_outputs
from seren_api_client import SerenAPIError, SerenAPIKeyManager
from serendb_store import SerenDBStore
from transfer_tracker import reconcile_transfers
import sys
from urllib.request import Request, urlopen

SKILL_NAME = "carf-dac8-crypto-asset-reporting"
DEFAULT_CONFIG_PATH = "config.json"
DISCLAIMER_ACK_PATH = Path("state/disclaimer_seen.flag")

IMPORTANT_DISCLAIMER = """IMPORTANT DISCLAIMERS — READ BEFORE USING

1. NOT TAX OR LEGAL ADVICE: This skill is a reconciliation utility and not tax advice.
2. USER ACCOUNTABILITY: You are responsible for final tax filings and local compliance.
3. DATA QUALITY LIMITS: Input files can be incomplete; outputs require user validation.
4. LOCAL-FIRST PROCESSING: Data is processed locally on your machine.
5. CPA ESCALATION: Material or ambiguous cases should be reviewed by a licensed CPA.
6. SOFTWARE PROVIDED AS-IS: No warranty is provided.
"""


class ConfigError(RuntimeError):
    pass


class PolicyError(RuntimeError):
    pass


def _default_config() -> dict[str, Any]:
    return {
        "inputs": {
            "timestamp_tolerance_hours": 24,
            "quantity_tolerance_pct": 0.5,
            "fiat_tolerance_pct": 1.0,
            "home_currency": "USD",
            "cost_basis_method": "fifo",
            "enable_dac8_extensions": True,
            "enable_transfer_tracking": True,
            "enable_bridge_1099da": True,
        },
        "cpa": {
            "materiality_threshold_usd": 1000.0,
        },
        "runtime": {
            "save_json_export": True,
        },
        "seren": {
            "auto_register_key": True,
            "api_base_url": "https://api.serendb.com",
        },
    }


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _bootstrap_config_path(path: str) -> Path:
    config_path = Path(path)
    if config_path.exists():
        return config_path
    example_path = config_path.with_name("config.example.json")
    if example_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(example_path.read_text(encoding="utf-8"), encoding="utf-8")
    return config_path


def load_config(path: str) -> dict[str, Any]:
    config_path = _bootstrap_config_path(path)
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        body = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config JSON is invalid: {exc}") from exc
    return deep_merge(_default_config(), body)


def validate_config(config: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    inputs = config.get("inputs", {})
    ts_hours = int(inputs.get("timestamp_tolerance_hours", 0))
    qty_pct = float(inputs.get("quantity_tolerance_pct", -1.0))
    fiat_pct = float(inputs.get("fiat_tolerance_pct", -1.0))
    materiality = float(config.get("cpa", {}).get("materiality_threshold_usd", 0.0))
    basis_method = str(inputs.get("cost_basis_method", "fifo")).lower()

    if ts_hours < 1 or ts_hours > 72:
        errors.append("inputs.timestamp_tolerance_hours must be between 1 and 72")
    if qty_pct < 0 or qty_pct > 10:
        errors.append("inputs.quantity_tolerance_pct must be between 0 and 10")
    if fiat_pct < 0 or fiat_pct > 20:
        errors.append("inputs.fiat_tolerance_pct must be between 0 and 20")
    if materiality < 0:
        errors.append("cpa.materiality_threshold_usd must be >= 0")
    if basis_method not in {"fifo", "lifo", "specific"}:
        errors.append("inputs.cost_basis_method must be one of: fifo, lifo, specific")

    return errors


def show_disclaimer_if_first_run(*, accept_risk_disclaimer: bool) -> bool:
    if DISCLAIMER_ACK_PATH.exists():
        return False
    print(IMPORTANT_DISCLAIMER)
    if not accept_risk_disclaimer:
        raise PolicyError(
            "First run requires explicit acknowledgment. Re-run with --accept-risk-disclaimer."
        )
    DISCLAIMER_ACK_PATH.parent.mkdir(parents=True, exist_ok=True)
    DISCLAIMER_ACK_PATH.write_text(datetime.now(tz=timezone.utc).isoformat(), encoding="utf-8")
    return True


def ensure_seren_api_key(config: dict[str, Any]) -> str:
    seren_cfg = config.get("seren", {})
    manager = SerenAPIKeyManager(
        api_base_url=str(seren_cfg.get("api_base_url", "https://api.serendb.com")),
        env_file=".env",
    )
    auto_register = bool(seren_cfg.get("auto_register_key", True))
    return manager.ensure_api_key(auto_register=auto_register)


def _parse_report_file(path: str, *, enable_dac8_extensions: bool) -> tuple[dict[str, str], list[dict[str, Any]]]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix == ".xml":
        head = file_path.read_text(encoding="utf-8", errors="ignore")[:5000]
        if enable_dac8_extensions and ("DAC8" in head or "EUMemberState" in head):
            return parse_dac8_xml(file_path)
        return parse_carf_xml(file_path)

    if suffix == ".csv":
        casp_name = file_path.stem.split("_", 1)[0]
        return parse_casp_csv(file_path, casp_name=casp_name)

    raise ConfigError(f"Unsupported report format: {path}")


def _persist_local_export(*, session_id: str, payload: dict[str, Any]) -> str:
    out = Path("state")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"session_{session_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return str(path)


def _report_formats(report_metadatas: list[dict[str, str]]) -> list[str]:
    return sorted(
        {
            str(item.get("report_format", "")).strip()
            for item in report_metadatas
            if item.get("report_format")
        }
    )


def _tax_treatment_breakdown(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("tax_treatment") or "unknown") for row in rows)
    return {key: int(value) for key, value in sorted(counts.items())}


def _year_from_iso(value: str, default_year: int = 2026) -> str:
    raw = (value or "").strip()
    if not raw:
        return str(default_year)
    try:
        return str(datetime.fromisoformat(raw.replace("Z", "+00:00")).year)
    except ValueError:
        return str(default_year)


def run_once(
    *,
    config_path: str,
    carf_reports: list[str],
    user_records: list[str],
    output_dir: str,
    bridge_1099da_path: str | None,
    accept_risk_disclaimer: bool,
) -> dict[str, Any]:
    try:
        show_disclaimer_if_first_run(accept_risk_disclaimer=accept_risk_disclaimer)
    except PolicyError as exc:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "policy_violation",
            "message": str(exc),
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

    if not carf_reports:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "missing_input",
            "message": "At least one --carf-report is required",
            "disclaimer": IMPORTANT_DISCLAIMER,
        }
    if not user_records:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "missing_input",
            "message": "At least one --user-records file is required",
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

    config = load_config(config_path)
    errors = validate_config(config)
    if errors:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "validation_error",
            "errors": errors,
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

    try:
        seren_api_key = ensure_seren_api_key(config)
    except SerenAPIError as exc:
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "seren_api_key_error",
            "message": str(exc),
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

    session_id = str(uuid.uuid4())
    logger = AuditLogger("logs")
    store = SerenDBStore(os.getenv("SERENDB_URL"))
    if store.enabled:
        store.ensure_schema()
    store.create_session(session_id, config)

    logger.log_event("run_started", {"session_id": session_id, "carf_reports": len(carf_reports)})
    jurisdictions: dict[str, Any] = {}
    report_metadatas: list[dict[str, str]] = []

    try:
        input_cfg = config["inputs"]
        ts_tolerance_seconds = int(float(input_cfg["timestamp_tolerance_hours"]) * 3600)
        qty_tolerance_pct = float(input_cfg["quantity_tolerance_pct"])
        fiat_tolerance_pct = float(input_cfg["fiat_tolerance_pct"])
        materiality = float(config["cpa"]["materiality_threshold_usd"])
        home_currency = str(input_cfg.get("home_currency", "USD")).upper().strip() or "USD"
        cost_basis_method = str(input_cfg.get("cost_basis_method", "fifo")).lower()

        carf_rows: list[dict[str, Any]] = []

        for report in carf_reports:
            metadata, rows = _parse_report_file(
                report,
                enable_dac8_extensions=bool(input_cfg.get("enable_dac8_extensions", True)),
            )
            report_id = str(metadata.get("report_id") or Path(report).stem)
            metadata["report_id"] = report_id

            for row in rows:
                row["report_id"] = str(row.get("report_id") or report_id)

            report_metadatas.append(metadata)
            carf_rows.extend(rows)
            store.persist_raw_report(metadata)

        user_rows: list[dict[str, Any]] = []
        for file_path in user_records:
            user_rows.extend(parse_user_csv(file_path, source="user_csv"))

        bridge_stats = {"bridge_total": 0, "dual_reported": 0, "bridge_added": 0}
        if bridge_1099da_path and bool(input_cfg.get("enable_bridge_1099da", True)):
            bridge_rows = parse_1099da_csv(bridge_1099da_path)
            bridge_report_id = f"{session_id}-1099da"
            for row in bridge_rows:
                row["report_id"] = bridge_report_id

            carf_rows, bridge_stats = merge_bridge_records(
                primary_records=carf_rows,
                bridge_records=bridge_rows,
                timestamp_tolerance_seconds=ts_tolerance_seconds,
                quantity_tolerance_pct=qty_tolerance_pct,
            )

            if bridge_rows:
                bridge_meta = {
                    "report_id": bridge_report_id,
                    "casp_name": str(bridge_rows[0].get("casp_name", "1099DA_BROKER")),
                    "casp_jurisdiction": "US",
                    "reporting_year": _year_from_iso(str(bridge_rows[0].get("timestamp", ""))),
                    "user_tin_hash": "",
                    "report_format": "1099DA_CSV",
                    "source_file": str(bridge_1099da_path),
                    "total_records": str(len(bridge_rows)),
                }
                report_metadatas.append(bridge_meta)
                store.persist_raw_report(bridge_meta)

        # Step 2 enrichment: normalize fiat values, classify tax treatment, then resolve cost basis.
        carf_rows = normalize_fiat_values(rows=carf_rows, home_currency=home_currency)
        user_rows = normalize_fiat_values(rows=user_rows, home_currency=home_currency)
        carf_rows = enrich_tax_treatments(carf_rows)
        carf_rows = resolve_cost_basis(carf_rows, method=cost_basis_method)

        carf_by_txid = {
            str(row.get("transaction_id", "")): row
            for row in carf_rows
            if str(row.get("transaction_id", ""))
        }

        jurisdictions = detect_jurisdictions(
            report_metadatas=report_metadatas,
            normalized_records=carf_rows + user_rows,
        )

        recon = reconcile_transactions(
            carf_records=carf_rows,
            user_records=user_rows,
            tolerance=ToleranceConfig(
                timestamp_tolerance_seconds=ts_tolerance_seconds,
                quantity_tolerance_pct=qty_tolerance_pct,
                fiat_tolerance_pct=fiat_tolerance_pct,
            ),
            materiality_threshold_usd=materiality,
        )

        for match in recon["matches"]:
            carf_txid = str(match.get("carf_transaction_id", "") or "")
            if not carf_txid:
                continue
            carf_row = carf_by_txid.get(carf_txid)
            if not carf_row:
                continue
            match["carf_sub_type"] = str(carf_row.get("sub_type", ""))
            match["carf_tax_treatment"] = str(carf_row.get("tax_treatment", ""))
            match["gain_loss_home"] = float(carf_row.get("gain_loss_home", 0.0) or 0.0)
            match["holding_period_days"] = carf_row.get("holding_period_days")

        transfer_summary = {
            "carf_transfer_count": 0,
            "user_transfer_count": 0,
            "matched_transfer_count": 0,
            "unmatched_transfer_ids": [],
            "potential_wash_sale_ids": [],
            "potential_transfer_as_disposition_ids": [],
        }
        if bool(input_cfg.get("enable_transfer_tracking", True)):
            transfer_summary = reconcile_transfers(
                carf_records=carf_rows,
                user_records=user_rows,
                timestamp_tolerance_seconds=ts_tolerance_seconds,
                quantity_tolerance_pct=qty_tolerance_pct,
            )

        summary = {
            **recon["summary"],
            "home_currency": home_currency,
            "tax_treatment_breakdown": _tax_treatment_breakdown(carf_rows),
            "currency_conversion_missing_count": int(
                sum(
                    1
                    for row in (carf_rows + user_rows)
                    if bool(row.get("currency_conversion_missing"))
                )
            ),
            "bridge": bridge_stats,
            "transfer_tracking": transfer_summary,
            "jurisdictions": jurisdictions,
        }

        carf_id_map = store.persist_carf_transactions(session_id, carf_rows)
        user_id_map = store.persist_user_transactions(session_id, user_rows)
        store.persist_reconciliation_results(
            session_id,
            recon["matches"],
            carf_id_map=carf_id_map,
            user_id_map=user_id_map,
        )

        output_paths = generate_reconciliation_outputs(
            session_id=session_id,
            summary=summary,
            jurisdictions=jurisdictions,
            matches=recon["matches"],
            output_dir=output_dir,
            report_template_path="templates/reconciliation_report.md",
            cpa_template_path="templates/cpa_escalation.md",
        )

        notifications = build_notifications(summary=summary, outputs=output_paths)

        payload = {
            "status": "ok",
            "skill": SKILL_NAME,
            "session_id": session_id,
            "seren_api_key_present": bool(seren_api_key),
            "report_count": len(report_metadatas),
            "report_formats": _report_formats(report_metadatas),
            "summary": summary,
            "outputs": output_paths,
            "notifications": notifications,
            "disclaimer": IMPORTANT_DISCLAIMER,
        }

        if bool(config.get("runtime", {}).get("save_json_export", True)):
            payload["local_export"] = _persist_local_export(session_id=session_id, payload=payload)

        store.close_session(
            session_id,
            "completed",
            summary,
            jurisdictions=jurisdictions,
            report_formats=_report_formats(report_metadatas),
        )
        logger.log_event("run_completed", {"session_id": session_id, "summary": summary})
        return payload

    except Exception as exc:  # pragma: no cover - covered by smoke failure path
        logger.log_error("run_once", str(exc), {"session_id": session_id})
        store.close_session(
            session_id,
            "error",
            {"message": str(exc)},
            jurisdictions=jurisdictions,
            report_formats=_report_formats(report_metadatas),
        )
        return {
            "status": "error",
            "skill": SKILL_NAME,
            "error_code": "runtime_error",
            "message": str(exc),
            "session_id": session_id,
            "disclaimer": IMPORTANT_DISCLAIMER,
        }
    finally:
        store.close()



# ---------------------------------------------------------------------------
# SerenBucks balance helpers
# ---------------------------------------------------------------------------



def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _runtime_api_key() -> str:
    """Return the Seren API key from environment (Desktop or .env)."""
    for env_name in ("API_KEY", "SEREN_API_KEY"):
        token = (os.getenv(env_name) or "").strip()
        if token:
            return token
    return ""


def _check_serenbucks_balance(api_key: str) -> float:
    """Check SerenBucks balance. Returns balance in USD or 0.0 on error."""
    try:
        request = Request(
            "https://api.serendb.com/wallet/balance",
            headers={
                "User-Agent": "seren-carf-dac8-crypto-asset-reporting/1.0",
                "Accept": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urlopen(request, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            sb = data.get("data") or data.get("serenbucks") or {}
            raw = sb.get("balance_usd") or sb.get("funded_balance_usd") or "0"
            return _safe_float(str(raw).replace("$", "").replace(",", ""), 0.0)
    except Exception as exc:
        print(f"WARNING: could not fetch SerenBucks balance: {exc}", file=sys.stderr)
        return 0.0

def main() -> int:
    parser = argparse.ArgumentParser(description="CARF/DAC8 crypto reconciliation")
    sub = parser.add_subparsers(dest="cmd")

    run = sub.add_parser("run", help="run reconciliation")
    run.add_argument("--config", default=DEFAULT_CONFIG_PATH)
    run.add_argument("--carf-report", action="append", required=True)
    run.add_argument("--user-records", action="append", required=True)
    run.add_argument("--bridge-1099da")
    run.add_argument("--output-dir", default="state/reports")
    run.add_argument("--accept-risk-disclaimer", action="store_true")

    args = parser.parse_args()
    if args.cmd != "run":
        parser.print_help()
        return 2

    result = run_once(
        config_path=str(args.config),
        carf_reports=[str(p) for p in args.carf_report],
        user_records=[str(p) for p in args.user_records],
        output_dir=str(args.output_dir),
        bridge_1099da_path=str(args.bridge_1099da) if args.bridge_1099da else None,
        accept_risk_disclaimer=bool(args.accept_risk_disclaimer),
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
