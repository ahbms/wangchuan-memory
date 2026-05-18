#!/usr/bin/env python3
"""
忘川 v3.0 - 遗忘机制
定期衰减不常用知识的权重
"""

import logging
import sqlite3
import math
from datetime import datetime, timedelta
from typing import Dict

logger = logging.getLogger(__name__)


class ForgettingEngine:
    """
    遗忘引擎
    
    核心逻辑：
    - 越久没访问的节点，pagerank_score 衰减越快
    - 被纠正过的节点衰减更快
    - 有正反馈的节点衰减更慢
    """
    
    def __init__(self, db_path: str,
                 half_life_days: int = 30,
                 min_score: float = 0.001):
        self.db_path = db_path
        self.half_life_days = half_life_days
        self.min_score = min_score
    
    def decay_all(self) -> Dict:
        """
        对所有节点执行衰减
        
        Returns:
            衰减统计
        """
        now = datetime.now()
        decay_factor = math.log(2) / self.half_life_days
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            c.execute("SELECT node_id, pagerank_score, last_accessed, access_count, fact_confidence FROM gm_nodes")
            nodes = c.fetchall()
            
            updated = 0
            forgotten = 0
            
            for node in nodes:
                score = node['pagerank_score'] or 0.1
                last_accessed = node['last_accessed']
                access_count = node['access_count'] or 0
                confidence = node['fact_confidence'] or 0.5
                
                if last_accessed:
                    try:
                        last = datetime.fromisoformat(str(last_accessed).replace('Z', '+00:00'))
                    except (ValueError, TypeError) as e:
                        logger.warning("【WangChuan】[Forget][Decay] last_accessed parse failed for node=%s: %s", node['node_id'], e)
                        last = now - timedelta(days=365)
                else:
                    last = now - timedelta(days=365)
                
                days_since = (now - last).days
                
                # 指数衰减
                decay = math.exp(-decay_factor * days_since)
                
                # 反馈权重影响衰减速度
                # 高权重节点衰减更慢
                confidence_factor = 0.5 + confidence * 0.5
                
                # 访问次数越多衰减越慢，但不应把分数反向抬高到超过原始值
                # 因此这里让 access_factor 在 (0.85, 1.0) 区间内随访问次数单调上升
                access_log = math.log(1 + access_count)
                access_factor = 0.85 + 0.15 * (access_log / (1.0 + access_log))
                
                new_score = score * decay * confidence_factor * access_factor
                new_score = max(self.min_score, new_score)
                
                if abs(new_score - score) > 0.001:
                    conn.execute(
                        "UPDATE gm_nodes SET pagerank_score = ? WHERE node_id = ?",
                        (new_score, node['node_id'])
                    )
                    updated += 1
                
                if new_score <= self.min_score * 2:
                    forgotten += 1
            
            conn.commit()
        
        return {
            'total_nodes': len(nodes),
            'updated': updated,
            'near_forgotten': forgotten,
            'half_life_days': self.half_life_days
        }
    
    def prune_forgotten(self, threshold: float = 0.005) -> int:
        """
        清除极低权重的节点（真正的遗忘）
        
        Returns:
            删除的节点数
        """
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            
            # 找到极低权重且很久没访问的节点
            c.execute("""
                SELECT node_id FROM gm_nodes
                WHERE pagerank_score < ?
                AND (last_accessed IS NULL OR last_accessed < datetime('now', '-60 days'))
                AND access_count < 2
            """, (threshold,))
            
            to_delete = [row[0] for row in c.fetchall()]
            
            if to_delete:
                placeholders = ','.join('?' * len(to_delete))
                conn.execute(f"DELETE FROM gm_edges WHERE source_node_id IN ({placeholders}) OR target_node_id IN ({placeholders})", 
                           (*to_delete, *to_delete))
                conn.execute(f"DELETE FROM gm_nodes WHERE node_id IN ({placeholders})", to_delete)
                conn.commit()
                logger.warning("【WangChuan】[Forget][Prune] deleted_nodes=%s threshold=%s", len(to_delete), threshold)
            
            return len(to_delete)
