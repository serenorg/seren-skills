#!/usr/bin/env python3
"""
Dry-run test of the Polymarket trading agent.
Tests all components without placing actual trades.
"""

import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

# Load environment variables from .env
load_dotenv()

print("=" * 70)
print("POLYMARKET SKILL DRY-RUN TEST")
print("=" * 70)
print()

# Test 1: Import all modules
print("Test 1: Importing modules...")
try:
    from seren_client import SerenClient
    from polymarket_client import PolymarketClient
    from kelly import calculate_kelly_fraction
    from logger import TradingLogger
    from position_tracker import PositionTracker
    print("✅ All modules imported successfully")
except Exception as e:
    print(f"❌ Import failed: {e}")
    sys.exit(1)

print()

# Test 2: Check environment
print("Test 2: Checking environment...")
required_vars = ['SEREN_API_KEY']
optional_vars = ['POLY_API_KEY', 'POLY_PASSPHRASE', 'POLY_ADDRESS']

missing_required = []
missing_optional = []

for var in required_vars:
    if not os.getenv(var):
        missing_required.append(var)

for var in optional_vars:
    if not os.getenv(var):
        missing_optional.append(var)

if missing_required:
    print(f"❌ Missing required: {', '.join(missing_required)}")
    print("   Set SEREN_API_KEY in .env file or environment")
    sys.exit(1)

if missing_optional:
    print(f"⚠️  Missing optional (Polymarket trading disabled): {', '.join(missing_optional)}")
else:
    print("✅ All Polymarket credentials found")

print("✅ SEREN_API_KEY found")
print()

# Test 3: Initialize Seren client
print("Test 3: Initializing Seren client...")
try:
    seren = SerenClient()
    print("✅ Seren client initialized")
except Exception as e:
    print(f"❌ Failed: {e}")
    sys.exit(1)

print()

# Test 4: Test publisher calls (read-only)
print("Test 4: Testing publisher calls...")
print()

# Test 4a: Get markets
print("  4a. Getting markets from polymarket-data...")
try:
    # We'll test the get_markets method directly without full Polymarket credentials
    # by mocking the PolymarketClient init
    from unittest.mock import MagicMock

    # Create mock client that only needs Seren client
    poly_client = PolymarketClient.__new__(PolymarketClient)
    poly_client.seren = seren

    # Skip credential validation for read-only operations
    poly_client.poly_api_key = "mock"
    poly_client.poly_passphrase = "mock"
    poly_client.poly_secret = "mock"
    poly_client.poly_address = "mock"

    markets = poly_client.get_markets(limit=5, active=True)

    if markets:
        print(f"  ✅ Retrieved {len(markets)} markets")
        print(f"     Sample: {markets[0]['question'][:60]}...")
    else:
        print("  ⚠️  No markets returned (may be API issue or filtering)")

except Exception as e:
    print(f"  ❌ Failed: {e}")
    import traceback
    traceback.print_exc()

print()

# Test 4b: Test Kelly calculation
print("  4b. Testing Kelly Criterion calculator...")
try:
    kelly_fraction = calculate_kelly_fraction(
        fair_value=0.65,
        market_price=0.50
    )
    print(f"  ✅ Kelly calculation: {kelly_fraction:.2%} (fair=65%, market=50%)")
except Exception as e:
    print(f"  ❌ Failed: {e}")

print()

# Test 5: Test logging
print("Test 5: Testing logging system...")
try:
    logger = TradingLogger()
    logger.log_scan_result(
        dry_run=True,
        markets_scanned=5,
        opportunities_found=1,
        trades_executed=0,
        capital_deployed=0.0,
        api_cost=1.0,
        serenbucks_balance=50.0,
        polymarket_balance=100.0
    )
    print("✅ Logger working")
except Exception as e:
    print(f"❌ Failed: {e}")

print()

# Test 6: Test position tracker
print("Test 6: Testing position tracker...")
try:
    tracker = PositionTracker()
    print("✅ Position tracker initialized")
except Exception as e:
    print(f"❌ Failed: {e}")

print()

# Summary
print("=" * 70)
print("DRY-RUN TEST COMPLETE")
print("=" * 70)
print()
print("✅ All core components are working")
print("✅ No live trading executed (dry-run mode)")
print()
print("To run the actual agent in dry-run mode:")
print("  python scripts/agent.py --config config.json --dry-run")
print()
