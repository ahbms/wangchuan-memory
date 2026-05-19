# CLI 参考

忘川公开 CLI 入口只有一个：

```bash
python3 -m wangchuan
```

## 稳定日常命令

### 查看状态

```bash
python3 -m wangchuan status --json
```

返回包含整体状态、health、migration 可见性与 `message` 摘要的 JSON 对象。

### 查看路径

```bash
python3 -m wangchuan paths --json
```

返回 workspace / data / state / db 解析路径。

### 写入一条记忆

```bash
python3 -m wangchuan remember "用户偏好简洁回复" --importance 0.9 --tag preference --json
```

### 召回记忆

```bash
python3 -m wangchuan recall "简洁回复" --limit 3 --json
```

### 召回原始证据

```bash
python3 -m wangchuan recall-raw "原话" --limit 3 --json
```

### 召回规则 / 教训

```bash
python3 -m wangchuan recall-scars "规则和教训" --limit 3 --json
```

### 运行健康检查

```bash
python3 -m wangchuan healthcheck --json
```

### 查看任务恢复面

```bash
python3 -m wangchuan task-resume --json
```

## 稳定 facade 命令

```bash
python3 -m wangchuan facade-version --json
python3 -m wangchuan facade-health --json
python3 -m wangchuan facade-capabilities --json
```

## 高级维护命令

这些命令仍使用同一个 CLI 路径，但不是新用户默认学习面：

- `remember-rule`
- `remember-lesson`
- `recall-at`
- `merge`
- `history`
- `chain`
- `rollback`
- `user-memories`
- `tag-search`
- `consolidate`
- `agent-tools`
- `recent`
- `cleanup`
- `question-like-rule-audit`
- `question-like-rule-cleanup`
- `canonical-repair`

## 说明

- 稳定兼容范围遵循 [`API_CONTRACT.md`](./API_CONTRACT.md)
- CLI 使用不要求用户直接导入内部实现路径
