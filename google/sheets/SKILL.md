---
name: sheets
display-name: "Google Sheets"
description: "Read and write Google Sheets data, create spreadsheets, manage worksheets, and apply formatting. Supports batch reads, cell updates, and formula operations with OAuth authentication."
---

# Google Sheets

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- read google sheet
- write to google sheet
- create google spreadsheet
- update sheet cells
- get sheet data

## Workflow Summary

1. `read_range` uses `connector.sheets.get`
2. `write_range` uses `connector.sheets.post`
3. `create_spreadsheet` uses `connector.sheets.post`
