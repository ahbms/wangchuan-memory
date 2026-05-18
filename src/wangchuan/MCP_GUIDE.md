# WangChuan MCP Server Guide

MCP support is optional and not installed with the base package.

## Install

```bash
pip install 'wangchuan-memory[mcp]'
```

## Run

```bash
python3 -m wangchuan.mcp_server
```

## Stable tool names

- `memory_write`
- `memory_search`
- `memory_search_raw`
- `memory_search_scars`
- `memory_status`
- `memory_healthcheck`
- `memory_recent`
- `memory_search_by_tag`

Advanced tools may be enabled with explicit profiles, but the stable profile above is the public alpha surface.
