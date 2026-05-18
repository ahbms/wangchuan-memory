# Contributing

Thanks for trying WangChuan.

The most useful issue reports include:

1. The exact memory you wrote
2. The query you used
3. The memory you expected
4. The memory you got instead
5. Whether the issue is in write, recall, explain, CLI, or MCP

Before opening a PR, run:

```bash
python scripts/release_check.py
pytest -q
```
