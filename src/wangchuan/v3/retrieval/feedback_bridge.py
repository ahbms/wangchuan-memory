#!/usr/bin/env python3
"""
忘川 v3.0 - 反馈闭环桥接层

将反馈系统连接到真实用户交互，解决以下问题：
1. gm_feedback 表全是 regression test 数据，没有真实用户反馈
2. feedback_used / feedback_correction 方法存在但从未被调用
3. 隐式反馈（追问/忽略）未被检测

核心数据流：
用户消息 → 意图检测 → 反馈信号 → gm_feedback → fact_confidence 调整 → 下次召回更准
"""

import sqlite3
import json
import time
import logging
import re
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ============================================================
# 意图检测：从用户消息中提取反馈信号
# ============================================================

class ImplicitFeedbackDetector:
    """
    隐式反馈检测器

    从用户消息序列中推断反馈信号：
    - 追问（follow_up）: 用户在上次回答后继续深入同一话题 → 强正反馈
    - 纠正（correction）: 用户说"不对"、"错了"、"不是这个" → 强负反馈
    - 忽略（ignored）: 召回了但用户完全没提及相关话题 → 弱负反馈
    - 满意（satisfaction）: 用户说"好的"、"明白了"、"谢谢" → 弱正反馈
    """

    # 纠正信号关键词
    CORRECTION_SIGNALS = [
        "不对", "错了", "不是", "不是这个", "不是这样", "搞错了",
        "重来", "重新", "换一个", "不要这个", "不对不对",
        "你说的不对", "方向错了", "不是我要的",
    ]

    # 满意信号关键词
    SATISFACTION_SIGNALS = [
        "好的", "明白了", "懂了", "知道了", "收到", "谢谢", "感谢",
        "不错", "可以", "行", "没问题", "搞定了", "完美",
        "👍", "赞", "棒", "厉害",
    ]

    # 追问信号：问句 + 话题延续
    FOLLOW_UP_PATTERNS = [
        r"那(.+?)呢",
        r"如果(.+?)会怎样",
        r"继续",
        r"还有吗",
        r"然后呢",
        r"再(.+?)一下",
        r"能不能(.+?)",
        r"如何(.+?)",
    ]

    # 低信息量信号（不构成反馈）
    LOW_INFO_SIGNALS = ["你好", "嗯", "哦", "ok", "OK", "嗯嗯", "收到"]

    def detect(
        self,
        user_msg: str,
        previous_context: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        检测用户消息中的反馈信号

        Args:
            user_msg: 当前用户消息
            previous_context: 上次交互上下文 {
                "last_query": str,
                "last_recalled_nodes": List[str],
                "last_assembled_nodes": List[str],
                "timestamp": str,
            }

        Returns:
            {"type": str, "confidence": float, "reason": str} 或 None
        """
        msg = user_msg.strip()

        # 过滤低信息量
        if len(msg) < 3 or msg in self.LOW_INFO_SIGNALS:
            return None

        # 1. 检测纠正
        for signal in self.CORRECTION_SIGNALS:
            if signal in msg:
                return {
                    "type": "correction",
                    "confidence": 0.9,
                    "reason": f"用户表达纠正: '{signal}'",
                }

        # 2. 检测满意
        for signal in self.SATISFACTION_SIGNALS:
            if signal in msg:
                return {
                    "type": "satisfaction",
                    "confidence": 0.6,
                    "reason": f"用户表达满意: '{signal}'",
                }

        # 3. 检测追问（需要上下文）
        if previous_context and previous_context.get("last_recalled_nodes"):
            # 检测是否在同一话题上追问
            for pattern in self.FOLLOW_UP_PATTERNS:
                if re.search(pattern, msg):
                    return {
                        "type": "follow_up",
                        "confidence": 0.7,
                        "reason": f"用户追问同一话题",
                    }

            # 检测话题延续：新消息和上次查询有语义重叠
            last_query = previous_context.get("last_query", "")
            if last_query and self._topic_overlap(msg, last_query):
                return {
                    "type": "follow_up",
                    "confidence": 0.5,
                    "reason": "用户继续同一话题",
                }

        return None

    def _topic_overlap(self, msg1: str, msg2: str) -> bool:
        """简单话题重叠检测（基于字符级 Jaccard）"""
        set1 = set(msg1)
        set2 = set(msg2)
        if not set1 or not set2:
            return False
        intersection = set1 & set2
        union = set1 | set2
        jaccard = len(intersection) / len(union)
        return jaccard > 0.4


# ============================================================
# 反馈闭环桥接器
# ============================================================

class FeedbackBridge:
    """
    反馈闭环桥接器

    职责：
    1. 接收隐式反馈检测结果，写入 gm_feedback
    2. 接收显式反馈（thumbs up/down），写入 gm_feedback
    3. 批量更新 fact_confidence
    4. 提供反馈统计

    不负责：
    - 召回逻辑（由 HybridRetriever 负责）
    - 上下文组装（由 Assembler 负责）
    """

    # 反馈类型 → fact_confidence 调整量
    CONFIDENCE_ADJUSTMENTS = {
        "satisfaction": 0.05,       # 满意：微弱正向
        "follow_up": 0.08,         # 追问：中等正向（说明召回有用）
        "implicit_use": 0.02,      # 被组装进上下文：微弱正向
        "ignored": -0.03,          # 被忽略：微弱负向
        "correction": -0.15,       # 纠正：强负向
        "explicit_pos": 0.12,      # 明确点赞：强正向
        "explicit_neg": -0.18,     # 明确点踩：强负向
    }

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_tables()

    def _ensure_tables(self):
        """确保反馈表和索引存在"""
        with sqlite3.connect(self.db_path) as conn:
            # gm_feedback 表（v3 主表）
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
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_feedback_created
                ON gm_feedback(created_at)
            """)

            # feedback 表（兼容旧接口）
            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    message_id TEXT,
                    feedback_type TEXT,
                    weight REAL,
                    timestamp TEXT,
                    content TEXT,
                    context TEXT,
                    source TEXT
                )
            """)
            conn.commit()

    # ----------------------------------------------------------
    # 显式反馈：用户主动点赞/点踩
    # ----------------------------------------------------------

    def record_explicit_feedback(
        self,
        node_id: str,
        positive: bool,
        session_id: str = "",
        query: str = "",
        message_id: str = "",
    ) -> Dict:
        """
        记录显式反馈（用户点赞/点踩）

        Args:
            node_id: 记忆节点ID
            positive: True=点赞, False=点踩
            session_id: 会话ID
            query: 触发召回的查询
            message_id: 用户消息ID

        Returns:
            {"success": bool, "adjustment": float, "new_confidence": float}
        """
        feedback_type = "explicit_pos" if positive else "explicit_neg"
        adjustment = self.CONFIDENCE_ADJUSTMENTS[feedback_type]

        # 写入 gm_feedback
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO gm_feedback
                (node_id, feedback_type, query, session_id, weight, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                node_id,
                feedback_type,
                query,
                session_id,
                1.0,
                json.dumps({"message_id": message_id, "source": "explicit"}),
            ))
            conn.commit()

        # 调整 fact_confidence
        new_confidence = self._adjust_confidence(node_id, adjustment)

        # 写入旧 feedback 表（兼容）
        self._record_legacy_feedback(
            feedback_type=feedback_type,
            session_id=session_id,
            message_id=message_id,
            content=query,
            weight=1.0,
        )

        logger.info(
            "【WangChuan】[FeedbackBridge][Explicit] node=%s positive=%s adjustment=%.3f new=%.3f",
            node_id, positive, adjustment, new_confidence,
        )

        return {
            "success": True,
            "adjustment": adjustment,
            "new_confidence": new_confidence,
        }

    # ----------------------------------------------------------
    # 隐式反馈：从用户交互中自动检测
    # ----------------------------------------------------------

    def record_implicit_feedback(
        self,
        node_ids: List[str],
        feedback_type: str,
        session_id: str = "",
        query: str = "",
        confidence: float = 1.0,
    ) -> Dict:
        """
        记录隐式反馈

        Args:
            node_ids: 涉及的节点ID列表
            feedback_type: 反馈类型 (satisfaction/follow_up/implicit_use/ignored/correction)
            session_id: 会话ID
            query: 原始查询
            confidence: 检测置信度

        Returns:
            {"success": bool, "recorded": int, "adjustments": Dict[str, float]}
        """
        if not node_ids:
            return {"success": True, "recorded": 0, "adjustments": {}}

        base_adjustment = self.CONFIDENCE_ADJUSTMENTS.get(feedback_type, 0)
        adjustment = base_adjustment * confidence

        recorded = 0
        adjustments = {}

        with sqlite3.connect(self.db_path) as conn:
            for nid in node_ids:
                conn.execute("""
                    INSERT INTO gm_feedback
                    (node_id, feedback_type, query, session_id, weight, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    nid,
                    feedback_type,
                    query,
                    session_id,
                    confidence,
                    json.dumps({"detection_confidence": confidence, "source": "implicit"}),
                ))
                recorded += 1

                # 调整 confidence
                new_conf = self._adjust_confidence(nid, adjustment)
                adjustments[nid] = new_conf

            conn.commit()

        logger.info(
            "【WangChuan】[FeedbackBridge][Implicit] type=%s nodes=%d recorded=%d adjustment=%.3f",
            feedback_type, len(node_ids), recorded, adjustment,
        )

        return {
            "success": True,
            "recorded": recorded,
            "adjustments": adjustments,
        }

    # ----------------------------------------------------------
    # 会话级反馈处理
    # ----------------------------------------------------------

    def process_user_message(
        self,
        user_msg: str,
        session_id: str,
        previous_context: Optional[Dict] = None,
        current_recalled_nodes: Optional[List[str]] = None,
        current_assembled_nodes: Optional[List[str]] = None,
    ) -> Dict:
        """
        处理用户消息，自动检测并记录反馈

        这是主入口：每次收到用户消息时调用

        Args:
            user_msg: 用户消息
            session_id: 会话ID
            previous_context: 上次交互上下文
            current_recalled_nodes: 本次召回的节点
            current_assembled_nodes: 本次组装进上下文的节点

        Returns:
            {"detected": Optional[Dict], "feedback_recorded": bool, "adjustments": Dict}
        """
        detector = ImplicitFeedbackDetector()
        detected = detector.detect(user_msg, previous_context)

        result = {
            "detected": detected,
            "feedback_recorded": False,
            "adjustments": {},
        }

        if not detected:
            # 没有检测到反馈信号
            return result

        feedback_type = detected["type"]
        confidence = detected["confidence"]

        # 根据反馈类型选择节点
        if feedback_type == "correction":
            # 纠正：对上次召回的所有节点施加负反馈
            nodes = previous_context.get("last_recalled_nodes", []) if previous_context else []
            if not nodes:
                nodes = current_recalled_nodes or []
        elif feedback_type in ("follow_up", "satisfaction"):
            # 追问/满意：对上次组装进上下文的节点施加正反馈
            nodes = previous_context.get("last_assembled_nodes", []) if previous_context else []
            if not nodes:
                nodes = current_assembled_nodes or []
        else:
            nodes = current_assembled_nodes or []

        if nodes:
            adj_result = self.record_implicit_feedback(
                node_ids=nodes,
                feedback_type=feedback_type,
                session_id=session_id,
                query=user_msg,
                confidence=confidence,
            )
            result["feedback_recorded"] = adj_result["success"]
            result["adjustments"] = adj_result["adjustments"]

        return result

    # ----------------------------------------------------------
    # 内部方法
    # ----------------------------------------------------------

    def _adjust_confidence(self, node_id: str, adjustment: float) -> float:
        """调整节点的 fact_confidence，clamp 到 [0.05, 1.0]"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT fact_confidence FROM gm_nodes WHERE node_id = ?",
                (node_id,)
            ).fetchone()

            current = row[0] if row and row[0] is not None else 0.5
            new_conf = max(0.05, min(1.0, current + adjustment))

            conn.execute("""
                UPDATE gm_nodes
                SET fact_confidence = ?, last_accessed = CURRENT_TIMESTAMP,
                    access_count = access_count + 1
                WHERE node_id = ?
            """, (new_conf, node_id))
            conn.commit()

        return new_conf

    def _record_legacy_feedback(
        self,
        feedback_type: str,
        session_id: str = "",
        message_id: str = "",
        content: str = "",
        weight: float = 1.0,
    ):
        """写入旧 feedback 表（兼容旧接口）"""
        import hashlib
        fb_id = "fb_" + hashlib.md5(
            f"{session_id}:{message_id}:{time.time()}".encode()
        ).hexdigest()[:16]

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO feedback
                (id, session_id, message_id, feedback_type, weight, timestamp, content, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'bridge')
            """, (
                fb_id,
                session_id,
                message_id,
                feedback_type,
                weight,
                datetime.now().isoformat(),
                content,
            ))
            conn.commit()

    # ----------------------------------------------------------
    # 统计和查询
    # ----------------------------------------------------------

    def get_session_feedback_summary(self, session_id: str) -> Dict:
        """获取某个会话的反馈统计"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # 反馈分布
            rows = conn.execute("""
                SELECT feedback_type, COUNT(*) as cnt, AVG(weight) as avg_w
                FROM gm_feedback
                WHERE session_id = ?
                GROUP BY feedback_type
            """, (session_id,)).fetchall()

            distribution = {
                r["feedback_type"]: {"count": r["cnt"], "avg_weight": r["avg_w"]}
                for r in rows
            }
            total = sum(d["count"] for d in distribution.values())

            return {
                "session_id": session_id,
                "total_signals": total,
                "distribution": distribution,
            }

    def get_node_feedback_history(self, node_id: str, limit: int = 20) -> List[Dict]:
        """获取节点的反馈历史"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT feedback_type, query, session_id, weight, created_at, metadata
                FROM gm_feedback
                WHERE node_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (node_id, limit)).fetchall()

            return [
                {
                    "feedback_type": r["feedback_type"],
                    "query": r["query"],
                    "session_id": r["session_id"],
                    "weight": r["weight"],
                    "created_at": r["created_at"],
                    "metadata": json.loads(r["metadata"]) if r["metadata"] else {},
                }
                for r in rows
            ]

    def get_quality_report(self) -> Dict:
        """获取反馈系统质量报告"""
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
            positive = sum(distribution.get(t, 0) for t in
                          ["implicit_use", "explicit_pos", "follow_up", "satisfaction"])
            negative = sum(distribution.get(t, 0) for t in
                          ["explicit_neg", "ignored", "correction"])

            # 自动 vs 手动
            auto_sessions = conn.execute("""
                SELECT COUNT(*) FROM gm_feedback
                WHERE session_id LIKE '%regression%' OR session_id LIKE '%test%'
            """).fetchone()[0]
            manual_sessions = total - auto_sessions

            # fact_confidence 分布
            conf_row = conn.execute("""
                SELECT AVG(fact_confidence), MIN(fact_confidence), MAX(fact_confidence),
                       COUNT(CASE WHEN fact_confidence > 0.6 THEN 1 END),
                       COUNT(CASE WHEN fact_confidence < 0.3 THEN 1 END)
                FROM gm_nodes
                WHERE fact_confidence IS NOT NULL
            """).fetchone()

            return {
                "total_signals": total,
                "distribution": distribution,
                "positive_ratio": positive / total if total > 0 else 0,
                "negative_ratio": negative / total if total > 0 else 0,
                "auto_signals": auto_sessions,
                "real_user_signals": manual_sessions,
                "confidence_stats": {
                    "avg": conf_row[0],
                    "min": conf_row[1],
                    "max": conf_row[2],
                    "high_count": conf_row[3],
                    "low_count": conf_row[4],
                } if conf_row[0] else None,
            }
