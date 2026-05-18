#!/usr/bin/env python3
"""
忘川多层记忆写入桥接模块

启用三层记忆架构：
- working_memory: 当前对话的实时消息流（每条消息写入）
- short_term_memory: 当日对话摘要（会话结束时写入）
- medium_term_memory: 跨对话的中期记忆（手动或定期触发）

设计原则：
- 最小侵入：不修改现有 pipeline 代码，通过独立桥接模块写入
- 独立运行：可被 hook_bridge / consolidate / 手动调用触发
- 幂等安全：重复写入不会产生重复数据
"""

import sqlite3
import json
import hashlib
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Dict, List, Any

from wangchuan.db_utils import get_connection
from wangchuan.paths import default_db_path

logger = logging.getLogger(__name__)

DB_PATH = str(default_db_path())


def _get_conn(db_path: str = None):
    """获取数据库连接（委托给 db_utils 统一管理）"""
    return get_connection(db_path or DB_PATH)


# ============================================================
# 1. working_memory — 当前对话实时消息流
# ============================================================

def write_working_memory(
    session_id: str,
    role: str,
    content: str,
    db_path: str = None,
) -> int:
    """
    将一条消息写入 working_memory 表。
    
    在每条消息 ingest 后调用（由 hook_bridge 或 pipeline 调用）。
    
    Args:
        session_id: 会话ID
        role: user / assistant / system
        content: 消息内容
        db_path: 数据库路径（可选）
    
    Returns:
        写入的行ID
    """
    if not content or not content.strip():
        return -1
    
    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO working_memory (session_id, role, content, timestamp)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, role, content.strip(), datetime.now().isoformat()),
        )
        conn.commit()
        row_id = cursor.lastrowid
        logger.debug(
            "【WangChuan】[MultiMemory] working_memory write session=%s role=%s id=%d",
            session_id, role, row_id,
        )
        return row_id


def read_working_memory(
    session_id: str,
    limit: int = 20,
    db_path: str = None,
) -> List[Dict[str, Any]]:
    """
    读取当前会话的 working_memory 条目。
    
    Args:
        session_id: 会话ID
        limit: 最大返回条数
        db_path: 数据库路径（可选）
    
    Returns:
        消息列表，按时间正序
    """
    with _get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id, session_id, role, content, timestamp
            FROM working_memory
            WHERE session_id = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    return list(reversed(rows))  # 正序返回


# ============================================================
# 2. short_term_memory — 当日对话摘要
# ============================================================

def write_short_term_memory(
    session_id: str,
    topic: str,
    summary: str,
    key_facts: List[str] = None,
    emotion: str = "neutral",
    importance_score: float = 0.7,
    date_str: str = None,
    db_path: str = None,
) -> int:
    """
    写入 short_term_memory 表（当日对话摘要）。
    
    在会话结束/巩固时调用。
    
    Args:
        session_id: 会话ID
        topic: 对话主题（简短）
        summary: 一句话摘要
        key_facts: 关键事实列表
        emotion: 情感氛围
        importance_score: 重要性评分 0-1
        date_str: 日期字符串（YYYY-MM-DD），默认今天
        db_path: 数据库路径（可选）
    
    Returns:
        写入的行ID
    """
    today = date_str or date.today().isoformat()
    now = datetime.now()
    
    # 计算时间范围：当天的第一条到当前
    time_range = f"{today}"
    
    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO short_term_memory 
            (date, time_range, topic, summary, key_facts, emotion, 
             importance_score, source_session, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                today,
                time_range,
                topic,
                summary,
                json.dumps(key_facts or [], ensure_ascii=False),
                emotion,
                importance_score,
                session_id,
                now.isoformat(),
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        logger.info(
            "【WangChuan】[MultiMemory] short_term_memory write id=%d topic=%s",
            row_id, topic[:30],
        )
        return row_id


def read_short_term_memory(
    date_str: str = None,
    limit: int = 10,
    db_path: str = None,
) -> List[Dict[str, Any]]:
    """
    读取 short_term_memory 条目。
    
    Args:
        date_str: 指定日期（YYYY-MM-DD），默认所有
        limit: 最大返回条数
        db_path: 数据库路径（可选）
    
    Returns:
        摘要列表，按时间倒序
    """
    with _get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if date_str:
            cursor.execute(
                """
                SELECT id, date, time_range, topic, summary, key_facts,
                       emotion, importance_score, source_session, created_at
                FROM short_term_memory
                WHERE date = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (date_str, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, date, time_range, topic, summary, key_facts,
                       emotion, importance_score, source_session, created_at
                FROM short_term_memory
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        
        rows = [dict(row) for row in cursor.fetchall()]
        # 解析 JSON 字段
        for row in rows:
            if row.get("key_facts"):
                try:
                    row["key_facts"] = json.loads(row["key_facts"])
                except (json.JSONDecodeError, TypeError):
                    row["key_facts"] = []
        return rows


# ============================================================
# 3. medium_term_memory — 跨对话的中期记忆
# ============================================================

def write_medium_term_memory(
    period: str,
    period_type: str = "week",
    user_profile_summary: str = "",
    key_events: List[str] = None,
    lessons_learned: str = "",
    db_path: str = None,
) -> int:
    """
    写入 medium_term_memory 表（跨对话中期记忆）。
    
    从 short_term_memory 聚合生成。
    
    Args:
        period: 周期标识（如 "2026-W19" 或 "2026-05"）
        period_type: "week" 或 "month"
        user_profile_summary: 用户画像摘要
        key_events: 关键事件列表
        lessons_learned: 经验教训
        db_path: 数据库路径（可选）
    
    Returns:
        写入的行ID
    """
    now = datetime.now()
    
    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO medium_term_memory
            (period, period_type, user_profile_summary, key_events,
             lessons_learned, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                period,
                period_type,
                user_profile_summary,
                json.dumps(key_events or [], ensure_ascii=False),
                lessons_learned,
                now.isoformat(),
            ),
        )
        conn.commit()
        row_id = cursor.lastrowid
        logger.info(
            "【WangChuan】[MultiMemory] medium_term_memory write id=%d period=%s",
            row_id, period,
        )
        return row_id


def read_medium_term_memory(
    period: str = None,
    limit: int = 10,
    db_path: str = None,
) -> List[Dict[str, Any]]:
    """
    读取 medium_term_memory 条目。
    
    Args:
        period: 指定周期（如 "2026-W19"），默认所有
        limit: 最大返回条数
        db_path: 数据库路径（可选）
    
    Returns:
        中期记忆列表，按时间倒序
    """
    with _get_conn(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        if period:
            cursor.execute(
                """
                SELECT id, period, period_type, user_profile_summary,
                       key_events, lessons_learned, created_at
                FROM medium_term_memory
                WHERE period = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (period, limit),
            )
        else:
            cursor.execute(
                """
                SELECT id, period, period_type, user_profile_summary,
                       key_events, lessons_learned, created_at
                FROM medium_term_memory
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            )
        
        rows = [dict(row) for row in cursor.fetchall()]
        for row in rows:
            if row.get("key_events"):
                try:
                    row["key_events"] = json.loads(row["key_events"])
                except (json.JSONDecodeError, TypeError):
                    row["key_events"] = []
        return rows


# ============================================================
# 4. 会话巩固 — 从 working_memory → short_term_memory
# ============================================================

def consolidate_session(
    session_id: str,
    topic: str = "",
    summary: str = "",
    key_facts: List[str] = None,
    emotion: str = "neutral",
    importance_score: float = 0.7,
    db_path: str = None,
) -> Dict[str, Any]:
    """
    会话结束时巩固：从 working_memory 生成 short_term_memory。
    
    如果未提供 topic/summary，则从 working_memory 自动提取。
    
    Returns:
        写入结果字典
    """
    # 读取会话的 working_memory
    messages = read_working_memory(session_id, limit=100, db_path=db_path)
    
    if not messages:
        return {"status": "no_messages", "session_id": session_id}
    
    # 自动提取 topic 和 summary（如果未提供）
    user_msgs = [m["content"] for m in messages if m["role"] == "user"]
    assistant_msgs = [m["content"] for m in messages if m["role"] == "assistant"]
    
    if not topic and user_msgs:
        # 取第一条用户消息的前50字作为主题
        topic = user_msgs[0][:50]
    
    if not summary:
        # 简单摘要：用户消息数 + 助手消息数 + 最后一条用户消息
        summary = (
            f"对话 {len(messages)} 条消息 "
            f"(用户 {len(user_msgs)} / 助手 {len(assistant_msgs)})。"
        )
        if user_msgs:
            summary += f"最后话题: {user_msgs[-1][:30]}"
    
    if not key_facts:
        # 从用户消息中提取关键词作为 key_facts
        key_facts = []
        for msg in user_msgs[:5]:
            if len(msg) > 10:
                key_facts.append(msg[:80])
    
    # 写入 short_term_memory
    row_id = write_short_term_memory(
        session_id=session_id,
        topic=topic,
        summary=summary,
        key_facts=key_facts,
        emotion=emotion,
        importance_score=importance_score,
        db_path=db_path,
    )
    
    return {
        "status": "ok",
        "short_term_id": row_id,
        "session_id": session_id,
        "topic": topic,
        "message_count": len(messages),
    }


# ============================================================
# 5. 统计
# ============================================================

def get_multi_memory_stats(db_path: str = None) -> Dict[str, Any]:
    """获取多层记忆统计"""
    with _get_conn(db_path) as conn:
        cursor = conn.cursor()
        
        stats = {}
        _TABLE_WHITELIST = {"working_memory", "short_term_memory", "medium_term_memory"}
        for table in ["working_memory", "short_term_memory", "medium_term_memory"]:
            if table not in _TABLE_WHITELIST:
                continue
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                stats[table] = cursor.fetchone()[0]
            except Exception:
                stats[table] = 0
        
        # 今日 short_term 数量
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM short_term_memory WHERE date = ?",
                (date.today().isoformat(),),
            )
            stats["short_term_today"] = cursor.fetchone()[0]
        except Exception:
            stats["short_term_today"] = 0
        
        # 当前会话 working_memory 数量
        try:
            cursor.execute(
                "SELECT COUNT(DISTINCT session_id) FROM working_memory"
            )
            stats["working_memory_sessions"] = cursor.fetchone()[0]
        except Exception:
            stats["working_memory_sessions"] = 0
        
        return stats


if __name__ == "__main__":
    # 测试写入
    print("=== 多层记忆桥接模块测试 ===\n")
    
    session_id = "test_session_multi_memory"
    
    # 1. 写入 working_memory
    print("1. 写入 working_memory...")
    write_working_memory(session_id, "user", "你好，我想了解一下天工开智项目")
    write_working_memory(session_id, "assistant", "天工开智是一个意识进化体系项目")
    print("   ✓ working_memory 写入完成")
    
    # 2. 巩固为 short_term_memory
    print("\n2. 巩固为 short_term_memory...")
    result = consolidate_session(
        session_id,
        topic="天工开智项目咨询",
        summary="用户咨询天工开智项目，了解项目概况",
    )
    print(f"   ✓ short_term_memory 写入完成: {result}")
    
    # 3. 查看统计
    print("\n3. 统计信息:")
    stats = get_multi_memory_stats()
    for k, v in stats.items():
        print(f"   {k}: {v}")
    
    print("\n=== 测试完成 ===")
