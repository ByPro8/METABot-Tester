# Meta Templates (metadata-family rules)

This folder stores **bank "families/templates"** for metadata forensics.

## Why this exists
MetaBot Lab is a sandbox to **perfect metadata templates + checking rules** and then copy the minimal parts into another project.

## Template philosophy (v1)
A template is:
1) A **strict keyset** of meaningful ExifTool keys (after ignoring noisy keys like filesystem timestamps).
2) A set of **expected exact values** for those keys.

If a PDF has:
- extra meaningful keys → alarm (likely different generator / edited / repacked)
- missing meaningful keys → alarm
- value mismatch → alarm

## ExifTool key naming
Keys are stored as:
`<Group>.<Tag>`

Example:
- `File.FileType`
- `PDF.Producer`

## Ignored items (v1)
We ignore:
- whole groups: `ExifTool`, `File:System`
- tags: `CreateDate`, `ModifyDate`, `CreationDate`, `MetadataDate`

Later we’ll add **timestamp rules** (instead of ignoring dates).

## How to port to another project
Copy:
- `meta_templates/*`
- `tools/template_check.py`
and call `run_template_check()` on extracted ExifTool metadata.
