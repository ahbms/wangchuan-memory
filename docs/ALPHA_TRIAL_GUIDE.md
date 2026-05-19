# Alpha 试用指南

用这份指南试用忘川并提交反馈。

## 目标

确认一个新用户可以完成：

1. 安装忘川
2. 写入记忆
3. 召回记忆
4. 查看 status / health
5. 用足够信息报告问题，便于复现

## 安装

```bash
git clone <仓库地址>
cd wangchuan-memory
python -m pip install -e '.[dev]'
```

可选安装档位：

```bash
python -m pip install -e '.[mcp]'
python -m pip install -e '.[llm]'
python -m pip install -e '.[crypto]'
```

## 试用场景 A：Python API

```bash
python3 examples/basic_memory.py
```

期望结果：

- 输出 JSON
- `preference_written: true`
- `fact_written: true`
- `recall_count >= 2`

## 试用场景 B：CLI

```bash
bash examples/cli_demo.sh
```

期望结果：

- paths JSON
- remember success JSON
- recall JSON 包含刚写入内容
- status JSON

## 试用场景 C：发布就绪度

```bash
python scripts/release_check.py
pytest -q
python -m build
```

期望结果：

- 干净工作树下 release check 通过
- 测试通过
- wheel / sdist 生成成功

## 提交反馈

使用 [`FEEDBACK_TEMPLATE.md`](./FEEDBACK_TEMPLATE.md)。

问题分级：

- P0：安装 / 首次运行 / 数据安全阻塞
- P1：召回质量、CLI、文档或令人困惑的行为
- P2：增强项
