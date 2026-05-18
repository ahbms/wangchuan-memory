#!/usr/bin/env python3
"""
忘川 v3.0 - DAG 多级摘要压缩
借鉴 lossless-claw 的无损上下文管理思路

核心特性：
- 原始消息永不删除
- 多级摘要（叶子→浓缩→高层）
- 摘要可展开回原始消息
- 上下文阈值触发压缩
"""

import sqlite3
import json
import hashlib
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class SummaryNode:
    """DAG 摘要节点"""
    node_id: str
    level: int              # 0=原始消息, 1=叶子摘要, 2=浓缩摘要, 3=高层摘要
    summary_text: str
    source_ids: List[int]   # 来源消息/摘要的 ID
    token_count: int
    created_at: str
    session_id: str


class DAGCompressor:
    """
    DAG 多级摘要压缩器

    层级结构：
    Level 0: 原始消息 (gm_messages)
    Level 1: 叶子摘要 (8-16条消息 → ~500字摘要)
    Level 2: 浓缩摘要 (4-8个叶子 → ~300字摘要)
    Level 3: 高层摘要 (多个浓缩 → ~200字概念)
    """

    # 配置
    CONTEXT_THRESHOLD = 0.75      # 上下文使用率触发压缩
    FRESH_TAIL = 10               # 保护最近N条消息
    LEAF_MIN_FANOUT = 8           # 叶子最少合并消息数
    CONDENSED_MIN_FANOUT = 4      # 浓缩最少合并叶子数
    LEAF_TARGET_CHARS = 500       # 叶子摘要目标字数
    CONDENSED_TARGET_CHARS = 300  # 浓缩摘要目标字数

    def __init__(self, db_path: str, llm_caller=None):
        self.db_path = db_path
        self.llm_caller = llm_caller
        self._ensure_table()

    def _ensure_table(self):
        """确保 DAG 摘要表存在"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gm_dag_summaries (
                    node_id TEXT PRIMARY KEY,
                    level INTEGER NOT NULL,
                    summary_text TEXT NOT NULL,
                    source_ids TEXT NOT NULL,
                    token_count INTEGER DEFAULT 0,
                    session_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expanded INTEGER DEFAULT 0
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_dag_level
                ON gm_dag_summaries(level)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_dag_session
                ON gm_dag_summaries(session_id)
            """)
            conn.commit()

    def should_compress(self, session_id: str, context_limit: int = 8000) -> bool:
        """检查是否需要压缩"""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()

            # 计算当前消息总 token 数
            c.execute("""
                SELECT SUM(LENGTH(content)) FROM gm_messages
                WHERE session_id = ?
            """, (session_id,))
            total_chars = c.fetchone()[0] or 0

            # 粗略估算 tokens (1 token ≈ 3 chars)
            estimated_tokens = total_chars // 3

            return estimated_tokens > context_limit * self.CONTEXT_THRESHOLD

    def compress_session(self, session_id: str) -> Dict:
        """
        压缩会话消息

        Returns:
            压缩统计
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # 获取需要压缩的消息（跳过新鲜尾巴）
            c.execute("""
                SELECT id, role, content, timestamp FROM gm_messages
                WHERE session_id = ?
                AND role IN ('user', 'assistant')
                ORDER BY id ASC
            """, (session_id,))

            all_messages = [dict(row) for row in c.fetchall()]

            if len(all_messages) <= self.FRESH_TAIL:
                return {'compressed': 0, 'reason': '消息太少'}

            # 分离需要压缩的和保护的
            to_compress = all_messages[:-self.FRESH_TAIL]
            protected = all_messages[-self.FRESH_TAIL:]

            if len(to_compress) < self.LEAF_MIN_FANOUT:
                return {'compressed': 0, 'reason': '未达到压缩阈值'}

            # 生成叶子摘要
            leaf_summaries = self._create_leaf_summaries(to_compress, session_id)

            # 如果叶子足够多，尝试浓缩
            condensed = []
            if len(leaf_summaries) >= self.CONDENSED_MIN_FANOUT:
                condensed = self._create_condensed_summaries(leaf_summaries, session_id)

            return {
                'compressed': len(to_compress),
                'protected': len(protected),
                'leaf_summaries': len(leaf_summaries),
                'condensed_summaries': len(condensed),
                'total_messages': len(all_messages)
            }

    def _create_leaf_summaries(self, messages: List[Dict], session_id: str) -> List[SummaryNode]:
        """创建叶子摘要"""
        summaries = []

        # 按 LEAF_MIN_FANOUT 分组
        chunks = []
        current_chunk = []

        for msg in messages:
            current_chunk.append(msg)
            if len(current_chunk) >= self.LEAF_MIN_FANOUT:
                chunks.append(current_chunk)
                current_chunk = []

        if current_chunk and len(current_chunk) >= 3:
            chunks.append(current_chunk)

        for chunk in chunks:
            # 拼接消息
            text = "\n".join([
                f"[{m['role']}] {self._parse_message_content(m['content'])[:200]}"
                for m in chunk
            ])
            source_ids = [m['id'] for m in chunk]

            # 生成摘要
            if self.llm_caller:
                summary = self._llm_summarize(text, self.LEAF_TARGET_CHARS)
            else:
                summary = self._rule_summarize(chunk)

            # 存储
            node_id = self._generate_node_id(source_ids, 1)
            node = SummaryNode(
                node_id=node_id,
                level=1,
                summary_text=summary,
                source_ids=source_ids,
                token_count=len(summary) // 3,
                created_at=datetime.now().isoformat(),
                session_id=session_id
            )

            self._store_summary(node)
            summaries.append(node)

        return summaries

    def _create_condensed_summaries(self, leaves: List[SummaryNode], session_id: str) -> List[SummaryNode]:
        """创建浓缩摘要"""
        summaries = []

        chunks = [leaves[i:i + self.CONDENSED_MIN_FANOUT]
                  for i in range(0, len(leaves), self.CONDENSED_MIN_FANOUT)]

        for chunk in chunks:
            if len(chunk) < 2:
                continue

            text = "\n".join([s.summary_text for s in chunk])
            source_ids = [s.source_ids[0] for s in chunk]

            if self.llm_caller:
                summary = self._llm_summarize(text, self.CONDENSED_TARGET_CHARS)
            else:
                summary = f"涵盖{len(chunk)}个主题的对话摘要：" + " | ".join(
                    s.summary_text[:50] for s in chunk
                )

            node_id = self._generate_node_id(source_ids, 2)
            node = SummaryNode(
                node_id=node_id,
                level=2,
                summary_text=summary,
                source_ids=source_ids,
                token_count=len(summary) // 3,
                created_at=datetime.now().isoformat(),
                session_id=session_id
            )

            self._store_summary(node)
            summaries.append(node)

        return summaries

    def _rule_summarize(self, messages: List[Dict]) -> str:
        """规则摘要（无 LLM 时的降级方案）"""
        topics = []
        for m in messages:
            if m['role'] == 'user':
                content = self._parse_message_content(m['content'])
                topics.append(content[:50])

        if len(topics) > 3:
            return f"讨论了{len(topics)}个话题：{'、'.join(topics[:3])}等"
        else:
            return f"讨论了：{'、'.join(topics)}"

    def _llm_summarize(self, text: str, target_chars: int) -> str:
        """调用 LLM 生成摘要"""
        prompt = (
            f"用{target_chars}字以内概括以下对话的核心内容，"
            f"只保留关键信息：\n\n{text}\n\n概括："
        )

        try:
            result = self.llm_caller(prompt)
            return result.strip()[:target_chars * 2]
        except Exception as e:
            logger.warning("【WangChuan】[DAG][Summarize] llm summarize failed; fallback to truncation: %s", e)
            return text[:target_chars]

    def _parse_message_content(self, content: str) -> str:
        """解析消息内容，提取纯文本"""
        if not content:
            return ""

        try:
            parsed = json.loads(content)
            if isinstance(parsed, str):
                return parsed
            elif isinstance(parsed, dict):
                return parsed.get('content', str(parsed)[:300])
            elif isinstance(parsed, list):
                return '\n'.join(
                    b.get('text', '') for b in parsed
                    if isinstance(b, dict) and b.get('type') == 'text'
                )
            else:
                return str(parsed)[:300]
        except (json.JSONDecodeError, TypeError):
            return str(content)[:300]

    def expand(self, node_id: str) -> Dict:
        """
        展开摘要节点，返回原始消息

        Agent 工具：lcm_expand 的忘川版
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # 获取摘要节点
            c.execute("SELECT * FROM gm_dag_summaries WHERE node_id = ?", (node_id,))
            node = c.fetchone()

            if not node:
                return {'error': '节点不存在'}

            source_ids = json.loads(node['source_ids'])

            # 获取原始消息
            placeholders = ','.join('?' * len(source_ids))
            c.execute(f"""
                SELECT id, role, content, timestamp FROM gm_messages
                WHERE id IN ({placeholders})
                ORDER BY id ASC
            """, source_ids)

            messages = [dict(row) for row in c.fetchall()]

            return {
                'node_id': node_id,
                'level': node['level'],
                'summary': node['summary_text'],
                'source_messages': messages,
                'message_count': len(messages)
            }

    def get_dag_stats(self, session_id: str = None) -> Dict:
        """获取 DAG 统计"""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()

            if session_id:
                c.execute("""
                    SELECT level, COUNT(*) as cnt, SUM(token_count) as tokens
                    FROM gm_dag_summaries
                    WHERE session_id = ?
                    GROUP BY level
                """, (session_id,))
            else:
                c.execute("""
                    SELECT level, COUNT(*) as cnt, SUM(token_count) as tokens
                    FROM gm_dag_summaries
                    GROUP BY level
                """)

            by_level = {}
            for row in c.fetchall():
                by_level[f'level_{row[0]}'] = {'count': row[1], 'tokens': row[2]}

            return {
                'by_level': by_level,
                'session_id': session_id
            }

    def get_session_summaries(self, session_id: str, level: int = 2, limit: int = 3) -> List[str]:
        """获取指定会话的摘要文本列表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT summary_text FROM gm_dag_summaries
                WHERE session_id = ? AND level = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (session_id, level, limit))
            return [row['summary_text'] for row in c.fetchall()]

    def _store_summary(self, node: SummaryNode):
        """存储摘要节点"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO gm_dag_summaries
                (node_id, level, summary_text, source_ids, token_count, session_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                node.node_id, node.level, node.summary_text,
                json.dumps(node.source_ids), node.token_count,
                node.session_id, node.created_at
            ))
            conn.commit()

    @staticmethod
    def _generate_node_id(source_ids: List[int], level: int) -> str:
        """生成节点 ID"""
        content = f"{'_'.join(str(sid) for sid in sorted(source_ids[:5]))}_{level}"
        hash_val = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"dag_L{level}_{hash_val}"
