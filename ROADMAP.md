# 路线图

## v3.0.0-alpha

本地基线已完成：

- 公开源码发布结构
- 稳定的最小 Python、CLI 与 facade surface
- API 契约与弃用策略
- 首次运行本地 SQLite 初始化
- schema version 可见性
- migration 幂等测试
- 备份 / 恢复 smoke 测试
- release safety check
- 快速开始 / CLI / MCP / 存储 / FAQ / 架构 / 排障文档
- Python 与 CLI 示例及 smoke 测试
- wheel/sdist 打包运行时资源
- 本地 wheel install smoke
- 本地依赖审计

## v3.0.0-beta.1

打 beta tag 前需要：

- GitHub Actions 在 Python 3.10 / 3.11 / 3.12 全绿
- CI 中运行 release check 与 pytest
- CI 中运行 build 与 wheel smoke
- 收集至少 3 个外部 alpha 试用反馈
- 修复或明确阻塞 P0/P1 反馈问题
- 按实际验证结果更新 CHANGELOG 与 release note

## v3.0.0 stable

打 stable tag 前需要：

- 1-2 周内没有开放的 P0/P1 bug
- 外部试用反馈已纳入
- GitHub Actions 持续全绿
- 干净树最终 release gate 通过
- README / QUICKSTART / FAQ 与实际命令一致
- issue 模板与反馈流程就位
- 稳定文档与示例再次验证
