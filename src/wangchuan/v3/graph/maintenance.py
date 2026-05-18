#!/usr/bin/env python3
"""
忘川 v3.0 - 周期性维护引擎
每 N 轮触发：社区检测 + PageRank 更新 + 社区摘要生成

参考 graph-memory v1.5.4 的 afterTurn 维护逻辑
"""

import logging
import sqlite3
import json
import time
import hashlib
from typing import Dict, List, Optional
from dataclasses import dataclass
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class MaintenanceResult:
    """维护结果"""
    pagerank_updated: int        # 更新了多少节点的 PageRank
    communities_detected: int    # 检测到多少社区
    summaries_generated: int     # 生成了多少摘要
    duration_ms: int             # 耗时毫秒


class MaintenanceEngine:
    """周期性维护引擎"""

    def __init__(self, db_path: str,
                 ppr_damping: float = 0.85,
                 ppr_iterations: int = 20,
                 maintain_interval: int = 7):
        """
        Args:
            db_path: 数据库路径
            ppr_damping: PageRank 阻尼系数
            ppr_iterations: PageRank 迭代次数
            maintain_interval: 每多少轮触发维护
        """
        self.db_path = db_path
        self.ppr_damping = ppr_damping
        self.ppr_iterations = ppr_iterations
        self.maintain_interval = maintain_interval
        self._turn_counters: Dict[str, int] = {}  # session_id -> turn_count

    def on_turn_complete(self, session_id: str) -> Optional[MaintenanceResult]:
        """
        每轮对话完成时调用

        如果达到维护间隔，执行维护；否则只计数
        """
        turns = self._turn_counters.get(session_id, 0) + 1
        self._turn_counters[session_id] = turns

        if turns % self.maintain_interval == 0:
            return self.run_maintenance()

        return None

    def run_maintenance(self) -> MaintenanceResult:
        """执行完整维护流程"""
        start = time.time()

        # 1. 全局 PageRank
        pr_count = self._compute_pagerank()

        # 2. 社区检测
        from .community import detect_communities
        result = detect_communities(self.db_path)

        # 3. 社区摘要生成
        summary_count = 0
        if result.count > 0:
            summary_count = self._generate_summaries(result.communities)

        # 4. 遗忘 decay
        from .forget import ForgettingEngine
        forget = ForgettingEngine(self.db_path)
        decay_result = forget.decay_all()

        # 5. 冲突检测（只检测不自动处理）
        from .conflict import ConflictDetector
        detector = ConflictDetector(self.db_path)
        conflicts = detector.detect()
        if conflicts:
            logger.warning("【WangChuan】[Maintenance][Conflict] detected=%s", len(conflicts))

        duration_ms = int((time.time() - start) * 1000)

        return MaintenanceResult(
            pagerank_updated=pr_count,
            communities_detected=result.count,
            summaries_generated=summary_count,
            duration_ms=duration_ms
        )

    def _compute_pagerank(self) -> int:
        """计算全局 PageRank 并写回数据库"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 获取所有节点
            cursor.execute("SELECT node_id FROM gm_nodes")
            nodes = [row['node_id'] for row in cursor.fetchall()]

            if not nodes:
                return 0

            N = len(nodes)

            # 获取边
            cursor.execute("SELECT source_node_id, target_node_id, weight FROM gm_edges")
            edges = cursor.fetchall()

            # 构建邻接表（出边）
            out_edges = defaultdict(list)
            in_edges = defaultdict(list)

            for e in edges:
                src, tgt, w = e['source_node_id'], e['target_node_id'], e['weight'] or 1.0
                out_edges[src].append((tgt, w))
                in_edges[tgt].append((src, w))

            # 初始化 PR
            pr = {nid: 1.0 / N for nid in nodes}

            # 标记悬挂节点（无出边）——用于均匀重分配
            dangling_nodes = [nid for nid in nodes if nid not in out_edges]

            # 迭代
            for _ in range(self.ppr_iterations):
                new_pr = {}
                # 悬挂节点的等分贡献
                dangling_sum = sum(pr.get(nid, 0) for nid in dangling_nodes) / N

                for nid in nodes:
                    rank_sum = dangling_sum  # 先加上悬挂节点的重分配
                    for src, w in in_edges.get(nid, []):
                        out_degree = len(out_edges.get(src, []))
                        if out_degree > 0:
                            rank_sum += pr.get(src, 0) * w / out_degree
                    new_pr[nid] = (1 - self.ppr_damping) / N + self.ppr_damping * rank_sum
                pr = new_pr

            # 写回数据库
            for nid, score in pr.items():
                cursor.execute(
                    "UPDATE gm_nodes SET pagerank_score = ? WHERE node_id = ?",
                    (score, nid)
                )

            conn.commit()
            return N

    def _generate_summaries(self, communities: Dict[str, List[str]]) -> int:
        """为每个社区生成摘要并存储"""
        from .community import generate_community_summary, generate_community_summary_llm, get_community_members

        # 尝试初始化 LLM 调用
        llm_caller = self._get_llm_caller()

        count = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            for comm_id, members in communities.items():
                member_details = get_community_members(self.db_path, comm_id)
                summary = generate_community_summary_llm(member_details, llm_caller=llm_caller)

                if summary:
                    cursor.execute("""
                        UPDATE gm_communities
                        SET description = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE community_id = ?
                    """, (summary, comm_id))
                    count += 1

            conn.commit()

        return count

    def _get_llm_caller(self):
        """尝试构建 LLM 调用函数，失败返回 None"""
        import os
        api_key = os.getenv('LLM_API_KEY')
        base_url = os.getenv('LLM_BASE_URL')
        if not api_key or not base_url:
            return None

        def llm_call(prompt: str) -> str:
            import urllib.request
            import json as _json
            url = f"{base_url.rstrip('/')}/chat/completions"
            payload = _json.dumps({
                "model": os.getenv('LLM_MODEL', 'doubao-seed-1.6-250615'),
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 100,
                "temperature": 0.3
            }).encode()
            req = urllib.request.Request(url, data=payload, headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}'
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = _json.loads(resp.read())
                return data['choices'][0]['message']['content']

        return llm_call

    def get_stats(self) -> Dict:
        """获取维护统计"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT COUNT(*) FROM gm_nodes")
            node_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM gm_edges")
            edge_count = cursor.fetchone()[0]

            try:
                cursor.execute("SELECT COUNT(*) FROM gm_communities")
                community_count = cursor.fetchone()[0]
            except Exception:
                community_count = 0

            return {
                'nodes': node_count,
                'edges': edge_count,
                'communities': community_count,
                'turn_counters': dict(self._turn_counters),
                'maintain_interval': self.maintain_interval
            }

    def cleanup_session(self, session_id: str):
        """会话结束时清理"""
        self._turn_counters.pop(session_id, None)

    def consolidate_session(self, session_id: str) -> Dict:
        """
        会话结束时巩固记忆

        核心逻辑：
        1. EVENT + SOLVED_BY → SKILL 提升
        2. 更新节点访问时间
        3. 重新计算 PageRank
        """
        promoted = []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()

            # 找到 EVENT → SOLVED_BY → SKILL 的模式
            c.execute("""
                SELECT e.node_id as event_id, e.name as event_name,
                       s.node_id as skill_id, s.name as skill_name
                FROM gm_nodes e
                JOIN gm_edges edge ON e.node_id = edge.source_node_id
                JOIN gm_nodes s ON edge.target_node_id = s.node_id
                WHERE e.node_type = 'EVENT'
                AND edge.edge_type = 'SOLVED_BY'
                AND s.node_type = 'SKILL'
            """)

            for row in c.fetchall():
                # 更新 EVENT 节点的描述，包含解决方案
                c.execute("""
                    UPDATE gm_nodes
                    SET description = ?, last_accessed = CURRENT_TIMESTAMP,
                        access_count = access_count + 1
                    WHERE node_id = ?
                """, (f"已解决: {row['event_name']} → {row['skill_name']}", row['event_id']))

                # 更新 SKILL 节点的访问时间
                c.execute("""
                    UPDATE gm_nodes
                    SET last_accessed = CURRENT_TIMESTAMP,
                        access_count = access_count + 1
                    WHERE node_id = ?
                """, (row['skill_id'],))

                promoted.append({
                    'event': row['event_name'],
                    'skill': row['skill_name']
                })

            conn.commit()

            # 更新温度（所有参与边的节点）
            c.execute("""
                UPDATE gm_nodes SET last_accessed = CURRENT_TIMESTAMP
                WHERE node_id IN (
                    SELECT DISTINCT source_node_id FROM gm_edges
                    UNION
                    SELECT DISTINCT target_node_id FROM gm_edges
                )
            """)
            conn.commit()

        # 清理会话计数
        self.cleanup_session(session_id)

        return {'promoted': promoted, 'count': len(promoted)}
