"""First-run bootstrap for pk-lead-intelligence (issue #853).

Owns three jobs and only these three:

1. Stage `config.example.json` and `.env.example` into the stable
   user-config dir on first run, without ever clobbering files an
   operator has already touched.
2. Auto-resolve every field that does NOT need a human answer —
   SerenDB project + database (returning a Postgres URI), the
   Google Drive output folder, and the cold-start Seren API key
   — so the chat AI is never asked to relay them.
3. Walk `config.json` and `.env` to list the small set of fields
   the operator alone can supply, and expose a sibling helper
   (`apply_set`) the chat AI calls once per answer to persist
   back to disk.

The chat AI (Claude in the user's Seren chat) is the only consumer
of the missing-field envelope. Seren Desktop does NOT prompt the
user — every value flows user → chat AI → `apply_set`.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol

from scripts.storage import persistence


# --------------------------------------------------------------------- #
# Field catalog                                                         #
# --------------------------------------------------------------------- #
#
# The catalog is the single source of truth for which fields the chat
# AI must ask Jill about. Adding a new operator-only field is a one-
# line change here; the envelope, missing-detection, and apply_set
# all read this list.


@dataclass(frozen=True)
class FieldSpec:
    """A field the operator alone can supply."""

    key: str
    scope: str  # "config" (writes inputs.<key> in config.json) or "env"
    prompt: str
    secret: bool


_CONFIG_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="salesforce_org_url",
        scope="config",
        prompt="What URL do you use to log in to Salesforce?",
        secret=False,
    ),
    FieldSpec(
        key="salesforce_owner_email",
        scope="config",
        prompt="What's your Salesforce SSO email?",
        secret=False,
    ),
    FieldSpec(
        key="nathan_share_email",
        scope="config",
        prompt="Who should receive the weekly status doc?",
        secret=False,
    ),
)

_ENV_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        key="SF_USERNAME",
        scope="env",
        prompt="Microsoft / SSO username (often the same as your SSO email).",
        secret=False,
    ),
    FieldSpec(
        key="SF_PASSWORD",
        scope="env",
        prompt="Microsoft / SSO password.",
        secret=True,
    ),
    FieldSpec(
        key="SF_TOTP_SECRET",
        scope="env",
        prompt=(
            "Authenticator TOTP secret — see SKILL.md §Path A step 2 for "
            "how to fetch the base32 seed from Salesforce MFA setup."
        ),
        secret=True,
    ),
)

_ALL_FIELDS: tuple[FieldSpec, ...] = _CONFIG_FIELDS + _ENV_FIELDS
_FIELDS_BY_KEY: dict[str, FieldSpec] = {f.key: f for f in _ALL_FIELDS}


# --------------------------------------------------------------------- #
# Result shape consumed by the CLI envelope                             #
# --------------------------------------------------------------------- #


class SerenDBLike(Protocol):
    """Re-exports the persistence.SerenDBClient Protocol so callers
    only need to import from this module."""

    def list_projects(self) -> list[dict]: ...
    def create_project(self, name: str) -> dict: ...
    def list_databases(self, project_id: str) -> list[dict]: ...
    def create_database(self, project_id: str, name: str) -> dict: ...
    def get_connection_uri(self, project_id: str, database_name: str) -> str: ...


SerenDBClientFactory = Callable[[], SerenDBLike]
DrivePublisherCall = Callable[[str, str, dict], dict]


@dataclass
class BootstrapResult:
    """What the CLI envelope and the chat AI need to know.

    `ready` is True only when no operator-only fields remain. The
    chat AI uses it to decide whether to drop into the Q&A loop or
    fall through to `--command run --dry-run`.
    """

    ready: bool
    auto_resolved: list[str] = field(default_factory=list)
    missing: list[FieldSpec] = field(default_factory=list)
    stable_dir: Optional[Path] = None


# --------------------------------------------------------------------- #
# Project / database naming                                             #
# --------------------------------------------------------------------- #


_SERENDB_PROJECT_NAME = "pk-lead-intelligence"
_SERENDB_DATABASE_NAME = "pk_lead_enrichment"
_DRIVE_FOLDER_NAME = "PK Lead Intelligence — Weekly Reports"


# --------------------------------------------------------------------- #
# Staging                                                               #
# --------------------------------------------------------------------- #


def _stage_if_absent(src: Path, dst: Path) -> bool:
    """Copy `src` to `dst` only if `dst` does not already exist.

    Returns True when a copy actually happened. The function is the
    invariant the operator relies on — once they edit `config.json`,
    re-running bootstrap MUST not clobber it.
    """

    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return True


# --------------------------------------------------------------------- #
# Placeholder detection                                                 #
# --------------------------------------------------------------------- #


def _is_placeholder(value) -> bool:
    """A field counts as 'missing' when empty OR still holds the
    example-file placeholder. The example file uses `<...>` wrappers
    (e.g. `<paste-folder-id-from-google-drive>`); `<` is not a legal
    URL or email character, so its presence flags the placeholder
    cleanly across every config field."""

    if value is None:
        return True
    if not isinstance(value, str):
        return False
    if value == "":
        return True
    if "<" in value:
        return True
    return False


# --------------------------------------------------------------------- #
# Env-file IO                                                           #
# --------------------------------------------------------------------- #


def _read_env_values(env_path: Path) -> dict[str, str]:
    """Parse a `.env`-style file into a dict.

    Deliberately simple — the file format is one `KEY=value` per
    line plus blank lines and `#`-prefixed comments. Quoted values
    are not supported; the skill's `.env` does not use them.
    """

    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in env_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value
    return values


def _write_env_values(env_path: Path, updates: dict[str, str]) -> None:
    """Merge `updates` into `env_path` in place, preserving order,
    comments, and blank lines for any line we don't touch.

    A key present in the file gets its value replaced. A key not
    present is appended at the end so the operator can see what
    bootstrap added.
    """

    existing_lines = (
        env_path.read_text().splitlines() if env_path.exists() else []
    )
    replaced: set[str] = set()
    new_lines: list[str] = []
    for raw in existing_lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(raw)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in updates:
            new_lines.append(f"{key}={updates[key]}")
            replaced.add(key)
        else:
            new_lines.append(raw)
    for key, value in updates.items():
        if key not in replaced:
            new_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(new_lines) + "\n")


# --------------------------------------------------------------------- #
# Config-file IO                                                        #
# --------------------------------------------------------------------- #


def _load_config(config_path: Path) -> dict:
    return json.loads(config_path.read_text())


def _save_config(config_path: Path, config: dict) -> None:
    config_path.write_text(json.dumps(config, indent=2) + "\n")


# --------------------------------------------------------------------- #
# Auto-resolve helpers                                                  #
# --------------------------------------------------------------------- #


def _maybe_auto_resolve_serendb(
    config: dict,
    serendb_client_factory: Optional[SerenDBClientFactory],
) -> bool:
    """Populate `inputs.serendb_connection_uri` if it's missing.

    Returns True when bootstrap actually filled the field.
    """

    inputs = config.setdefault("inputs", {})
    if not _is_placeholder(inputs.get("serendb_connection_uri")):
        return False
    if serendb_client_factory is None:
        return False
    client = serendb_client_factory()
    uri = persistence.bootstrap_serendb(
        project_name=_SERENDB_PROJECT_NAME,
        database_name=_SERENDB_DATABASE_NAME,
        client=client,
    )
    inputs["serendb_connection_uri"] = uri
    return True


def _maybe_auto_resolve_drive_folder(
    config: dict,
    drive_publisher_call: Optional[DrivePublisherCall],
) -> bool:
    inputs = config.setdefault("inputs", {})
    if not _is_placeholder(inputs.get("google_drive_folder_id")):
        return False
    if drive_publisher_call is None:
        return False
    resp = drive_publisher_call(
        "google-drive",
        "/files",
        {
            "name": _DRIVE_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    folder_id = resp.get("id")
    if not folder_id:
        return False
    inputs["google_drive_folder_id"] = folder_id
    return True


def _maybe_record_seren_api_key(
    env_values: dict[str, str],
) -> bool:
    """Mark SEREN_API_KEY as auto-resolved when a key is already
    reachable through any of the read-only resolution layers.

    Bootstrap deliberately does NOT trigger `resolve_api_key`'s
    cold-start `POST /auth/agent` here — that has a network side
    effect and would punish CI / offline hosts. The first publisher
    call below this layer fires the cold-start exactly as it always
    has. We only check whether a key is reachable so the chat-AI
    envelope can truthfully report it as `auto_resolved` and never
    ask Jill for it.
    """

    if env_values.get("SEREN_API_KEY"):
        return True
    for var in ("API_KEY", "SEREN_API_KEY"):
        if os.environ.get(var):
            return True
    return False


# --------------------------------------------------------------------- #
# Public surface                                                        #
# --------------------------------------------------------------------- #


def run_bootstrap(
    *,
    stable_dir: Path,
    skill_root: Path,
    serendb_client_factory: Optional[SerenDBClientFactory] = None,
    drive_publisher_call: Optional[DrivePublisherCall] = None,
) -> BootstrapResult:
    """Run the first-run bootstrap pass and return what's left for
    the chat AI to ask the user.

    The function is idempotent — calling it after every operator
    answer is the supported chat-AI loop. Existing files are never
    clobbered, already-resolved fields are never re-resolved, and
    `live_mode` is force-clamped to False so the user cannot
    accidentally fast-track past dry-run review.
    """

    stable_dir = Path(stable_dir)
    skill_root = Path(skill_root)

    config_path = stable_dir / "config.json"
    env_path = stable_dir / ".env"

    _stage_if_absent(skill_root / "config.example.json", config_path)
    _stage_if_absent(skill_root / ".env.example", env_path)

    config = _load_config(config_path)
    env_values = _read_env_values(env_path)

    auto_resolved: list[str] = []
    if _maybe_record_seren_api_key(env_values):
        auto_resolved.append("SEREN_API_KEY")
    if _maybe_auto_resolve_serendb(config, serendb_client_factory):
        auto_resolved.append("serendb_connection_uri")
    if _maybe_auto_resolve_drive_folder(config, drive_publisher_call):
        auto_resolved.append("google_drive_folder_id")

    # Force live_mode False on every bootstrap pass. The double-gate
    # (`--allow-live` × config `live_mode=true`) stays the only path
    # to writes, but bootstrap explicitly clamps the config side so
    # a copied-from-example True can never sneak through.
    config.setdefault("inputs", {})["live_mode"] = False

    _save_config(config_path, config)
    _write_env_values(env_path, env_values)

    missing = _scan_missing(config=config, env_values=env_values)

    return BootstrapResult(
        ready=not missing,
        auto_resolved=auto_resolved,
        missing=missing,
        stable_dir=stable_dir,
    )


def _scan_missing(*, config: dict, env_values: dict[str, str]) -> list[FieldSpec]:
    inputs = config.get("inputs") or {}
    out: list[FieldSpec] = []
    for spec in _CONFIG_FIELDS:
        if _is_placeholder(inputs.get(spec.key)):
            out.append(spec)
    for spec in _ENV_FIELDS:
        if _is_placeholder(env_values.get(spec.key)):
            out.append(spec)
    return out


def apply_set(
    *,
    stable_dir: Path,
    assignments: Iterable[tuple[str, str]],
) -> None:
    """Persist operator answers collected by the chat AI.

    Each assignment is a `(key, value)` tuple. The key MUST appear
    in `_FIELDS_BY_KEY`; an unknown key raises `KeyError` so the
    chat AI sees a clean failure rather than silently dropping the
    answer.

    Config-scope keys land under `config.inputs.<key>`. Env-scope
    keys land in `.env`. Sibling config keys and unrelated `.env`
    lines are preserved exactly.
    """

    stable_dir = Path(stable_dir)
    config_path = stable_dir / "config.json"
    env_path = stable_dir / ".env"

    config = _load_config(config_path) if config_path.exists() else {}
    env_values = _read_env_values(env_path)

    config_dirty = False
    env_dirty = False
    for key, value in assignments:
        spec = _FIELDS_BY_KEY.get(key)
        if spec is None:
            raise KeyError(
                f"unknown bootstrap field {key!r}; expected one of "
                f"{sorted(_FIELDS_BY_KEY)}"
            )
        if spec.scope == "config":
            config.setdefault("inputs", {})[key] = value
            config_dirty = True
        else:
            env_values[key] = value
            env_dirty = True

    if config_dirty:
        _save_config(config_path, config)
    if env_dirty:
        _write_env_values(env_path, env_values)


def format_envelope(
    result: BootstrapResult,
    *,
    skill: str = "pk-lead-intelligence",
    persist_command: str = (
        "python scripts/agent.py --command bootstrap --set <key>=<value>"
    ),
) -> dict:
    """Build the JSON envelope the chat AI consumes.

    The shape is the contract documented in `SKILL.md > First-Run
    Bootstrap`. Renaming a key here is a breaking change for every
    chat surface that drives this skill.
    """

    return {
        "bootstrap": "ready" if result.ready else "needs_input",
        "skill": skill,
        "auto_resolved": list(result.auto_resolved),
        "missing": [
            {
                "key": m.key,
                "scope": m.scope,
                "prompt": m.prompt,
                "secret": m.secret,
            }
            for m in result.missing
        ],
        "persist_command": persist_command,
    }
