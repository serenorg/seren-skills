---
name: microsoft-sharepoint
display-name: "Microsoft SharePoint"
description: "Manage SharePoint sites, document libraries, files, folders, and lists via Microsoft Graph API. Browse sites, upload and download files, create folders, copy files, and search across sites with OAuth2 authentication."
---

# Microsoft Sharepoint

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- upload file to sharepoint
- list sharepoint sites
- search sharepoint documents
- create sharepoint folder
- read sharepoint list

## Workflow Summary

1. `list_sites` uses `connector.sharepoint.get`
2. `browse_drive` uses `connector.sharepoint.get`
3. `upload_file` uses `connector.sharepoint.post`
4. `search` uses `connector.sharepoint.get`
