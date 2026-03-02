#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from getpass import getpass
from pathlib import Path
from typing import Any

from categorize import categorize_transactions
from checkpoint import CheckpointStore
from common import (
    append_jsonl,
    dump_json,
    ensure_dir,
    load_json,
    mask_account,
    sha256_file,
    sha256_text,
    utc_now_iso,
)
from pdf_extract import parse_statement_pdf
from report import write_report
from serendb_load import persist_run
from wf_download import (
    AuthError,
    Credentials,
    SelectorError,
    WorkflowError,
    load_selector_profile,
    login_and_download_statements,
)

SCRIPT_DIR = Path(__file__).resolve().parent
MIN_STATEMENT_MONTHS = 3
RUN_LOCK_FILENAME = ".run.lock.json"

BROWSER_TARGETS: list[tuple[str, str]] = [
    ("Firefox", "moz-firefox"),
    ("Firefox Developer Edition", "moz-firefox"),
    ("Firefox Nightly", "moz-firefox"),
    ("Google Chrome", "chrome"),
    ("Google Chrome for Testing", "chrome"),
    ("Brave Browser", "chrome"),
    ("Chromium", "chrome"),
    ("Microsoft Edge", "msedge"),
]

_ACTIVE_RUN_LOCK: Path | None = None


@dataclass
class RunLogger:
    log_path: Path

    def emit(self, step: str, message: str, **data: Any) -> None:
        payload = {"ts": utc_now_iso(), "step": step, "message": message, "data": data}
        append_jsonl(self.log_path, payload)
        suffix = f" | {json.dumps(data, sort_keys=True)}" if data else ""
        print(f"[{payload['ts']}] {step}: {message}{suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Wells Fargo read-only statement automation")
    parser.add_argument("--config", default="config.json", help="Path to config JSON")
    parser.add_argument("--mode", default="read-only", choices=["read-only"], help="Execution mode")
    parser.add_argument(
        "--auth-method",
        default="",
        choices=["password", "passkey", "manual"],
        help="Authentication method override (default from config.runtime.auth_method)",
    )
    parser.add_argument(
        "--months",
        type=int,
        default=MIN_STATEMENT_MONTHS,
        help=f"Number of statement rows to download (minimum {MIN_STATEMENT_MONTHS})",
    )
    parser.add_argument("--out", default="artifacts/wellsfargo", help="Artifact root directory")
    parser.add_argument("--resume", action="store_true", help="Resume from checkpoint")
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip browser and use local PDFs",
    )
    parser.add_argument("--skip-serendb", action="store_true", help="Do not write to SerenDB")
    parser.add_argument(
        "--strict-parse",
        action="store_true",
        help="Fail run if any PDF parse error occurs",
    )
    parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        help="Deprecated for MCP mode (browser mode is controlled by Playwright MCP server)",
    )
    parser.add_argument(
        "--headed",
        dest="headless",
        action="store_false",
        help="Deprecated for MCP mode (browser mode is controlled by Playwright MCP server)",
    )
    parser.add_argument(
        "--replay-serendb",
        default="",
        help="Replay SerenDB sync for a prior run_id",
    )
    parser.add_argument(
        "--cdp-url",
        default="",
        help=(
            "Attach Playwright MCP to an existing browser via CDP "
            "(for example http://127.0.0.1:9222)"
        ),
    )
    parser.add_argument(
        "--browser-app",
        default="",
        help="Preferred macOS browser app to focus for manual handoff (e.g. 'Google Chrome')",
    )
    parser.add_argument(
        "--browser-type",
        default="",
        help=(
            "Playwright MCP browser type override "
            "(for example: moz-firefox, chrome, msedge)"
        ),
    )
    parser.set_defaults(headless=None)
    return parser.parse_args()


def merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Path, skill_root: Path) -> dict[str, Any]:
    default_config = {
        "runtime": {
            "strict_read_only": True,
            "selector_profile_version": "unknown",
            "artifacts_subdir": "wellsfargo",
            "auth_method": "password",
        },
        "playwright": {
            "headless": True,
            "timeout_ms": 30000,
            "mcp_command": "node",
            "mcp_args": [],
            "mcp_script": "",
            "mcp_script_env": "PLAYWRIGHT_MCP_SCRIPT",
            "connect_cdp_url": "",
            "browser_app": "Firefox",
            "browser_type": "moz-firefox",
        },
        "selectors_path": "config/selectors.wellsfargo.json",
        "categorization": {
            "llm_mode": "heuristic",
            "llm_endpoint": "",
            "taxonomy_version": "v1",
        },
        "serendb": {
            "enabled": True,
            "database_url_env": "WF_SERENDB_URL",
            "auto_resolve_via_seren_cli": True,
            "pooled_connection": True,
            "project_id": "",
            "project_name": "",
            "branch_id": "",
            "branch_name": "",
            "database_name": "serendb",
            "schema_path": "sql/schema.sql",
            "views_path": "sql/views.sql",
        },
    }

    if config_path.exists():
        user_config = json.loads(config_path.read_text(encoding="utf-8"))
        cfg = merge_dict(default_config, user_config)
    else:
        cfg = default_config

    selectors_path = Path(cfg["selectors_path"])
    if not selectors_path.is_absolute():
        cfg["selectors_path"] = str((skill_root / selectors_path).resolve())

    schema_path = Path(cfg["serendb"]["schema_path"])
    views_path = Path(cfg["serendb"]["views_path"])
    if not schema_path.is_absolute():
        cfg["serendb"]["schema_path"] = str((skill_root / schema_path).resolve())
    if not views_path.is_absolute():
        cfg["serendb"]["views_path"] = str((skill_root / views_path).resolve())

    return cfg


def create_run_id() -> str:
    import uuid
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"wf_{ts}_{uuid.uuid4().hex[:8]}"


def _pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _release_active_run_lock() -> None:
    global _ACTIVE_RUN_LOCK
    lock_path = _ACTIVE_RUN_LOCK
    _ACTIVE_RUN_LOCK = None
    if lock_path is None:
        return

    try:
        payload = load_json(lock_path, default={})
        lock_pid = int(payload.get("pid", 0) or 0)
    except Exception:
        lock_pid = 0

    if lock_pid not in (0, os.getpid()):
        return

    try:
        if lock_path.exists():
            lock_path.unlink()
    except OSError:
        pass


def acquire_run_lock(state_dir: Path, run_id: str) -> None:
    global _ACTIVE_RUN_LOCK
    lock_path = state_dir / RUN_LOCK_FILENAME

    if lock_path.exists():
        payload = load_json(lock_path, default={})
        existing_pid = int(payload.get("pid", 0) or 0)
        existing_run_id = str(payload.get("run_id", "")).strip() or "unknown"
        if _pid_is_running(existing_pid) and existing_pid != os.getpid():
            raise RuntimeError(
                "Another Wells Fargo run is already active "
                f"(pid={existing_pid}, run_id={existing_run_id}). "
                "Finish or stop that run before starting a new one."
            )
        try:
            lock_path.unlink()
        except OSError:
            pass

    payload = {
        "pid": os.getpid(),
        "run_id": run_id,
        "started_at": utc_now_iso(),
    }
    dump_json(lock_path, payload)
    _ACTIVE_RUN_LOCK = lock_path
    atexit.register(_release_active_run_lock)


def _app_bundle_exists(app_name: str) -> bool:
    app_suffix = f"{app_name}.app"
    app_paths = [
        Path("/Applications") / app_suffix,
        Path.home() / "Applications" / app_suffix,
    ]
    return any(path.exists() for path in app_paths)


def detect_installed_browser_targets() -> list[tuple[str, str]]:
    installed: list[tuple[str, str]] = []
    for app_name, browser_type in BROWSER_TARGETS:
        if _app_bundle_exists(app_name):
            installed.append((app_name, browser_type))
    return installed


def choose_browser_target_interactive(
    *,
    installed_targets: list[tuple[str, str]],
    preferred_app: str,
    preferred_type: str,
) -> tuple[str, str]:
    if not installed_targets:
        return preferred_app, preferred_type

    preferred_app_lower = preferred_app.strip().lower()
    preferred_type_lower = preferred_type.strip().lower()

    default_index = 0
    for idx, (app_name, browser_type) in enumerate(installed_targets):
        if app_name.strip().lower() == preferred_app_lower:
            default_index = idx
            break
        if browser_type.strip().lower() == preferred_type_lower:
            default_index = idx

    print("Select browser for this run:")
    for idx, (app_name, browser_type) in enumerate(installed_targets, start=1):
        marker = " (default)" if idx - 1 == default_index else ""
        print(f"  {idx}. {app_name} [{browser_type}]{marker}")

    selection = input("Choose browser number (press Enter for default): ").strip()
    if not selection:
        return installed_targets[default_index]

    try:
        selected_index = int(selection) - 1
    except ValueError:
        print("Invalid choice; using default browser.")
        return installed_targets[default_index]

    if selected_index < 0 or selected_index >= len(installed_targets):
        print("Choice out of range; using default browser.")
        return installed_targets[default_index]

    return installed_targets[selected_index]


def _browser_type_for_app(app_name: str) -> str | None:
    app_lower = app_name.strip().lower()
    if not app_lower:
        return None
    for target_app, target_type in BROWSER_TARGETS:
        if target_app.strip().lower() == app_lower:
            return target_type
    return None


def _default_app_for_browser_type(browser_type: str) -> str | None:
    target_lower = browser_type.strip().lower()
    if not target_lower:
        return None
    for target_app, target_type in BROWSER_TARGETS:
        if target_type.strip().lower() == target_lower:
            return target_app
    return None


def _normalize_browser_family(browser_type: str, browser_app: str = "") -> str:
    parts = [str(browser_type or "").strip().lower(), str(browser_app or "").strip().lower()]
    token = " ".join(part for part in parts if part)
    if "firefox" in token or "moz-firefox" in token:
        return "firefox"
    if (
        "chrome" in token
        or "chromium" in token
        or "brave" in token
        or "edge" in token
        or "msedge" in token
    ):
        return "chrome"
    # Preserve historically successful behavior when unknown.
    return "firefox"


def resolve_playwright_mcp_script(config: dict[str, Any]) -> str:
    playwright_cfg = config.get("playwright", {})
    configured = str(playwright_cfg.get("mcp_script", "")).strip()
    if configured:
        return configured

    env_key = str(playwright_cfg.get("mcp_script_env", "PLAYWRIGHT_MCP_SCRIPT")).strip()
    if env_key:
        from_env = os.getenv(env_key, "").strip()
        if from_env:
            return from_env

    home = Path.home()
    workspace_root = SCRIPT_DIR
    for parent in SCRIPT_DIR.parents:
        if (parent / ".git").exists():
            workspace_root = parent
            break

    candidates = [
        # SerenDesktop app bundle path.
        Path(
            "/Applications/SerenDesktop.app/Contents/Resources/embedded-runtime/"
            "mcp-servers/playwright-stealth/dist/index.js"
        ),
        # SerenDesktop source build output (preferred for local development).
        workspace_root.parent
        / "seren-desktop/mcp-servers/playwright-stealth/dist/index.js",
        # Common absolute source build output path.
        home
        / "Projects/Seren_Projects/seren-desktop/mcp-servers/playwright-stealth/"
        "dist/index.js",
        # SerenDesktop dev build output.
        home
        / "Projects/Seren_Projects/seren-desktop/src-tauri/target/debug/"
        "mcp-servers/playwright-stealth/dist/index.js",
    ]

    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())
    return ""


def _parse_dotenv_value(dotenv_path: Path, key: str) -> str:
    if not dotenv_path.exists():
        return ""
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        if k.strip() != key:
            continue
        value = v.strip()
        if (
            len(value) >= 2
            and value[0] in ("'", '"')
            and value[-1] == value[0]
        ):
            value = value[1:-1]
        return value
    return ""


def _extract_project_id_from_context_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("project_id", "projectId", "default_project_id", "defaultProjectId"):
        value = str(payload.get(key, "")).strip()
        if value:
            return value
    project_node = payload.get("project")
    if isinstance(project_node, dict):
        for key in ("id", "project_id", "projectId"):
            value = str(project_node.get(key, "")).strip()
            if value:
                return value
    return ""


def _extract_project_rows(payload: Any) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        for key in ("projects", "data", "items", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if not candidates:
            candidates = [payload]

    rows: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("id", "")).strip()
        if not project_id:
            continue
        name = str(item.get("name", "")).strip()
        rows.append({"id": project_id, "name": name})
    return rows


def _extract_branch_id_from_context_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("branch_id", "branchId", "default_branch_id", "defaultBranchId"):
        value = str(payload.get(key, "")).strip()
        if value:
            return value
    branch_node = payload.get("branch")
    if isinstance(branch_node, dict):
        for key in ("id", "branch_id", "branchId"):
            value = str(branch_node.get(key, "")).strip()
            if value:
                return value
    return ""


def _extract_branch_rows(payload: Any) -> list[dict[str, Any]]:
    candidates: list[Any] = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict):
        for key in ("branches", "data", "items", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if not candidates:
            candidates = [payload]

    rows: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        branch_id = str(item.get("id", "")).strip()
        if not branch_id:
            continue
        name = str(item.get("name", "")).strip()
        is_default = bool(item.get("is_default") or item.get("isDefault"))
        rows.append({"id": branch_id, "name": name, "is_default": is_default})
    return rows


def _run_seren_json(seren_bin: str, args: list[str]) -> tuple[int, Any, str]:
    cmd = [seren_bin, *args, "-o", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    stdout = (result.stdout or "").strip()
    payload: Any = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None
    return result.returncode, payload, (result.stderr or "").strip()


def _extract_database_rows(payload: Any) -> list[dict[str, Any]]:
    rows_payload: list[Any] = []
    if isinstance(payload, list):
        rows_payload = payload
    elif isinstance(payload, dict):
        for key in ("databases", "data", "items", "rows"):
            value = payload.get(key)
            if isinstance(value, list):
                rows_payload = value
                break
        if not rows_payload:
            rows_payload = [payload]

    rows: list[dict[str, Any]] = []
    for item in rows_payload:
        if not isinstance(item, dict):
            continue
        project_id = str(item.get("project_id", "") or item.get("projectId", "")).strip()
        branch_id = str(item.get("branch_id", "") or item.get("branchId", "")).strip()
        if not project_id or not branch_id:
            continue
        project_name_value = item.get("project_name", "") or item.get("project", "")
        if isinstance(project_name_value, dict):
            project_name_value = project_name_value.get("name", "")
        branch_name_value = item.get("branch_name", "") or item.get("branch", "")
        if isinstance(branch_name_value, dict):
            branch_name_value = branch_name_value.get("name", "")
        database_name_value = (
            item.get("database_name", "")
            or item.get("database", "")
            or item.get("name", "")
        )
        rows.append(
            {
                "project_id": project_id,
                "branch_id": branch_id,
                "project_name": str(project_name_value).strip(),
                "branch_name": str(branch_name_value).strip(),
                "database_name": str(database_name_value).strip(),
                "is_default": bool(
                    item.get("is_default")
                    or item.get("isDefault")
                    or item.get("is_default_branch")
                    or item.get("isDefaultBranch")
                ),
                "created_at": str(item.get("created_at", "")).strip(),
            }
        )
    return rows


def _resolve_serendb_project_id(seren_bin: str, serendb_cfg: dict[str, Any]) -> str:
    explicit_id = str(serendb_cfg.get("project_id", "")).strip()
    if explicit_id:
        return explicit_id

    rc, context_payload, _ = _run_seren_json(seren_bin, ["set-context", "show"])
    if rc == 0:
        context_project_id = _extract_project_id_from_context_payload(context_payload)
        if context_project_id:
            return context_project_id

    desired_project_name = str(serendb_cfg.get("project_name", "")).strip().lower()
    rc, projects_payload, _ = _run_seren_json(seren_bin, ["projects", "list"])
    if rc != 0:
        return ""

    rows = _extract_project_rows(projects_payload)
    if not rows:
        return ""
    if desired_project_name:
        for row in rows:
            if row.get("name", "").strip().lower() == desired_project_name:
                return row["id"]
    if len(rows) == 1:
        return rows[0]["id"]
    return ""


def _resolve_serendb_branch_id(
    seren_bin: str,
    serendb_cfg: dict[str, Any],
    project_id: str,
) -> str:
    explicit_id = str(serendb_cfg.get("branch_id", "")).strip()
    if explicit_id:
        return explicit_id

    rc, context_payload, _ = _run_seren_json(seren_bin, ["set-context", "show"])
    if rc == 0:
        context_branch_id = _extract_branch_id_from_context_payload(context_payload)
        if context_branch_id:
            return context_branch_id

    if not project_id:
        return ""

    desired_branch_name = str(serendb_cfg.get("branch_name", "")).strip().lower()
    rc, branches_payload, _ = _run_seren_json(
        seren_bin,
        ["branches", "--project-id", project_id, "list"],
    )
    if rc != 0:
        return ""

    rows = _extract_branch_rows(branches_payload)
    if not rows:
        return ""
    if desired_branch_name:
        for row in rows:
            if row.get("name", "").strip().lower() == desired_branch_name:
                return row["id"]
    for row in rows:
        if row.get("is_default"):
            return row["id"]
    if len(rows) == 1:
        return rows[0]["id"]
    return ""


def _rank_serendb_targets_from_database_catalog(
    seren_bin: str,
    serendb_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    rc, payload, _ = _run_seren_json(seren_bin, ["list-all-databases"])
    if rc != 0:
        return []
    rows = _extract_database_rows(payload)
    if not rows:
        return []

    desired_project_id = str(serendb_cfg.get("project_id", "")).strip().lower()
    desired_branch_id = str(serendb_cfg.get("branch_id", "")).strip().lower()
    desired_project = str(serendb_cfg.get("project_name", "")).strip().lower()
    desired_branch = str(serendb_cfg.get("branch_name", "")).strip().lower()
    desired_database = str(serendb_cfg.get("database_name", "serendb")).strip().lower()

    def score(row: dict[str, Any]) -> tuple[int, str, str, str, str]:
        points = 0
        row_project_id = row.get("project_id", "").strip().lower()
        row_branch_id = row.get("branch_id", "").strip().lower()
        row_project = row.get("project_name", "").strip().lower()
        row_branch = row.get("branch_name", "").strip().lower()
        row_database = row.get("database_name", "").strip().lower()

        if desired_project_id and row_project_id == desired_project_id:
            points += 16
        if desired_branch_id and row_branch_id == desired_branch_id:
            points += 16
        if desired_project and row_project == desired_project:
            points += 8
        if desired_branch and row_branch == desired_branch:
            points += 8
        if desired_database and row_database == desired_database:
            points += 8
        if row.get("is_default"):
            points += 4
        if row_database == "serendb":
            points += 2
        return (
            points,
            row.get("created_at", ""),
            row.get("project_name", ""),
            row.get("branch_name", ""),
            row.get("database_name", ""),
        )

    return sorted(rows, key=score, reverse=True)


def _build_serendb_env_init_candidates(
    seren_bin: str,
    serendb_cfg: dict[str, Any],
) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(project_id: str, branch_id: str, source: str) -> None:
        project = str(project_id).strip()
        branch = str(branch_id).strip()
        if not project or not branch:
            return
        key = (project, branch)
        if key in seen:
            return
        seen.add(key)
        candidates.append((project, branch, source))

    resolved_project_id = _resolve_serendb_project_id(seren_bin, serendb_cfg)
    resolved_branch_id = _resolve_serendb_branch_id(
        seren_bin,
        serendb_cfg,
        resolved_project_id,
    )
    add(resolved_project_id, resolved_branch_id, "resolver")

    ranked_catalog = _rank_serendb_targets_from_database_catalog(seren_bin, serendb_cfg)
    if resolved_project_id and not resolved_branch_id:
        for row in ranked_catalog:
            if row.get("project_id", "") == resolved_project_id:
                add(
                    row.get("project_id", ""),
                    row.get("branch_id", ""),
                    "catalog_project_match",
                )
    if resolved_branch_id and not resolved_project_id:
        for row in ranked_catalog:
            if row.get("branch_id", "") == resolved_branch_id:
                add(
                    row.get("project_id", ""),
                    row.get("branch_id", ""),
                    "catalog_branch_match",
                )
    for row in ranked_catalog:
        label = (
            "catalog:"
            f"{row.get('project_name', '')}/"
            f"{row.get('branch_name', '')}/"
            f"{row.get('database_name', '')}"
        )
        add(row.get("project_id", ""), row.get("branch_id", ""), label)
    return candidates


def resolve_serendb_database_url(
    config: dict[str, Any],
    logger: RunLogger,
) -> tuple[str, str]:
    serendb_cfg = config.get("serendb", {})
    env_key = str(serendb_cfg.get("database_url_env", "WF_SERENDB_URL")).strip() or "WF_SERENDB_URL"

    from_env = os.getenv(env_key, "").strip()
    if from_env:
        return from_env, f"env:{env_key}"

    if not bool(serendb_cfg.get("auto_resolve_via_seren_cli", True)):
        raise RuntimeError(
            f"SerenDB is enabled but {env_key} is empty and auto-resolve is disabled."
        )

    seren_bin = shutil.which("seren")
    if not seren_bin:
        raise RuntimeError(
            f"SerenDB is enabled but {env_key} is empty and `seren` CLI was not found in PATH."
        )

    with tempfile.TemporaryDirectory(prefix="wf-serendb-env-") as temp_dir:
        env_path = Path(temp_dir) / ".env"
        base_cmd = [
            seren_bin,
            "env",
            "init",
            "--env",
            str(env_path),
            "--key",
            env_key,
            "--yes",
            "-o",
            "json",
        ]
        if bool(serendb_cfg.get("pooled_connection", True)):
            base_cmd.append("--pooled")

        candidates = _build_serendb_env_init_candidates(seren_bin, serendb_cfg)
        if not candidates:
            raise RuntimeError(
                "Failed to resolve SerenDB URL via logged-in Seren CLI context. "
                f"Could not infer a project/branch for {env_key}. "
                "Set `serendb.project_id` + `serendb.branch_id`, or provide WF_SERENDB_URL."
            )

        attempt_errors: list[str] = []
        for project_id, branch_id, source in candidates:
            cmd = [
                *base_cmd,
                "--project-id",
                project_id,
                "--branch-id",
                branch_id,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                resolved = _parse_dotenv_value(env_path, env_key).strip()
                if resolved:
                    os.environ[env_key] = resolved
                    logger.emit(
                        "serendb_url_resolved",
                        "Resolved SerenDB URL from Seren CLI context",
                        env_key=env_key,
                        source=f"seren_cli_context:{source}",
                        project_id=project_id,
                        branch_id=branch_id,
                    )
                    return resolved, f"seren_cli_context:{source}"
                attempt_errors.append(
                    f"{source} project={project_id} branch={branch_id}: empty dotenv write"
                )
                continue

            stderr = (result.stderr or "").strip()
            stdout = (result.stdout or "").strip()
            details = stderr or stdout or "unknown error"
            attempt_errors.append(
                f"{source} project={project_id} branch={branch_id}: {details}"
            )

        preview = "; ".join(attempt_errors[:5])
        if len(attempt_errors) > 5:
            preview += f"; ... ({len(attempt_errors) - 5} more)"
        raise RuntimeError(
            "Failed to resolve SerenDB URL via logged-in Seren CLI context. "
            f"Tried {len(candidates)} project/branch candidates for {env_key}. "
            f"Recent errors: {preview}"
        )


def prompt_credentials(auth_method: str) -> Credentials:
    method = str(auth_method).strip().lower()
    if method == "manual":
        return Credentials(username="", password="")

    username = input("Wells Fargo username: ").strip()
    if not username:
        raise ValueError("Username is required")

    if method == "password":
        password = getpass("Wells Fargo password: ")
        if not password:
            raise ValueError("Password is required for password auth")
    elif method == "passkey":
        # Passkey/WebAuthn does not require collecting the login password.
        password = ""
    else:
        raise ValueError(f"Unsupported auth method: {auth_method}")

    return Credentials(username=username, password=password)


def scan_local_pdfs(out_dir: Path) -> list[dict[str, Any]]:
    statements: list[dict[str, Any]] = []
    for pdf in sorted((out_dir / "pdfs").glob("*.pdf")):
        sha = sha256_file(pdf)
        statements.append(
            {
                "file_id": sha256_text(f"{sha}:{pdf.name}"),
                "local_file_path": str(pdf.resolve()),
                "account_masked": "****",
                "statement_period_start": None,
                "statement_period_end": None,
                "sha256": sha,
                "bytes": pdf.stat().st_size,
                "download_status": "local",
                "source_hint": pdf.name,
            }
        )
    return statements


def load_state_payload(state_dir: Path, run_id: str, key: str) -> dict[str, Any]:
    path = state_dir / f"{run_id}.{key}.json"
    return load_json(path, default={})


def save_state_payload(state_dir: Path, run_id: str, key: str, payload: dict[str, Any]) -> Path:
    path = state_dir / f"{run_id}.{key}.json"
    dump_json(path, payload)
    return path


def replay_serendb_sync(
    out_dir: Path,
    replay_run_id: str,
    config: dict[str, Any],
    logger: RunLogger,
) -> int:
    state_dir = ensure_dir(out_dir / "state")
    run_payload = load_state_payload(state_dir, replay_run_id, "run")
    download_payload = load_state_payload(state_dir, replay_run_id, "download")
    parsed_payload = load_state_payload(state_dir, replay_run_id, "parsed")
    categorized_payload = load_state_payload(state_dir, replay_run_id, "categorized")

    if not run_payload:
        raise FileNotFoundError(f"Run payload not found for replay run_id={replay_run_id}")

    db_url, db_source = resolve_serendb_database_url(config, logger)

    persist_run(
        database_url=db_url,
        schema_path=Path(config["serendb"]["schema_path"]),
        views_path=Path(config["serendb"]["views_path"]),
        run_record=run_payload,
        statement_files=download_payload.get("downloaded_statements", []),
        transactions=parsed_payload.get("transactions", []),
        categories=categorized_payload.get("categories", []),
    )
    logger.emit(
        "serendb_synced",
        "Replay sync complete",
        run_id=replay_run_id,
        db_source=db_source,
    )
    return 0


def focus_playwright_browser_window(preferred_app: str = "") -> str | None:
    app_candidates: list[str] = []
    preferred = preferred_app.strip()
    if preferred:
        app_candidates.append(preferred)

    app_candidates.extend(
        [
            "Firefox",
            "Firefox Developer Edition",
            "Firefox Nightly",
            "Google Chrome",
            "Brave Browser",
            "Microsoft Edge",
            "Chromium",
            "Google Chrome for Testing",
        ]
    )
    app_candidates = list(dict.fromkeys(app_candidates))
    for app_name in app_candidates:
        script = [
            "osascript",
            "-e",
            f'tell application "{app_name}"',
            "-e",
            "if running then",
            "-e",
            "activate",
            "-e",
            "return count of windows",
            "-e",
            "else",
            "-e",
            "return -1",
            "-e",
            "end if",
            "-e",
            "end tell",
        ]
        try:
            result = subprocess.run(script, capture_output=True, text=True, check=False)
            value = result.stdout.strip()
            if value.isdigit() and int(value) >= 1:
                return app_name
        except Exception:
            continue
    return None


def open_url_in_browser_app(url: str, preferred_app: str = "") -> str | None:
    target_url = str(url or "").strip()
    if not target_url:
        return None

    app_candidates: list[str] = []
    preferred = preferred_app.strip()
    if preferred:
        app_candidates.append(preferred)

    app_candidates.extend([app_name for app_name, _ in BROWSER_TARGETS])
    app_candidates = list(dict.fromkeys(app_candidates))

    for app_name in app_candidates:
        if not _app_bundle_exists(app_name):
            continue
        try:
            result = subprocess.run(
                ["open", "-a", app_name, target_url],
                capture_output=True,
                text=True,
                check=False,
            )
        except Exception:
            continue
        if result.returncode == 0:
            return app_name

    try:
        subprocess.run(
            ["open", target_url],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        pass
    return None


def main() -> int:
    args = parse_args()
    skill_root = SCRIPT_DIR.parent
    config = load_config(Path(args.config), skill_root)

    out_dir = ensure_dir(Path(args.out))
    state_dir = ensure_dir(out_dir / "state")
    log_dir = ensure_dir(out_dir / "logs")
    logger = RunLogger(log_path=log_dir / "run.log.jsonl")

    if args.mode != "read-only":
        raise RuntimeError("Only read-only mode is supported")
    if not bool(config["runtime"].get("strict_read_only", True)):
        raise RuntimeError("Config must keep runtime.strict_read_only=true")

    if args.replay_serendb:
        return replay_serendb_sync(
            out_dir=out_dir,
            replay_run_id=args.replay_serendb,
            config=config,
            logger=logger,
        )

    checkpoint = CheckpointStore(state_dir / "checkpoint.json")

    run_id = (
        checkpoint.state.get("run_id")
        if args.resume and checkpoint.state.get("run_id")
        else create_run_id()
    )
    acquire_run_lock(state_dir, run_id)
    checkpoint.start_run(run_id)

    run_payload_path = state_dir / f"{run_id}.run.json"
    run_payload = load_json(run_payload_path, default={})
    started_at = run_payload.get("started_at") or utc_now_iso()

    run_record: dict[str, Any] = {
        "run_id": run_id,
        "started_at": started_at,
        "ended_at": None,
        "status": "running",
        "mode": args.mode,
        "auth_method": None,
        "browser_app": None,
        "browser_type": None,
        "browser_family": None,
        "error_code": None,
        "selector_profile_version": None,
        "artifact_root": str(out_dir.resolve()),
    }
    save_state_payload(state_dir, run_id, "run", run_record)

    try:
        requested_months = max(1, int(args.months))
        effective_months = max(requested_months, MIN_STATEMENT_MONTHS)
        if effective_months != requested_months:
            logger.emit(
                "months_normalized",
                "Requested months below standard minimum; using minimum",
                requested_months=requested_months,
                effective_months=effective_months,
                minimum_months=MIN_STATEMENT_MONTHS,
            )

        logger.emit(
            "start",
            "Run started",
            run_id=run_id,
            mode=args.mode,
            requested_months=requested_months,
            months=effective_months,
        )

        statements_payload = load_state_payload(state_dir, run_id, "download")
        downloaded_statements: list[dict[str, Any]] = statements_payload.get(
            "downloaded_statements",
            [],
        )
        configured_auth_method = str(config["runtime"].get("auth_method", "password")).strip().lower()
        auth_method = str(args.auth_method).strip().lower() or configured_auth_method or "password"
        if auth_method not in {"password", "passkey", "manual"}:
            raise RuntimeError(f"Unsupported auth method: {auth_method}")
        run_record["auth_method"] = auth_method

        if not args.skip_download and not checkpoint.is_completed("pdf_downloaded"):
            if not checkpoint.is_completed("auth_prompted"):
                _ = prompt_credentials(auth_method)
                checkpoint.mark_complete(
                    "auth_prompted",
                    {"provided": True, "auth_method": auth_method, "at": utc_now_iso()},
                )
            else:
                _ = prompt_credentials(auth_method)

            credentials = _
            configured_cdp_url = str(config["playwright"].get("connect_cdp_url", "")).strip()
            cdp_url = str(args.cdp_url).strip() or configured_cdp_url
            if cdp_url:
                os.environ["PLAYWRIGHT_MCP_CONNECT_CDP_URL"] = cdp_url
                logger.emit(
                    "playwright_mode",
                    "Using CDP-attached browser session",
                    cdp_url=cdp_url,
                )
            elif "PLAYWRIGHT_MCP_CONNECT_CDP_URL" in os.environ:
                os.environ.pop("PLAYWRIGHT_MCP_CONNECT_CDP_URL", None)
                logger.emit(
                    "playwright_mode",
                    "Cleared inherited CDP URL; MCP will launch its own browser",
                )
            if auth_method == "manual" and not cdp_url:
                logger.emit(
                    "playwright_mode",
                    (
                        "Manual mode without CDP URL may use an automation browser. "
                        "Set --cdp-url to attach your real browser."
                    ),
                    warning=True,
                )
            arg_browser_app = str(args.browser_app).strip()
            arg_browser_type = str(args.browser_type).strip()
            preferred_browser_app = arg_browser_app or str(
                config["playwright"].get("browser_app", "")
            ).strip()
            browser_type = (
                arg_browser_type
                or str(config["playwright"].get("browser_type", "")).strip()
                or "moz-firefox"
            )
            if arg_browser_app and not arg_browser_type:
                inferred_type = _browser_type_for_app(arg_browser_app)
                if inferred_type:
                    browser_type = inferred_type
            if arg_browser_type and not arg_browser_app:
                inferred_app = _default_app_for_browser_type(arg_browser_type)
                if inferred_app:
                    preferred_browser_app = inferred_app
            should_prompt_browser = (
                auth_method == "manual"
                and sys.stdin.isatty()
                and not str(args.browser_app).strip()
                and not str(args.browser_type).strip()
            )
            if should_prompt_browser:
                installed_targets = detect_installed_browser_targets()
                if installed_targets:
                    preferred_browser_app, browser_type = choose_browser_target_interactive(
                        installed_targets=installed_targets,
                        preferred_app=preferred_browser_app,
                        preferred_type=browser_type,
                    )
                else:
                    logger.emit(
                        "browser_selection",
                        "No supported macOS browser apps were auto-detected; using configured defaults",
                        browser_app=preferred_browser_app or "auto",
                        browser_type=browser_type,
                    )
            browser_family = _normalize_browser_family(
                browser_type=browser_type,
                browser_app=preferred_browser_app,
            )
            os.environ["BROWSER_TYPE"] = browser_type
            os.environ["WF_BROWSER_FAMILY"] = browser_family
            run_record["browser_app"] = preferred_browser_app or "auto"
            run_record["browser_type"] = browser_type
            run_record["browser_family"] = browser_family
            logger.emit(
                "playwright_browser",
                "Using Playwright MCP browser type",
                browser_type=browser_type,
                browser_app=preferred_browser_app or "auto",
                browser_family=browser_family,
            )
            logger.emit(
                "browser_path_selected",
                "Selected browser-specific automation path",
                browser_family=browser_family,
                preserve_firefox_path=(browser_family == "firefox"),
            )

            def otp_provider() -> str:
                checkpoint.mark_complete("otp_waiting", {"required": True, "at": utc_now_iso()})
                return input("Enter Wells Fargo OTP code: ").strip()

            def passkey_approval_provider() -> None:
                checkpoint.mark_complete("passkey_waiting", {"required": True, "at": utc_now_iso()})
                print(
                    "Passkey step: bring browser to front, then approve Touch ID/passkey if prompted."
                )
                input(
                    "If no macOS prompt appears, click Wells Fargo 'Use a passkey' again in the browser, "
                    "then press Enter to continue..."
                )

            def manual_login_provider() -> None:
                checkpoint.mark_complete("manual_login_waiting", {"required": True, "at": utc_now_iso()})
                opened_app = None
                if cdp_url:
                    opened_app = open_url_in_browser_app(
                        "https://wellsfargo.com/",
                        preferred_browser_app,
                    )
                focused_app = focus_playwright_browser_window(preferred_browser_app)
                if cdp_url:
                    print(
                        "Manual login handoff: your real browser session is attached over CDP. "
                        "The run has already opened https://wellsfargo.com/ in that same window."
                    )
                else:
                    print(
                        "Manual login handoff: a controlled browser window is open. "
                        "It has already opened https://wellsfargo.com/."
                    )
                if opened_app:
                    print(f"Opened Wells Fargo in: {opened_app}")
                if focused_app:
                    print(f"Focused browser window: {focused_app}")
                input(
                    "Once login is complete, press Enter so the agent can continue. "
                    "It will auto-navigate Accounts -> Statements & Documents."
                )
                checkpoint.mark_complete(
                    "manual_login_confirmed",
                    {"confirmed": True, "at": utc_now_iso()},
                )

            selector_profile = load_selector_profile(Path(config["selectors_path"]))
            run_record["selector_profile_version"] = selector_profile.get(
                "profile_version",
                "unknown",
            )
            checkpoint.mark_complete("authenticated", {"status": "session_active"})

            mcp_script = resolve_playwright_mcp_script(config)
            if not mcp_script:
                raise RuntimeError(
                    "Playwright MCP script is missing. Set config.playwright.mcp_script "
                    f"or env var {config['playwright'].get('mcp_script_env', 'PLAYWRIGHT_MCP_SCRIPT')}."
                )
            if not Path(mcp_script).exists():
                raise RuntimeError(f"Playwright MCP script path not found: {mcp_script}")

            mcp_command = str(config["playwright"].get("mcp_command", "node")).strip() or "node"
            raw_args = config["playwright"].get("mcp_args", [])
            mcp_args = [str(value) for value in (raw_args if isinstance(raw_args, list) else [])]
            if not mcp_args or Path(mcp_args[0]).resolve() != Path(mcp_script).resolve():
                mcp_args = [mcp_script, *mcp_args]

            download_payload = login_and_download_statements(
                credentials=credentials,
                selector_profile=selector_profile,
                out_dir=out_dir,
                months=effective_months,
                headless=(
                    config["playwright"]["headless"]
                    if args.headless is None
                    else args.headless
                ),
                timeout_ms=int(config["playwright"].get("timeout_ms", 30000)),
                otp_provider=otp_provider,
                mcp_command=mcp_command,
                mcp_args=mcp_args,
                auth_method=auth_method,
                passkey_approval_provider=passkey_approval_provider,
                manual_login_provider=manual_login_provider,
                browser_family=browser_family,
                progress=lambda step, payload: logger.emit(step, "progress", **payload),
            )
            downloaded_statements = download_payload.get("downloaded_statements", [])
            save_state_payload(state_dir, run_id, "download", download_payload)
            checkpoint.mark_complete("statement_indexed", {"rows": len(downloaded_statements)})
            checkpoint.mark_complete("pdf_downloaded", {"count": len(downloaded_statements)})

        if args.skip_download:
            if downloaded_statements:
                logger.emit(
                    "pdf_downloaded",
                    "Using previously downloaded statements",
                    count=len(downloaded_statements),
                )
            else:
                downloaded_statements = scan_local_pdfs(out_dir)
                save_state_payload(
                    state_dir,
                    run_id,
                    "download",
                    {
                        "selector_profile_version": config["runtime"].get(
                            "selector_profile_version",
                            "unknown",
                        ),
                        "downloaded_statements": downloaded_statements,
                        "downloaded_at": utc_now_iso(),
                    },
                )
                logger.emit(
                    "pdf_downloaded",
                    "Scanned local PDFs",
                    count=len(downloaded_statements),
                )

        parsed_payload = load_state_payload(state_dir, run_id, "parsed")
        transactions = parsed_payload.get("transactions", [])
        parse_errors = parsed_payload.get("parse_errors", [])

        if not checkpoint.is_completed("pdf_parsed"):
            transactions = []
            parse_errors = []
            for statement in downloaded_statements:
                pdf_path = Path(statement["local_file_path"])
                result = parse_statement_pdf(pdf_path=pdf_path, file_id=statement["file_id"])
                if result.metadata.get("account_masked") not in (None, "****"):
                    statement["account_masked"] = mask_account(
                        result.metadata.get("account_masked")
                    )
                for row in result.transactions:
                    row["account_masked"] = statement.get(
                        "account_masked",
                        row.get("account_masked", "****"),
                    )
                    if not row.get("statement_period_start"):
                        row["statement_period_start"] = statement.get("statement_period_start")
                    if not row.get("statement_period_end"):
                        row["statement_period_end"] = statement.get("statement_period_end")
                    transactions.append(row)
                parse_errors.extend([f"{pdf_path.name}:{err}" for err in result.parse_errors])

            parsed_payload = {
                "transactions": transactions,
                "parse_errors": parse_errors,
                "parsed_at": utc_now_iso(),
            }
            save_state_payload(state_dir, run_id, "parsed", parsed_payload)
            checkpoint.mark_complete(
                "pdf_parsed",
                {
                    "transactions": len(transactions),
                    "parse_errors": len(parse_errors),
                },
            )
            logger.emit(
                "pdf_parsed",
                "Parsed statement PDFs",
                transactions=len(transactions),
                parse_errors=len(parse_errors),
            )

        if args.strict_parse and parse_errors:
            raise RuntimeError(
                f"Strict parse enabled; encountered parse errors: {len(parse_errors)}"
            )

        categorized_payload = load_state_payload(state_dir, run_id, "categorized")
        categories = categorized_payload.get("categories", [])
        if not checkpoint.is_completed("classified"):
            categories = categorize_transactions(
                transactions=transactions,
                llm_mode=config["categorization"].get("llm_mode", "heuristic"),
                llm_endpoint=(
                    config["categorization"].get("llm_endpoint")
                    or os.getenv("WF_LLM_ENDPOINT", "")
                ).strip(),
                llm_api_key=os.getenv("WF_LLM_API_KEY"),
            )
            categorized_payload = {
                "categories": categories,
                "taxonomy_version": config["categorization"].get("taxonomy_version", "v1"),
                "classified_at": utc_now_iso(),
            }
            save_state_payload(state_dir, run_id, "categorized", categorized_payload)
            checkpoint.mark_complete("classified", {"count": len(categories)})
            logger.emit("classified", "Categorization complete", categorized=len(categories))

        if not args.skip_serendb and bool(config["serendb"].get("enabled", True)):
            db_url, db_source = resolve_serendb_database_url(config, logger)
            persist_run(
                database_url=db_url,
                schema_path=Path(config["serendb"]["schema_path"]),
                views_path=Path(config["serendb"]["views_path"]),
                run_record=run_record,
                statement_files=downloaded_statements,
                transactions=transactions,
                categories=categories,
            )
            checkpoint.mark_complete("serendb_synced", {"status": "ok"})
            logger.emit(
                "serendb_synced",
                "Persisted masked records to SerenDB",
                run_id=run_id,
                db_source=db_source,
            )
        else:
            logger.emit("serendb_synced", "SerenDB sync skipped", skip=True)

        run_record["status"] = "success"
        run_record["ended_at"] = utc_now_iso()
        save_state_payload(state_dir, run_id, "run", run_record)

        report_paths = write_report(
            out_dir=out_dir,
            run_record=run_record,
            statement_files=downloaded_statements,
            transactions=transactions,
            categories=categories,
        )
        checkpoint.mark_complete("complete", {"report_json": report_paths["report_json"]})
        checkpoint.set_status("success")
        logger.emit(
            "complete",
            "Run completed successfully",
            report_json=report_paths["report_json"],
            report_md=report_paths["report_md"],
            transactions_jsonl=report_paths["transactions_jsonl"],
        )
        return 0

    except (SelectorError, AuthError) as exc:
        run_record["status"] = "failed"
        run_record["error_code"] = getattr(exc, "error_code", "workflow_error")
        run_record["ended_at"] = utc_now_iso()
        save_state_payload(state_dir, run_id, "run", run_record)
        checkpoint.set_status("failed", run_record["error_code"])
        logger.emit("error", str(exc), error_code=run_record["error_code"])
        return 2
    except WorkflowError as exc:
        run_record["status"] = "failed"
        run_record["error_code"] = getattr(exc, "error_code", "workflow_error")
        run_record["ended_at"] = utc_now_iso()
        save_state_payload(state_dir, run_id, "run", run_record)
        checkpoint.set_status("failed", run_record["error_code"])
        logger.emit("error", str(exc), error_code=run_record["error_code"])
        return 3
    except Exception as exc:
        run_record["status"] = "failed"
        run_record["error_code"] = "unhandled_error"
        run_record["ended_at"] = utc_now_iso()
        save_state_payload(state_dir, run_id, "run", run_record)
        checkpoint.set_status("failed", run_record["error_code"])
        logger.emit("error", str(exc), error_code=run_record["error_code"])
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
