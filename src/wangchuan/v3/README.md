# WangChuan v3 内部实现层

这个目录是忘川当前内部实现承载层。

公开 alpha 用户请优先使用：

- Python API：`from wangchuan import Memory, remember, recall`
- CLI：`python3 -m wangchuan ...`
- MCP：安装 `mcp` extra 后使用 `python3 -m wangchuan.mcp_server`

`wangchuan.v3.*` 不是推荐 stable import surface。它保留给内部结构、兼容性与高级调试使用。
