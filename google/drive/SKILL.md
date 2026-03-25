---
name: google-drive
description: "Create, read, update, and manage files and folders in Google Drive. Upload documents, organize folder structures, search files, and manage sharing permissions with OAuth authentication."
---

# Google Drive

## For Claude: How to Use This Skill

Skill instructions are preloaded in context when this skill is active. Do not perform filesystem searches or tool-driven exploration to rediscover them; use the guidance below directly.

## When to Use

- upload file to google drive
- list google drive files
- search google drive
- create google drive folder
- share google drive file

## Workflow Summary

1. `list_files` uses `connector.drive.get`
2. `search_files` uses `connector.drive.get`
3. `create_folder` uses `connector.drive.post`
4. `upload_file` uses `connector.drive.post`
