#!/usr/bin/env python3
"""
Apply required SerenDB schemas for sass-short-trader-delta-neutral.

Users can provide either:
- --dsn (explicit), or
- --api-key only (auto-resolves/creates SerenDB target)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from serendb_bootstrap import resolve_dsn
from serendb_storage import SerenDBStorage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply strategy + learning schemas")
    parser.add_argument("--dsn", default=os.getenv("SERENDB_DSN", ""), help="SerenDB DSN (optional)")
    parser.add_argument("--api-key", default=os.getenv("SEREN_API_KEY", ""), help="Seren API key (required if --dsn not provided)")
    parser.add_argument("--project-name", default=os.getenv("SEREN_PROJECT_NAME", "alpaca-sass-short-trader-delta-neutral"))
    parser.add_argument("--database-name", default=os.getenv("SEREN_DATABASE_NAME", "alpaca_sass_short_bot_dn"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dsn = resolve_dsn(
        dsn=args.dsn,
        api_key=args.api_key,
        project_name=args.project_name,
        database_name=args.database_name,
    )
    root = Path(__file__).resolve().parent
    storage = SerenDBStorage(dsn)
    storage.ensure_schemas(
        base_sql=root / "serendb_schema.sql",
        learning_sql=root / "self_learning_schema.sql",
    )
    print("Schemas applied successfully.")
    print(f"Resolved database name: {args.database_name}")


if __name__ == "__main__":
    main()
