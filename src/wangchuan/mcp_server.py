#!/usr/bin/env python3
"""
忘川记忆 MCP Server (基于官方 mcp SDK)

通过 MCP 协议暴露记忆能力，任何 MCP 客户端都能使用。

工具 profile：
  - stable（默认）: memory_write / memory_search / memory_search_raw /
    memory_search_scars / memory_status / memory_healthcheck /
    memory_recent / memory_search_by_tag
  - full（显式开启）: stable + memory_write_rule / memory_write_lesson /
    memory_search_at / memory_history / memory_chain / memory_merge /
    memory_forget / memory_user_view / memory_consolidate

旧 MCP 工具名 remember / recall / recall_raw / recall_scars 不再作为 contract 暴露。
"""

import sys
import os
import json
from pathlib import Path
from typing import Any

from wangchuan.paths import default_db_path

MCP_AVAILABLE = True
MCP_IMPORT_ERROR: ModuleNotFoundError | None = None

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    from mcp.server import InitializationOptions
except ModuleNotFoundError as e:
    if getattr(e, "name", "") != "mcp":
        raise
    MCP_AVAILABLE = False
    MCP_IMPORT_ERROR = e
    Server = None  # type: ignore[assignment]
    stdio_server = None  # type: ignore[assignment]

    class Tool:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TextContent:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class InitializationOptions:  # type: ignore[no-redef]
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

# 添加项目路径
_current_dir = Path(__file__).resolve().parent
_workspace_root = _current_dir.parent.parent
if str(_workspace_root) not in sys.path:
    sys.path.insert(0, str(_workspace_root))

# 导入忘川记忆 API
from wangchuan.memory_api import Memory

# 初始化记忆系统延迟到实际调用时，避免模块导入副作用
memory: Memory | None = None
app = None


STABLE_MCP_TOOLS = {
    "memory_write",
    "memory_search",
    "memory_search_raw",
    "memory_search_scars",
    "memory_status",
    "memory_healthcheck",
    "memory_recent",
    "memory_search_by_tag",
}

ADVANCED_MCP_TOOLS = {
    "memory_write_rule",
    "memory_write_lesson",
    "memory_search_at",
    "memory_history",
    "memory_chain",
    "memory_merge",
    "memory_forget",
    "memory_user_view",
    "memory_consolidate",
}

LEGACY_MCP_ALIASES = {
    "remember",
    "recall",
    "recall_raw",
    "recall_scars",
    "recall_at",
    "history",
    "get_supersession_chain",
}

ALL_CANONICAL_MCP_TOOLS = STABLE_MCP_TOOLS | ADVANCED_MCP_TOOLS


def _current_allowed_tool_names() -> set[str]:
    """Resolve the current MCP exposed tool allowlist.

    Default profile is intentionally small and stable. Operators can opt into
    the full profile or an exact comma-separated allowlist when they need
    power-user tools.
    """

    explicit = os.environ.get("WANGCHUAN_MCP_ALLOWED_TOOLS", "").strip()
    if explicit:
        requested = {item.strip() for item in explicit.split(",") if item.strip()}
        return requested & ALL_CANONICAL_MCP_TOOLS

    profile = os.environ.get("WANGCHUAN_MCP_TOOL_PROFILE", "stable").strip().lower()
    if profile == "full":
        return set(ALL_CANONICAL_MCP_TOOLS)
    return set(STABLE_MCP_TOOLS)


def _missing_mcp_dependency_error() -> ModuleNotFoundError:
    return ModuleNotFoundError(
        "No module named 'mcp'. Install optional dependency with `pip install -e '.[mcp]'` to use wangchuan.mcp_server."
    )


class _UnavailableMCPApp:
    def list_tools(self):
        def _decorator(fn):
            return fn
        return _decorator

    def call_tool(self):
        def _decorator(fn):
            return fn
        return _decorator

    async def run(self, *_args, **_kwargs):
        raise _missing_mcp_dependency_error()


def _get_memory() -> Memory:
    global memory
    if memory is None:
        _default_db = os.environ.get("WANGCHUAN_DB_PATH") or str(default_db_path())
        memory = Memory(db_path=_default_db)
    return memory


def _get_app():
    global app
    if app is not None:
        return app

    if not MCP_AVAILABLE:
        app = _UnavailableMCPApp()
        return app

    server = Server("wangchuan-memory")
    server.list_tools()(list_tools)
    server.call_tool()(call_tool)
    app = server
    return app


async def list_tools() -> list[Tool]:
    """列出当前 profile 允许暴露的工具。"""
    allowed = _current_allowed_tool_names()
    return [tool for tool in _all_tools() if tool.name in allowed]


def _all_tools() -> list[Tool]:
    """定义所有 canonical MCP 工具；是否暴露由 allowlist 控制。"""
    return [
        Tool(
            name="memory_write",
            description="记住一件事。将信息存入长期记忆。",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "要记住的内容，如'用户喜欢冰美式'"
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要性 0-1（默认 0.6）",
                        "default": 0.6
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签列表，如['偏好','饮品']",
                        "default": []
                    }
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="memory_write_rule",
            description="写入一条规则/默认判断类记忆。",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "规则内容，如'默认先验证再宣称完成'"
                    },
                    "importance": {
                        "type": "number",
                        "description": "重要性 0-1（默认 0.8）",
                        "default": 0.8
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "标签列表，如['rule','ops']",
                        "default": []
                    }
                },
                "required": ["content"]
            }
        ),
        Tool(
            name="memory_write_lesson",
            description="写入一条 lesson 记忆，兼容 candidate/promote 流。",
            inputSchema={
                "type": "object",
                "properties": {
                    "lesson": {
                        "type": "object",
                        "description": "lesson payload，至少包含 content"
                    }
                },
                "required": ["lesson"]
            }
        ),
        Tool(
            name="memory_search",
            description="回忆相关记忆。根据查询搜索过去的记忆。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "查询内容，如'用户偏好'"
                    },
                    "limit": {
                        "type": "number",
                        "description": "返回条数（默认 5）",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="memory_search_raw",
            description="回忆原话/原始记录链。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询内容"},
                    "limit": {"type": "number", "description": "返回条数（默认 5）", "default": 5}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="memory_search_scars",
            description="回忆规则、教训、判断链。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "查询内容"},
                    "limit": {"type": "number", "description": "返回条数（默认 5）", "default": 5}
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="memory_status",
            description="查看记忆系统当前状态：运行态、时间节奏、记忆总数。",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="memory_healthcheck",
            description="查看用户视角记忆健康状态。",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="memory_recent",
            description="获取最近的记忆条目。",
            inputSchema={
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "number",
                        "description": "返回条数（默认 10）",
                        "default": 10
                    }
                }
            }
        ),
        Tool(
            name="memory_merge",
            description="合并/更新记忆。当新信息与旧记忆矛盾时使用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "old_query": {
                        "type": "string",
                        "description": "旧记忆的关键词"
                    },
                    "new_content": {
                        "type": "string",
                        "description": "新内容"
                    },
                    "importance": {
                        "type": "number",
                        "description": "新内容重要性（默认 0.7）",
                        "default": 0.7
                    }
                },
                "required": ["old_query", "new_content"]
            }
        ),
        Tool(
            name="memory_forget",
            description="删除匹配的记忆。谨慎使用。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要删除的记忆内容关键词"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="memory_search_at",
            description="回忆特定时间点的记忆（时序查询）。用于查看某个历史时刻的记忆状态。",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "查询内容"
                    },
                    "as_of": {
                        "type": "string",
                        "description": "历史时间点 (ISO格式如 '2026-04-15' 或 '2026-04-15T10:30:00')"
                    },
                    "limit": {
                        "type": "number",
                        "description": "返回条数（默认 5）",
                        "default": 5
                    }
                },
                "required": ["query", "as_of"]
            }
        ),
        Tool(
            name="memory_history",
            description="查看某条记忆或某类事实的版本历史。",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {"type": "number", "description": "记忆ID"},
                    "query": {"type": "string", "description": "关键词查询"},
                    "limit": {"type": "number", "description": "返回条数（默认 10）", "default": 10}
                }
            }
        ),
        Tool(
            name="memory_chain",
            description="获取某个记忆的版本迁移链。查看记忆的完整演变历史。",
            inputSchema={
                "type": "object",
                "properties": {
                    "memory_id": {
                        "type": "number",
                        "description": "记忆ID"
                    }
                },
                "required": ["memory_id"]
            }
        ),
        Tool(
            name="memory_user_view",
            description="查看某个 user_id 可访问的记忆。",
            inputSchema={
                "type": "object",
                "properties": {
                    "user_id": {"type": "string", "description": "用户ID"},
                    "limit": {"type": "number", "description": "返回条数（默认 50）", "default": 50}
                },
                "required": ["user_id"]
            }
        ),
        Tool(
            name="memory_search_by_tag",
            description="按标签搜索记忆。",
            inputSchema={
                "type": "object",
                "properties": {
                    "tag": {"type": "string", "description": "标签名"},
                    "limit": {"type": "number", "description": "返回条数（默认 10）", "default": 10}
                },
                "required": ["tag"]
            }
        ),
        Tool(
            name="memory_consolidate",
            description="触发一次 session consolidation。",
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string", "description": "会话 ID，默认 default"}
                }
            }
        ),
    ]


async def call_tool(name: str, arguments: dict | None) -> list[TextContent]:
    """调用当前 profile 允许的 canonical 工具。"""
    arguments = arguments or {}
    if name in LEGACY_MCP_ALIASES:
        return [TextContent(type="text", text=f"工具已下线，请使用 canonical MCP 工具名: {name}")]
    if name not in _current_allowed_tool_names():
        return [TextContent(type="text", text=f"工具未在当前 MCP profile 中启用: {name}")]

    memory = _get_memory()
    try:
        if name == "memory_write":
            content = arguments.get("content", "")
            importance = arguments.get("importance", 0.6)
            tags = arguments.get("tags", [])
            
            result = memory.remember(
                content=content,
                importance=importance,
                tags=tags
            )
            
            return [TextContent(
                type="text",
                text=result.get("message", "记忆成功")
            )]

        elif name == "memory_write_rule":
            content = arguments.get("content", "")
            importance = arguments.get("importance", 0.8)
            tags = list(arguments.get("tags", []) or [])

            result = memory.remember(
                content=content,
                importance=importance,
                tags=list(dict.fromkeys(tags + ["rule"])),
                metadata={"memory_type": "rule", "source_layer": "scar", "user_explicit": True},
            )

            return [TextContent(type="text", text=result.get("message", "规则记忆成功"))]

        elif name == "memory_write_lesson":
            lesson = arguments.get("lesson", {})
            result = memory.remember_lesson(lesson)
            return [TextContent(type="text", text=result.get("message", "lesson 记忆成功"))]

        elif name == "memory_search":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 5)
            
            results = memory.recall(query=query, limit=limit)
            
            if results:
                lines = []
                for r in results:
                    score = r.get("score", 0.0)
                    content_text = (r.get("content") or "")[:150]
                    memory_type = r.get("memory_type") or r.get("type") or "unknown"
                    lines.append(f"• [{score:.2f}] {memory_type}: {content_text}")
                text = "\n".join(lines)
            else:
                text = "没有找到相关记忆。"
            
            return [TextContent(type="text", text=text)]

        elif name == "memory_search_raw":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 5)
            results = memory.recall_raw(query=query, limit=limit)
            if results:
                text = "\n".join(f"• [{(r.get('score', 0.0)):.2f}] {(r.get('content') or '')[:150]}" for r in results)
            else:
                text = "没有找到相关原始记忆。"
            return [TextContent(type="text", text=text)]

        elif name == "memory_search_scars":
            query = arguments.get("query", "")
            limit = arguments.get("limit", 5)
            results = memory.recall_scars(query=query, limit=limit)
            if results:
                text = "\n".join(f"• [{(r.get('score', 0.0)):.2f}] {(r.get('memory_type') or '?')}: {(r.get('content') or '')[:150]}" for r in results)
            else:
                text = "没有找到相关规则/教训记忆。"
            return [TextContent(type="text", text=text)]

        elif name == "memory_status":
            status = memory.status()
            
            structured = status.get("structured_memory", {})
            schema_index = status.get("memory_schema_index", {})
            
            lines = [
                status.get("message", "忘川记忆系统"),
                f"记忆总数: {structured.get('total', 0)}",
                f"高质量记忆: {structured.get('high_quality', 0)}",
                f"热记忆候选: {structured.get('hot_candidates', 0)}",
                f"索引状态: {schema_index.get('status', 'unknown')}",
            ]
            
            return [TextContent(type="text", text="\n".join(lines))]

        elif name == "memory_healthcheck":
            payload = memory.user_healthcheck()
            return [TextContent(type="text", text=payload.get("summary", "memory healthcheck done"))]

        elif name == "memory_recent":
            limit = arguments.get("limit", 10)
            
            items = memory.recent(limit=limit)
            
            if items:
                lines = []
                for r in items:
                    content_text = (r.get("content") or "")[:100]
                    memory_type = r.get("memory_type") or r.get("type") or "?"
                    lifecycle = r.get("lifecycle") or "?"
                    lines.append(f"• [{lifecycle}] {memory_type}: {content_text}")
                text = "\n".join(lines)
            else:
                text = "暂无记忆。"
            
            return [TextContent(type="text", text=text)]

        elif name == "memory_merge":
            old_query = arguments.get("old_query", "")
            new_content = arguments.get("new_content", "")
            importance = arguments.get("importance", 0.7)

            result = memory.merge(old_query=old_query, new_content=new_content, importance=importance)
            
            return [TextContent(
                type="text",
                text=f"已更新记忆: {result.get('message', '完成')}"
            )]

        elif name == "memory_forget":
            query = arguments.get("query", "")
            
            result = memory.forget(query)
            
            return [TextContent(
                type="text",
                text=result.get("message", "已删除记忆")
            )]

        elif name == "memory_search_at":
            query = arguments.get("query", "")
            as_of = arguments.get("as_of", "")
            limit = arguments.get("limit", 5)
            
            results = memory.recall_at(query=query, as_of=as_of, limit=limit)
            
            if results:
                lines = []
                for r in results:
                    content_text = (r.get("content") or "")[:100]
                    valid_from = r.get("valid_from", "?")
                    valid_until = r.get("valid_until", "now")
                    lines.append(f"• [{valid_from} ~ {valid_until}] {content_text}")
                text = "\n".join(lines)
            else:
                text = f"在 {as_of} 时间点没有找到相关记忆。"
            
            return [TextContent(type="text", text=text)]

        elif name == "memory_history":
            memory_id = arguments.get("memory_id")
            query = arguments.get("query")
            limit = arguments.get("limit", 10)

            items = memory.history(memory_id=memory_id, query=query, limit=limit)
            if items:
                lines = []
                for item in items:
                    lines.append(
                        f"• #{item.get('memory_id')} [{item.get('truth_state', '?')}] {(item.get('content') or '')[:100]}"
                    )
                text = "\n".join(lines)
            else:
                text = "没有找到相关版本历史。"
            return [TextContent(type="text", text=text)]

        elif name == "memory_chain":
            memory_id = arguments.get("memory_id", 0)
            
            chain = memory.get_supersession_chain(memory_id=memory_id)
            
            if chain and "error" not in chain[0]:
                lines = [f"记忆 #{memory_id} 的版本历史:"]
                for item in chain:
                    content_text = (item.get("content") or "")[:80]
                    valid_from = item.get("valid_from", "?")[:10]
                    valid_until = item.get("valid_until", "current")[:10] if item.get("valid_until") else "current"
                    lines.append(f"  {valid_from} ~ {valid_until}: {content_text}")
                text = "\n".join(lines)
            else:
                text = f"未找到记忆 #{memory_id} 的版本历史。"
            
            return [TextContent(type="text", text=text)]

        elif name == "memory_user_view":
            user_id = arguments.get("user_id", "")
            limit = arguments.get("limit", 50)
            rows = memory.get_user_memories(user_id=user_id, limit=limit)
            if rows:
                text = "\n".join(f"• [{row.get('permission', '?')}] #{row.get('memory_id')} {(row.get('content') or '')[:100]}" for row in rows)
            else:
                text = "该用户暂无可访问记忆。"
            return [TextContent(type="text", text=text)]

        elif name == "memory_search_by_tag":
            tag = arguments.get("tag", "")
            limit = arguments.get("limit", 10)
            rows = memory.find_by_tag(tag=tag, limit=limit)
            if rows:
                text = "\n".join(f"• #{row.get('memory_id') or row.get('id')} {(row.get('content') or '')[:100]}" for row in rows)
            else:
                text = f"标签 {tag} 下没有找到记忆。"
            return [TextContent(type="text", text=text)]

        elif name == "memory_consolidate":
            session_id = arguments.get("session_id")
            from wangchuan.memory_api import consolidate as memory_consolidate
            result = memory_consolidate(session_id=session_id)
            return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]

        else:
            return [TextContent(type="text", text=f"未知工具: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"错误: {str(e)}")]


_get_app()


async def main():
    """MCP Server 主入口"""
    if not MCP_AVAILABLE:
        raise _missing_mcp_dependency_error()

    server = _get_app()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="wangchuan-memory",
                server_version="1.0.0",
                capabilities={}
            )
        )


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
