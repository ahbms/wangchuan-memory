# 架构说明

忘川是面向 AI Agent 的证据感知记忆引擎。

## 公开使用面

稳定公开入口：

- Python 根包：`wangchuan`
- CLI：`python3 -m wangchuan`
- 消费者 facade：`wangchuan.facade`

详细边界见 [`API_CONTRACT.md`](./API_CONTRACT.md)。

## 高层分层

1. **公开 facade**
   - `wangchuan`
   - `wangchuan.facade`
   - `wangchuan.__main__` 中的公开 CLI

2. **运行期记忆 API**
   - `memory_api.py`
   - `memory_diagnostics.py`
   - migration helpers
   - write / read / health / status 主流程

3. **内部实现承载层**
   - `wangchuan.v3.*`
   - 内部 retrieval / ingest / graph / vector / runtime helpers

4. **兼容层与适配器**
   - `wangchuan.compat`
   - `wangchuan.runtime_state`
   - `_adapters/*`

## 存储模型

- 本地 SQLite 运行时数据库
- 通过 `schema_version` 与 `meta` 跟踪 migration version
- 非 editable 安装必须打包运行时 schema 资源

## 设计目标

- 外部 API 小而稳定
- recall 可解释
- 核心能力 standalone-first
- 可选集成缺失时安全降级
