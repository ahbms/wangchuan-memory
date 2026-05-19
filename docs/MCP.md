# MCP

忘川 MCP 支持是 optional，不随基础包默认安装。

## 安装

```bash
pip install 'wangchuan-memory[mcp]'
```

## 运行

```bash
python3 -m wangchuan.mcp_server
```

## 稳定 tool 名称

- `memory_write`
- `memory_search`
- `memory_search_raw`
- `memory_search_scars`
- `memory_status`
- `memory_healthcheck`
- `memory_recent`
- `memory_search_by_tag`

## 说明

- 基础包不依赖 MCP
- MCP 支持通过 optional install profile 启用
- 稳定 tool 兼容范围遵循 [`API_CONTRACT.md`](./API_CONTRACT.md)
