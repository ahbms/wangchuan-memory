#!/usr/bin/env python3
"""
忘川 v3.0 - 冲突检测
检测知识图谱中的矛盾知识
"""

import logging
import sqlite3
import json
from typing import List, Dict, Tuple
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Conflict:
    """冲突检测结果"""
    node_a: str
    node_b: str
    conflict_type: str  # contradictory / outdated / duplicate
    description: str
    confidence: float


class ConflictDetector:
    """冲突检测器"""
    
    # 冲突关系模式
    CONFLICT_PATTERNS = [
        # 同一主题的不同方案
        ("USED_SKILL", "USED_SKILL", "同一任务使用不同技能"),
        ("SOLVED_BY", "SOLVED_BY", "同一问题有不同解法"),
        ("REQUIRES", "CONFLICTS_WITH", "依赖关系矛盾"),
    ]
    
    def __init__(self, db_path: str):
        self.db_path = db_path
    
    def detect(self) -> List[Conflict]:
        """检测所有冲突"""
        conflicts = []
        
        # 1. 同名不同描述（可能矛盾）
        conflicts.extend(self._detect_name_conflicts())
        
        # 2. 同一源节点的不同 SOLVED_BY（多解法）
        conflicts.extend(self._detect_multiple_solutions())
        
        # 3. CONFLICTS_WITH 边标记的冲突
        conflicts.extend(self._detect_explicit_conflicts())
        
        return conflicts
    
    def _detect_name_conflicts(self) -> List[Conflict]:
        """检测同名节点的描述冲突"""
        conflicts = []
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # 找同名节点
            c.execute("""
                SELECT name, COUNT(*) as cnt, GROUP_CONCAT(node_id, '|') as ids,
                       GROUP_CONCAT(description, '|') as descs
                FROM gm_nodes
                GROUP BY name
                HAVING cnt > 1
            """)
            
            for row in c.fetchall():
                ids = row['ids'].split('|')
                descs = row['descs'].split('|')
                
                # 检查描述是否有矛盾
                for i in range(len(ids)):
                    for j in range(i+1, len(ids)):
                        if self._descriptions_conflict(descs[i], descs[j]):
                            conflicts.append(Conflict(
                                node_a=ids[i],
                                node_b=ids[j],
                                conflict_type="contradictory",
                                description=f"同名节点 '{row['name']}' 有不同描述",
                                confidence=0.6
                            ))
        
        return conflicts
    
    def _detect_multiple_solutions(self) -> List[Conflict]:
        """检测同一问题的多种解法"""
        conflicts = []
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            # 找有多个 SOLVED_BY 的源节点
            c.execute("""
                SELECT e1.source_node_id, 
                       e1.target_node_id as sol1, 
                       e2.target_node_id as sol2,
                       n.name as source_name
                FROM gm_edges e1
                JOIN gm_edges e2 ON e1.source_node_id = e2.source_node_id 
                    AND e1.target_node_id < e2.target_node_id
                JOIN gm_nodes n ON e1.source_node_id = n.node_id
                WHERE e1.edge_type = 'SOLVED_BY' AND e2.edge_type = 'SOLVED_BY'
            """)
            
            for row in c.fetchall():
                conflicts.append(Conflict(
                    node_a=row['sol1'],
                    node_b=row['sol2'],
                    conflict_type="duplicate",
                    description=f"'{row['source_name']}' 有多种解法",
                    confidence=0.4
                ))
        
        return conflicts
    
    def _detect_explicit_conflicts(self) -> List[Conflict]:
        """检测显式标记的冲突边"""
        conflicts = []
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            
            c.execute("""
                SELECT e.source_node_id, e.target_node_id, e.weight,
                       n1.name as name_a, n2.name as name_b
                FROM gm_edges e
                JOIN gm_nodes n1 ON e.source_node_id = n1.node_id
                JOIN gm_nodes n2 ON e.target_node_id = n2.node_id
                WHERE e.edge_type = 'CONFLICTS_WITH'
            """)
            
            for row in c.fetchall():
                conflicts.append(Conflict(
                    node_a=row['source_node_id'],
                    node_b=row['target_node_id'],
                    conflict_type="contradictory",
                    description=f"'{row['name_a']}' 与 '{row['name_b']}' 冲突",
                    confidence=row['weight'] or 0.5
                ))
        
        return conflicts
    
    def _descriptions_conflict(self, desc_a: str, desc_b: str) -> bool:
        """简单判断两个描述是否矛盾"""
        if not desc_a or not desc_b:
            return False
        
        # 完全相同不是冲突
        if desc_a == desc_b:
            return False
        
        # 一个是另一个的子串，通常是补充而非冲突
        if desc_a in desc_b or desc_b in desc_a:
            return False
        
        # 包含矛盾关键词
        conflict_words = ['不', '非', '错误', '不能', '不要', '禁止']
        a_has_neg = any(w in desc_a for w in conflict_words)
        b_has_neg = any(w in desc_b for w in conflict_words)
        
        # 一个有否定词一个没有 → 可能冲突
        if a_has_neg != b_has_neg:
            return True
        
        return False
    
    def resolve(self, conflict: Conflict, keep: str):
        """
        解决冲突：保留一个，删除另一个
        
        Args:
            conflict: 冲突对象
            keep: 要保留的 node_id
        """
        remove = conflict.node_b if keep == conflict.node_a else conflict.node_a
        
        with sqlite3.connect(self.db_path) as conn:
            # 删除节点及其关联边
            conn.execute("DELETE FROM gm_edges WHERE source_node_id = ? OR target_node_id = ?", 
                        (remove, remove))
            conn.execute("DELETE FROM gm_nodes WHERE node_id = ?", (remove,))
            conn.commit()

        logger.warning("【WangChuan】[Conflict][Resolve] keep=%s remove=%s type=%s", keep, remove, conflict.conflict_type)
