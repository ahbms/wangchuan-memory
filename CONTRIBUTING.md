# 贡献指南

感谢你试用忘川。

## 报告问题前

如果条件允许，请先运行：

```bash
python scripts/release_check.py
pytest -q
python -m wangchuan status --json
```

如果你在测试安装行为，请额外在全新 venv 中跑一次 wheel smoke。

## 有用的问题报告应包含

1. 操作系统与 Python 版本
2. 安装方式：源码 / editable / wheel / PyPI
3. 写入的原始记忆内容
4. 使用的查询语句
5. 期待结果
6. 实际结果
7. 问题发生在 write、recall、explain、CLI、MCP、packaging、docs 还是 CI

可以使用：

- `.github/ISSUE_TEMPLATE/bug_report.yml`
- `.github/ISSUE_TEMPLATE/alpha_feedback.yml`
- `docs/FEEDBACK_TEMPLATE.md`

## 提交 PR 前

请运行：

```bash
python scripts/release_check.py
pytest -q
python -m build
```

然后在全新 venv 中验证 wheel 安装 smoke。

## 公开 API 规则

不要在未同步文档与测试的情况下扩大稳定公开 API。

如果新增或调整 stable API，请同时更新：

- `docs/API_CONTRACT.md`
- `docs/DEPRECATION_POLICY.md`
- README 示例
- import / API 测试

`wangchuan.v3.*`、`wangchuan.memory_api`、`wangchuan.runtime_state` 等内部实现路径不是默认外部集成面。
