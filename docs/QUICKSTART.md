# 快速开始

用 5 分钟完成忘川首次成功运行。

## 安装

```bash
pip install wangchuan-memory
```

从源码安装：

```bash
git clone <仓库地址>
cd wangchuan-memory
pip install -e .
```

## 第一次 Python API 成功

```python
from wangchuan import remember, recall, status

remember("用户偏好简洁回复。", importance=0.9, tags=["preference"])
rows = recall("应该怎么回复？", limit=3)
print(rows)
print(status()["message"])
```

期望结果：

- `remember(...)` 返回 `{"success": true, ...}`
- `recall(...)` 返回 list
- `status()` 返回带 `message` 的 dict

## 第一次 CLI 成功

```bash
python3 -m wangchuan status --json
python3 -m wangchuan remember "用户偏好简洁回复。" --importance 0.9 --tag preference --json
python3 -m wangchuan recall "简洁回复" --limit 3 --json
```

期望结果：

- `status --json` 返回 JSON object
- `remember ... --json` 返回 success
- `recall ... --json` 返回 JSON list

## 数据位置

默认情况下，忘川把数据放在：

```text
$WANGCHUAN_HOME/.index/index.sqlite
```

如果没有设置 `WANGCHUAN_HOME`，忘川会使用当前 workspace root。

## 后续文档

- [`CLI.md`](./CLI.md)
- [`STORAGE.md`](./STORAGE.md)
- [`API_CONTRACT.md`](./API_CONTRACT.md)
- [`FAQ.md`](./FAQ.md)
- [`TROUBLESHOOTING.md`](./TROUBLESHOOTING.md)
