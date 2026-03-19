#!/usr/bin/env python3
# Generated from seren/skill-runtime/scripts/seren_runtime.py. Do not edit by hand.
from __future__ import annotations

import os
import warnings
from pathlib import Path


class LegacyRuntimePathWarning(UserWarning):
    """Emitted when a file is resolved from the deprecated skill install directory."""


SKILL_SLUG = "kraken-grid-trader"
SKILL_ROOT = Path(__file__).resolve().parents[1]


def _is_windows() -> bool:
    return os.name == "nt"


def _shared_runtime_root() -> Path:
    if _is_windows():
        appdata = os.getenv("APPDATA")
        if appdata:
            return Path(appdata).expanduser() / "seren"
    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "seren"
    return Path.home() / ".config" / "seren"


def _project_runtime_dir(skill_slug: str, start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".seren").is_dir():
            return candidate / ".seren" / "skills-data" / skill_slug
    return None


def _default_runtime_dir(skill_slug: str, start: Path | None = None) -> Path:
    project = _project_runtime_dir(skill_slug, start=start)
    if project is not None:
        return project
    return _shared_runtime_root() / "skills-data" / skill_slug


def _warn_legacy(legacy: Path, preferred: Path, warned: set[Path]) -> None:
    resolved = legacy.resolve()
    if resolved in warned:
        return
    warned.add(resolved)
    warnings.warn(
        f"Using deprecated legacy path '{resolved}'. Move this file to '{preferred}'.",
        LegacyRuntimePathWarning,
        stacklevel=4,
    )


def make_runtime_paths(skill_slug: str, skill_root: Path):
    warned: set[Path] = set()

    def default_runtime_dir(start: Path | None = None) -> Path:
        return _default_runtime_dir(skill_slug, start=start)

    def _resolve(raw: str, *, default_name: str, start: Path | None = None) -> Path:
        candidate = Path(raw).expanduser()
        if candidate.is_absolute():
            return candidate
        if candidate.parent != Path("."):
            return (Path.cwd() / candidate).resolve()
        filename = candidate.name or default_name
        preferred = _default_runtime_dir(skill_slug, start=start) / filename
        legacy = skill_root / filename
        if preferred.exists():
            return preferred
        if legacy.exists():
            _warn_legacy(legacy, preferred, warned)
            return legacy
        return preferred

    def resolve_config_path(config_path: str = "config.json", *, start: Path | None = None) -> Path:
        return _resolve(config_path, default_name="config.json", start=start)

    def resolve_env_path(env_path: str | None = None, *, start: Path | None = None) -> Path:
        raw = env_path or os.getenv("SEREN_SKILL_ENV_FILE") or ".env"
        return _resolve(raw, default_name=".env", start=start)

    def resolve_runtime_dir(config_path: str | Path | None = None, *, start: Path | None = None) -> Path:
        if config_path is not None:
            return resolve_config_path(str(config_path), start=start).parent
        return default_runtime_dir(start=start)

    def load_skill_env(
        env_path: str | None = None,
        *,
        start: Path | None = None,
        override: bool = False,
    ) -> Path | None:
        resolved = resolve_env_path(env_path, start=start)
        if not resolved.exists():
            return None
        try:
            from dotenv import load_dotenv  # type: ignore
        except ImportError:
            for raw_line in resolved.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                key, sep, value = line.partition("=")
                if sep != "=":
                    continue
                key = key.strip()
                if not key:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if override or key not in os.environ:
                    os.environ[key] = value
        else:
            load_dotenv(resolved, override=override)
        return resolved

    def activate_runtime(config_path: str = "config.json", *, start: Path | None = None, create: bool = True) -> Path:
        start_path = (start or Path.cwd()).resolve()
        resolved_config = resolve_config_path(config_path, start=start_path)
        runtime_dir = resolve_runtime_dir(str(resolved_config), start=start_path)
        if create:
            runtime_dir.mkdir(parents=True, exist_ok=True)
        load_skill_env(start=start_path)
        os.chdir(runtime_dir)
        return resolved_config

    return resolve_config_path, resolve_env_path, resolve_runtime_dir, default_runtime_dir, load_skill_env, activate_runtime


resolve_config_path, resolve_env_path, resolve_runtime_dir, default_runtime_dir, load_skill_env, activate_runtime = (
    make_runtime_paths(SKILL_SLUG, SKILL_ROOT)
)
