"""
忘川 legacy 对话历史引擎（compat / sidecar）
天工开智 - Layer 2 历史兼容链

职责：
- 维护旧版对话历史持久化能力
- 维护旧版上下文协议与检索接口
- 为历史流程/测试/兼容调用提供承接
- 通过 `wangchuan.legacy_types` 转发 legacy 数据类型，避免本文件继续承担类型仓职责

⚠️ 说明：
- 本文件属于 v2/legacy 对话历史链；仅在兼容路径直接使用 ChatMemory 且未传 db_path 时，才会落到 .wangchuan/memory.db
- `.wangchuan/memory.db` 是历史兼容存档库，不是当前默认主库；当前默认主库见 paths.default_db_path() / .index/index.sqlite
- 不应作为新的生产扩展入口；当前主入口优先看 wangchuan.recall_service
- recall_service 当前内部再承载到 wangchuan.v3.pipeline_v3.WangchuanPipeline
- 若只是想理解当前生产记忆主链，请不要先读本文件
- 若只是需要 legacy fallback factory，也应优先通过 wangchuan.compat 收口，而不是直接依赖本文件
"""

import os
import json
import sqlite3
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any

# 常量定义
DEFAULT_MESSAGE_LIMIT = 50
MAX_MESSAGE_LIMIT = 1000

logger = logging.getLogger(__name__)

try:
    from .chat_memory import ChatMemory
    from .context_protocol import ContextProtocol, ContextScope, ContextPriority
    from .legacy_types import ChatMessage, MemoryQuery, SearchResult
except ImportError:
    from chat_memory import ChatMemory
    from context_protocol import ContextProtocol, ContextScope, ContextPriority
    from legacy_types import ChatMessage, MemoryQuery, SearchResult


class WangchuanEngine:
    """
    忘川 legacy 引擎兼容壳（compat shell）

    保留原因：
    1. 承接旧版对话历史持久化
    2. 承接旧版上下文协议与关键词检索接口
    3. 为历史流程 / 测试 / 兼容调用提供最小可用壳

    注意：
    - 它不是当前生产 recall 主链
    - 若排查当前生产主链，请优先看 wangchuan.recall_service / v3.pipeline_v3
    - 若只是需要 legacy fallback，也优先通过 wangchuan.compat 的工厂函数收口
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Args:
            db_path: 数据库路径
        """
        # 初始化子模块
        self.chat_memory = ChatMemory(db_path)
        self.context_manager = ContextProtocol()

        # 数据库路径
        self.db_path = self.chat_memory.db_path

        logger.warning(
            "【WangChuan】[Legacy][WARN] WangchuanEngine 是 compat/sidecar 历史链，不是当前图谱增强 recall 主链: %s",
            self.db_path,
        )

        print(f"【WangChuan】[Legacy][INIT] compat/sidecar 历史引擎已初始化")
        logger.info("【WangChuan】[Legacy][DB] database=%s", self.db_path)

    # ==================== 对话管理 ====================

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Optional[Dict] = None
    ) -> str:
        """
        添加消息

        Args:
            session_id: 会话ID
            role: 角色 ('user' / 'assistant')
            content: 消息内容
            metadata: 元数据

        Returns:
            消息ID
        """
        return self.chat_memory.save_message(
            session_id=session_id,
            role=role,
            content=content,
            metadata=metadata or {}
        )

    def get_history(
        self,
        session_id: str,
        limit: int = DEFAULT_MESSAGE_LIMIT
    ) -> List[ChatMessage]:
        """获取会话历史"""
        return self.chat_memory.get_session_history(session_id, limit)

    def get_recent_messages(
        self,
        session_id: str,
        count: int = 10
    ) -> List[ChatMessage]:
        """获取最近的消息"""
        return self.chat_memory.get_session_history(session_id, count)

    # ==================== 语义检索 ====================

    def search(
        self,
        query: str,
        session_id: Optional[str] = None,
        limit: int = 10
    ) -> List[SearchResult]:
        """
        搜索记忆

        Args:
            query: 搜索关键词
            session_id: 限定会话（可选）
            limit: 返回数量

        Returns:
            搜索结果列表
        """
        # 获取所有消息
        if session_id:
            messages = self.chat_memory.get_session_history(session_id, limit=MAX_MESSAGE_LIMIT)
        else:
            messages = self._get_all_messages(limit=MAX_MESSAGE_LIMIT)

        # 关键词匹配
        keywords = query.lower().split()
        results = []

        for msg in messages:
            content_lower = msg.content.lower()
            matched = []

            for kw in keywords:
                if kw in content_lower:
                    matched.append(kw)

            if matched:
                relevance = len(matched) / len(keywords)
                results.append(SearchResult(
                    message=msg,
                    relevance=relevance,
                    matched_keywords=matched
                ))

        # 按相关度排序
        results.sort(key=lambda r: r.relevance, reverse=True)
        return results[:limit]

    def _get_all_messages(self, limit: int = MAX_MESSAGE_LIMIT) -> List[ChatMessage]:
        """获取所有消息"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute('''
                SELECT id, session_id, role, content, timestamp, metadata
                FROM chat_history
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (limit,))

            messages = []
            for row in cursor.fetchall():
                messages.append(ChatMessage(
                    id=row[0],
                    session_id=row[1],
                    role=row[2],
                    content=row[3],
                    timestamp=row[4],
                    metadata=json.loads(row[5]) if row[5] else {}
                ))

        return messages

    # ==================== 会话管理 ====================

    def list_sessions(
        self,
        limit: int = 20,
        active_hours: int = 24
    ) -> List[Dict[str, Any]]:
        """
        列出会话

        Args:
            limit: 返回数量
            active_hours: 活跃时间范围（小时）

        Returns:
            会话列表
        """
        conn = self.chat_memory.db_path
        with sqlite3.connect(conn) as db_conn:
            cursor = db_conn.cursor()

            cutoff = (datetime.now() - timedelta(hours=active_hours)).isoformat()

            cursor.execute('''
                SELECT session_id, user_id, platform, start_time, last_active, message_count, summary
                FROM chat_sessions
                WHERE last_active > ?
                ORDER BY last_active DESC
                LIMIT ?
            ''', (cutoff, limit))

            sessions = []
            for row in cursor.fetchall():
                sessions.append({
                    "session_id": row[0],
                    "user_id": row[1],
                    "platform": row[2],
                    "start_time": row[3],
                    "last_active": row[4],
                    "message_count": row[5],
                    "summary": row[6]
                })

        return sessions

    def get_session_summary(self, session_id: str) -> str:
        """
        获取会话摘要

        自动生成摘要（简单版：取最近的用户输入）
        """
        messages = self.get_history(session_id, limit=20)

        if not messages:
            return "无对话记录"

        # 统计信息
        user_msgs = [m for m in messages if m.role == "user"]
        assistant_msgs = [m for m in messages if m.role == "assistant"]

        summary_parts = []
        summary_parts.append(f"共 {len(messages)} 条消息")
        summary_parts.append(f"用户 {len(user_msgs)} 条")
        summary_parts.append(f"助手 {len(assistant_msgs)} 条")

        # 最近的主题
        if user_msgs:
            recent = user_msgs[-1].content[:50]
            summary_parts.append(f"最近: {recent}...")

        return " | ".join(summary_parts)

    # ==================== 上下文管理 ====================

    def set_context(
        self,
        session_id: str,
        key: str,
        value: Any,
        scope: str = "session",
        priority: str = "normal"
    ):
        """设置上下文"""
        scope_enum = ContextScope(scope)
        priority_enum = ContextPriority[priority.upper()]
        self.context_manager.set(session_id, key, value, scope_enum, priority_enum)

    def get_context(
        self,
        session_id: str,
        key: str,
        default: Any = None
    ) -> Any:
        """获取上下文"""
        return self.context_manager.get(session_id, key, default)

    def get_all_context(self, session_id: str) -> Dict[str, Any]:
        """获取所有上下文"""
        return self.context_manager.get_all(session_id)

    # ==================== 统计 ====================

    def get_stats(self) -> Dict[str, Any]:
        """获取统计"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM chat_history")
            total_messages = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM chat_sessions")
            total_sessions = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(DISTINCT session_id) FROM chat_history WHERE timestamp > ?",
                           ((datetime.now() - timedelta(hours=24)).isoformat(),))
            active_sessions_24h = cursor.fetchone()[0]

        return {
            "version": "1.3",
            "total_messages": total_messages,
            "total_sessions": total_sessions,
            "active_sessions_24h": active_sessions_24h,
            "db_path": self.db_path
        }

    def clear_session(self, session_id: str) -> int:
        """清除会话历史"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("DELETE FROM chat_history WHERE session_id = ?", (session_id,))
            cursor.execute("DELETE FROM chat_sessions WHERE session_id = ?", (session_id,))

            deleted = cursor.rowcount
            conn.commit()

        return deleted


# 便捷函数
def create_chat_memory(db_path: Optional[str] = None) -> WangchuanEngine:
    """创建 legacy/compat 忘川历史引擎壳。"""
    return WangchuanEngine(db_path)


__all__ = [
    "ChatMessage",
    "MemoryQuery",
    "SearchResult",
    "WangchuanEngine",
    "create_chat_memory",
]
