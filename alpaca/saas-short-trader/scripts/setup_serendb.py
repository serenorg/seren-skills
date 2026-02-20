#!/usr/bin/env python3
"""
Apply required SerenDB schemas for saas-short-trader.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from serendb_storage import SerenDBStorage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Apply strategy + learning schemas")
    parser.add_argument("--dsn", required=True, help="SerenDB DSN")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = Path(__file__).resolve().parent
    storage = SerenDBStorage(args.dsn)
    storage.ensure_schemas(
        base_sql=root / "serendb_schema.sql",
        learning_sql=root / "self_learning_schema.sql",
    )
    print("Schemas applied successfully.")


if __name__ == "__main__":
    main()
