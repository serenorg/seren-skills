#!/bin/bash
# Setup script for Polymarket Trading Bot testing
# Creates config.json and .env files with safe defaults for dry-run testing

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "$SKILL_DIR"

echo "============================================================"
echo "Polymarket Trading Bot - Test Setup"
echo "============================================================"
echo ""

# Check Python version
echo "Checking Python version..."
python3 --version || {
    echo "❌ Python 3 not found. Please install Python 3.9+"
    exit 1
}
echo "✅ Python 3 found"
echo ""

# Install dependencies
echo "Installing Python dependencies..."
pip3 install -r requirements.txt --quiet || {
    echo "❌ Failed to install dependencies"
    exit 1
}
echo "✅ Dependencies installed"
echo ""

# Create .env file
echo "Setting up .env file..."
if [ -f .env ]; then
    echo "⚠️  .env already exists, keeping existing file"
    echo "   To reconfigure, delete .env and run this script again"
else
    echo ""
    echo "Enter your SEREN_API_KEY (required for testing):"
    echo "(Get it from https://app.serendb.com/settings/api-keys)"
    read -r SEREN_API_KEY

    if [ -z "$SEREN_API_KEY" ]; then
        echo "❌ SEREN_API_KEY is required"
        exit 1
    fi

    cat > .env << EOF2
# Seren API credentials (REQUIRED)
SEREN_API_KEY=$SEREN_API_KEY

# Polymarket credentials (optional for dry-run testing)
# For live trading, get these from https://polymarket.com/settings/api
POLY_API_KEY=mock_key_for_testing
POLY_PASSPHRASE=mock_passphrase_for_testing
POLY_SECRET=mock_secret_for_testing
POLY_ADDRESS=0xMockAddressForTesting
EOF2

    echo "✅ .env created"
fi
echo ""

# Create config.json
echo "Setting up config.json..."
if [ -f config.json ]; then
    echo "⚠️  config.json already exists, keeping existing file"
    echo "   To reconfigure, delete config.json and run this script again"
else
    cp config.example.json config.json
    echo "✅ config.json created from example"
    echo ""
    echo "Default configuration:"
    cat config.json | python3 -m json.tool 2>/dev/null || cat config.json
fi
echo ""

# Run syntax validation
echo "============================================================"
echo "Running Syntax Validation..."
echo "============================================================"
echo ""
python3 scripts/test_syntax.py
echo ""

# Run dry-run test
echo "============================================================"
echo "Running Dry-Run Test..."
echo "============================================================"
echo ""
python3 scripts/test_dry_run.py
echo ""

# Summary
echo "============================================================"
echo "Setup Complete!"
echo "============================================================"
echo ""
echo "Next steps:"
echo ""
echo "1. Review your config.json settings:"
echo "   cat config.json"
echo ""
echo "2. Run a single dry-run scan (no live trades):"
echo "   python3 scripts/agent.py --config config.json --dry-run"
echo ""
echo "3. When ready for live trading:"
echo "   - Update .env with real Polymarket API credentials"
echo "   - Review config.json risk parameters"
echo "   - Ensure you have $550+ total budget (see SKILL.md Phase 4)"
echo "   - Start the agent server: python3 scripts/run_agent_server.py --config config.json"
echo "   - Setup cron job: python3 scripts/setup_cron.py --url http://localhost:8080/run"
echo ""
echo "4. Monitor logs:"
echo "   tail -f logs/trading_*.log"
echo ""
