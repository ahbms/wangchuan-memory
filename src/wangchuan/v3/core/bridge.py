#!/usr/bin/env python3
"""
忘川 v3.0 - 图谱桥接模块 (Bridge)
从消息中提取知识图谱节点和边，实现 gm_messages → gm_nodes/gm_edges 的自动转换

零LLM，纯规则提取，设计目标：
- 每条消息提取 0~5 个节点
- 同一会话内的节点自动建立边
- 增量处理，不重复提取
"""

import sqlite3
import hashlib
import re
import json
import logging
from datetime import datetime
from typing import List, Dict, Tuple, Optional, Set
from collections import defaultdict

from wangchuan.db_utils import get_connection

logger = logging.getLogger(__name__)


# ─── 实体提取规则 ────────────────────────────────────────

# 中文实体模式（按优先级排列）
ENTITY_PATTERNS = [
    # 技术名词（英文大写开头，2-30字符）
    (r'\b([A-Z][a-zA-Z0-9]{1,29})\b', 'TECH', 0.7),
    # 带版本号的技术
    (r'\b((?:Python|Node|Docker|Redis|MySQL|PostgreSQL|Nginx|Linux|Ubuntu|CentOS|macOS|Windows|Git|Kubernetes|K8s|Terraform|Ansible|Jenkins|GitHub|GitLab|VSCode|Vim|Neovim|tmux|iTerm|Chrome|Firefox|Safari|API|SDK|CLI|GUI|HTTP|HTTPS|TCP|UDP|SSH|SSL|TLS|DNS|CDN|AWS|GCP|Azure|Docker|K8s|Kubernetes|Prometheus|Grafana|Elastic|Kibana|Logstash|TensorFlow|PyTorch|React|Vue|Angular|Next|Nuxt|Express|Fastify|Django|Flask|FastAPI|Spring|Rails|Laravel)\s*v?\d*(?:\.\d+)?)\b', 'TECH', 0.75),
    # 中文技术词汇
    (r'(网关|服务|数据库|缓存|日志|配置|部署|重启|更新|升级|安装|卸载|编译|调试|测试|监控|备份|恢复|迁移|优化|重构|修复|部署|上线|回滚)', 'ACTION', 0.6),
    # 任务/项目名称（引号内或特定模式）
    (r'["\「\「]([^"\」\」]{2,40})["\」\」]', 'CONCEPT', 0.7),
    # 错误类型
    (r'((?:\w+Error|Exception|Warning|Warning)\s*(?:[:：]\s*.+)?)', 'ERROR', 0.8),
    # 文件路径
    (r'(/[a-zA-Z0-9_./-]+\.\w{1,10})', 'FILE', 0.65),
    # URL
    (r'(https?://[^\s<>\"]+)', 'URL', 0.7),
    # 命令（以常见命令开头）
    (r'```(?:bash|sh|zsh|shell)?\n?(.*?)```', 'COMMAND', 0.75),
    # 带动作的中文短语（"修复了XX"、"完成了XX"）
    (r'(?:修复|解决|完成|搞定|实现|部署|安装|配置|创建|删除|修改|更新|升级|优化|调试|测试|重启|启动|停止|监控)(?:了|过)?\s*["\「\「]?([\u4e00-\u9fffA-Za-z0-9]{2,20})["\」\」]?', 'TASK', 0.7),
    # 用户偏好标记
    (r'(?:偏好|喜欢|不喜欢|习惯|选择|决定|采用|使用)\s*[：:]\s*(.+)', 'PREFERENCE', 0.7),
    # 规则/铁律
    (r'(?:铁律|规则|禁止|必须|一定不能|永远不要|注意|记住)\s*[：:]\s*(.+)', 'RULE', 0.8),
]

# 信号类型 → 节点类型的映射
SIGNAL_NODE_TYPE_MAP = {
    'error': 'EVENT',
    'correction': 'EVENT',
    'completion': 'EVENT',
    'question': 'FACT',
}


class BridgeExtractor:
    """图谱桥接提取器 - 从消息中提取节点和边"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def extract_from_message(self, msg_id: int, session_id: str,
                             role: str, content: str) -> Tuple[List[Dict], List[Dict]]:
        """
        从单条消息中提取节点和边

        Returns:
            (nodes, edges) - 提取的节点列表和边列表
        """
        if not content or len(content.strip()) < 5:
            return [], []

        nodes = self._extract_entities(content, msg_id, session_id, role)
        edges = []  # 边在批量处理时由 session 上下文创建

        return nodes, edges

    def extract_edges_for_session(self, session_id: str) -> List[Dict]:
        """
        为指定会话创建节点间的边
        同一会话中出现的节点之间建立 RELATED_TO 边
        """
        edges = []
        try:
            with get_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # 获取该会话所有节点（按 first_seen 排序）
                cursor.execute("""
                    SELECT node_id, node_type, name, first_seen, source_message_ids
                    FROM gm_nodes
                    WHERE source_message_ids IS NOT NULL
                    ORDER BY first_seen ASC
                """)

                # 按会话分组节点
                session_nodes = defaultdict(list)
                for row in cursor.fetchall():
                    try:
                        msg_ids = json.loads(row['source_message_ids']) if row['source_message_ids'] else []
                        if msg_ids:
                            # 检查是否属于该会话（参数化 IN 子句）
                            sliced = msg_ids[:20]
                            placeholders = ','.join('?' * len(sliced))
                            cursor.execute(
                                f"SELECT id FROM gm_messages WHERE session_id = ? AND id IN ({placeholders}) LIMIT 1",
                                (session_id, *sliced),
                            )
                            if cursor.fetchone():
                                session_nodes[session_id].append({
                                    'node_id': row['node_id'],
                                    'node_type': row['node_type'],
                                    'name': row['name'],
                                })
                    except (json.JSONDecodeError, TypeError):
                        continue

                # 为同会话节点创建边
                for sid, node_list in session_nodes.items():
                    if len(node_list) < 2:
                        continue

                    for i in range(len(node_list)):
                        for j in range(i + 1, min(i + 4, len(node_list))):  # 最多连3个邻居
                            n1 = node_list[i]
                            n2 = node_list[j]

                            # 跳过相同节点
                            if n1['node_id'] == n2['node_id']:
                                continue

                            # 确定边类型
                            edge_type = self._determine_edge_type(n1['node_type'], n2['node_type'])

                            edge_id = self._generate_edge_id(n1['node_id'], edge_type, n2['node_id'])

                            edges.append({
                                'edge_id': edge_id,
                                'source_node_id': n1['node_id'],
                                'target_node_id': n2['node_id'],
                                'edge_type': edge_type,
                                'weight': 0.6,
                            })

        except Exception as e:
            logger.warning("【WangChuan】[Bridge] extract_edges failed: %s", e)

        return edges

    def _extract_entities(self, content: str, msg_id: int,
                          session_id: str, role: str) -> List[Dict]:
        """使用规则从内容中提取实体"""
        entities = []
        seen_names: Set[str] = set()

        for pattern, entity_type, confidence in ENTITY_PATTERNS:
            try:
                matches = re.finditer(pattern, content, re.MULTILINE | re.IGNORECASE)
                for match in matches:
                    name = match.group(1).strip() if match.lastindex else match.group(0).strip()

                    # 清理和过滤
                    name = self._clean_entity_name(name)
                    if not name or len(name) < 2 or len(name) > 80:
                        continue

                    # 去重（同一消息内）
                    name_key = name.lower()
                    if name_key in seen_names:
                        continue
                    seen_names.add(name_key)

                    # 过滤噪音
                    if self._is_noise(name):
                        continue

                    # 确定节点类型
                    node_type = self._infer_node_type(name, entity_type, content, role)

                    # 生成节点ID
                    node_id = self._generate_node_id(name, node_type)

                    entities.append({
                        'node_id': node_id,
                        'node_type': node_type,
                        'name': name,
                        'description': f"{node_type}: {name}",
                        'confidence': confidence,
                        'source_message_ids': json.dumps([msg_id]),
                    })

            except re.error:
                continue

        return entities

    def _clean_entity_name(self, name: str) -> str:
        """清理实体名称"""
        # 去除首尾标点
        name = name.strip('.,;:!?。，；：！？、 ')
        # 去除多余空格
        name = re.sub(r'\s+', ' ', name)
        # 去除纯数字
        if re.match(r'^\d+$', name):
            return ''
        return name

    def _is_noise(self, name: str) -> bool:
        """判断是否为噪音实体"""
        noise_words = {
            'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will',
            'would', 'could', 'should', 'may', 'might', 'can', 'shall',
            'this', 'that', 'these', 'those', 'it', 'its', 'not', 'no',
            'yes', 'ok', 'okay', 'true', 'false', 'none', 'null',
            'www', 'com', 'org', 'net', 'http', 'https', 'ftp',
            'def', 'class', 'import', 'from', 'return', 'if', 'else',
            'for', 'while', 'in', 'as', 'with', 'to', 'of', 'at',
            '用户', '助手', '消息', '内容', '回复', '输入', '输出',
        }
        return name.lower() in noise_words or len(name) <= 1

    def _infer_node_type(self, name: str, entity_type: str,
                         content: str, role: str) -> str:
        """推断节点类型"""
        # 基于实体提取器的类型
        type_map = {
            'TECH': 'SKILL',
            'ACTION': 'TASK',
            'CONCEPT': 'FACT',
            'ERROR': 'EVENT',
            'FILE': 'FACT',
            'URL': 'FACT',
            'COMMAND': 'SKILL',
            'TASK': 'TASK',
            'PREFERENCE': 'FACT',
            'RULE': 'FACT',
        }

        base_type = type_map.get(entity_type, 'FACT')

        # 上下文调整
        content_lower = content.lower()
        name_lower = name.lower()

        # 错误相关
        if any(kw in content_lower for kw in ['error', 'exception', '失败', '报错', '异常', 'bug']):
            if 'error' in name_lower or 'exception' in name_lower:
                return 'EVENT'

        # 任务相关
        if any(kw in content_lower for kw in ['完成', '搞定', '解决', 'done', 'completed']):
            return 'TASK'

        # 规则相关
        if any(kw in content_lower for kw in ['铁律', '规则', '禁止', '必须', '注意']):
            return 'FACT'

        return base_type

    def _generate_node_id(self, name: str, node_type: str) -> str:
        """生成确定性节点ID"""
        content = f"{node_type}:{name.lower().strip()}"
        hash_val = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"node_{hash_val}"

    def _generate_edge_id(self, source: str, edge_type: str, target: str) -> str:
        """生成确定性边ID"""
        # 排序确保方向无关
        pair = tuple(sorted([source, target]))
        content = f"{pair[0]}|{edge_type}|{pair[1]}"
        hash_val = hashlib.sha256(content.encode()).hexdigest()[:12]
        return f"edge_{hash_val}"

    def _determine_edge_type(self, type1: str, type2: str) -> str:
        """根据节点类型确定边类型"""
        type_pair = tuple(sorted([type1, type2]))

        edge_map = {
            ('EVENT', 'TASK'): 'CAUSES',
            ('EVENT', 'EVENT'): 'RELATED_TO',
            ('FACT', 'FACT'): 'RELATED_TO',
            ('FACT', 'SKILL'): 'REQUIRES',
            ('FACT', 'TASK'): 'RELATED_TO',
            ('SKILL', 'SKILL'): 'RELATED_TO',
            ('SKILL', 'TASK'): 'USED_SKILL',
            ('TASK', 'TASK'): 'RELATED_TO',
        }

        return edge_map.get(type_pair, 'RELATED_TO')

    def save_nodes(self, nodes: List[Dict]) -> int:
        """保存节点到数据库（INSERT OR IGNORE 避免重复）"""
        if not nodes:
            return 0

        saved = 0
        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            for node in nodes:
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO gm_nodes
                        (node_id, node_type, name, description, source_message_ids,
                         first_seen, last_accessed, access_count, pagerank_score, fact_confidence)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """, (
                        node['node_id'],
                        node['node_type'],
                        node['name'],
                        node.get('description', ''),
                        node.get('source_message_ids', '[]'),
                        now, now,
                        node.get('confidence', 0.5) * 0.1,  # 初始 pagerank
                        node.get('confidence', 0.5),
                    ))
                    if cursor.rowcount > 0:
                        saved += 1
                except sqlite3.IntegrityError:
                    # 节点已存在，更新 source_message_ids
                    try:
                        existing = cursor.execute(
                            "SELECT source_message_ids FROM gm_nodes WHERE node_id = ?",
                            (node['node_id'],)
                        ).fetchone()
                        if existing and existing[0]:
                            old_ids = json.loads(existing[0])
                            new_ids = json.loads(node.get('source_message_ids', '[]'))
                            merged = list(dict.fromkeys(old_ids + new_ids))[-50:]  # 最多保留50个，保持顺序
                            cursor.execute(
                                "UPDATE gm_nodes SET source_message_ids = ?, last_accessed = ? WHERE node_id = ?",
                                (json.dumps(merged), now, node['node_id'])
                            )
                    except Exception:
                        pass

            conn.commit()
        return saved

    def save_edges(self, edges: List[Dict]) -> int:
        """保存边到数据库（INSERT OR IGNORE 避免重复）"""
        if not edges:
            return 0

        saved = 0
        with get_connection(self.db_path) as conn:
            cursor = conn.cursor()
            now = datetime.now().isoformat()

            for edge in edges:
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO gm_edges
                        (edge_id, source_node_id, target_node_id, edge_type,
                         weight, first_seen, last_accessed, access_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                    """, (
                        edge['edge_id'],
                        edge['source_node_id'],
                        edge['target_node_id'],
                        edge['edge_type'],
                        edge.get('weight', 0.6),
                        now, now,
                    ))
                    if cursor.rowcount > 0:
                        saved += 1
                except sqlite3.IntegrityError:
                    # 边已存在，增加权重
                    try:
                        cursor.execute("""
                            UPDATE gm_edges
                            SET weight = MIN(1.0, weight + 0.05),
                                access_count = access_count + 1,
                                last_accessed = ?
                            WHERE edge_id = ?
                        """, (now, edge['edge_id']))
                    except Exception:
                        pass

            conn.commit()
        return saved

    def rebuild_fts(self):
        """重建 FTS 索引"""
        try:
            with get_connection(self.db_path) as conn:
                conn.execute("INSERT INTO gm_nodes_fts(gm_nodes_fts) VALUES('rebuild')")
                conn.commit()
        except Exception as e:
            logger.warning("【WangChuan】[Bridge] FTS rebuild failed: %s", e)

    def populate_bridge(self) -> int:
        """
        填充 gm_wangchuan_bridge 表
        将 gm_nodes 与 memories 表中内容相似的记录关联
        """
        bridged = 0
        try:
            with get_connection(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()

                # 获取所有还没有 bridge 的节点
                cursor.execute("""
                    SELECT n.node_id, n.name, n.description
                    FROM gm_nodes n
                    WHERE n.node_id NOT IN (
                        SELECT gm_node_id FROM gm_wangchuan_bridge
                    )
                    LIMIT 500
                """)
                nodes = cursor.fetchall()

                for node in nodes:
                    node_name = (node['name'] or '').strip()
                    node_desc = (node['description'] or '').strip()
                    search_text = f"{node_name} {node_desc}"[:100]

                    if not search_text.strip():
                        continue

                    # 在 memories 表中搜索相似内容
                    try:
                        cursor.execute("""
                            SELECT id, content FROM memories
                            WHERE content LIKE ? OR content LIKE ? OR content LIKE ?
                            LIMIT 3
                        """, (f"%{node_name[:20]}%", f"%{node_desc[:20]}%", f"%{search_text[:30]}%"))

                        for mem_row in cursor.fetchall():
                            cursor.execute("""
                                INSERT OR IGNORE INTO gm_wangchuan_bridge
                                (gm_node_id, wc_memory_id, bridge_type)
                                VALUES (?, ?, ?)
                            """, (node['node_id'], mem_row['id'], 'content_similarity'))
                            if cursor.rowcount > 0:
                                bridged += 1
                    except Exception:
                        continue

                conn.commit()
        except Exception as e:
            logger.warning("【WangChuan】[Bridge] populate_bridge failed: %s", e)

        return bridged


def run_backfill(db_path: str, batch_size: int = 500, limit: int = 0) -> Dict:
    """
    回填现有消息到图谱

    使用单个连接 + 事务批量提交，避免每条消息创建新连接的性能问题。

    Args:
        db_path: 数据库路径
        batch_size: 每批提交的事务大小
        limit: 最大处理消息数（0=全部）

    Returns:
        处理统计
    """
    extractor = BridgeExtractor(db_path)
    stats = {
        'messages_processed': 0,
        'nodes_created': 0,
        'edges_created': 0,
        'bridges_created': 0,
    }

    try:
        with get_connection(db_path, timeout=60) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            cursor = conn.cursor()

            # 获取需要处理的消息（排除 knowledge_base 会话）
            if limit > 0:
                cursor.execute("""
                    SELECT id, session_id, role, content
                    FROM gm_messages
                    WHERE session_id != 'knowledge_base'
                    ORDER BY id ASC
                    LIMIT ?
                """, (limit,))
            else:
                cursor.execute("""
                    SELECT id, session_id, role, content
                    FROM gm_messages
                    WHERE session_id != 'knowledge_base'
                    ORDER BY id ASC
                """)

            all_messages = cursor.fetchall()
            total = len(all_messages)
            logger.info("【WangChuan】[Bridge][Backfill] total messages to process: %d", total)

        # 按会话分组
        sessions = defaultdict(list)
        for msg in all_messages:
            sessions[msg['session_id']].append(msg)

        # 处理每个会话（使用单个连接）
        now = datetime.now().isoformat()
        with get_connection(db_path, timeout=60) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            cursor = conn.cursor()

            for session_id, messages in sessions.items():
                all_nodes = []

                for msg in messages:
                    msg_id = msg['id']
                    role = msg['role']
                    content = msg['content']

                    # 提取节点
                    nodes, _ = extractor.extract_from_message(
                        msg_id, session_id, role, content
                    )

                    if nodes:
                        for node in nodes:
                            try:
                                cursor.execute("""
                                    INSERT OR IGNORE INTO gm_nodes
                                    (node_id, node_type, name, description, source_message_ids,
                                     first_seen, last_accessed, access_count, pagerank_score, fact_confidence)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                                """, (
                                    node['node_id'], node['node_type'], node['name'],
                                    node.get('description', ''), node.get('source_message_ids', '[]'),
                                    now, now,
                                    node.get('confidence', 0.5) * 0.1,
                                    node.get('confidence', 0.5),
                                ))
                                if cursor.rowcount > 0:
                                    stats['nodes_created'] += 1
                                    all_nodes.append(node)
                            except sqlite3.IntegrityError:
                                pass

                    stats['messages_processed'] += 1

                    # 每 batch_size 条消息提交一次 + 进度报告
                    if stats['messages_processed'] % batch_size == 0:
                        conn.commit()
                        logger.info("【WangChuan】[Bridge][Backfill] progress: %d/%d messages, %d nodes, %d edges",
                                   stats['messages_processed'], total,
                                   stats['nodes_created'], stats['edges_created'])

                # 为该会话创建边
                if len(all_nodes) >= 2:
                    for i in range(len(all_nodes)):
                        for j in range(i + 1, min(i + 4, len(all_nodes))):
                            n1 = all_nodes[i]
                            n2 = all_nodes[j]
                            if n1['node_id'] == n2['node_id']:
                                continue
                            edge_type = extractor._determine_edge_type(n1['node_type'], n2['node_type'])
                            edge_id = extractor._generate_edge_id(n1['node_id'], edge_type, n2['node_id'])
                            try:
                                cursor.execute("""
                                    INSERT OR IGNORE INTO gm_edges
                                    (edge_id, source_node_id, target_node_id, edge_type,
                                     weight, first_seen, last_accessed, access_count)
                                    VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                                """, (edge_id, n1['node_id'], n2['node_id'], edge_type, 0.6, now, now))
                                if cursor.rowcount > 0:
                                    stats['edges_created'] += 1
                            except sqlite3.IntegrityError:
                                pass

            # 最终提交
            conn.commit()

        # 重建 FTS
        extractor.rebuild_fts()

        # 填充 bridge
        stats['bridges_created'] = extractor.populate_bridge()

        logger.info("【WangChuan】[Bridge][Backfill] done: %s", stats)

    except Exception as e:
        logger.error("【WangChuan】[Bridge][Backfill] failed: %s", e)
        raise

    return stats
