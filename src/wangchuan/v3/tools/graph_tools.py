#!/usr/bin/env python3
"""
忘川 v3 图谱工具 - 接入 L4 利器
提供 graph_stats 和 graph_search 两个工具
"""

import sqlite3
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from wangchuan.memory_api import Memory
from wangchuan.fts_utils import build_safe_fts_match_query


WORKSPACE_ROOT = Path(os.getenv("OPENCLAW_WORKSPACE", Path(__file__).resolve().parents[4]))
# 默认数据库路径（与忘川 v3 共享）
DEFAULT_DB = str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite")


class GraphTools:
    """图谱查询工具集"""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or DEFAULT_DB
    
    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def stats(self) -> Dict:
        """gm_stats: 图谱统计概览"""
        with self._connect() as conn:
            c = conn.cursor()
            
            stats = {}
            
            # 节点统计
            c.execute("SELECT COUNT(*) FROM gm_nodes")
            stats["total_nodes"] = c.fetchone()[0]
            
            c.execute("SELECT node_type, COUNT(*) as cnt FROM gm_nodes GROUP BY node_type")
            stats["nodes_by_type"] = {row["node_type"]: row["cnt"] for row in c.fetchall()}
            
            # 边统计
            c.execute("SELECT COUNT(*) FROM gm_edges")
            stats["total_edges"] = c.fetchone()[0]
            
            c.execute("SELECT edge_type, COUNT(*) as cnt FROM gm_edges GROUP BY edge_type")
            stats["edges_by_type"] = {row["edge_type"]: row["cnt"] for row in c.fetchall()}
            
            # 社区统计
            try:
                c.execute("SELECT COUNT(*) FROM gm_communities")
                stats["total_communities"] = c.fetchone()[0]
            except Exception as e:
                logger.error(f"Graph tool error: {e}")
                stats["total_communities"] = 0
            
            # 消息统计
            try:
                c.execute("SELECT COUNT(*) FROM gm_messages")
                stats["total_messages"] = c.fetchone()[0]
            except Exception as e:
                logger.error(f"Graph tool error: {e}")
                stats["total_messages"] = 0
            
            # Top 节点 (按 PageRank)
            c.execute("""
                SELECT name, node_type, pagerank_score 
                FROM gm_nodes 
                ORDER BY pagerank_score DESC 
                LIMIT 5
            """)
            stats["top_nodes"] = [
                {"name": r["name"], "type": r["node_type"], "pagerank": round(r["pagerank_score"], 4)}
                for r in c.fetchall()
            ]
            
            return stats
    
    def search(self, query: str, top_k: int = 5) -> Dict:
        """gm_search: 搜索图谱节点"""
        # P5-05 延伸：图谱工具搜索入口也优先走统一结构化 recall，
        # 让工具侧默认消费 `memory_schema_index` + sidecar 真值层；
        # gm_nodes 图搜索继续保留为 fallback，同时保持原有
        # {nodes, edges} 结果形状兼容。
        try:
            memory = Memory(db_path=self.db_path)
            rows = memory.recall(query, limit=top_k)
            structured_nodes = []
            for row in rows:
                structured_nodes.append({
                    "memory_id": row.get("memory_id"),
                    "type": row.get("memory_type") or row.get("type") or "unknown",
                    "name": row.get("content", "")[:80],
                    "description": row.get("content", ""),
                    "score": row.get("score"),
                    "source_layer": row.get("source_layer"),
                    "source_anchor": row.get("source_anchor"),
                    "source_session": row.get("source_session"),
                    "turn_signature": row.get("turn_signature"),
                    "lifecycle": row.get("lifecycle"),
                    "promotion_state": row.get("promotion_state"),
                    "recall_source_type": row.get("recall_source_type"),
                    "quality_score": row.get("quality_score"),
                    "schema_version": row.get("schema_version"),
                    "reader": row.get("reader") or "memory_api.recall",
                    "structured": True,
                    "source": "memory_api.recall",
                })
            if structured_nodes:
                return {"nodes": structured_nodes, "edges": []}
        except Exception:
            pass

        with self._connect() as conn:
            c = conn.cursor()
            results = []

            # 1. FTS5 搜索
            match_query = build_safe_fts_match_query(query, max_terms=10)
            if match_query:
                try:
                    c.execute("""
                        SELECT n.node_id, n.node_type, n.name, n.description,
                               n.pagerank_score, n.community_id
                        FROM gm_nodes n
                        JOIN gm_nodes_fts fts ON n.id = fts.rowid
                        WHERE gm_nodes_fts MATCH ?
                        ORDER BY n.pagerank_score DESC
                        LIMIT ?
                    """, (match_query, top_k))

                    for row in c.fetchall():
                        results.append({
                            "node_id": row["node_id"],
                            "type": row["node_type"],
                            "name": row["name"],
                            "description": row["description"],
                            "pagerank": round(row["pagerank_score"], 4),
                            "source": "fts",
                            "reader": "gm_nodes_fallback",
                            "structured": False,
                        })
                except Exception:
                    pass

            # 2. LIKE 搜索（FTS5 不支持中文分词，LIKE 作为主搜索）
            if len(results) < top_k:
                existing_ids = {r["node_id"] for r in results}
                remaining = top_k - len(results)

                if existing_ids:
                    placeholders_ex = ",".join("?" * len(existing_ids))
                    c.execute(f"""
                        SELECT node_id, node_type, name, description, pagerank_score
                        FROM gm_nodes
                        WHERE (name LIKE ? OR description LIKE ? OR content LIKE ?)
                        AND node_id NOT IN ({placeholders_ex})
                        ORDER BY pagerank_score DESC
                        LIMIT ?
                    """, (f"%{query}%", f"%{query}%", f"%{query}%", *existing_ids, remaining))
                else:
                    c.execute("""
                        SELECT node_id, node_type, name, description, pagerank_score
                        FROM gm_nodes
                        WHERE name LIKE ? OR description LIKE ? OR content LIKE ?
                        ORDER BY pagerank_score DESC
                        LIMIT ?
                    """, (f"%{query}%", f"%{query}%", f"%{query}%", remaining))

                for row in c.fetchall():
                    results.append({
                        "node_id": row["node_id"],
                        "type": row["node_type"],
                        "name": row["name"],
                        "description": row["description"],
                        "pagerank": round(row["pagerank_score"], 4),
                        "source": "like",
                        "reader": "gm_nodes_fallback",
                        "structured": False,
                    })

            # 3. 获取关联边
            if results:
                node_ids = [r["node_id"] for r in results]
                placeholders = ",".join("?" * len(node_ids))

                c.execute(f"""
                    SELECT source_node_id, target_node_id, edge_type, weight
                    FROM gm_edges
                    WHERE source_node_id IN ({placeholders})
                    OR target_node_id IN ({placeholders})
                    LIMIT 20
                """, (*node_ids, *node_ids))

                edges = [
                    {
                        "from": row["source_node_id"],
                        "to": row["target_node_id"],
                        "type": row["edge_type"],
                        "weight": round(row["weight"], 2)
                    }
                    for row in c.fetchall()
                ]

                return {"nodes": results, "edges": edges}

            return {"nodes": results, "edges": []}
    
    def node_detail(self, node_id: str) -> Optional[Dict]:
        """获取节点详情（含关联边和溯源消息）"""
        with self._connect() as conn:
            c = conn.cursor()
            
            # 节点信息
            c.execute("SELECT * FROM gm_nodes WHERE node_id = ?", (node_id,))
            node = c.fetchone()
            if not node:
                return None
            
            result = dict(node)
            
            # 关联边
            c.execute("""
                SELECT source_node_id, target_node_id, edge_type, weight
                FROM gm_edges
                WHERE source_node_id = ? OR target_node_id = ?
            """, (node_id, node_id))
            
            result["edges"] = [
                {"from": r["source_node_id"], "to": r["target_node_id"], 
                 "type": r["edge_type"], "weight": round(r["weight"], 2)}
                for r in c.fetchall()
            ]
            
            # 溯源消息
            source_ids = result.get("source_message_ids")
            if source_ids:
                try:
                    msg_ids = json.loads(source_ids) if isinstance(source_ids, str) else source_ids
                    if msg_ids:
                        placeholders = ",".join("?" * min(len(msg_ids), 10))
                        c.execute(f"""
                            SELECT role, content, timestamp
                            FROM gm_messages
                            WHERE id IN ({placeholders})
                            ORDER BY timestamp ASC
                            LIMIT 10
                        """, msg_ids[:10])
                        
                        result["source_messages"] = [
                            {"role": r["role"], "content": r["content"][:200], "time": r["timestamp"]}
                            for r in c.fetchall()
                        ]
                except (json.JSONDecodeError, TypeError):
                    pass
            
            return result
    
    def communities(self) -> List[Dict]:
        """查看所有社区"""
        with self._connect() as conn:
            c = conn.cursor()
            
            try:
                c.execute("""
                    SELECT community_id, name, description, node_count, dominant_type
                    FROM gm_communities
                    ORDER BY node_count DESC
                """)
                
                communities = []
                for row in c.fetchall():
                    comm = dict(row)
                    
                    # 获取社区成员
                    c2 = conn.cursor()
                    c2.execute("""
                        SELECT name, node_type FROM gm_nodes
                        WHERE community_id = ?
                        ORDER BY pagerank_score DESC
                        LIMIT 5
                    """, (comm["community_id"],))
                    
                    comm["members"] = [f"{r['node_type']}:{r['name']}" for r in c2.fetchall()]
                    communities.append(comm)
                
                return communities
            except Exception as e:
                logger.error(f"Graph tool error: {e}")
                return []


# ─── 格式化输出（供 agent 直接使用）───────────────────

def format_stats(db_path: str = None) -> str:
    """格式化图谱统计"""
    tools = GraphTools(db_path)
    s = tools.stats()
    
    lines = [f"📊 图谱统计: {s['total_nodes']}节点, {s['total_edges']}边, {s['total_communities']}社区, {s['total_messages']}消息"]
    
    if s["nodes_by_type"]:
        type_str = ", ".join(f"{t}:{c}" for t, c in s["nodes_by_type"].items())
        lines.append(f"  节点类型: {type_str}")
    
    if s["edges_by_type"]:
        type_str = ", ".join(f"{t}:{c}" for t, c in s["edges_by_type"].items())
        lines.append(f"  边类型: {type_str}")
    
    if s["top_nodes"]:
        lines.append("  Top节点:")
        for n in s["top_nodes"]:
            lines.append(f"    [{n['type']}] {n['name']} (PR:{n['pagerank']})")
    
    return "\n".join(lines)


def format_search(query: str, db_path: str = None, top_k: int = 5) -> str:
    """格式化图谱搜索结果"""
    tools = GraphTools(db_path)
    result = tools.search(query, top_k)
    
    if not result["nodes"]:
        return f"图谱搜索 '{query}': 无结果"
    
    lines = [f"🔍 图谱搜索 '{query}': {len(result['nodes'])}个节点"]
    
    for n in result["nodes"]:
        lines.append(f"  [{n['type']}] {n['name']} (PR:{n['pagerank']}, via:{n['source']})")
        if n.get("description"):
            lines.append(f"    {n['description'][:80]}")
    
    if result["edges"]:
        lines.append(f"  关联边: {len(result['edges'])}条")
        for e in result["edges"][:5]:
            lines.append(f"    {e['from']} --{e['type']}--> {e['to']}")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else None
    
    print(format_stats(db))
    print()
    print(format_search("Docker", db))
