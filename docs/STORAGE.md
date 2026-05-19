# 存储说明

忘川使用本地 SQLite 文件保存记忆。

## 默认数据库路径

默认数据库路径：

```text
$WANGCHUAN_HOME/.index/index.sqlite
```

如果没有设置 `WANGCHUAN_HOME`，忘川会从当前 workspace root 解析路径。

## 存储什么

主要运行时数据：

- SQLite 数据库：`.index/index.sqlite`
- migration version metadata：`schema_version` 表 + `meta.schema_version`

## 备份

最小备份单位就是 SQLite 文件本身：

```bash
cp .index/index.sqlite /path/to/backup/index.sqlite
```

## 恢复

```bash
cp /path/to/backup/index.sqlite .index/index.sqlite
python3 -m wangchuan healthcheck --json
```

## 运行注意事项

- 不要把运行时 `.index/` 或 `state/` 目录提交到发布产物
- `scripts/release_check.py` 会把 runtime 目录视为 forbidden release files
- build artifacts 必须包含 `wangchuan/v3/schema.sql` 等静态运行资源

## 相关文档

- [`QUICKSTART.md`](./QUICKSTART.md)
- [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md)
