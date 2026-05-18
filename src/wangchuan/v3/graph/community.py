#!/usr/bin/env python3
"""
忘川 v3.0 - 社区检测模块
基于 Label Propagation Algorithm（标签传播算法）

参考 graph-memory v1.5.4 实现
"""

import logging
import sqlite3
import json
import random
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class CommunityResult:
    """社区检测结果"""
    labels: Dict[str, str]           # node_id -> community_id
    communities: Dict[str, List[str]]  # community_id -> [node_ids]
    count: int                       # 社区数量


def detect_communities(db_path: str, max_iter: int = 50) -> CommunityResult:
    """
    运行 Label Propagation 社区检测

    原理：每个节点初始自成一个社区，迭代中每个节点采纳邻居中最频繁的社区标签。
    收敛后自然形成社区划分。

    把有向边当无向边处理（知识关联不分方向）
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # 读取活跃节点
        cursor.execute("SELECT node_id FROM gm_nodes")
        node_rows = cursor.fetchall()

        if not node_rows:
            return CommunityResult(labels={}, communities={}, count=0)

        node_ids = [row['node_id'] for row in node_rows]

        # 读取边，构建无向邻接表
        cursor.execute("SELECT source_node_id, target_node_id FROM gm_edges")
        edge_rows = cursor.fetchall()

        node_set = set(node_ids)
        adj = {nid: [] for nid in node_ids}

        for e in edge_rows:
            src, tgt = e['source_node_id'], e['target_node_id']
            if src in node_set and tgt in node_set:
                adj[src].append(tgt)
                adj[tgt].append(src)

    # 初始标签：每个节点 = 自己的 ID
    label = {nid: nid for nid in node_ids}

    # 迭代
    for _iter in range(max_iter):
        changed = False

        # 随机打乱遍历顺序（减少震荡）
        shuffled = list(node_ids)
        random.shuffle(shuffled)

        for nid in shuffled:
            neighbors = adj.get(nid, [])
            if not neighbors:
                continue

            # 统计邻居的标签频率
            label_counts = defaultdict(int)
            for neighbor in neighbors:
                label_counts[label[neighbor]] += 1

            # 选择频率最高的标签（随机打破平局）
            max_count = max(label_counts.values())
            candidates = [lbl for lbl, cnt in label_counts.items() if cnt == max_count]
            new_label = random.choice(candidates)

            if new_label != label[nid]:
                label[nid] = new_label
                changed = True

        if not changed:
            break

    # 聚合结果
    communities = defaultdict(list)
    for nid, lbl in label.items():
        communities[lbl].append(nid)

    # 写回数据库
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # 确保 gm_communities 表存在
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS gm_communities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                community_id TEXT UNIQUE NOT NULL,
                name TEXT,
                description TEXT,
                node_count INTEGER DEFAULT 0,
                dominant_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 清空旧社区
        cursor.execute("DELETE FROM gm_communities")

        # 写入新社区
        for comm_id, members in communities.items():
            # 统计主导类型
            type_counts = defaultdict(int)
            for nid in members:
                cursor.execute("SELECT node_type FROM gm_nodes WHERE node_id = ?", (nid,))
                row = cursor.fetchone()
                if row:
                    type_counts[row[0]] += 1

            dominant_type = max(type_counts, key=type_counts.get) if type_counts else "UNKNOWN"

            cursor.execute("""
                INSERT INTO gm_communities (community_id, name, node_count, dominant_type)
                VALUES (?, ?, ?, ?)
            """, (comm_id, f"社区-{comm_id[:8]}", len(members), dominant_type))

        # 更新节点的 community_id
        for nid, comm_id in label.items():
            cursor.execute("""
                UPDATE gm_nodes SET community_id = ? WHERE node_id = ?
            """, (comm_id, nid))

        conn.commit()

    return CommunityResult(
        labels=dict(label),
        communities=dict(communities),
        count=len(communities)
    )


def get_community_members(db_path: str, community_id: str) -> List[Dict]:
    """获取社区成员节点"""
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        cursor.execute("""
            SELECT node_id, node_type, name, description, pagerank_score
            FROM gm_nodes
            WHERE community_id = ?
            ORDER BY pagerank_score DESC
            LIMIT 10
        """, (community_id,))

        return [dict(row) for row in cursor.fetchall()]


def generate_community_summary(members: List[Dict]) -> str:
    """
    生成社区摘要（规则版，无需LLM）

    格式：[类型分布] 主要节点名
    """
    if not members:
        return ""

    # 类型分布
    type_counts = defaultdict(int)
    names = []
    for m in members:
        type_counts[m.get('node_type', 'UNKNOWN')] += 1
        names.append(m.get('name', ''))

    type_str = ', '.join(f"{t}:{c}" for t, c in sorted(type_counts.items(), key=lambda x: -x[1]))
    name_str = ', '.join(names[:5])

    return f"[{type_str}] {name_str}"


def generate_community_summary_llm(members: List[Dict], llm_caller=None) -> str:
    """
    使用 LLM 生成社区摘要

    如果 llm_caller 为 None，降级到规则版
    """
    if not llm_caller:
        return generate_community_summary(members)

    member_text = "\n".join(
        f"- [{m.get('node_type', '?')}] {m.get('name', '')}: {m.get('description', '')}"
        for m in members[:10]
    )

    prompt = f"""用一句话概括以下知识节点组成的社区主题（20字以内）：

{member_text}

社区主题："""

    try:
        summary = llm_caller(prompt)
        return summary.strip()[:100]
    except Exception as e:
        logger.warning("【WangChuan】[Community][Summary] llm generation failed; fallback to rules: %s", e)
        return generate_community_summary(members)
