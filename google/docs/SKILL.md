---
name: google-docs
display-name: "Google Docs"
description: "Create, read, and update Google Docs documents. Supports document creation, content insertion, formatting, and batch updates with OAuth authentication."
---

# Google Docs

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- create google doc
- read google doc
- update google doc
- insert text into document
- format google doc

## Workflow Summary

1. `get_document` uses `connector.docs.get`
2. `create_document` uses `connector.docs.post`
3. `batch_update` uses `connector.docs.post`
