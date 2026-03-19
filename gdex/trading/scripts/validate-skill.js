#!/usr/bin/env node
/**
 * Validate SKILL.md frontmatter against seren-skills spec rules.
 * Run: node scripts/validate-skill.js
 *
 * Spec rules:
 *   - Required: name, description
 *   - name: 1-64 chars, lowercase letters/digits/hyphens, no leading/trailing
 *     hyphen, no consecutive hyphens, must match parent directory name
 *   - description: non-empty, <= 1024 chars
 *   - Optional: license, compatibility, metadata, allowed-tools
 *   - metadata must be string key/value pairs only
 */

'use strict';

const fs = require('fs');
const path = require('path');

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const YELLOW = '\x1b[33m';
const RESET = '\x1b[0m';

let passed = 0;
let failed = 0;
const errors = [];

function pass(label) {
  console.log(`${GREEN}✓${RESET} ${label}`);
  passed++;
}

function fail(label, detail) {
  const msg = detail ? `${label}: ${detail}` : label;
  console.log(`${RED}✗${RESET} ${msg}`);
  errors.push(msg);
  failed++;
}

function warn(label) {
  console.log(`${YELLOW}⚠${RESET} ${label}`);
}

// ─── Locate SKILL.md ──────────────────────────────────────────────────────────
// This script lives in scripts/, so SKILL.md is one level up
const SKILL_ROOT = path.resolve(__dirname, '..');
const SKILL_MD = path.join(SKILL_ROOT, 'SKILL.md');
const PARENT_DIR = path.basename(SKILL_ROOT);

console.log(`Validating SKILL.md in: ${SKILL_ROOT}\n`);

if (!fs.existsSync(SKILL_MD)) {
  fail('SKILL.md', 'file does not exist');
  process.exit(1);
}

const content = fs.readFileSync(SKILL_MD, 'utf8');

// ─── Extract YAML frontmatter ─────────────────────────────────────────────────
const fmMatch = content.match(/^---\r?\n([\s\S]*?)\r?\n---/);
if (!fmMatch) {
  fail('Frontmatter', 'SKILL.md must start with --- YAML frontmatter ---');
  process.exit(1);
}
pass('SKILL.md has --- frontmatter delimiters');

// Minimal YAML line parser for seren-skills SKILL.md frontmatter.
// Handles: bare scalars, single-quoted, and double-quoted values (with \\-escaped
// sequences). Does NOT support multi-line scalars or complex YAML structures;
// those are outside the scope of this spec validator.
function parseSimpleYaml(raw) {
  const result = {};
  let currentKey = null;
  const lines = raw.split('\n');
  for (const line of lines) {
    // Skip blank/comment lines
    if (!line.trim() || line.trim().startsWith('#')) continue;

    // Indented lines — sub-keys under the current mapping key
    if (/^\s+/.test(line) && currentKey) {
      const sub = parseKeyValue(line.trim());
      if (sub) {
        if (typeof result[currentKey] !== 'object' || result[currentKey] === null) {
          result[currentKey] = {};
        }
        result[currentKey][sub.key] = sub.value;
      }
      continue;
    }

    // Top-level key: value
    const kv = parseKeyValue(line);
    if (kv) {
      result[kv.key] = kv.value;
      currentKey = kv.key;
    }
  }
  return result;
}

// Parse a single "key: value" line.  Returns { key, value } or null.
// Handles bare scalars, "double-quoted", and 'single-quoted' values.
function parseKeyValue(line) {
  // Key portion: word chars and hyphens
  const keyMatch = line.match(/^([\w][\w-]*):\s*/);
  if (!keyMatch) return null;
  const key = keyMatch[1];
  const rest = line.slice(keyMatch[0].length);

  let value = '';
  if (rest.startsWith('"')) {
    // Double-quoted: consume until the closing unescaped "
    const inner = rest.slice(1);
    value = inner.replace(/\\(["\\nrt])/g, (_, c) => ({ '"': '"', '\\': '\\', n: '\n', r: '\r', t: '\t' }[c] || c));
    const closeIdx = findClosingQuote(inner, '"');
    value = closeIdx === -1 ? inner : inner.slice(0, closeIdx);
    // Unescape standard backslash sequences
    value = value.replace(/\\(["\\nrt])/g, (_, c) => ({ '"': '"', '\\': '\\', n: '\n', r: '\r', t: '\t' }[c] || c));
  } else if (rest.startsWith("'")) {
    // Single-quoted: '' is an escaped single quote inside
    const inner = rest.slice(1);
    const closeIdx = findClosingQuote(inner, "'");
    value = (closeIdx === -1 ? inner : inner.slice(0, closeIdx)).replace(/''/g, "'");
  } else {
    // Bare scalar — strip inline comment
    value = rest.replace(/\s+#.*$/, '').trim();
  }

  return { key, value };
}

// Find the index of the first unescaped closing quote character in str.
function findClosingQuote(str, quoteChar) {
  for (let i = 0; i < str.length; i++) {
    if (str[i] === '\\' && quoteChar === '"') { i++; continue; }
    if (str[i] === quoteChar) return i;
  }
  return -1;
}

const fm = parseSimpleYaml(fmMatch[1]);

// ─── Required field: name ─────────────────────────────────────────────────────
console.log('\n── name ──');
const name = fm.name;

if (!name || typeof name !== 'string' || name.length === 0) {
  fail('name', 'required field is missing or empty');
} else {
  pass(`name is present: "${name}"`);

  // Must match parent directory name
  if (name === PARENT_DIR) {
    pass(`name "${name}" matches parent directory "${PARENT_DIR}"`);
  } else {
    fail('name vs directory', `name "${name}" must match parent directory name "${PARENT_DIR}"`);
  }

  // Length 1-64
  if (name.length >= 1 && name.length <= 64) {
    pass(`name length ${name.length} is within 1-64 chars`);
  } else {
    fail('name length', `${name.length} chars — must be 1-64`);
  }

  // Charset: lowercase letters, digits, hyphens only
  if (/^[a-z0-9-]+$/.test(name)) {
    pass('name uses only lowercase letters, digits, and hyphens');
  } else {
    fail('name charset', `"${name}" contains invalid characters — only a-z, 0-9, - allowed`);
  }

  // No leading hyphen
  if (!name.startsWith('-')) {
    pass('name does not start with a hyphen');
  } else {
    fail('name leading hyphen', `"${name}" must not start with a hyphen`);
  }

  // No trailing hyphen
  if (!name.endsWith('-')) {
    pass('name does not end with a hyphen');
  } else {
    fail('name trailing hyphen', `"${name}" must not end with a hyphen`);
  }

  // No consecutive hyphens
  if (!name.includes('--')) {
    pass('name has no consecutive hyphens');
  } else {
    fail('name consecutive hyphens', `"${name}" must not contain consecutive hyphens`);
  }
}

// ─── Required field: description ─────────────────────────────────────────────
console.log('\n── description ──');
const desc = fm.description;

if (!desc || typeof desc !== 'string' || desc.length === 0) {
  fail('description', 'required field is missing or empty');
} else {
  pass('description is present and non-empty');

  if (desc.length <= 1024) {
    pass(`description length ${desc.length} is <= 1024 chars`);
  } else {
    fail('description length', `${desc.length} chars — must be <= 1024`);
  }
}

// ─── Optional fields ──────────────────────────────────────────────────────────
console.log('\n── optional fields ──');
const KNOWN_OPTIONAL = ['license', 'compatibility', 'metadata', 'allowed-tools'];
const KNOWN_FIELDS = ['name', 'description', ...KNOWN_OPTIONAL];

const unknownFields = Object.keys(fm).filter((k) => !KNOWN_FIELDS.includes(k));
if (unknownFields.length > 0) {
  warn(`Unknown frontmatter fields (not in spec): ${unknownFields.join(', ')}`);
} else {
  pass('All frontmatter fields are spec-defined');
}

// metadata must be string key/value pairs
if (fm.metadata !== undefined) {
  if (typeof fm.metadata === 'object' && fm.metadata !== null) {
    const nonStringValues = Object.entries(fm.metadata).filter(([, v]) => typeof v !== 'string');
    if (nonStringValues.length === 0) {
      pass('metadata values are all strings');
    } else {
      fail('metadata', `non-string values: ${nonStringValues.map(([k]) => k).join(', ')}`);
    }
  } else {
    fail('metadata', 'must be a mapping of string keys to string values');
  }
} else {
  pass('metadata field is absent (optional)');
}

// ─── Body: H1 heading ─────────────────────────────────────────────────────────
console.log('\n── document body ──');
const bodyStart = content.indexOf('---', 3) + 3;
const body = content.slice(bodyStart);
const h1 = body.match(/^# .+/m);
if (h1) {
  pass(`H1 heading found: "${h1[0].trim()}"`);
} else {
  warn('No H1 heading found — spec recommends using the first H1 as display name');
}

// ─── Summary ──────────────────────────────────────────────────────────────────
console.log(`\n${'─'.repeat(50)}`);
console.log(`${GREEN}Passed: ${passed}${RESET}  ${failed > 0 ? RED : ''}Failed: ${failed}${RESET}`);

if (failed > 0) {
  console.log(`\n${RED}Validation errors:${RESET}`);
  for (const e of errors) console.log(`  • ${e}`);
  process.exit(1);
} else {
  console.log(`\n${GREEN}SKILL.md is valid ✓${RESET}`);
}
