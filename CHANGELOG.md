# 更新日志

## 3.0.0-alpha

忘川独立包的首次公开 alpha 版本。

### 新增

- 独立的 `wangchuan-memory` 包结构
- 稳定的最小 Python API、CLI 与 `wangchuan.facade` 契约
- 本地 SQLite 记忆引擎与首次运行自动初始化
- 通过 `schema_version` 与 `meta.schema_version` 暴露 schema 版本
- 迁移幂等测试与备份/恢复测试
- 发布安全检查脚本，覆盖密钥、运行时产物、本机路径等 gate
- GitHub Actions workflow，包含测试矩阵、build、wheel smoke、依赖审计任务
- API 契约与弃用策略文档
- 快速开始、CLI、MCP、存储、FAQ、架构、排障、alpha 试用与反馈文档
- Python 与 CLI 示例，并接入 smoke 测试
- wheel/sdist 打包运行时资源，包括 `wangchuan/v3/schema.sql`
- 不可用数据库路径的清晰 CLI 配置错误提示

### 变更

- 根包 `__all__` 与文档中的稳定 API 面保持一致
- README 改为中文主入口，并新增英文 `README_EN.md`
- README 链接完整文档结构与示例
- `release_check.py` 不再把正常 `docs/...` 链接误判为内部文档链接问题
- build 元数据改用 SPDX 风格 `license = "MIT"` 与 `license-files`

### 修复

- 仓库根目录直接执行 `pytest -q` 可用于 `src` layout
- `healthcheck()` 通过根包 helper 调用不再失败
- 新库与 legacy 数据库都能稳定暴露当前 schema version
- migration 可修复 baseline-like legacy 数据库中缺失的表/列
- wheel 安装不再缺少运行时 schema 资源
- 错误的 `WANGCHUAN_HOME` 会返回简洁配置错误，不再泄漏 traceback

### 已验证

- 本地 alpha gate 中 `pytest -q` 通过 39 个测试场景
- Python 3.11 与 3.12 在独立本地 venv 中通过
- wheel install smoke、first-run remember-recall、CLI smoke、extras install smoke 与 `pip-audit` 本地通过

### stable 前已知阻塞

- GitHub Actions 远端实跑仍需仓库侧确认
- Python 3.10 仍需远端或本地解释器确认
- 仍需至少 3 个真实外部试用反馈闭环

### 安全

- release check 会阻断 `.env`、运行时数据库、`.index`、`.wangchuan`、`state`、已知 key 特征、字面量 secret 与本机路径
- 本地 `pip-audit` 未发现已知漏洞
