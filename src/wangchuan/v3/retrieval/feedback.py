#!/usr/bin/env python3
"""
忘川 v3.0 - 反馈闭环系统
通过隐式/显式信号优化召回质量

核心数据流：
召回 → 组装 → 用户使用 → 反馈信号 → 更新节点权重 → 下次召回更准
"""

import sqlite3
import json
import time
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class FeedbackType(Enum):
    """反馈类型"""
    IMPLICIT_USE = "implicit_use"      # 节点被组装进上下文（隐式正反馈）
    EXPLICIT_POSITIVE = "explicit_pos" # 用户明确表示有用
    EXPLICIT_NEGATIVE = "explicit_neg" # 用户纠正/否定
    FOLLOW_UP = "follow_up"            # 用户追问（强正反馈）
    IGNORED = "ignored"                # 召回了但没被组装（弱负反馈）


@dataclass
class FeedbackSignal:
    """反馈信号"""
    node_id: str
    feedback_type: FeedbackType
    query: str                    # 原始查询
    session_id: str
    timestamp: float = field(default_factory=time.time)
    weight: float = 1.0           # 信号强度
    metadata: Dict = field(default_factory=dict)


class FeedbackEngine:
    """
    反馈闭环引擎

    功能：
    1. 记录反馈信号到 gm_feedback 表
    2. 根据反馈调整节点权重（stored in gm_nodes.fact_confidence）
    3. 提供反馈统计和质量指标
    """

    # 反馈类型对应的权重调整
    WEIGHT_ADJUSTMENTS = {
        FeedbackType.IMPLICIT_USE: 0.02,       # 微小正向
        FeedbackType.EXPLICIT_POSITIVE: 0.10,  # 明确正向
        FeedbackType.EXPLICIT_NEGATIVE: -0.15, # 明确负向
        FeedbackType.FOLLOW_UP: 0.08,          # 追问 = 好召回
        FeedbackType.IGNORED: -0.03,           # 被忽略 = 弱负向
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_table()

    def _ensure_table(self):
        """确保反馈表存在"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gm_feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    feedback_type TEXT NOT NULL,
                    query TEXT,
                    session_id TEXT,
                    weight REAL DEFAULT 1.0,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (node_id) REFERENCES gm_nodes(node_id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_node
                ON gm_feedback(node_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_type
                ON gm_feedback(feedback_type)
            """)
            conn.commit()

    def record(self, signal: FeedbackSignal):
        """记录一条反馈信号"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO gm_feedback
                (node_id, feedback_type, query, session_id, weight, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                signal.node_id,
                signal.feedback_type.value,
                signal.query,
                signal.session_id,
                signal.weight,
                json.dumps(signal.metadata) if signal.metadata else None
            ))
            conn.commit()

        logger.info(
            "【WangChuan】[Feedback][Record] node=%s type=%s weight=%.2f session=%s",
            signal.node_id,
            signal.feedback_type.value,
            signal.weight,
            signal.session_id,
        )

        # 更新节点权重
        self._adjust_node_weight(signal)

    def record_batch(self, signals: List[FeedbackSignal]):
        """批量记录反馈"""
        for signal in signals:
            self.record(signal)

    def _adjust_node_weight(self, signal: FeedbackSignal):
        """根据反馈调整节点权重"""
        adjustment = self.WEIGHT_ADJUSTMENTS.get(signal.feedback_type, 0)
        adjustment *= signal.weight  # 应用信号强度

        with sqlite3.connect(self.db_path) as conn:
            # 读取当前权重
            row = conn.execute(
                "SELECT fact_confidence FROM gm_nodes WHERE node_id = ?",
                (signal.node_id,)
            ).fetchone()

            current = row[0] if row and row[0] is not None else 0.5

            # 调整（clamp 到 0.05 ~ 1.0）
            new_weight = max(0.05, min(1.0, current + adjustment))

            conn.execute("""
                UPDATE gm_nodes
                SET fact_confidence = ?, last_accessed = CURRENT_TIMESTAMP,
                    access_count = access_count + 1
                WHERE node_id = ?
            """, (new_weight, signal.node_id))
            conn.commit()

        logger.info(
            "【WangChuan】[Feedback][Weight] node=%s type=%s current=%.3f adjustment=%.3f new=%.3f",
            signal.node_id,
            signal.feedback_type.value,
            current,
            adjustment,
            new_weight,
        )

    def on_recall_used(self, query: str, session_id: str,
                       recalled_node_ids: List[str],
                       assembled_node_ids: List[str]):
        """
        召回完成后调用

        - assembled 节点 → IMPLICIT_USE 正反馈
        - recalled 但未 assembled → IGNORED 弱负反馈
        """
        signals = []

        for nid in assembled_node_ids:
            signals.append(FeedbackSignal(
                node_id=nid,
                feedback_type=FeedbackType.IMPLICIT_USE,
                query=query,
                session_id=session_id
            ))

        for nid in recalled_node_ids:
            if nid not in assembled_node_ids:
                signals.append(FeedbackSignal(
                    node_id=nid,
                    feedback_type=FeedbackType.IGNORED,
                    query=query,
                    session_id=session_id
                ))

        logger.info(
            "【WangChuan】[Feedback][Recall] query=%s recalled=%s assembled=%s ignored=%s session=%s",
            query,
            len(recalled_node_ids),
            len(assembled_node_ids),
            max(0, len(recalled_node_ids) - len(assembled_node_ids)),
            session_id,
        )
        self.record_batch(signals)

    def on_follow_up(self, query: str, session_id: str,
                     previous_node_ids: List[str]):
        """
        用户追问时调用（强正反馈）

        追问说明上次召回有用，相关节点应该加权
        """
        signals = []
        for nid in previous_node_ids:
            signals.append(FeedbackSignal(
                node_id=nid,
                feedback_type=FeedbackType.FOLLOW_UP,
                query=query,
                session_id=session_id,
                weight=1.5  # 追问信号更强
            ))
        logger.info(
            "【WangChuan】[Feedback][FollowUp] query=%s nodes=%s session=%s",
            query,
            len(previous_node_ids),
            session_id,
        )
        self.record_batch(signals)

    def on_correction(self, corrected_node_ids: List[str],
                      query: str, session_id: str):
        """
        用户纠正时调用（负反馈）

        用户说"不对"或给出了不同答案，相关节点降权
        """
        signals = []
        for nid in corrected_node_ids:
            signals.append(FeedbackSignal(
                node_id=nid,
                feedback_type=FeedbackType.EXPLICIT_NEGATIVE,
                query=query,
                session_id=session_id,
                weight=2.0  # 纠正信号很强
            ))
        logger.info(
            "【WangChuan】[Feedback][Correction] query=%s nodes=%s session=%s",
            query,
            len(corrected_node_ids),
            session_id,
        )
        self.record_batch(signals)

    def get_node_feedback_score(self, node_id: str) -> Dict:
        """获取节点的反馈统计"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # 当前权重
            row = conn.execute(
                "SELECT fact_confidence FROM gm_nodes WHERE node_id = ?",
                (node_id,)
            ).fetchone()
            current_weight = row['fact_confidence'] if row else None

            # 反馈统计
            rows = conn.execute("""
                SELECT feedback_type, COUNT(*) as cnt, AVG(weight) as avg_w
                FROM gm_feedback
                WHERE node_id = ?
                GROUP BY feedback_type
            """, (node_id,)).fetchall()

            stats = {r['feedback_type']: {'count': r['cnt'], 'avg_weight': r['avg_w']}
                     for r in rows}

            return {
                'node_id': node_id,
                'current_weight': current_weight,
                'feedback_stats': stats,
                'total_feedback': sum(s['count'] for s in stats.values())
            }

    def get_quality_metrics(self) -> Dict:
        """获取整体质量指标"""
        with sqlite3.connect(self.db_path) as conn:
            # 反馈分布
            rows = conn.execute("""
                SELECT feedback_type, COUNT(*) as cnt
                FROM gm_feedback
                GROUP BY feedback_type
            """).fetchall()

            distribution = {r[0]: r[1] for r in rows}
            total = sum(distribution.values())

            # 正负比例
            positive = distribution.get('implicit_use', 0) + \
                       distribution.get('explicit_pos', 0) + \
                       distribution.get('follow_up', 0)
            negative = distribution.get('explicit_neg', 0) + \
                       distribution.get('ignored', 0)

            # 权重分布
            weight_rows = conn.execute("""
                SELECT AVG(fact_confidence), MIN(fact_confidence), MAX(fact_confidence)
                FROM gm_nodes
                WHERE fact_confidence IS NOT NULL
            """).fetchone()

            return {
                'total_signals': total,
                'distribution': distribution,
                'positive_ratio': positive / total if total > 0 else 0,
                'negative_ratio': negative / total if total > 0 else 0,
                'avg_node_weight': weight_rows[0] if weight_rows[0] else None,
                'min_node_weight': weight_rows[1] if weight_rows[1] else None,
                'max_node_weight': weight_rows[2] if weight_rows[2] else None,
            }

    def apply_weights_to_search(self, search_results: List[Dict]) -> List[Dict]:
        """
        将反馈权重应用到搜索结果

        在 HybridRetriever 返回结果后调用，
        用 fact_confidence 调整最终分数
        """
        with sqlite3.connect(self.db_path) as conn:
            for result in search_results:
                node_id = result.get('node_id') or result.get('node', {}).get('node_id')
                if not node_id:
                    continue

                row = conn.execute(
                    "SELECT fact_confidence FROM gm_nodes WHERE node_id = ?",
                    (node_id,)
                ).fetchone()

                if row and row[0] is not None:
                    confidence = row[0]
                    if 'score' in result:
                        result['score'] *= confidence
                    result['feedback_weight'] = confidence

        return search_results
