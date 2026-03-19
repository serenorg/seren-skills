#!/usr/bin/env python3
"""Initialize required SerenDB tables for Kraken Smart DCA Bot."""

from __future__ import annotations

import argparse
import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv() -> bool:
        return False

from serendb_store import SerenDBStore
from runtime_paths import activate_runtime


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialize SerenDB schema for smart-dca-bot")
    parser.add_argument(
        "--dsn",
        default="",
        help="Optional Postgres DSN override. Defaults to SERENDB_URL env var.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    activate_runtime()
    load_dotenv()
    dsn = args.dsn.strip() or os.getenv("SERENDB_URL", "")
    store = SerenDBStore(dsn)

    if not store.enabled:
        print("SerenDB disabled: set SERENDB_URL and install psycopg dependencies.")
        return 1

    store.ensure_schema()
    store.close()
    print("SerenDB schema initialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
