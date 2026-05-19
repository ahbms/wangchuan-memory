# API 契约

忘川对外只承诺一个**很小的稳定公开 API**。

本文是 `3.0.0-alpha` 阶段稳定接口的真值来源，也说明哪些内容暂时**不属于**公开兼容性承诺。

## 稳定导入面

### 根包 `wangchuan`

稳定函数式 API：

```python
from wangchuan import remember, recall, recall_raw, recall_scars
from wangchuan import status, healthcheck, task_resume
```

稳定对象 API：

```python
from wangchuan import Memory
```

稳定辅助 API：

```python
from wangchuan import paths
```

### Facade 模块 `wangchuan.facade`

稳定 facade API：

```python
from wangchuan.facade import version, health, capabilities, invoke
```

### CLI 契约

稳定 CLI 入口：

```bash
python3 -m wangchuan
```

稳定面向用户命令：

```bash
python3 -m wangchuan status --json
python3 -m wangchuan healthcheck --json
python3 -m wangchuan remember "..." --json
python3 -m wangchuan recall "..." --json
python3 -m wangchuan recall-raw "..." --json
python3 -m wangchuan recall-scars "..." --json
python3 -m wangchuan paths --json
python3 -m wangchuan facade-version --json
python3 -m wangchuan facade-health --json
python3 -m wangchuan facade-capabilities --json
```

## 行为契约

### `remember(...)`

- 写入一条记忆
- 返回 dict payload
- 核心使用不要求外部 LLM 配置

### `recall(...)`

- 返回 list
- 空结果默认返回 `[]`，不抛异常
- 可用时结果应携带 `recall_explain`

### `recall_raw(...)`

- 面向原始证据/原话召回
- 返回 list

### `recall_scars(...)`

- 面向规则 / 教训 / 伤疤类召回
- 返回 list

### `status()`

- 返回 dict
- 通过 `message` 暴露用户可读状态摘要
- 通过 `foundation` 与 `migration_status` 暴露 migration/schema 可见性

### `healthcheck()`

- 返回 dict
- 暴露整体 `status`、`passed`、`total`
- 包含 schema version 可见性检查

### `task_resume()`

- 返回 dict；可用时描述可恢复任务上下文

### `Memory`

- 稳定的 class-based 使用入口
- `Memory().remember(...)`、`Memory().recall(...)`、`Memory().status()` 属于稳定面

### `WangchuanPipeline`

- 当前可能因兼容性仍可导入，但视为**内部实现承载层**，不是新的外部集成默认入口
- 新外部集成应优先使用 `Memory`、根包函数 API 与 `wangchuan.facade`

### `facade.version()`

- 返回稳定版本字符串

### `facade.health()`

- 返回 `LayerHealth`

### `facade.capabilities()`

- 返回 `LayerCapability`

### `facade.invoke()`

- 返回 `LayerResponse`
- 不支持的 operation 必须返回结构化错误，错误码为 `unsupported_operation`

## Stable / Preview / Internal 边界

### Stable

可放心用于外部集成：

- 上文列出的 `wangchuan` 根包导出
- `wangchuan.facade.version/health/capabilities/invoke`
- 上文列出的稳定 CLI 命令
- README 中记录的稳定 MCP tool 名称

### Preview

可用，但 beta/stable 前仍可能变化：

- 高级维护 / 修复 CLI 子命令
- optional extras 行为细节（`[mcp]`、`[llm]`、`[crypto]`）
- `status` 里超出最低承诺的丰富结构化字段
- 未列入根包稳定导入面的兼容可见 alias

### Internal

默认不要让外部集成直接依赖：

- `wangchuan.v3.*`
- `wangchuan.memory_api`
- `wangchuan.recall_service`
- `wangchuan.runtime_state`
- `WangchuanPipeline`
- `memory_*`、`_protocol/*`、`_adapters/*` 等实现辅助模块

这些模块在被明确提升为 stable 前，可能移动、拆分或改变结构。

## 兼容性承诺

对本文列出的 stable surface：

- 兼容 minor 版本内保持根导入可用
- `wangchuan.facade` 的文档化函数保持导入兼容
- CLI 命令名保持稳定，或按弃用策略迁移
- breaking change 必须写入 `CHANGELOG.md`

## 非目标

本文不承诺：

- 内部 SQLite schema 细节稳定，除非另有操作性保证
- 未文档化 payload 字段稳定
- 内部排序启发式或 sidecar 实现结构稳定
- `wangchuan.v3.*` 下内部模块路径稳定
