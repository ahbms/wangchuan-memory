# WangChuan v3.0.0-alpha release note

WangChuan is an evidence-aware memory engine for AI agents.

## Current status

This release is a public alpha:

- suitable for source review and early trial use
- not yet guaranteed as production-stable
- stable public surface is intentionally small

## Stable alpha surface

- Python package: `wangchuan`
- CLI: `python3 -m wangchuan`
- Core API: `Memory`, `remember`, `recall`, `recall_raw`, `recall_scars`, `status`, `healthcheck`
- Optional MCP server: `python3 -m wangchuan.mcp_server`

## Verified before release

- no `.env`, runtime database, cache, or compiled files
- no known API key or token signatures
- no machine-local absolute paths
- no old Tiangong package public references
- first-run empty directory initialization works
- `remember → recall` smoke test works
- `python3 -m wangchuan status --json` works
- `scripts/release_check.py` passes

## Known alpha boundaries

- `wangchuan.v3.*` is an implementation carrier, not the recommended stable import surface
- L4/L5/L6 Tiangong integrations are optional and degrade to standalone stubs
- Web API remains preview/local-only
- More pytest coverage and external trial feedback are required before beta/stable
