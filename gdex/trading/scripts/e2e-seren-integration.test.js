#!/usr/bin/env node
/**
 * End-to-end integration tests: GDEX Trading skill in seren-skills
 * Run: node scripts/e2e-seren-integration.test.js
 *
 * Tests run entirely offline — no network requests are made.
 * Exit code 0 = all pass, exit code 1 = any failure.
 */

'use strict';

const path = require('path');
const fs = require('fs');

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const YELLOW = '\x1b[33m';
const CYAN = '\x1b[36m';
const RESET = '\x1b[0m';

let passed = 0;
let failed = 0;

function pass(label) {
  console.log(`  ${GREEN}✓${RESET} ${label}`);
  passed++;
}

function fail(label, detail) {
  console.log(`  ${RED}✗${RESET} ${label}`);
  if (detail) console.log(`    ${RED}${detail}${RESET}`);
  failed++;
}

function section(title) {
  console.log(`\n${CYAN}▶ ${title}${RESET}`);
}

function assert(condition, label, detail) {
  if (condition) {
    pass(label);
  } else {
    fail(label, detail);
  }
}

// ─── Skill root (two levels up from scripts/) ────────────────────────────────
const SKILL_ROOT = path.resolve(__dirname, '..');
const SKILL_MD_PATH = path.join(SKILL_ROOT, 'SKILL.md');

// ─── A. Skill Structure Validation ───────────────────────────────────────────
section('A. Skill Structure Validation');

// SKILL.md exists
assert(fs.existsSync(SKILL_MD_PATH), 'SKILL.md exists');

let frontmatter = null;
let skillMdContent = '';
if (fs.existsSync(SKILL_MD_PATH)) {
  skillMdContent = fs.readFileSync(SKILL_MD_PATH, 'utf8');

  // Extract YAML frontmatter between --- delimiters
  const fmMatch = skillMdContent.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (fmMatch) {
    // Minimal YAML parser for key: value and nested key: value lines
    const raw = fmMatch[1];
    frontmatter = {};
    for (const line of raw.split('\n')) {
      const m = line.match(/^(\w[\w-]*):\s+"?([^"]*)"?\s*$/);
      if (m) frontmatter[m[1]] = m[2].trim();
    }
    pass('SKILL.md has valid YAML frontmatter delimiters');
  } else {
    fail('SKILL.md is missing YAML frontmatter (--- delimiters)');
  }
}

if (frontmatter) {
  // name field
  const name = frontmatter.name;
  assert(typeof name === 'string' && name.length > 0, 'frontmatter has name field');

  // name matches parent directory name ("trading")
  const parentDir = path.basename(SKILL_ROOT);
  assert(name === parentDir, `name "${name}" matches parent directory "${parentDir}"`);

  // name spec rules
  assert(name.length >= 1 && name.length <= 64, `name length (${name.length}) is 1-64 chars`);
  assert(/^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$/.test(name), 'name uses only lowercase letters, digits, hyphens; no leading/trailing hyphen');
  assert(!name.includes('--'), 'name has no consecutive hyphens');

  // description field
  const desc = frontmatter.description;
  assert(typeof desc === 'string' && desc.length > 0, 'frontmatter has non-empty description');
  assert(desc.length <= 1024, `description length (${desc.length}) is <= 1024 chars`);
}

// H1 heading exists in body
const h1Match = skillMdContent.match(/^# .+/m);
assert(h1Match !== null, 'SKILL.md has an H1 heading as display name');

// ─── B. SDK Import & Initialization ──────────────────────────────────────────
section('B. SDK Import & Initialization');

let sdk = null;
try {
  sdk = require('@gdexsdk/gdex-skill');
  pass('require("@gdexsdk/gdex-skill") succeeded');
} catch (err) {
  fail('require("@gdexsdk/gdex-skill") failed — run: npm install', err.message);
  console.log(`\n${RED}Cannot continue SDK tests without the package installed.${RESET}`);
  printSummary();
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
  validateSlippage,
  validateChain,
  GdexAuthError,
  GdexValidationError,
  GdexApiError,
  GdexNetworkError,
  GdexRateLimitError,
  generateEvmWallet,
  generateGdexSessionKeyPair,
  encryptGdexComputedData,
  decryptGdexComputedData,
  buildGdexSignInComputedData,
  ENDPOINTS,
} = sdk;

// GdexSkill class is exported
assert(typeof GdexSkill === 'function', 'GdexSkill class is exported');

// Instantiate with defaults
let skill = null;
try {
  skill = new GdexSkill();
  pass('new GdexSkill() instantiated with defaults');
} catch (err) {
  fail('new GdexSkill() threw', err.message);
}

// Instantiate with custom config
try {
  const custom = new GdexSkill({ baseUrl: 'https://trade-api.gemach.io/v1', timeout: 10000 });
  pass('new GdexSkill({ baseUrl, timeout }) instantiated with custom config');
} catch (err) {
  fail('new GdexSkill({ baseUrl, timeout }) threw', err.message);
}

// Expected exports are present
const expectedExports = [
  ['GdexSkill', GdexSkill],
  ['GdexApiClient', GdexApiClient],
  ['ChainId', ChainId],
  ['getChainName', getChainName],
  ['getNativeToken', getNativeToken],
  ['formatUsd', formatUsd],
  ['formatTokenAmount', formatTokenAmount],
  ['GdexAuthError', GdexAuthError],
  ['GdexValidationError', GdexValidationError],
  ['GdexApiError', GdexApiError],
  ['GdexNetworkError', GdexNetworkError],
];
for (const [name, val] of expectedExports) {
  assert(val !== undefined, `${name} is exported from SDK`);
}

// ─── C. Authentication Flow (Offline) ────────────────────────────────────────
section('C. Authentication Flow (Offline)');

if (skill) {
  // isAuthenticated() before login
  try {
    const auth = skill.isAuthenticated();
    assert(auth === false, 'isAuthenticated() returns false before login');
  } catch (err) {
    fail('isAuthenticated() threw', err.message);
  }

  // logout() resets state
  try {
    skill.logout();
    const afterLogout = skill.isAuthenticated();
    assert(afterLogout === false, 'logout() resets isAuthenticated() to false');
  } catch (err) {
    fail('logout() threw', err.message);
  }
}

// Session keypair generation
if (typeof generateGdexSessionKeyPair === 'function') {
  try {
    const kp = generateGdexSessionKeyPair();
    assert(typeof kp === 'object' && kp !== null, 'generateGdexSessionKeyPair() returns an object');
    const hasKeys = ('privateKey' in kp || 'secretKey' in kp) && ('publicKey' in kp || 'address' in kp);
    assert(hasKeys, 'generateGdexSessionKeyPair() result has key fields');
  } catch (err) {
    fail('generateGdexSessionKeyPair() threw', err.message);
  }
} else {
  fail('generateGdexSessionKeyPair is not exported');
}

// AES encrypt/decrypt round-trip
if (typeof encryptGdexComputedData === 'function' && typeof decryptGdexComputedData === 'function') {
  try {
    const payload = JSON.stringify({ test: 'data', value: 42 });
    const key = 'test-aes-key-for-offline-verify';
    const encrypted = encryptGdexComputedData(payload, key);
    assert(typeof encrypted === 'string' && encrypted.length > 0, 'encryptGdexComputedData() returns a non-empty string');
    const decrypted = decryptGdexComputedData(encrypted, key);
    assert(decrypted === payload, 'AES encrypt/decrypt round-trip produces original payload');
  } catch (err) {
    fail('AES encrypt/decrypt round-trip failed', err.message);
  }
} else {
  fail('encryptGdexComputedData or decryptGdexComputedData is not exported');
}

// buildGdexSignInComputedData
if (typeof buildGdexSignInComputedData === 'function') {
  try {
    const result = buildGdexSignInComputedData({ walletAddress: '0x1234567890123456789012345678901234567890' });
    assert(result !== undefined, 'buildGdexSignInComputedData() returns a value');
  } catch (err) {
    fail('buildGdexSignInComputedData() threw', err.message);
  }
} else {
  fail('buildGdexSignInComputedData is not exported');
}

// ─── D. Chain Configuration ───────────────────────────────────────────────────
section('D. Chain Configuration');

const expectedChainIds = {
  ETHEREUM: 1,
  BASE: 8453,
  ARBITRUM: 42161,
  BSC: 56,
  AVALANCHE: 43114,
  POLYGON: 137,
  OPTIMISM: 10,
  SOLANA: 622112261,
};

for (const [chainName, expectedId] of Object.entries(expectedChainIds)) {
  if (ChainId) {
    assert(ChainId[chainName] === expectedId, `ChainId.${chainName} === ${expectedId}`);
  } else {
    fail(`ChainId.${chainName} (ChainId not exported)`);
  }
}

// getChainName
if (typeof getChainName === 'function') {
  const chainNameTests = [[1, 'Ethereum'], [8453, 'Base'], [42161, 'Arbitrum'], [56, 'BSC']];
  for (const [id, expected] of chainNameTests) {
    try {
      const name = getChainName(id);
      assert(
        typeof name === 'string' && name.toLowerCase().includes(expected.toLowerCase()),
        `getChainName(${id}) contains "${expected}" (got "${name}")`
      );
    } catch (err) {
      fail(`getChainName(${id}) threw`, err.message);
    }
  }
} else {
  fail('getChainName is not a function');
}

// getNativeToken
if (typeof getNativeToken === 'function') {
  const nativeTokenTests = [[1, 'ETH'], [56, 'BNB'], [622112261, 'SOL']];
  for (const [id, expected] of nativeTokenTests) {
    try {
      const token = getNativeToken(id);
      assert(
        typeof token === 'string' && token === expected,
        `getNativeToken(${id}) === "${expected}" (got "${token}")`
      );
    } catch (err) {
      fail(`getNativeToken(${id}) threw`, err.message);
    }
  }
} else {
  fail('getNativeToken is not a function');
}

// ─── E. Validation Utilities ─────────────────────────────────────────────────
section('E. Validation Utilities');

if (typeof validateAddress === 'function') {
  // Valid EVM address
  try {
    const r = validateAddress('0x1234567890123456789012345678901234567890');
    assert(r === true || r === undefined || r === null, 'validateAddress() accepts valid EVM address');
  } catch (err) {
    fail('validateAddress() threw on valid EVM address', err.message);
  }

  // Invalid address
  try {
    let threw = false;
    try {
      validateAddress('not-an-address');
    } catch {
      threw = true;
    }
    // Either throws or returns falsy
    assert(threw, 'validateAddress() rejects invalid address');
  } catch (err) {
    fail('validateAddress() invalid-address test failed', err.message);
  }
} else {
  fail('validateAddress is not exported');
}

if (typeof validateAmount === 'function') {
  try {
    const r = validateAmount(1.0);
    assert(r === true || r === undefined || r === null, 'validateAmount() accepts positive amount');
  } catch (err) {
    fail('validateAmount() threw on valid amount', err.message);
  }

  try {
    let threw = false;
    try {
      validateAmount(0);
    } catch {
      threw = true;
    }
    assert(threw, 'validateAmount() rejects zero');
  } catch (err) {
    fail('validateAmount() zero-test failed', err.message);
  }

  try {
    let threw = false;
    try {
      validateAmount(-1);
    } catch {
      threw = true;
    }
    assert(threw, 'validateAmount() rejects negative');
  } catch (err) {
    fail('validateAmount() negative-test failed', err.message);
  }
} else {
  fail('validateAmount is not exported');
}

if (typeof validateSlippage === 'function') {
  try {
    const r = validateSlippage(0.5);
    assert(r === true || r === undefined || r === null, 'validateSlippage() accepts 0.5');
  } catch (err) {
    fail('validateSlippage() threw on valid slippage', err.message);
  }

  try {
    let threw = false;
    try {
      validateSlippage(101);
    } catch {
      threw = true;
    }
    assert(threw, 'validateSlippage() rejects > 100');
  } catch (err) {
    fail('validateSlippage() >100 test failed', err.message);
  }
} else {
  fail('validateSlippage is not exported');
}

if (typeof validateChain === 'function') {
  try {
    const r = validateChain(1);
    assert(r === true || r === undefined || r === null, 'validateChain() accepts chainId 1');
  } catch (err) {
    fail('validateChain() threw on valid chain', err.message);
  }

  try {
    let threw = false;
    try {
      validateChain(99999999);
    } catch {
      threw = true;
    }
    assert(threw, 'validateChain() rejects unknown chainId');
  } catch (err) {
    fail('validateChain() invalid-chain test failed', err.message);
  }
} else {
  fail('validateChain is not exported');
}

// ─── F. Wallet Generation (Offline) ──────────────────────────────────────────
section('F. Wallet Generation (Offline)');

if (typeof generateEvmWallet === 'function') {
  try {
    const wallet = generateEvmWallet();
    assert(typeof wallet === 'object' && wallet !== null, 'generateEvmWallet() returns an object');
    const address = wallet.address || wallet.walletAddress;
    assert(typeof address === 'string' && address.startsWith('0x') && address.length === 42, `EVM address starts with 0x and is 42 chars (got "${address}")`);
    const privKey = wallet.privateKey;
    assert(typeof privKey === 'string' && privKey.length === 66, `EVM private key is 66 chars (got length ${privKey ? privKey.length : 'N/A'})`);
  } catch (err) {
    fail('generateEvmWallet() threw', err.message);
  }
} else {
  fail('generateEvmWallet is not exported');
}

// ─── G. Error Classes ─────────────────────────────────────────────────────────
section('G. Error Classes');

const errorClassTests = [
  ['GdexAuthError', GdexAuthError],
  ['GdexValidationError', GdexValidationError],
  ['GdexApiError', GdexApiError],
  ['GdexNetworkError', GdexNetworkError],
  ['GdexRateLimitError', GdexRateLimitError],
];

for (const [name, Cls] of errorClassTests) {
  if (typeof Cls === 'function') {
    try {
      const err = new Cls('test error');
      assert(err instanceof Error, `${name} extends Error`);
      assert(err instanceof Cls, `${name} is instanceof-safe`);
      assert(err.message === 'test error', `${name} preserves message`);
    } catch (err) {
      fail(`${name} constructor threw`, err.message);
    }
  } else {
    fail(`${name} is not exported`);
  }
}

// ─── H. Formatting Utilities ─────────────────────────────────────────────────
section('H. Formatting Utilities');

if (typeof formatUsd === 'function') {
  try {
    const result = formatUsd(1234567.89);
    assert(typeof result === 'string', 'formatUsd() returns a string');
    assert(result.includes('$') || result.includes(',') || result.includes('1234'), `formatUsd(1234567.89) looks reasonable: "${result}"`);
  } catch (err) {
    fail('formatUsd() threw', err.message);
  }
} else {
  fail('formatUsd is not exported');
}

if (typeof formatTokenAmount === 'function') {
  try {
    const result = formatTokenAmount(1.23456789, 'ETH');
    assert(typeof result === 'string', 'formatTokenAmount() returns a string');
    assert(result.includes('1.2') || result.includes('1.23'), `formatTokenAmount(1.23456789, "ETH") is reasonable: "${result}"`);
  } catch (err) {
    fail('formatTokenAmount() threw', err.message);
  }
} else {
  fail('formatTokenAmount is not exported');
}

if (typeof shortenAddress === 'function') {
  try {
    const evmAddr = '0x1234567890123456789012345678901234567890';
    const shortened = shortenAddress(evmAddr);
    assert(typeof shortened === 'string' && shortened.length < evmAddr.length, `shortenAddress() shortens EVM address: "${shortened}"`);
  } catch (err) {
    fail('shortenAddress() threw on EVM address', err.message);
  }

  try {
    const solAddr = 'So11111111111111111111111111111111111111112';
    const shortened = shortenAddress(solAddr);
    assert(typeof shortened === 'string' && shortened.length < solAddr.length, `shortenAddress() shortens Solana address: "${shortened}"`);
  } catch (err) {
    fail('shortenAddress() threw on Solana address', err.message);
  }
} else {
  fail('shortenAddress is not exported');
}

// ─── I. API Client Configuration ─────────────────────────────────────────────
section('I. API Client Configuration');

if (typeof GdexApiClient === 'function') {
  try {
    const client = new GdexApiClient({ baseUrl: 'https://trade-api.gemach.io/v1' });
    pass('new GdexApiClient({ baseUrl }) created without error');

    // Default base URL
    const defaultClient = new GdexApiClient({});
    const url = defaultClient.baseUrl || defaultClient.config?.baseUrl || defaultClient._baseUrl;
    if (url) {
      assert(url === 'https://trade-api.gemach.io/v1', `default baseUrl is "https://trade-api.gemach.io/v1" (got "${url}")`);
    } else {
      pass('GdexApiClient instantiated (baseUrl not directly accessible)');
    }
  } catch (err) {
    fail('GdexApiClient instantiation threw', err.message);
  }
} else {
  fail('GdexApiClient is not exported or not a constructor');
}

if (ENDPOINTS !== undefined) {
  assert(typeof ENDPOINTS === 'object' && ENDPOINTS !== null, 'ENDPOINTS module is exported as an object');
  const endpointKeys = Object.keys(ENDPOINTS);
  assert(endpointKeys.length > 0, `ENDPOINTS has ${endpointKeys.length} endpoint entries`);
} else {
  fail('ENDPOINTS is not exported');
}

// ─── Summary ──────────────────────────────────────────────────────────────────
function printSummary() {
  console.log(`\n${'─'.repeat(50)}`);
  const total = passed + failed;
  console.log(`Results: ${GREEN}${passed} passed${RESET} / ${failed > 0 ? RED : ''}${failed} failed${RESET} / ${total} total`);
  if (failed === 0) {
    console.log(`\n${GREEN}All integration tests passed ✓${RESET}`);
  } else {
    console.log(`\n${RED}Some tests failed — see details above${RESET}`);
  }
}

printSummary();
process.exit(failed > 0 ? 1 : 0);
