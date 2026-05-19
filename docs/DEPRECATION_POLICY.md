# 弃用策略

本文定义忘川如何调整**稳定公开接口**。

## 适用范围

本策略适用于：

- `wangchuan` 根包中的 stable imports
- `wangchuan.facade` 中已文档化函数
- 已文档化稳定 CLI 命令
- 已文档化稳定 MCP tool 名称

本策略不适用于内部实现路径，例如：

- `wangchuan.v3.*`
- `wangchuan.memory_api`
- `wangchuan.recall_service`
- `wangchuan.runtime_state`
- 未文档化 helper 模块

## 当前发布阶段

当前阶段：`3.0.0-alpha`

alpha 阶段：

- internal implementation 仍可能自由调整
- preview 功能仍可能变化或删除
- stable documented surface 已应保守对待

## Beta 及以后规则

从 `3.0.0-beta` 开始：

- stable public API 不应在未弃用的情况下直接删除
- stable API 在发出弃用通知后至少保留一个 minor version
- stable CLI 命令在弃用窗口内应保留 alias 或迁移说明

## 如何传达弃用

一次弃用必须包含：

1. `CHANGELOG.md` 条目
2. 如需用户操作，在文档中提供简短迁移说明
3. 可行时，对直接使用的 deprecated stable entry 给出 runtime warning

## Breaking change

对 stable public surface 的 breaking change 必须：

- 在 `CHANGELOG.md` 中明确说明
- 给出替代路径或迁移说明
- 不得作为未文档化行为变化静默发布

## 内部模块

内部模块在被提升进 API contract 前，可以不另行通知地调整。

外部用户应优先依赖：

- `docs/API_CONTRACT.md`

## 实用规则

如果一个 symbol / path / command 没有列在 `docs/API_CONTRACT.md` 中，就按以下之一处理：

- preview
- internal
- 不承诺长期外部依赖
