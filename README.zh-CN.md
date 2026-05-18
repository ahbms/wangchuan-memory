# 忘川（WangChuan）

中文 | [English](./README.md)

**面向 AI Agent 的证据感知记忆引擎。**

忘川帮助 Agent 记住真正重要的内容，在召回时保留证据边界，并尽量解释"为什么这条记忆会被命中"。
它适合那些不满足于"纯向量库 + 原始对话堆积"的 Agent 系统。

> **当前状态：** `3.0.0 alpha`
>
> 忘川已经达到**可公开试用**的阶段。核心 remember → recall → inspect 闭环可以跑通。
> 但还不是"零引导即可生产落地"的成熟产品。

---

## 为什么是忘川

很多 Agent 记忆系统最后会落到下面几种形态：

- 原始 transcript 日志
- 语义搜索外壳
- 图存储，但边界不够清晰
- "能记忆"，但召回错了很难解释为什么

忘川想做的是另一种取舍：

- **证据感知召回** — 原始证据、结构化记忆、规则/伤疤类记忆分开处理
- **可检查行为** — 召回排序、惩罚、解释字段都可以查看
- **运维边界明确** — healthcheck、恢复、数据库单文件备份
- **公开面保守** — 先给稳定入口，不承诺零散内部接口

---

## 快速开始

### 安装

```bash
pip install wangchuan-memory
```

或从源码安装：

```bash
git clone <仓库地址>
cd wangchuan-memory
pip install -e .
```

### 试 Python API

```python
from wangchuan import remember, recall, status

remember("用户偏好简洁的回复", importance=0.9, tags=["preference"])
print(recall("应该怎么回复？", limit=3))
print(status())
```

### 试 CLI

```bash
python3 -m wangchuan status --json
python3 -m wangchuan recall "用户偏好" --limit 3 --json
```

---

## 核心 API

```python
from wangchuan import (
    Memory,            # 基于类的 API
    remember,          # 函数式：写入记忆
    recall,            # 函数式：混合结构化召回
    recall_raw,        # 函数式：原始证据召回
    recall_scars,      # 函数式：规则/教训类召回
    status,            # 函数式：系统状态
    healthcheck,       # 函数式：健康检查
)
```

### 类 API

```python
from wangchuan import Memory

m = Memory()
m.remember("用户住在石家庄", importance=0.6)
results = m.recall("用户位置", limit=5)
```

每条结果包含：

| 字段 | 说明 |
|------|------|
| `content` | 记忆内容 |
| `score` | 排序分数 |
| `memory_type` | 记忆类型（`preference` / `rule` / `fact` 等）|
| `recall_explain` | 召回解释（为什么命中了这条）|
| `created_at` | 创建时间 |

---

## 使用方式

### Python 包

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
python3 -m wangchuan recall "用户偏好" --limit 3 --json
python3 -m wangchuan facade-version --json
```

### MCP Server

```bash
pip install wangchuan-memory[mcp]
python3 -m wangchuan.mcp_server
```

稳定 MCP 工具名：`memory_write`, `memory_search`, `memory_search_raw`, `memory_search_scars`, `memory_status`, `memory_healthcheck`

---

## 工作原理

忘川使用本地 SQLite 数据库存储记忆，包含：

1. **图谱知识库** — 节点（事实/任务/技能/事件）+ 边（关系）
2. **温度分层生命周期** — 热点/渐冷/沉睡记忆状态管理
3. **证据边界** — 原始证据、结构化记忆、规则记忆分开存储
4. **可解释召回** — 每次召回附带 `recall_explain` 字段，包含排名分解

**无需外部服务。核心功能不依赖 LLM。**
可选的 LLM 三元组提取通过 `pip install wangchuan-memory[llm]` 启用。

---

## 安装档位

| 档位 | 命令 | 附加内容 |
|------|------|---------|
| 基础 | `pip install wangchuan-memory` | 核心引擎 + CLI |
| LLM | `wangchuan-memory[llm]` | OpenAI + Anthropic SDK |
| MCP | `wangchuan-memory[mcp]` | MCP Server 支持 |
| 加密 | `wangchuan-memory[crypto]` | 加密辅助 |
| 开发 | `wangchuan-memory[dev]` | pytest |
| 全量 | `wangchuan-memory[full]` | 全部 |

---

## License

MIT License。见 `LICENSE`。
