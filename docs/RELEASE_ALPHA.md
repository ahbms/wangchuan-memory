# WangChuan v3.0.0-alpha 发布说明

忘川是面向 AI Agent 的证据感知记忆引擎。

## 当前状态

这个版本是公开 alpha / beta-candidate 基线：

- 适合源码审阅与早期试用
- 适合内部集成与外部 alpha 试用
- 还不保证为 mature stable production software
- 稳定公开面很小，并已文档化

## 稳定 alpha 使用面

- Python 包：`wangchuan`
- CLI：`python3 -m wangchuan`
- 核心 API：`Memory`、`remember`、`recall`、`recall_raw`、`recall_scars`、`status`、`healthcheck`、`task_resume`
- 消费者 facade：`wangchuan.facade.version/health/capabilities/invoke`
- 可选 MCP server：`python3 -m wangchuan.mcp_server`

参见 [`API_CONTRACT.md`](./API_CONTRACT.md) 与 [`DEPRECATION_POLICY.md`](./DEPRECATION_POLICY.md)。

## 本地发布前已验证

- 干净树下 `scripts/release_check.py` 返回 `OVERALL PASS`
- `pytest -q` 通过超过 20 个测试
- 首次运行空目录初始化可用
- `remember → recall` smoke 可用
- `python3 -m wangchuan status --json` 可用
- `python -m build` 可生成 wheel 与 sdist
- wheel install smoke 可在全新 venv 中通过
- 打包产物包含 `wangchuan/v3/schema.sql`
- optional extras `[mcp]`、`[llm]`、`[crypto]` 可独立安装
- 本地 `pip-audit` 未发现已知漏洞

## 已知 alpha 边界

- GitHub Actions 远端实跑仍需仓库侧确认
- Python 3.10 support 已在 CI 声明，但仍需远端或本地解释器确认
- `wangchuan.v3.*` 是内部实现承载层，不是推荐 stable import surface
- L4/L5/L6 天工集成为 optional，缺失时应降级到 standalone stub
- Web API 仍是 preview / local-only
- stable 前仍需真实外部试用反馈

## Stable 就绪规则

不要在满足以下条件前宣称 mature stable：

1. GitHub Actions 在支持的 Python 版本上全绿
2. 至少 3 个真实外部试用反馈闭环已处理
3. 修复后再次通过最终 gate：

```bash
python scripts/release_check.py
pytest -q
python -m build
# wheel install smoke
```
