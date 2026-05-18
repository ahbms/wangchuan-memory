# WangChuan

[中文说明](./README.zh-CN.md) | English

**Evidence-aware memory engine for AI agents.**

WangChuan helps agents remember the right things, recall them with evidence boundaries, and explain why a memory was surfaced.
It is designed for teams building agent systems that need more than a flat vector store or a raw conversation log.

> **Status:** `3.0.0 alpha`
>
> WangChuan is ready for public alpha use.
> It is not yet "fully polished, zero-guidance production software", but the core remember → recall → inspect loop works.

---

## Why WangChuan

Most agent memory systems give you one of these:

- a raw transcript log
- a semantic search wrapper
- a graph store with weak external boundaries
- a memory abstraction that is hard to inspect when retrieval goes wrong

WangChuan focuses on a different tradeoff:

- **Evidence-aware recall** — keep raw evidence, structured memory, and rule-like memory distinct
- **Inspectable behavior** — explain why a memory ranked high or got penalized
- **Operational boundaries** — healthcheck, restore drill, clean gate, deployment templates
- **Conservative public surface** — small stable entry points first, internals second

---

## Quick start

### Install

```bash
pip install wangchuan-memory
```

Or from source:

```bash
git clone <repo-url>
cd wangchuan-memory
pip install -e .
```

### Try the Python API

```python
from wangchuan import remember, recall, status

remember("User prefers concise, segmented replies.", importance=0.9, tags=["preference"])
print(recall("How should I respond?", limit=3))
print(status())
```

### Try the CLI

```bash
python3 -m wangchuan status --json
python3 -m wangchuan recall "user preferences" --limit 3 --json
```

---

## Core API

```python
from wangchuan import (
    Memory,            # class-based API
    remember,          # functional: write a memory
    recall,            # functional: mixed structured recall
    recall_raw,        # functional: raw evidence recall
    recall_scars,      # functional: rules/lessons recall
    status,            # functional: system status
    healthcheck,       # functional: health check
)
```

### Class API

```python
from wangchuan import Memory

m = Memory()
m.remember("User lives in Shijiazhuang", importance=0.6)
results = m.recall("user location", limit=5)
```

Each result is a dict with:

| Field | Description |
|-------|-------------|
| `content` | Memory content text |
| `score` | Rank score |
| `memory_type` | `preference` / `rule` / `fact` / etc. |
| `recall_explain` | Why this memory was retrieved |
| `created_at` | Timestamp |

---

## Choose your surface

### Python package

```python
from wangchuan import (
    remember, recall, recall_raw, recall_scars,
    status, healthcheck, task_resume,
)
from wangchuan.facade import invoke, health, capabilities, version
```

### CLI

```bash
python3 -m wangchuan status
python3 -m wangchuan healthcheck --json
python3 -m wangchuan recall "user preferences" --limit 3 --json
python3 -m wangchuan recall-raw "exact wording" --limit 3 --json
python3 -m wangchuan recall-scars "rules and lessons" --limit 3 --json
python3 -m wangchuan facade-version --json
python3 -m wangchuan facade-health --json
```

### MCP server

```bash
pip install wangchuan-memory[mcp]
python3 -m wangchuan.mcp_server
```

Stable MCP tools: `memory_write`, `memory_search`, `memory_search_raw`, `memory_search_scars`, `memory_status`, `memory_healthcheck`

---

## How it works

WangChuan stores memories in a local SQLite database with:

1. **Graph-based knowledge store** — nodes (facts, tasks, skills, events) + edges (relationships)
2. **Temperature-based lifecycle** — hot/stale/dormant memory states
3. **Evidence boundary** — raw evidence, structured memory, and rule memory are kept separate
4. **Explainable recall** — every recall returns a `recall_explain` field with ranking breakdown

No external services required. No LLM dependency for core functionality.
Optional LLM-powered triple extraction is available via `pip install wangchuan-memory[llm]`.

---

## Installation profiles

| Profile | Command | Adds |
|---------|---------|------|
| Base | `pip install wangchuan-memory` | Core engine + CLI |
| LLM | `wangchuan-memory[llm]` | OpenAI + Anthropic SDKs |
| MCP | `wangchuan-memory[mcp]` | MCP server support |
| Crypto | `wangchuan-memory[crypto]` | Encryption helpers |
| Dev | `wangchuan-memory[dev]` | pytest |
| Full | `wangchuan-memory[full]` | Everything above |

---

## Reliability

- **Healthcheck**: `python3 -m wangchuan healthcheck --json`
- **Backup**: SQLite database is a single file, easy to backup
- **Restore**: Copy the database file back, healthcheck to verify

---

## Contributing

WangChuan is in active development. Useful feedback includes:

- A minimal reproduction query
- What memory you expected vs. what you got
- Whether the issue is in remember / recall / explain / boundaries

---

## License

MIT License. See `LICENSE`.
