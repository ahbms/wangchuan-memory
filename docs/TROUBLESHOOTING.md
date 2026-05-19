# 排障指南

## `ModuleNotFoundError: No module named 'wangchuan'`

如果你从源码运行，请先安装包：

```bash
pip install -e .
```

本仓库的 pytest 已在 `pyproject.toml` 中声明：

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]
```

## `release_check.py` 因 `.index` / `state` 失败

这说明工作树里存在运行时文件。
这些文件在本地运行时是正常的，但不能进入发布产物。

请清理或排除：

- `.index/`
- `state/`

## wheel 安装后提示缺少 `schema.sql`

这说明 build artifact 缺少运行时资源。
当前打包应包含：

- `wangchuan/v3/schema.sql`
- `wangchuan/v3/.env.example`

重新构建：

```bash
python -m build
```

## fresh empty database 下 `healthcheck` 是 risky

这对首次运行的空库可以是正常现象。
即使用户记忆很少或没有，schema 可见性仍应正确。

## `recall(...)` 返回 []

检查：

- 是否已经写入相关记忆？
- 是否应该用 `recall_raw(...)`？
- 是否应该用 `recall_scars(...)`？

## Optional extras 安装问题

按需安装：

```bash
pip install 'wangchuan-memory[mcp]'
pip install 'wangchuan-memory[llm]'
pip install 'wangchuan-memory[crypto]'
```
