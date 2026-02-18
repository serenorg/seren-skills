#!/usr/bin/env python3
"""
Setup SerenDB for Polymarket Trading Bot

This script:
1. Connects to SerenDB
2. Creates the polymarket_trader database
3. Creates required tables
4. Optionally migrates existing local data to SerenDB
"""

import os
import sys
import json
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

from seren_client import SerenClient
from serendb_storage import SerenDBStorage


def migrate_local_data(storage: SerenDBStorage):
    """Migrate existing local positions.json to SerenDB"""
    positions_file = Path('logs/positions.json')

    if not positions_file.exists():
        print("No local positions.json found - skipping migration")
        return

    try:
        with open(positions_file, 'r') as f:
            data = json.load(f)

        positions = data.get('positions', [])
        if not positions:
            print("No positions to migrate")
            return

        print(f"Migrating {len(positions)} positions to SerenDB...")

        for pos in positions:
            if storage.save_position(pos):
                print(f"  ✓ Migrated: {pos['market'][:50]}...")
            else:
                print(f"  ✗ Failed: {pos['market'][:50]}...")

        print(f"✓ Migration complete")

        # Backup the old file
        backup_file = positions_file.with_suffix('.json.backup')
        positions_file.rename(backup_file)
        print(f"  Backed up local file to: {backup_file}")

    except Exception as e:
        print(f"Migration error: {e}")


def main():
    print("=" * 70)
    print("POLYMARKET BOT - SERENDB SETUP")
    print("=" * 70)
    print()

    # Check for API key
    if not os.getenv('SEREN_API_KEY'):
        print("❌ Error: SEREN_API_KEY environment variable not set")
        print()
        print("Set your API key:")
        print("  export SEREN_API_KEY='your-api-key'")
        print()
        print("Or add it to .env file:")
        print("  SEREN_API_KEY=your-api-key")
        print()
        sys.exit(1)

    try:
        # Initialize Seren client
        print("1. Connecting to Seren Gateway...")
        seren = SerenClient()
        print("   ✓ Connected")
        print()

        # Initialize SerenDB storage
        print("2. Initializing SerenDB storage...")
        storage = SerenDBStorage(seren)
        print("   ✓ Storage client initialized")
        print()

        # Setup database
        print("3. Setting up database...")
        if storage.setup_database():
            print("   ✓ Database setup complete")
        else:
            print("   ✗ Database setup failed")
            sys.exit(1)
        print()

        # Optional: Migrate local data
        print("4. Checking for local data to migrate...")
        migrate_local_data(storage)
        print()

        print("=" * 70)
        print("SETUP COMPLETE")
        print("=" * 70)
        print()
        print("Your Polymarket bot is now configured to use SerenDB for storage.")
        print()
        print("Benefits:")
        print("  ✓ Cloud-backed storage (no data loss on upgrades)")
        print("  ✓ Access from any device")
        print("  ✓ Automatic backups")
        print("  ✓ No additional cost")
        print()
        print("Next steps:")
        print("  1. Run the bot: python scripts/agent.py --config config.json")
        print("  2. Bot will automatically use SerenDB for all data")
        print()

    except Exception as e:
        print(f"\n❌ Setup failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
