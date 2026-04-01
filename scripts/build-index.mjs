#!/usr/bin/env node
// ABOUTME: Builds a skills index JSON from all SKILL.md files in the repo.
// ABOUTME: Output is uploaded to Cloudflare R2 by the build-skills-index workflow.

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const REPO_ROOT = new URL("..", import.meta.url).pathname;
const RAW_BASE = "https://raw.githubusercontent.com/serenorg/seren-skills/main";

function walkDir(dir) {
  const results = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const fullPath = join(dir, entry.name);
    if (entry.name.startsWith(".")) continue;
    if (entry.isDirectory()) {
      results.push(...walkDir(fullPath));
    } else {
      results.push(fullPath);
    }
  }
  return results;
}

function parseFrontmatter(content) {
  const trimmed = content.trim();
  if (!trimmed.startsWith("---")) return {};
  const endIndex = trimmed.indexOf("---", 3);
  if (endIndex === -1) return {};
  const yaml = trimmed.slice(3, endIndex).trim();
  const result = {};
  for (const line of yaml.split("\n")) {
    const colonIndex = line.indexOf(":");
    if (colonIndex === -1) continue;
    const key = line.slice(0, colonIndex).trim();
    let value = line.slice(colonIndex + 1).trim();
    // Strip surrounding quotes
    if ((value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    }
    result[key] = value;
  }
  return result;
}

function parseTags(fm) {
  if (!fm.tags) return [];
  // tags can be comma-separated or YAML array
  if (fm.tags.startsWith("[")) {
    return fm.tags.slice(1, -1).split(",").map(t => t.trim().replace(/^["']|["']$/g, "")).filter(Boolean);
  }
  return fm.tags.split(",").map(t => t.trim()).filter(Boolean);
}

const allFiles = walkDir(REPO_ROOT);
const skillFiles = allFiles.filter(f => f.endsWith("/SKILL.md"));

const skills = [];
const tree = [];

// Build tree listing (all files, relative paths)
for (const f of allFiles) {
  const rel = relative(REPO_ROOT, f);
  if (!rel.startsWith(".") && !rel.startsWith("node_modules")) {
    tree.push(rel);
  }
}

for (const skillPath of skillFiles) {
  const rel = relative(REPO_ROOT, skillPath);
  const parts = rel.split("/");
  if (parts.length !== 3) continue; // expect org/skill/SKILL.md

  const org = parts[0];
  const skillName = parts[1];
  const slug = `${org}-${skillName}`.toLowerCase();

  const content = readFileSync(skillPath, "utf-8");
  const fm = parseFrontmatter(content);

  const sourceUrl = `${RAW_BASE}/${encodeURIComponent(org)}/${encodeURIComponent(skillName)}/SKILL.md`;

  skills.push({
    slug,
    name: fm.name || skillName,
    description: fm.description || "",
    source: "serenorg",
    sourceUrl,
    tags: parseTags(fm),
    author: fm.author,
    version: fm.version,
  });
}

skills.sort((a, b) => a.name.localeCompare(b.name));

const index = {
  version: "1",
  updatedAt: new Date().toISOString(),
  skills,
  tree: tree.sort(),
};

process.stdout.write(JSON.stringify(index));
