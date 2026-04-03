---
name: google-trends
display-name: "Google Trends"
description: "Access real-time Google Trends data including interest over time, regional breakdowns, related topics and queries. Supports up to 5 keywords per search, geographic filtering, date ranges, and search property filters."
---

# Google Trends

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- search google trends
- compare trend keywords
- get trending topics
- analyze search interest

## Workflow Summary

1. `interest_over_time` uses `connector.trends.get`
2. `regional_interest` uses `connector.trends.get`
3. `related_queries` uses `connector.trends.get`
