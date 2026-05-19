# FAQ

## 忘川和向量数据库有什么区别？

忘川不是单纯的向量检索外壳。
它强调三件事：
- 原始证据、结构化记忆、规则/伤疤分层
- recall 结果尽量可解释
- 有运行/发布边界，例如 healthcheck、release check、备份恢复

## 数据存在哪里？

默认在：

```text
$WANGCHUAN_HOME/.index/index.sqlite
```

详见 [`STORAGE.md`](./STORAGE.md)。

## 如何改数据库路径？

最稳的方式是设置 `WANGCHUAN_HOME`，让 WangChuan 在目标目录下创建 `.index/index.sqlite`。

## 是否需要 LLM？

不需要，也可以说：核心功能**无需 LLM**。
核心 remember / recall / status / healthcheck 可以在**无 LLM 配置**下运行。

## 为什么 recall 返回空？

常见原因：
- 你还没有写入相关记忆
- 查询词和已写入内容差太远
- 你查的是规则类内容，但应该用 `recall_scars`
- 你期待原话，但应该用 `recall_raw`

## 什么是 recall_raw / recall_scars？

- `recall_raw`：偏原始证据/原话
- `recall_scars`：偏规则、教训、伤疤类记忆
- `recall`：综合召回入口

## MCP 怎么配置？

安装：

```bash
pip install 'wangchuan-memory[mcp]'
```

启动：

```bash
python3 -m wangchuan.mcp_server
```

详见 [`MCP.md`](./MCP.md)。

## 可以生产用吗？

当前是 `3.0.0-alpha`。
适合公开 alpha 试用、内部集成验证、源代码评估。
还不应宣称为“零引导即可生产落地”的 fully stable 版本。

## 怎么备份？

直接备份 SQLite 文件：

```bash
cp .index/index.sqlite /path/to/backup/index.sqlite
```

恢复后再跑：

```bash
python3 -m wangchuan healthcheck --json
```
