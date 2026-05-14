"""Zero-friction first-run config bootstrap (#542 Fix 1).

The arb-bot's `--command setup` used to require the operator to copy
`config.example.json` to `config.json` by hand and edit the email +
provider. Auto-discover (#538) made the manual_pairs edit obsolete, so
the only remaining touch was the email — small enough to fold into a
CLI flag and a one-time bootstrap.

Contract:
  - If `config.json` already exists, do nothing (idempotent). Operator
    state is sacred; we never overwrite their tuning.
  - Otherwise copy `config.example.json`, flip `auto_discover.enabled`
    to true and `live_mode` to false (safe defaults for the very first
    cycle), and persist `prophet_email` / `email_provider` when passed.
  - If neither file exists, raise — the skill is misinstalled and
    fabricating an empty dict would mask the real problem.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BootstrapResult:
    """Outcome surfaced for tests and the cmd_setup payload."""

    created: bool
    config_path: str


def bootstrap_config_if_missing(
    *,
    config_path: str,
    example_path: str,
    prophet_email: str | None,
    email_provider: str | None,
) -> BootstrapResult:
    """Create `config.json` from `config.example.json` if absent.

    Idempotent: returns `created=False` without touching the file when
    `config.json` already exists, even if `prophet_email` was passed.
    Operator-tuned configs are never silently mutated — they edit by
    hand or delete and re-bootstrap.
    """
    config = Path(config_path)
    example = Path(example_path)

    if config.exists():
        return BootstrapResult(created=False, config_path=str(config))

    if not example.exists():
        raise FileNotFoundError(
            f"config bootstrap: neither {config} nor {example} exists; "
            f"the skill appears to be misinstalled"
        )

    shutil.copy(example, config)
    data = json.loads(config.read_text(encoding="utf-8"))

    # Safe defaults on a fresh install: auto-discover on so the first
    # `--command run` finds candidates, live_mode off so the first run
    # is always dry-run regardless of what the operator copies later.
    auto = data.setdefault("auto_discover", {})
    auto["enabled"] = True
    data["live_mode"] = False

    inputs = data.setdefault("inputs", {})
    if prophet_email:
        inputs["prophet_email"] = prophet_email
    if email_provider:
        inputs["email_provider"] = email_provider

    config.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    return BootstrapResult(created=True, config_path=str(config))
