#!/usr/bin/env python3
"""Validate execution-safety guardrails for trading skills."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
WAIVER_PATH = ROOT / ".github" / "trading-skill-safety-waivers.json"

TEXT_SUFFIXES = {
    ".env",
    ".example",
    ".json",
    ".js",
    ".md",
    ".py",
    ".sh",
    ".spec",
    ".text",
    ".toml",
    ".ts",
    ".txt",
    ".yaml",
    ".yml",
}
SKIP_PARTS = {
    ".git",
    ".pytest_cache",
    ".test-venv",
    ".venv",
    "node_modules",
    "output",
    "state",
    "__pycache__",
}
RUNTIME_ROOT_FILENAMES = {
    ".env.example",
    "config.example.json",
    "package.json",
    "requirements.txt",
    "skill.spec.yaml",
}

HIGH_CONFIDENCE_TRADING_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bgrid trad(?:e|ing)\b",
        r"\bmarket mak(?:e|ing|er)\b",
        r"\bmaker rebate\b",
        r"\bpaired (?:market )?basis\b",
        r"\bshort trader\b",
        r"\bdollar-?cost averaging\b",
        r"\bdca bot\b",
        r"\byield trader\b",
        r"\bliquidity trade\b",
        r"\bprediction market\b",
        r"\btrade spot markets\b",
        r"\bperpetual futures?\b",
        r"\blive trading\b",
    )
]
AMBIGUOUS_STRATEGY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bvault bot\b",
        r"\brebalance\b",
        r"\bstaking\b",
        r"\blending\b",
        r"\bportfolio management\b",
        r"\bblock trades?\b",
    )
]
SUPPORTING_MODE_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"--yes-live",
        r"--allow-live",
        r"--accept-risk-disclaimer",
        r"\blive_mode\b",
        r"\bdry_run\b",
        r"\bpaper-sim\b",
        r"\bpaper\b",
        r"\bbacktest\b",
        r"\bquote\b",
        r"\bmonitor\b",
        r"\blive execution\b",
        r"\breal broker execution\b",
    )
]
SUPPORTING_EXECUTION_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bplace(?:_[a-z]+)?_?orders?\b",
        r"\bsubmit(?:ted)? orders?\b",
        r"\bcancel_all\b",
        r"\bopen_orders\b",
        r"\bpositions?\b",
        r"\bposition marks?\b",
        r"\bfills?\b",
        r"\btrading_pair\b",
        r"\bwallet_mode\b",
        r"\beth_sendRawTransaction\b",
        r"\bDirectClobTrader\b",
        r"\bmaker_rebate\b",
        r"\bquote_notional\b",
        r"\bsell_only\b",
        r"\bunwind\b",
        r"\bflatten\b",
        r"\brebalance\b",
        r"\bdeposit\b",
        r"\bwithdraw\b",
        r"\bbridge assets\b",
        r"\bperpetual futures?\b",
        r"\bgrid\b",
        r"\bdca\b",
    )
]
ORDERBOOK_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bclob\b",
        r"\border[- ]book\b",
        r"\borderbook\b",
        r"\btick_size\b",
        r"\bbest bid\b",
        r"\bbest ask\b",
        r"\bpy-clob-client\b",
        r"\bvisible bid depth\b",
        r"\bmarketable\b",
    )
]
INVENTORY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\binventory\b",
        r"\bposition marks?\b",
        r"\bpositions?\b",
        r"\bfills?\b",
        r"\bbalances?\b",
        r"\bopen orders\b",
        r"\bcancel all orders\b",
        r"\bhold(?:ing|ed)? inventory\b",
        r"\bpnl\b",
    )
]

TEST_PATTERN_GROUPS = {
    "live_confirmation": (
        r"--yes-live",
        r"--allow-live",
        r"--accept-risk-disclaimer",
        r"live_confirmation_required",
        r"allow_live",
        r"yes_live",
    ),
    "dependency_fail_closed": (
        r"\bmissing\b",
        r"\brequired\b",
        r"\bunsupported\b",
        r"\bblocked\b",
        r"PublisherError",
        r"RuntimeError",
        r"ValueError",
    ),
    "emergency_exit": (
        r"\bunwind\b",
        r"\bflatten\b",
        r"\bcancel_all\b",
        r"\bclose-all\b",
        r"\bstop trading\b",
        r"\bclose all\b",
    ),
    "marketability_exit": (
        r"\btick_size\b",
        r"\bbest_bid\b",
        r"\bestimated_exit_value\b",
        r"\bestimated_fill_size\b",
        r"\bmarketable\b",
    ),
}

RULE_REMEDIATIONS = {
    "skill_trade_execution_contract": "Add a `Trade Execution Contract` section to `SKILL.md` that explains how direct exit instructions (`sell`, `close`, `exit`, `unwind`, `flatten`) are handled immediately or clarified minimally.",
    "skill_pre_trade_checklist": "Add a `Pre-Trade Checklist` section to `SKILL.md` that lists the live book/snapshot fetch, price snapping, dependency verification, reachability checks, and fail-closed behavior.",
    "skill_dependency_validation": "Document dependency validation in `SKILL.md`, including the exact required libraries, credentials, publishers, and the fail-closed remediation when they are missing.",
    "skill_live_safety_opt_in": "Document the exact live opt-in contract in `SKILL.md`, including the config flag and the explicit CLI or operator confirmation required for live execution.",
    "skill_emergency_exit_path": "Document an emergency/operator exit path in `SKILL.md`, such as `--unwind-all`, `stop`, `flatten`, `close-all`, or an explicit reason the skill never holds inventory.",
    "skill_clob_orderbook_rules": "For CLOB or order-book execution skills, add explicit order-book rules to `SKILL.md` covering marketable exits, `tick_size` handling, no passive immediate exits, and visible-depth recovery estimates.",
    "runtime_live_confirmation": "Add an explicit live confirmation flag in runtime code such as `--yes-live` or `--allow-live`. Config-only live gating is not enough.",
    "runtime_default_safe_mode": "Make the runtime default to a safe mode (`dry_run`, `paper`, `paper-sim`, `backtest`, `quote`, or `monitor`) and surface that default in the config or CLI parser.",
    "runtime_dependency_fail_closed": "Add fail-closed runtime checks for missing credentials, unsupported publishers/RPCs, blocked reachability, or missing client libraries with actionable error text.",
    "runtime_scheduler_safety": "Ensure schedulers and trigger servers preserve the same live confirmation semantics and fail closed when prerequisites are missing.",
    "runtime_emergency_exit_support": "Add a runtime unwind, flatten, close-all, or stop path for skills that hold inventory, or document in `SKILL.md` why the skill cannot hold positions.",
    "runtime_marketable_exit": "Implement marketability-aware exit pricing for order-book-based live execution. Immediate exits must use current `tick_size`, full displayed depth, and non-passive prices.",
    "tests_guardrail_coverage": "Add focused regression tests that cover the live confirmation gate, dependency failure behavior, and any emergency-exit or marketability-aware exit path used by the skill.",
}


@dataclass
class Violation:
    rule: str
    message: str
    remediation: str


@dataclass
class SkillResult:
    skill: str
    is_trading: bool
    reasons: list[str] = field(default_factory=list)
    has_runtime: bool = False
    holds_inventory: bool = False
    orderbook_applicable: bool = False
    runtime_changed: bool = False
    violations: list[Violation] = field(default_factory=list)
    waived_rules: list[str] = field(default_factory=list)


@dataclass
class SkillContext:
    skill_dir: Path
    skill_key: str
    skill_name: str
    docs_text: str
    all_text: str
    runtime_text: str
    scheduler_text: str
    config_text: str
    runtime_files: list[Path]
    scheduler_files: list[Path]
    related_tests: dict[Path, str]
    changed_files: list[Path]

    @property
    def has_runtime(self) -> bool:
        return bool(self.runtime_files)

    @property
    def runtime_changed(self) -> bool:
        return any(
            path.name in RUNTIME_ROOT_FILENAMES or "scripts" in path.parts
            for path in self.changed_files
        )

    @property
    def has_scheduler(self) -> bool:
        return bool(self.scheduler_files)


def is_text_file(path: Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    if path.name.endswith(".example.json"):
        return True
    if path.name.endswith(".test.js"):
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def is_test_file(path: Path) -> bool:
    if any(part in SKIP_PARTS for part in path.parts):
        return False
    return (
        "tests" in path.parts
        or path.name.startswith("test_")
        or path.name.endswith("_test.py")
        or path.name.endswith(".test.js")
        or path.name.endswith(".spec.ts")
    )


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def discover_skill_dirs() -> list[Path]:
    return sorted(path.parent for path in ROOT.glob("*/*/SKILL.md"))


def skill_dir_from_path(path_str: str) -> Path | None:
    path = Path(path_str.strip())
    if len(path.parts) < 2:
        return None
    candidate = ROOT / path.parts[0] / path.parts[1]
    if (candidate / "SKILL.md").exists():
        return candidate
    return None


def changed_skill_dirs(base_ref: str) -> dict[Path, list[Path]]:
    stdout = run_git("diff", "--name-only", "--diff-filter=ACMRTUXB", f"{base_ref}...HEAD")
    changed: dict[Path, list[Path]] = {}
    for raw in stdout.splitlines():
        skill_dir = skill_dir_from_path(raw)
        if skill_dir is None:
            continue
        rel_path = Path(raw)
        changed.setdefault(skill_dir, []).append(rel_path)
    return changed


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def collect_runtime_files(skill_dir: Path) -> tuple[list[Path], list[Path]]:
    runtime_files: list[Path] = []
    scheduler_files: list[Path] = []
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or not is_text_file(path) or is_test_file(path):
            continue
        rel = path.relative_to(skill_dir)
        include = "scripts" in rel.parts or rel.name in RUNTIME_ROOT_FILENAMES
        if not include:
            continue
        runtime_files.append(path)
        lower_name = path.name.lower()
        if any(token in lower_name for token in ("cron", "scheduler", "run_agent_server", "runner")):
            scheduler_files.append(path)
    return runtime_files, scheduler_files


def collect_repo_tests() -> dict[Path, str]:
    tests: dict[Path, str] = {}
    for path in sorted(ROOT.rglob("*")):
        if path.is_file() and is_text_file(path) and is_test_file(path):
            tests[path] = read_text(path)
    return tests


def collect_related_tests(skill_dir: Path, repo_tests: dict[Path, str]) -> dict[Path, str]:
    try:
        skill_key = skill_dir.relative_to(ROOT).as_posix()
    except ValueError:
        if len(skill_dir.parts) >= 2:
            skill_key = "/".join(skill_dir.parts[-2:])
        else:
            skill_key = skill_dir.as_posix()
    org_name = skill_dir.parent.name
    basename = skill_dir.name
    slug = skill_key.replace("/", "-")
    related: dict[Path, str] = {}
    for path, text in repo_tests.items():
        haystack = f"{path.as_posix()}\n{text}"
        if skill_key in haystack or basename in haystack or slug in haystack:
            related[path] = text
        elif path.is_relative_to(skill_dir):
            related[path] = text
        elif path.parent.name == "tests" and path.parent.parent.name == org_name:
            related[path] = text
    return related


def build_context(skill_dir: Path, changed_files: Iterable[Path], repo_tests: dict[Path, str]) -> SkillContext:
    try:
        skill_key = skill_dir.relative_to(ROOT).as_posix()
    except ValueError:
        if len(skill_dir.parts) >= 2:
            skill_key = "/".join(skill_dir.parts[-2:])
        else:
            skill_key = skill_dir.as_posix()
    docs_text = read_text(skill_dir / "SKILL.md")
    runtime_files, scheduler_files = collect_runtime_files(skill_dir)
    runtime_text = "\n".join(read_text(path) for path in runtime_files)
    config_text = "\n".join(
        read_text(path)
        for path in runtime_files
        if path.name in {".env.example", "config.example.json", "package.json", "requirements.txt", "skill.spec.yaml"}
    )
    all_text_parts = [docs_text, runtime_text]
    for extra_name in ("README.md", "skill.spec.yaml"):
        extra = skill_dir / extra_name
        if extra.exists():
            all_text_parts.append(read_text(extra))
    all_text = "\n".join(part for part in all_text_parts if part)
    return SkillContext(
        skill_dir=skill_dir,
        skill_key=skill_key,
        skill_name=skill_dir.name,
        docs_text=docs_text,
        all_text=all_text,
        runtime_text=runtime_text,
        scheduler_text="\n".join(read_text(path) for path in scheduler_files),
        config_text=config_text,
        runtime_files=runtime_files,
        scheduler_files=scheduler_files,
        related_tests=collect_related_tests(skill_dir, repo_tests),
        changed_files=sorted(changed_files),
    )


def count_matches(patterns: Iterable[re.Pattern[str]], text: str) -> int:
    return sum(1 for pattern in patterns if pattern.search(text))


def detect_trading(context: SkillContext, overrides: dict[str, dict[str, object]]) -> tuple[bool, list[str]]:
    override = overrides.get(context.skill_key)
    if override is not None:
        trading = bool(override.get("trading"))
        reason = str(override.get("reason", "")).strip() or "manual classification override"
        return trading, [reason]

    docs = context.docs_text
    all_text = context.all_text
    runtime_surface = "\n".join((context.runtime_text, context.config_text))

    reasons: list[str] = []
    if count_matches(HIGH_CONFIDENCE_TRADING_PATTERNS, all_text):
        reasons.append("high-confidence trading terms found")
    mode_hits = count_matches(SUPPORTING_MODE_PATTERNS, runtime_surface)
    execution_hits = count_matches(SUPPORTING_EXECUTION_PATTERNS, runtime_surface)
    ambiguous_hits = count_matches(AMBIGUOUS_STRATEGY_PATTERNS, all_text)
    if ambiguous_hits and mode_hits >= 1 and execution_hits >= 1:
        reasons.append("ambiguous trading domain terms are paired with execution controls")
    if mode_hits >= 2 and execution_hits >= 1:
        reasons.append("runtime exposes live/dry-run modes plus execution primitives")
    if re.search(r"\b(executes|submitted|place|cancel)\b.{0,30}\b(order|trade|position)", all_text, re.IGNORECASE | re.DOTALL):
        reasons.append("text describes live order or trade execution")
    if re.search(r"\b(real broker execution|trade live|live trading|market make|grid trader|smart dca)\b", docs, re.IGNORECASE):
        reasons.append("skill summary describes live trading behavior")
    return bool(reasons), reasons


def holds_inventory(context: SkillContext) -> bool:
    return count_matches(INVENTORY_PATTERNS, context.all_text) >= 2


def orderbook_applicable(context: SkillContext) -> bool:
    return count_matches(ORDERBOOK_PATTERNS, context.all_text) >= 2


def has_live_opt_in_docs(text: str) -> bool:
    return bool(
        re.search(r"(--yes-live|--allow-live|--accept-risk-disclaimer)", text, re.IGNORECASE)
        and re.search(r"(requires both|explicit|opt-?in|approval|required)", text, re.IGNORECASE)
    )


def has_trade_execution_contract_docs(text: str) -> bool:
    exit_terms = len(re.findall(r"\b(sell|close|exit|unwind|flatten)\b", text, re.IGNORECASE))
    return exit_terms >= 3 and bool(
        re.search(
            r"(execute .* immediately|immediate(?:ly)?|minimum clarifying question|clarify minimally|ask only the minimum clarifying question)",
            text,
            re.IGNORECASE,
        )
    )


def has_pre_trade_checklist_docs(text: str) -> bool:
    return bool(
        re.search(r"pre[- ]trade checklist|preflight checklist", text, re.IGNORECASE)
        and re.search(r"(fetch|verify|snap|fail closed|fail-closed|required)", text, re.IGNORECASE)
    )


def has_dependency_validation_docs(text: str) -> bool:
    return bool(
        re.search(r"(dependency validation|verify .* (installed|loaded)|missing .* credentials|required .* credentials|required .* libraries)", text, re.IGNORECASE)
        and re.search(r"(SEREN_API_KEY|POLY_API_KEY|WALLET_PRIVATE_KEY|py-clob-client|rpc|publisher|credential|library)", text, re.IGNORECASE)
        and re.search(r"(required|missing|stop|fail|remediation|set )", text, re.IGNORECASE)
    )


def has_emergency_exit_docs(text: str) -> bool:
    return bool(
        re.search(r"(emergency exit|operator exit|--unwind-all|close-all|flatten|close all|stop trading)", text, re.IGNORECASE)
        and re.search(r"(cancel all orders|market-sell|liquidate|unwind|stop)", text, re.IGNORECASE)
    )


def has_clob_rules_docs(text: str) -> bool:
    required_hits = [
        re.search(r"(tick_size|tick size)", text, re.IGNORECASE),
        re.search(r"(best bid|marketable)", text, re.IGNORECASE),
        re.search(r"(passive sell|no passive)", text, re.IGNORECASE),
        re.search(r"(full book|all levels|visible bid depth|sweeping visible bid levels)", text, re.IGNORECASE),
    ]
    return sum(1 for hit in required_hits if hit) >= 3


def has_runtime_live_confirmation(text: str) -> bool:
    return bool(re.search(r"(--yes-live|--allow-live|--accept-risk-disclaimer)", text, re.IGNORECASE))


def has_default_safe_mode(context: SkillContext) -> bool:
    combined = "\n".join((context.runtime_text, context.config_text, context.docs_text))
    return bool(
        re.search(r'"dry_run"\s*:\s*true', combined, re.IGNORECASE)
        or re.search(r'"live_mode"\s*:\s*false', combined, re.IGNORECASE)
        or re.search(r"\b(default mode is dry-run|paper-sim|paper only|backtest \(default\)|quote \(default\)|monitor)", combined, re.IGNORECASE)
    )


def has_fail_closed_dependency_checks(text: str) -> bool:
    return bool(
        re.search(r"(raise|error_code|RuntimeError|ValueError|SystemExit|PublisherError)", text, re.IGNORECASE)
        and re.search(r"(required|missing|unsupported|blocked|not installed|unable to fetch|must set|could not be reached|strict_required_feeds)", text, re.IGNORECASE)
    )


def has_scheduler_safety(context: SkillContext) -> bool:
    if not context.has_scheduler:
        return True
    text = "\n".join((context.scheduler_text, context.docs_text))
    return bool(
        re.search(r"(--yes-live|--allow-live|live_mode|dry_run)", text, re.IGNORECASE)
        and re.search(r"(required|insufficient funds|pause|missing|stop here|do not create a duplicate)", text, re.IGNORECASE)
    )


def has_runtime_emergency_exit(text: str) -> bool:
    return bool(
        re.search(r"(unwind|flatten|close-all|close all|cancel_all|cancel all orders|stop trading)", text, re.IGNORECASE)
        and re.search(r"(position|inventory|orders|market-sell|liquidat)", text, re.IGNORECASE)
    )


def has_marketable_exit_runtime(text: str) -> bool:
    required_hits = [
        re.search(r"(tick_size|tick size)", text, re.IGNORECASE),
        re.search(r"(best_bid|best bid|marketable)", text, re.IGNORECASE),
        re.search(r"(estimated_exit_value|estimated_fill_size|visible bid depth|all levels|sweep)", text, re.IGNORECASE),
    ]
    return sum(1 for hit in required_hits if hit) >= 3


def has_test_patterns(texts: Iterable[str], patterns: Iterable[str]) -> bool:
    combined = "\n".join(texts)
    return any(re.search(pattern, combined, re.IGNORECASE) for pattern in patterns)


def load_waivers(path: Path) -> tuple[dict[str, dict[str, object]], dict[str, dict[str, object]]]:
    if not path.exists():
        return {}, {}
    data = json.loads(read_text(path) or "{}")
    classification = data.get("classification_overrides", {})
    waivers = data.get("waivers", {})
    if not isinstance(classification, dict) or not isinstance(waivers, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain object fields `classification_overrides` and `waivers`.")
    for skill_key, override in classification.items():
        if not isinstance(override, dict):
            raise ValueError(f"classification_overrides[{skill_key!r}] must be an object.")
        if "trading" not in override or not isinstance(override["trading"], bool):
            raise ValueError(f"classification_overrides[{skill_key!r}] must include boolean field `trading`.")
        if not str(override.get("reason", "")).strip():
            raise ValueError(f"classification_overrides[{skill_key!r}] must include a non-empty `reason`.")
    for skill_key, waiver in waivers.items():
        if not isinstance(waiver, dict):
            raise ValueError(f"waivers[{skill_key!r}] must be an object.")
        rules = waiver.get("rules")
        if not isinstance(rules, list) or not rules or not all(isinstance(rule, str) for rule in rules):
            raise ValueError(f"waivers[{skill_key!r}] must include non-empty string list `rules`.")
        if not str(waiver.get("reason", "")).strip():
            raise ValueError(f"waivers[{skill_key!r}] must include a non-empty `reason`.")
    return classification, waivers


def is_rule_waived(rule: str, skill_key: str, waivers: dict[str, dict[str, object]]) -> bool:
    waiver = waivers.get(skill_key)
    if waiver is None:
        return False
    rules = set(str(value) for value in waiver.get("rules", []))
    return "*" in rules or rule in rules


def add_violation(result: SkillResult, rule: str, message: str, waivers: dict[str, dict[str, object]]) -> None:
    if is_rule_waived(rule, result.skill, waivers):
        result.waived_rules.append(rule)
        return
    result.violations.append(
        Violation(rule=rule, message=message, remediation=RULE_REMEDIATIONS[rule])
    )


def validate_context(
    context: SkillContext,
    classification_overrides: dict[str, dict[str, object]],
    waivers: dict[str, dict[str, object]],
    enforce_tests: bool,
) -> SkillResult:
    is_trading, reasons = detect_trading(context, classification_overrides)
    result = SkillResult(
        skill=context.skill_key,
        is_trading=is_trading,
        reasons=reasons,
        has_runtime=context.has_runtime,
        holds_inventory=holds_inventory(context),
        orderbook_applicable=orderbook_applicable(context),
        runtime_changed=context.runtime_changed,
    )
    if not is_trading:
        return result

    if not has_trade_execution_contract_docs(context.docs_text):
        add_violation(
            result,
            "skill_trade_execution_contract",
            "Missing direct-exit operator contract in `SKILL.md`.",
            waivers,
        )
    if not has_pre_trade_checklist_docs(context.docs_text):
        add_violation(
            result,
            "skill_pre_trade_checklist",
            "Missing `Pre-Trade Checklist` coverage in `SKILL.md`.",
            waivers,
        )
    if not has_dependency_validation_docs(context.docs_text):
        add_violation(
            result,
            "skill_dependency_validation",
            "Missing dependency validation and fail-closed remediation text in `SKILL.md`.",
            waivers,
        )
    if not has_live_opt_in_docs(context.docs_text):
        add_violation(
            result,
            "skill_live_safety_opt_in",
            "Missing explicit live opt-in contract in `SKILL.md`.",
            waivers,
        )
    if result.holds_inventory and not has_emergency_exit_docs(context.docs_text):
        add_violation(
            result,
            "skill_emergency_exit_path",
            "Missing emergency or operator exit path in `SKILL.md`.",
            waivers,
        )
    if result.orderbook_applicable and not has_clob_rules_docs(context.docs_text):
        add_violation(
            result,
            "skill_clob_orderbook_rules",
            "Missing CLOB or order-book exit rules in `SKILL.md`.",
            waivers,
        )

    if context.has_runtime:
        if not has_runtime_live_confirmation(context.runtime_text):
            add_violation(
                result,
                "runtime_live_confirmation",
                "Runtime lacks an explicit live confirmation flag beyond config-only gating.",
                waivers,
            )
        if not has_default_safe_mode(context):
            add_violation(
                result,
                "runtime_default_safe_mode",
                "Runtime does not clearly default to a safe non-live mode.",
                waivers,
            )
        if not has_fail_closed_dependency_checks(context.runtime_text):
            add_violation(
                result,
                "runtime_dependency_fail_closed",
                "Runtime is missing fail-closed dependency or credential checks with actionable errors.",
                waivers,
            )
        if context.has_scheduler and not has_scheduler_safety(context):
            add_violation(
                result,
                "runtime_scheduler_safety",
                "Scheduler or trigger runtime does not clearly preserve live gating and fail-closed behavior.",
                waivers,
            )
        if result.holds_inventory and not has_runtime_emergency_exit(context.runtime_text):
            add_violation(
                result,
                "runtime_emergency_exit_support",
                "Runtime lacks an unwind, flatten, close-all, or stop path for held positions.",
                waivers,
            )
        if result.orderbook_applicable and not has_marketable_exit_runtime(context.runtime_text):
            add_violation(
                result,
                "runtime_marketable_exit",
                "Runtime lacks marketability-aware exit logic for order-book-based execution.",
                waivers,
            )

        if enforce_tests:
            test_texts = list(context.related_tests.values())
            missing_areas: list[str] = []
            if not test_texts:
                missing_areas.append("no related tests found")
            else:
                if not has_test_patterns(test_texts, TEST_PATTERN_GROUPS["live_confirmation"]):
                    missing_areas.append("live confirmation")
                if not has_test_patterns(test_texts, TEST_PATTERN_GROUPS["dependency_fail_closed"]):
                    missing_areas.append("dependency failures")
                if result.holds_inventory and not has_test_patterns(test_texts, TEST_PATTERN_GROUPS["emergency_exit"]):
                    missing_areas.append("emergency exit")
                if result.orderbook_applicable and not has_test_patterns(test_texts, TEST_PATTERN_GROUPS["marketability_exit"]):
                    missing_areas.append("marketability-aware exits")
            if missing_areas:
                add_violation(
                    result,
                    "tests_guardrail_coverage",
                    "Focused regression coverage is missing for: " + ", ".join(missing_areas) + ".",
                    waivers,
                )

    return result


def format_results_text(results: list[SkillResult]) -> str:
    trading = [result for result in results if result.is_trading]
    lines = [
        f"Scanned {len(results)} skill(s); detected {len(trading)} trading skill(s).",
    ]
    failures = [result for result in trading if result.violations]
    if not failures:
        lines.append("No trading-skill safety violations found.")
        return "\n".join(lines)

    lines.append("Trading-skill safety violations:")
    for result in failures:
        reason_text = "; ".join(result.reasons) if result.reasons else "detected as trading"
        lines.append(f"- {result.skill} ({reason_text})")
        for violation in result.violations:
            lines.append(f"  - [{violation.rule}] {violation.message}")
            lines.append(f"    Remediation: {violation.remediation}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true", help="Scan every skill in the repository.")
    scope.add_argument("--base-ref", help="Validate only skills changed relative to this git ref.")
    parser.add_argument(
        "--skill",
        action="append",
        default=[],
        help="Explicit skill directory to validate, for example `kraken/grid-trader`.",
    )
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        classification_overrides, waivers = load_waivers(WAIVER_PATH)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    repo_tests = collect_repo_tests()
    explicit_skills = [ROOT / skill for skill in args.skill]
    skill_dirs: list[Path]
    changed_map: dict[Path, list[Path]] = {}

    if explicit_skills:
        skill_dirs = []
        for skill_dir in explicit_skills:
            if not (skill_dir / "SKILL.md").exists():
                print(f"Unknown skill directory: {skill_dir.relative_to(ROOT)}", file=sys.stderr)
                return 2
            skill_dirs.append(skill_dir)
            changed_map[skill_dir] = [Path(skill_dir.relative_to(ROOT).as_posix())]
    elif args.all:
        skill_dirs = discover_skill_dirs()
        changed_map = {skill_dir: [] for skill_dir in skill_dirs}
    elif args.base_ref:
        changed_map = changed_skill_dirs(args.base_ref)
        skill_dirs = sorted(changed_map)
    else:
        print("Choose `--all`, `--base-ref`, or one or more `--skill` values.", file=sys.stderr)
        return 2

    if not skill_dirs:
        payload = {
            "summary": {"scanned": 0, "trading": 0, "violations": 0},
            "skills": [],
        }
        if args.format == "json":
            print(json.dumps(payload, indent=2))
        else:
            print("No changed skills to validate.")
        return 0

    results: list[SkillResult] = []
    enforce_tests = args.all
    for skill_dir in skill_dirs:
        context = build_context(skill_dir, changed_map.get(skill_dir, []), repo_tests)
        results.append(
            validate_context(
                context=context,
                classification_overrides=classification_overrides,
                waivers=waivers,
                enforce_tests=enforce_tests or context.runtime_changed,
            )
        )

    failure_count = sum(len(result.violations) for result in results if result.is_trading)
    summary = {
        "scanned": len(results),
        "trading": sum(1 for result in results if result.is_trading),
        "violations": failure_count,
    }
    if args.format == "json":
        print(
            json.dumps(
                {
                    "summary": summary,
                    "skills": [
                        {
                            **asdict(result),
                            "violations": [asdict(item) for item in result.violations],
                        }
                        for result in results
                    ],
                },
                indent=2,
            )
        )
    else:
        print(format_results_text(results))

    return 1 if failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
