#!/usr/bin/env python3
"""
忘川 v3.0 - 记忆激活器
让重要记忆在对话中被召回，避免记忆系统"写了就忘"
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import logging
import sqlite3
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Optional

WORKSPACE_ROOT = _v3_ws_root()
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

try:
    from ..fts_utils import build_safe_fts_match_query
except ImportError:
    from wangchuan.fts_utils import build_safe_fts_match_query

from wangchuan.memory_api import Memory

logger = logging.getLogger(__name__)

DB_PATH = str(WORKSPACE_ROOT / 'tiangong' / 'wangchuan' / '.index' / 'index.sqlite')
_MEMORY_API = Memory(DB_PATH)


def get_recall_candidates(query: str, limit: int = 10) -> List[Dict]:
    """
    根据查询获取应该被回忆的记忆候选
    优先级: importance * recency * relevance
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 先尝试FTS搜索
    try:
        match_query = build_safe_fts_match_query(query, max_terms=5)
        if match_query:
            c.execute("""
                SELECT m.* 
                FROM memories m
                JOIN fts_memories f ON m.id = f.rowid
                WHERE fts_memories MATCH ?
                ORDER BY m.importance DESC
                LIMIT ?
            """, (match_query, limit * 2))
            results = [dict(row) for row in c.fetchall()]
            if results:
                conn.close()
                return results
    except Exception as e:
        logger.warning("【WangChuan】[MemoryActivator][RecallCandidates] fts fallback triggered: %s", e)
        pass
    
    # 回退：按重要性 + 最近访问
    c.execute("""
        SELECT * FROM memories 
        WHERE importance >= 0.6
        ORDER BY importance DESC, created_at DESC
        LIMIT ?
    """, (limit,))
    
    results = [dict(row) for row in c.fetchall()]
    conn.close()
    return results


def get_important_memories(limit: int = 10, min_importance: float = 0.5) -> List[Dict]:
    """获取高重要性记忆，用于注入对话上下文
    
    优化：降低默认阈值到0.5，扩大召回范围
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 按重要性排序，优先召回长期未被唤醒的记忆
    c.execute("""
        SELECT id, content, type, importance, 
               created_at, last_recall, trigger_count
        FROM memories 
        WHERE importance >= ?
        ORDER BY 
            CASE WHEN last_recall IS NULL THEN 0 ELSE 1 END,
            trigger_count ASC,
            importance DESC
        LIMIT ?
    """, (min_importance, limit))
    
    results = [dict(row) for row in c.fetchall()]
    
    # 更新召回时间
    if results:
        ids = [r['id'] for r in results]
        placeholders = ','.join('?' * len(ids))
        recall_ts = datetime.now().isoformat()
        c.execute(f"""
            UPDATE memories 
            SET last_recall = ?, trigger_count = trigger_count + 1
            WHERE id IN ({placeholders})
        """, [recall_ts] + ids)
        conn.commit()
        _MEMORY_API.sync_maintenance_updates(
            ids,
            last_recall=recall_ts,
            trigger_delta=1,
            lifecycle="active",
            promotion_state="recalled",
            last_confirmed_at=recall_ts,
        )
    
    conn.close()
    return results


def get_user_preferences() -> List[Dict]:
    """获取用户偏好记忆"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    c.execute("""
        SELECT content, importance, trigger_count
        FROM memories 
        WHERE type IN ('preference', 'habit', 'aversion')
        ORDER BY importance DESC
        LIMIT 20
    """)
    
    results = [dict(row) for row in c.fetchall()]
    conn.close()
    return results


def format_memories_for_prompt(memories: List[Dict]) -> str:
    """格式化记忆为可注入prompt的文本"""
    if not memories:
        return ""
    
    lines = ["## 🧠 重要记忆（激活）", ""]
    
    for m in memories:
        content = m.get('content', '')
        if content:
            lines.append(f"- {content}")
    
    return '\n'.join(lines)


def batch_activate(limit: int = 20) -> List[Dict]:
    """批量激活高价值但沉睡的记忆
    
    优先选择：高重要性 + 低触发次数 + 长期未召回
    用于定期批量唤醒沉睡知识
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    # 排除最近1小时内已激活的记忆，避免重复
    one_hour_ago = (datetime.now() - timedelta(hours=1)).isoformat()
    
    c.execute("""
        SELECT id, content, type, importance, 
               created_at, last_recall, trigger_count
        FROM memories 
        WHERE importance >= 0.6
          AND (last_recall IS NULL OR last_recall < ?)
          AND trigger_count < 5
        ORDER BY 
            importance DESC,
            trigger_count ASC
        LIMIT ?
    """, (one_hour_ago, limit))
    
    results = [dict(row) for row in c.fetchall()]
    
    # 更新召回标记
    if results:
        ids = [r['id'] for r in results]
        placeholders = ','.join('?' * len(ids))
        recall_ts = datetime.now().isoformat()
        c.execute(f"""
            UPDATE memories 
            SET last_recall = ?, trigger_count = trigger_count + 1
            WHERE id IN ({placeholders})
        """, [recall_ts] + ids)
        conn.commit()
        _MEMORY_API.sync_maintenance_updates(
            ids,
            last_recall=recall_ts,
            trigger_delta=1,
            lifecycle="active",
            promotion_state="recalled",
            last_confirmed_at=recall_ts,
        )
    
    conn.close()
    return results


def boost_memory_importance(memory_id: int, boost: float = 0.1, db_path: str = None):
    """提升记忆重要性（当记忆被证实用到时）"""
    target_db = db_path or DB_PATH
    conn = sqlite3.connect(target_db)
    c = conn.cursor()
    c.execute("SELECT importance FROM memories WHERE id = ?", (memory_id,))
    row = c.fetchone()
    if not row:
        conn.close()
        return
    new_importance = min(1.0, float(row[0] or 0.0) + boost)
    c.execute("""
        UPDATE memories 
        SET importance = ?
        WHERE id = ?
    """, (new_importance, memory_id))
    conn.commit()
    conn.close()
    Memory(target_db).sync_maintenance_updates([memory_id], importance=new_importance, lifecycle="active")


def decay_unused_memories(days_threshold: int = 7, decay: float = 0.05):
    """衰减长期未使用的记忆重要性"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    threshold_date = (datetime.now() - timedelta(days=days_threshold)).isoformat()
    rows = c.execute(
        """
        SELECT id, importance FROM memories
        WHERE (last_recall IS NULL AND created_at < ?)
           OR (last_recall < ?)
        """,
        (threshold_date, threshold_date)
    ).fetchall()

    c.execute("""
        UPDATE memories 
        SET importance = MAX(0.1, importance - ?)
        WHERE (last_recall IS NULL AND created_at < ?)
           OR (last_recall < ?)
    """, (decay, threshold_date, threshold_date))
    
    affected = c.rowcount
    conn.commit()
    conn.close()

    if rows:
        for row in rows:
            new_importance = max(0.1, float(row["importance"] or 0.0) - decay)
            _MEMORY_API.sync_maintenance_updates([row["id"]], importance=new_importance, lifecycle="aging")
    
    return affected


def get_memory_stats() -> Dict:
    """获取记忆统计。

    P5-05 延伸：统计面优先消费 `memory_schema_index` 的统一结构层，
    避免继续只按旧主表 `importance/type/trigger_count` 做阶段 2 之后的
    半结构统计。
    """
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    stats = {}

    # 主表总量仍保留，方便兼容现有运行口径
    c.execute("SELECT COUNT(*) FROM memories")
    stats['total'] = c.fetchone()[0]

    structured_overview = _MEMORY_API.structured_memory_overview()
    schema_status = _MEMORY_API.memory_schema_index_status()
    stats['structured_reader'] = structured_overview.get('reader')
    stats['schema_total'] = schema_status.get('total', 0)

    # 高价值信号优先读统一结构层；无结构层时回退旧主表口径
    if structured_overview.get('reader') == 'memory_schema_index' and schema_status.get('total', 0) > 0:
        stats['high_importance'] = structured_overview.get('high_quality', 0)
        stats['recalled'] = schema_status.get('promoted', 0)
        stats['by_type'] = structured_overview.get('by_memory_type', {})
        stats['by_lifecycle'] = structured_overview.get('by_lifecycle', {})
        stats['by_promotion_state'] = structured_overview.get('by_promotion_state', {})
    else:
        c.execute("SELECT COUNT(*) FROM memories WHERE importance >= 0.7")
        stats['high_importance'] = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM memories WHERE trigger_count > 0")
        stats['recalled'] = c.fetchone()[0]

        c.execute("SELECT type, COUNT(*) FROM memories GROUP BY type ORDER BY COUNT(*) DESC")
        stats['by_type'] = dict(c.fetchall())
        stats['by_lifecycle'] = {}
        stats['by_promotion_state'] = {}

    conn.close()
    return stats


def run_activation(query: str = None, limit: int = 10) -> str:
    """运行记忆激活，返回注入文本"""
    if query:
        memories = get_recall_candidates(query, limit)
    else:
        memories = get_important_memories(limit)
    
    return format_memories_for_prompt(memories)


def recall_payload(query: str = "", limit: int = 6) -> Dict:
    """返回结构化 recall 结果，供选择性注入与外层裁剪使用。"""
    if query:
        memories = get_recall_candidates(query, limit)
        mode = "query"
    else:
        memories = get_important_memories(limit)
        mode = "important"

    payload = {
        'mode': mode,
        'query': query,
        'count': len(memories),
        'items': [],
    }
    for item in memories[:limit]:
        payload['items'].append({
            'id': item.get('id'),
            'content': str(item.get('content', '') or '')[:260],
            'type': item.get('type', ''),
            'importance': item.get('importance'),
            'last_recall': item.get('last_recall'),
            'trigger_count': item.get('trigger_count'),
        })
    return payload


if __name__ == '__main__':
    import sys
    
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'stats'
    
    if cmd == 'stats':
        stats = get_memory_stats()
        logger.info("【WangChuan】[MemoryActivator][Stats] total=%s high_importance=%s recalled=%s",
                    stats['total'], stats['high_importance'], stats['recalled'])
        for t, c in stats.get('by_type', {}).items():
            logger.info("【WangChuan】[MemoryActivator][Stats] type=%s count=%s", t, c)
    
    elif cmd == 'recall':
        query = sys.argv[2] if len(sys.argv) > 2 else ''
        result = run_activation(query)
        print(result)

    elif cmd == 'recall_json':
        query = sys.argv[2] if len(sys.argv) > 2 else ''
        limit = int(sys.argv[3]) if len(sys.argv) > 3 else 6
        print(json.dumps(recall_payload(query, limit), ensure_ascii=False, indent=2))
    
    elif cmd == 'preferences':
        prefs = get_user_preferences()
        logger.info("【WangChuan】[MemoryActivator][Preferences] total=%s", len(prefs))
        for p in prefs:
            logger.info("【WangChuan】[MemoryActivator][Preferences] importance=%.2f content=%s", p['importance'], p['content'][:80])
    
    elif cmd == 'decay':
        days = int(sys.argv[2]) if len(sys.argv) > 2 else 7
        affected = decay_unused_memories(days)
        logger.info("【WangChuan】[MemoryActivator][Decay] affected=%s days=%s", affected, days)
    
    elif cmd == 'batch':
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 20
        memories = batch_activate(limit)
        logger.info("【WangChuan】[MemoryActivator][Batch] activated=%s", len(memories))
        for m in memories:
            logger.info("【WangChuan】[MemoryActivator][Batch] importance=%.2f content=%s", m['importance'], m['content'][:60])
    
    elif cmd == 'warmup':
        """预热激活：批量唤醒高价值沉睡记忆"""
        memories = batch_activate(30)
        stats = get_memory_stats()
        logger.info("【WangChuan】[MemoryActivator][Warmup] activated=%s recalled=%s total=%s ratio=%.1f%%",
                    len(memories), stats['recalled'], stats['total'], stats['recalled']/stats['total']*100 if stats['total'] else 0.0)
    
    else:
        logger.info("【WangChuan】[MemoryActivator] usage=memory_activator.py [stats|recall|preferences|decay|batch|warmup]")
