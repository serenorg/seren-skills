#!/usr/bin/env node
/**
 * Offline SDK smoke test for @gdexsdk/gdex-skill
 * Run: node scripts/verify.js
 *
 * This script verifies the SDK can be imported and core utilities work
 * without making any network requests.
 */

'use strict';

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const YELLOW = '\x1b[33m';
const RESET = '\x1b[0m';

let passed = 0;
let failed = 0;

function pass(label) {
  console.log(`${GREEN}✓${RESET} ${label}`);
  passed++;
}

function fail(label, err) {
  console.log(`${RED}✗${RESET} ${label}`);
  if (err) console.log(`  ${RED}${err.message || err}${RESET}`);
  failed++;
}

function section(title) {
  console.log(`\n${YELLOW}▶ ${title}${RESET}`);
}

async function run() {
  console.log('GDEX Skill — Offline Smoke Test\n');

  // ── 1. Import ──────────────────────────────────────────────────────────────
  section('SDK Import');
  let sdk;
  try {
    sdk = require('@gdexsdk/gdex-skill');
    pass('require("@gdexsdk/gdex-skill") succeeded');
  } catch (err) {
    fail('require("@gdexsdk/gdex-skill") failed', err);
    console.log(`\n${RED}Cannot continue — install dependencies first:${RESET}`);
    console.log('  npm install\n');
    process.exit(1);
  }

  const {
    GdexSkill,
    GdexApiClient,
    ChainId,
    getChainName,
    getNativeToken,
    formatUsd,
    formatTokenAmount,
    shortenAddress,
    validateAddress,
    validateAmount,
    GdexAuthError,
    GdexValidationError,
    GdexApiError,
    GdexNetworkError,
  } = sdk;

  // ── 2. GdexSkill instantiation ─────────────────────────────────────────────
  section('GdexSkill Instantiation');
  let skill;
  try {
    skill = new GdexSkill();
    pass('GdexSkill() instantiated with defaults');
  } catch (err) {
    fail('GdexSkill() instantiation failed', err);
  }

  try {
    const custom = new GdexSkill({ baseUrl: 'https://trade-api.gemach.io/v1', timeout: 5000 });
    pass('GdexSkill({ baseUrl, timeout }) instantiated with custom config');
  } catch (err) {
    fail('GdexSkill({ baseUrl, timeout }) failed', err);
  }

  // ── 3. Auth state management ───────────────────────────────────────────────
  section('Authentication State');
  try {
    if (skill && typeof skill.isAuthenticated === 'function') {
      const before = skill.isAuthenticated();
      if (before === false) {
        pass('isAuthenticated() returns false before login');
      } else {
        fail('isAuthenticated() should be false before login');
      }
    } else {
      fail('skill.isAuthenticated is not a function');
    }
  } catch (err) {
    fail('isAuthenticated() threw', err);
  }

  try {
    if (skill && typeof skill.logout === 'function') {
      skill.logout();
      pass('logout() can be called without error');
    } else {
      fail('skill.logout is not a function');
    }
  } catch (err) {
    fail('logout() threw', err);
  }

  // ── 4. API key check ───────────────────────────────────────────────────────
  section('Environment');
  if (process.env.GDEX_API_KEY) {
    pass('GDEX_API_KEY is set');
  } else {
    console.log(`${YELLOW}⚠${RESET}  GDEX_API_KEY not set — live trading will not work`);
  }

  if (process.env.CONTROL_WALLET_PRIVATE_KEY) {
    pass('CONTROL_WALLET_PRIVATE_KEY is set');
  } else {
    console.log(`${YELLOW}⚠${RESET}  CONTROL_WALLET_PRIVATE_KEY not set — managed-custody trading will not work`);
  }

  // ── 5. Chain configuration ─────────────────────────────────────────────────
  section('Chain Configuration');
  const expectedChains = {
    ETHEREUM: 1,
    BASE: 8453,
    ARBITRUM: 42161,
    BSC: 56,
    SOLANA: 622112261,
  };

  for (const [name, id] of Object.entries(expectedChains)) {
    if (ChainId && ChainId[name] === id) {
      pass(`ChainId.${name} === ${id}`);
    } else {
      fail(`ChainId.${name} should be ${id}, got ${ChainId ? ChainId[name] : 'N/A'}`);
    }
  }

  if (typeof getChainName === 'function') {
    try {
      const name = getChainName(1);
      pass(`getChainName(1) = "${name}"`);
    } catch (err) {
      fail('getChainName(1) threw', err);
    }
  } else {
    fail('getChainName is not exported');
  }

  if (typeof getNativeToken === 'function') {
    try {
      const token = getNativeToken(1);
      pass(`getNativeToken(1) = "${token}"`);
    } catch (err) {
      fail('getNativeToken(1) threw', err);
    }
  } else {
    fail('getNativeToken is not exported');
  }

  // ── 6. Formatting utilities ────────────────────────────────────────────────
  section('Formatting Utilities');
  if (typeof formatUsd === 'function') {
    try {
      const result = formatUsd(1234.56);
      pass(`formatUsd(1234.56) = "${result}"`);
    } catch (err) {
      fail('formatUsd() threw', err);
    }
  } else {
    fail('formatUsd is not exported');
  }

  if (typeof formatTokenAmount === 'function') {
    try {
      const result = formatTokenAmount(1.5, 'ETH');
      pass(`formatTokenAmount(1.5, "ETH") = "${result}"`);
    } catch (err) {
      fail('formatTokenAmount() threw', err);
    }
  } else {
    fail('formatTokenAmount is not exported');
  }

  // ── 7. Error classes ───────────────────────────────────────────────────────
  section('Error Classes');
  const errorClasses = { GdexAuthError, GdexValidationError, GdexApiError, GdexNetworkError };
  for (const [name, Cls] of Object.entries(errorClasses)) {
    if (typeof Cls === 'function') {
      try {
        const err = new Cls('test');
        if (err instanceof Error && err instanceof Cls) {
          pass(`${name} is a proper Error subclass`);
        } else {
          fail(`${name} instance check failed`);
        }
      } catch (e) {
        fail(`${name} constructor threw`, e);
      }
    } else {
      fail(`${name} is not exported`);
    }
  }

  // ── Summary ────────────────────────────────────────────────────────────────
  console.log(`\n${'─'.repeat(40)}`);
  console.log(`${GREEN}Passed: ${passed}${RESET}  ${failed > 0 ? RED : ''}Failed: ${failed}${RESET}`);

  if (failed > 0) {
    process.exit(1);
  }
}

run().catch((err) => {
  console.error(`${RED}Unexpected error:${RESET}`, err);
  process.exit(1);
});
