# WangChuan v3 implementation layer

This directory contains the current implementation carrier for WangChuan internals.

For public alpha users, prefer:

- Python API: `from wangchuan import Memory, remember, recall`
- CLI: `python3 -m wangchuan ...`
- MCP: `python3 -m wangchuan.mcp_server` with the `mcp` extra installed

`wangchuan.v3.*` is not the recommended stable import surface. It is kept for internal structure, compatibility, and advanced debugging.
