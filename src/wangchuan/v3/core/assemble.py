#!/usr/bin/env python3
"""
忘川 v3.0 - 上下文组装模块 (Assemble)
零LLM处理，组装图谱上下文
"""

import sqlite3
import json
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass

from ...fts_utils import build_safe_fts_match_query, tokenize_search_terms
from wangchuan._adapters.context_adapter import get_session_state_store as _get_session_state_store

logger = logging.getLogger(__name__)

@dataclass
class ContextAssembly:
    """组装后的上下文"""
    graph_xml: str           # 图谱子图XML
    fresh_tail: List[Dict]   # 原始消息尾巴
    total_tokens: int        # 估算总token数
    node_count: int          # 图谱节点数
    episodic_xml: str = ""        # 溯源选拉XML
    episodic_tokens: int = 0      # 溯源选拉token数
    dag_summary: str = ""         # DAG 多级摘要
    session_summary: str = ""     # 外置会话摘要
    task_checkpoint: str = ""     # 外置任务状态
    handoff_pack: str = ""        # 外置交接包
    evidence_pack: str = ""       # 证据/锚点包
    stable_prefix: str = ""       # 稳定前缀（适合缓存）
    dynamic_suffix: str = ""      # 动态后缀（每轮变化）

class AssembleEngine:
    """上下文组装引擎"""

    def __init__(self, db_path: str, max_context_nodes: int = 20, fresh_tail_messages: int = 6, max_context_tokens: int = 2000):
        self.db_path = db_path
        self.max_context_nodes = max_context_nodes
        self.fresh_tail_messages = fresh_tail_messages
        self.max_context_tokens = max_context_tokens
        self.state_store = _get_session_state_store()
        
        # 反馈闭环引擎
        try:
            from ..retrieval.feedback import FeedbackEngine
            self.feedback = FeedbackEngine(db_path)
        except Exception:
            self.feedback = None

    def assemble(self, session_id: str, query: Optional[str] = None) -> ContextAssembly:
        """
        组装上下文

        Args:
            session_id: 当前会话ID
            query: 当前查询(用于PPR排序)

        Returns:
            ContextAssembly: 组装后的上下文
        """
        # 1. 获取新鲜尾巴 (最近N条原始消息)
        fresh_tail = self._get_fresh_tail(session_id)

        # 2. 获取相关图谱节点
        if query:
            relevant_nodes = self._get_relevant_nodes_with_ppr(query)
        else:
            relevant_nodes = self._get_recent_nodes()

        # 3. 构建图谱XML
        graph_xml = self._build_graph_xml(relevant_nodes)

        # 4. 溯源选拉 (PPR top 3 节点的原始对话)
        episodic_msgs = self._get_episodic_messages(relevant_nodes)
        episodic_xml = self._build_episodic_xml(episodic_msgs)
        episodic_tokens = len(episodic_xml) // 4

        # 4. 压缩（可选优化：不超长就不压缩）
        try:
            from .compressor import ContextCompressor
            compressor = ContextCompressor(max_tokens=self.max_context_tokens)
            graph_xml = compressor.compress_graph_xml(graph_xml)
            episodic_xml = compressor.compress_episodic(episodic_xml)
        except Exception as e:
            logger.warning("【WangChuan】[Assemble][Compress] context compression failed: %s", e)

        # 5. 计算token数
        session_summary_text = self._build_session_summary_text(session_id)
        task_checkpoint_text = self._build_task_checkpoint_text(session_id)
        handoff_pack_text = self._build_handoff_pack_text(session_id)
        evidence_pack_text = self._build_evidence_pack_text(session_id)
        total_tokens = self._estimate_tokens(graph_xml, fresh_tail) + episodic_tokens
        total_tokens += len(session_summary_text) // 4 + len(task_checkpoint_text) // 4 + len(handoff_pack_text) // 4
        total_tokens += len(evidence_pack_text) // 4

        
        # 记录隐式反馈
        if hasattr(self, "feedback") and self.feedback:
            try:
                all_ids = [n["node_id"] for n in relevant_nodes]
                assembled_ids = all_ids[:self.max_context_nodes]
                self.feedback.on_recall_used(
                    query=query or "",
                    session_id=session_id,
                    recalled_node_ids=all_ids,
                    assembled_node_ids=assembled_ids
                )
            except Exception as e:
                logger.warning("【WangChuan】[Assemble][Feedback] on_recall_used failed: %s", e)
        # 6. DAG 多级摘要
        dag_summary = ""
        try:
            from .dag_compressor import DAGCompressor
            dag = DAGCompressor(self.db_path)
            condensed = dag.get_session_summaries(session_id, level=2, limit=3)
            dag_summary = "\n".join(condensed) if condensed else ""
        except Exception as e:
            logger.warning("【WangChuan】[Assemble][DAG] get_session_summaries failed: %s", e)

        return ContextAssembly(
            graph_xml=graph_xml,
            fresh_tail=fresh_tail,
            total_tokens=total_tokens,
            node_count=len(relevant_nodes),
            episodic_xml=episodic_xml,
            episodic_tokens=episodic_tokens,
            dag_summary=dag_summary,
            session_summary=session_summary_text,
            task_checkpoint=task_checkpoint_text,
            handoff_pack=handoff_pack_text,
            evidence_pack=evidence_pack_text,
        )

    def _get_fresh_tail(self, session_id: str) -> List[Dict]:
        """获取最近N条原始消息"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT id, role, content, timestamp
                FROM gm_messages
                WHERE session_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (session_id, self.fresh_tail_messages))

            rows = cursor.fetchall()
            # 反转顺序(从早到晚)
            return [dict(row) for row in reversed(rows)]

    def _get_relevant_nodes_with_ppr(self, query: str) -> List[Dict]:
        """使用PPR获取相关节点"""
        # 简化实现：先通过FTS5找到种子节点，然后扩展
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            # 1. FTS5搜索种子节点（核心 token 优先）
            core_tokens, expanded_tokens = self._split_query_tokens_with_priority(query)
            seed_nodes = []
            seen_ids = set()
            for token in core_tokens + expanded_tokens:
                try:
                    cursor.execute("""
                        SELECT n.* FROM gm_nodes n
                        JOIN gm_nodes_fts fts ON n.id = fts.rowid
                        WHERE gm_nodes_fts MATCH ?
                        ORDER BY n.pagerank_score DESC
                        LIMIT 5
                    """, (token,))
                    for row in cursor.fetchall():
                        d = dict(row)
                        if d['node_id'] not in seen_ids:
                            seed_nodes.append(d)
                            seen_ids.add(d['node_id'])
                except Exception:
                    continue

            # LIKE 兜底：FTS5 对短中文查询可能无结果
            if not seed_nodes:
                like_pattern = f"%{query}%"
                cursor.execute("""
                    SELECT * FROM gm_nodes
                    WHERE name LIKE ? OR description LIKE ? OR content LIKE ?
                    ORDER BY pagerank_score DESC
                    LIMIT 5
                """, (like_pattern, like_pattern, like_pattern))
                seed_nodes = [dict(row) for row in cursor.fetchall()]

            # 2. 图遍历扩展 (获取邻居)
            all_nodes = set()
            for node in seed_nodes:
                all_nodes.add(node['node_id'])

                # 获取邻居
                cursor.execute("""
                    SELECT n.* FROM gm_nodes n
                    JOIN gm_edges e ON n.node_id = e.target_node_id
                    WHERE e.source_node_id = ?
                    UNION
                    SELECT n.* FROM gm_nodes n
                    JOIN gm_edges e ON n.node_id = e.source_node_id
                    WHERE e.target_node_id = ?
                    LIMIT 10
                """, (node['node_id'], node['node_id']))

                for row in cursor.fetchall():
                    all_nodes.add(row['node_id'])

            # 3. 获取所有节点详情
            if all_nodes:
                placeholders = ','.join('?' * len(all_nodes))
                cursor.execute(f"""
                    SELECT * FROM gm_nodes
                    WHERE node_id IN ({placeholders})
                    ORDER BY pagerank_score DESC
                    LIMIT ?
                """, (*list(all_nodes), self.max_context_nodes))

                return [dict(row) for row in cursor.fetchall()]

            return []

    def _get_recent_nodes(self) -> List[Dict]:
        """获取最近的活跃节点"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM gm_nodes
                ORDER BY last_accessed DESC NULLS LAST
                LIMIT ?
            """, (self.max_context_nodes,))

            return [dict(row) for row in cursor.fetchall()]

    def _build_graph_xml(self, nodes: List[Dict]) -> str:
        """构建图谱XML"""
        if not nodes:
            return "<graph></graph>"

        # 获取节点间的边
        node_ids = [n['node_id'] for n in nodes]

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            placeholders = ','.join('?' * len(node_ids))
            cursor.execute(f"""
                SELECT * FROM gm_edges
                WHERE source_node_id IN ({placeholders})
                AND target_node_id IN ({placeholders})
                LIMIT 50
            """, (*node_ids, *node_ids))

            edges = [dict(row) for row in cursor.fetchall()]

        # 构建XML
        xml_parts = ['<graph>']

        # 添加节点
        xml_parts.append('  <nodes>')
        for node in nodes:
            xml_parts.append(f'    <node id="{node["node_id"]}" type="{node["node_type"]}">')
            xml_parts.append(f'      <name>{self._escape_xml(node["name"])}</name>')
            if node.get('description'):
                xml_parts.append(f'      <desc>{self._escape_xml(node["description"])}</desc>')
            xml_parts.append('    </node>')
        xml_parts.append('  </nodes>')

        # 添加边
        if edges:
            xml_parts.append('  <edges>')
            for edge in edges:
                xml_parts.append(f'    <edge type="{edge["edge_type"]}" '
                               f'source="{edge["source_node_id"]}" '
                               f'target="{edge["target_node_id"]}" '
                               f'weight="{edge["weight"]:.2f}"/>')
            xml_parts.append('  </edges>')

        xml_parts.append('</graph>')

        return '\n'.join(xml_parts)

    def _escape_xml(self, text: str) -> str:
        """XML转义"""
        return (text
                .replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))

    @staticmethod
    def _split_query_tokens_with_priority(query: str):
        """
        拆分查询，返回 (core_tokens, expanded_tokens)
        """
        import re

        raw_parts = re.split(r"[\s\u3000,，。！？、；：:;\"'“”‘’【】（）()\[\]{}<>《》]+", query.strip())

        core = []
        expanded = []
        seen_core = set()
        seen_exp = set()

        def add_core(t):
            t = t.strip()
            if len(t) >= 2 and t not in seen_core:
                core.append(t)
                seen_core.add(t)

        def add_exp(t):
            t = t.strip()
            if len(t) >= 2 and t not in seen_exp and t not in seen_core:
                expanded.append(t)
                seen_exp.add(t)

        for part in raw_parts:
            part = part.strip()
            if not part:
                continue
            segments = tokenize_search_terms(part, min_len=2, max_terms=12)
            if segments:
                for seg in segments:
                    add_core(seg)
                seed = segments[0]
                if re.match(r'^[\u4e00-\u9fff]+$', seed) and len(seed) >= 4:
                    for win in range(3, min(7, len(seed) + 1)):
                        for i in range(len(seed) - win + 1):
                            add_exp(seed[i:i+win])

        if not core and not expanded:
            core = tokenize_search_terms(query, min_len=1, max_terms=1) or [query]

        return core, expanded

    def _estimate_tokens(self, graph_xml: str, fresh_tail: List[Dict]) -> int:
        """估算总token数"""
        graph_tokens = len(graph_xml) // 4
        tail_tokens = sum(len(msg['content']) // 4 for msg in fresh_tail)
        return graph_tokens + tail_tokens

    def _build_session_summary_text(self, session_id: str) -> str:
        summary = self.state_store.load_session_summary(session_id)
        if not summary:
            return ""
        lines = ["## 会话摘要"]
        for key, label in [("topic", "主题"), ("user_goal", "目标"), ("current_focus", "焦点"), ("next_step", "下一步")]:
            value = str(summary.get(key) or "").strip()
            if value:
                lines.append(f"- {label}: {value[:220]}")
        for key, label in [("decisions", "决策"), ("done", "已完成"), ("open_questions", "待解")]:
            values = summary.get(key) or []
            if values:
                joined = " | ".join(str(v)[:120] for v in values[:3])
                lines.append(f"- {label}: {joined}")
        return "\n".join(lines)

    def _build_task_checkpoint_text(self, session_id: str) -> str:
        checkpoint = self.state_store.load_task_checkpoint(session_id)
        if not checkpoint:
            return ""
        lines = ["## 任务检查点"]
        for key, label in [("title", "任务"), ("current_step", "当前步骤"), ("next_action", "下一动作"), ("state", "状态")]:
            value = str(checkpoint.get(key) or "").strip()
            if value:
                lines.append(f"- {label}: {value[:220]}")
        for key, label in [("completed_steps", "已完成"), ("pending_steps", "待做"), ("blockers", "阻塞")]:
            values = checkpoint.get(key) or []
            if values:
                joined = " | ".join(str(v)[:120] for v in values[:3])
                lines.append(f"- {label}: {joined}")
        return "\n".join(lines)

    def _build_handoff_pack_text(self, session_id: str) -> str:
        handoff = self.state_store.load_handoff_pack(session_id)
        if not handoff:
            return ""
        lines = ["## 交接摘要"]
        critical = handoff.get('critical_memories') or []
        evidence = handoff.get('evidence') or []
        if critical:
            lines.append("- 关键记忆: " + " | ".join(str(v)[:120] for v in critical[:3]))
        if evidence:
            lines.append("- 证据: " + " | ".join(str(v)[:120] for v in evidence[:3]))
        return "\n".join(lines)

    def _build_evidence_pack_text(self, session_id: str) -> str:
        view = self.state_store.handoff_resume_view(session_id)
        evidence = view.get('evidence') or []
        critical = view.get('critical_memories') or []
        if not evidence and not critical:
            return ""
        lines = ["## 证据锚点"]
        if critical:
            lines.append("- 关键记忆: " + " | ".join(str(v)[:120] for v in critical[:4]))
        for item in evidence[:5]:
            lines.append(f"- 证据: {str(item)[:180]}")
        return "\n".join(lines)

    @staticmethod
    def _resolve_prompt_plan(profile: Optional[Dict[str, object]] = None) -> Dict[str, object]:
        default_sections = [
            'session_summary', 'task_checkpoint', 'graph', 'episodic',
            'dag_summary', 'tail', 'handoff_pack', 'evidence_pack'
        ]
        profile = dict(profile or {})
        context_route = str(profile.get('context_route') or 'default')
        preferred_sections = [
            str(item) for item in list(profile.get('preferred_sections', []) or default_sections)
            if str(item) in default_sections
        ]
        suppressed_sections = {str(item) for item in list(profile.get('suppressed_sections', []) or [])}
        selected_sections = [item for item in preferred_sections if item not in suppressed_sections]
        if not selected_sections:
            selected_sections = [item for item in default_sections if item not in suppressed_sections] or default_sections

        priority_map = {
            'session_summary': 1,
            'task_checkpoint': 1,
            'evidence_pack': 2,
            'graph': 2,
            'episodic': 2,
            'dag_summary': 4,
            'tail': 3,
            'handoff_pack': 5,
        }
        route_priority_overrides = {
            'summary': {
                'session_summary': 1,
                'task_checkpoint': 1,
                'dag_summary': 2,
                'tail': 3,
                'evidence_pack': 4,
            },
            'checkpoint': {
                'task_checkpoint': 1,
                'session_summary': 2,
                'handoff_pack': 2,
                'tail': 3,
                'evidence_pack': 3,
            },
            'evidence': {
                'evidence_pack': 1,
                'episodic': 1,
                'graph': 2,
                'tail': 3,
                'session_summary': 4,
                'task_checkpoint': 4,
            },
            'handoff': {
                'task_checkpoint': 1,
                'handoff_pack': 1,
                'session_summary': 2,
                'tail': 3,
                'evidence_pack': 3,
            },
        }
        priority_map.update(route_priority_overrides.get(context_route, {}))

        stable_budget_ratio = 0.72
        if context_route == 'summary':
            stable_budget_ratio = 0.82
        elif context_route == 'checkpoint':
            stable_budget_ratio = 0.8
        elif context_route == 'handoff':
            stable_budget_ratio = 0.78
        elif context_route == 'evidence':
            stable_budget_ratio = 0.68

        return {
            'context_route': context_route,
            'selected_sections': selected_sections,
            'priority_map': priority_map,
            'stable_budget_ratio': stable_budget_ratio,
        }

    def _get_episodic_messages(self, nodes: List[Dict], max_chars: int = 1500) -> List[Dict]:
        """
        溯源选拉：为 PPR 排名前 3 的节点拉取原始对话片段

        只拉 user 和 assistant 消息，按时间接近度排序
        跳过 system/tool/toolResult 消息
        """
        if not nodes:
            return []

        top_nodes = nodes[:3]
        results = []
        used_chars = 0

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            for node in top_nodes:
                if used_chars >= max_chars:
                    break

                # 获取节点相关的源消息ID
                source_msg_ids = node.get('source_message_ids')
                if not source_msg_ids:
                    continue

                # 解析 JSON 数组
                if isinstance(source_msg_ids, str):
                    try:
                        source_msg_ids = json.loads(source_msg_ids)
                    except (json.JSONDecodeError, TypeError):
                        continue

                if not source_msg_ids:
                    continue

                # 查询相关消息（只拉 user/assistant）
                placeholders = ','.join('?' * min(len(source_msg_ids), 10))
                cursor.execute(f"""
                    SELECT session_id, role, content, timestamp
                    FROM gm_messages
                    WHERE id IN ({placeholders})
                    AND role IN ('user', 'assistant')
                    ORDER BY timestamp ASC
                    LIMIT 6
                """, source_msg_ids[:10])

                for row in cursor.fetchall():
                    if used_chars >= max_chars:
                        break

                    text = self._parse_message_content(row['content'])
                    if not text.strip():
                        continue

                    truncated = text[:min(len(text), max_chars - used_chars)]
                    results.append({
                        'session_id': row['session_id'],
                        'role': row['role'],
                        'text': truncated,
                        'node_name': node.get('name', ''),
                        'timestamp': row['timestamp']
                    })
                    used_chars += len(truncated)

        return results

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

    def _build_episodic_xml(self, episodic_msgs: List[Dict]) -> str:
        """构建溯源 XML"""
        if not episodic_msgs:
            return ""

        traces = {}
        for msg in episodic_msgs:
            node_name = msg.get('node_name', 'unknown')
            if node_name not in traces:
                traces[node_name] = []
            traces[node_name].append(msg)

        parts = []
        for node_name, msgs in traces.items():
            lines = []
            for m in msgs:
                role = m['role'].upper()
                text = self._escape_xml(m['text'][:200])
                lines.append(f"    [{role}] {text}")
            parts.append(f'  <trace node="{self._escape_xml(node_name)}">\n' + '\n'.join(lines) + '\n  </trace>')

        return f'<episodic_context>\n' + '\n'.join(parts) + '\n</episodic_context>'

    def build_prompt_sections(self, assembly: ContextAssembly, max_tokens: int = 2000, profile: Optional[Dict[str, object]] = None) -> Dict[str, str]:
        """显式拆分稳定前缀和动态后缀，减少前缀漂移。"""
        from .compressor import ContextCompressor

        plan = self._resolve_prompt_plan(profile)
        selected_sections = set(plan.get('selected_sections', []) or [])
        priority_map = dict(plan.get('priority_map', {}) or {})

        stable_parts = []
        dynamic_parts = []

        if assembly.session_summary and 'session_summary' in selected_sections:
            stable_parts.append({
                'content': assembly.session_summary,
                'priority': priority_map.get('session_summary', 1),
                'type': 'session_summary'
            })

        if assembly.task_checkpoint and 'task_checkpoint' in selected_sections:
            stable_parts.append({
                'content': assembly.task_checkpoint,
                'priority': priority_map.get('task_checkpoint', 1),
                'type': 'task_checkpoint'
            })

        if assembly.evidence_pack and 'evidence_pack' in selected_sections:
            stable_parts.append({
                'content': assembly.evidence_pack,
                'priority': priority_map.get('evidence_pack', 2),
                'type': 'evidence_pack'
            })

        if assembly.graph_xml and assembly.node_count > 0 and 'graph' in selected_sections:
            stable_parts.append({
                'content': f"## 相关知识图谱\n{assembly.graph_xml}",
                'priority': priority_map.get('graph', 2),
                'type': 'graph'
            })

        if assembly.episodic_xml and 'episodic' in selected_sections:
            stable_parts.append({
                'content': f"## 原始对话溯源\n{assembly.episodic_xml}",
                'priority': priority_map.get('episodic', 2),
                'type': 'episodic'
            })

        if assembly.dag_summary and 'dag_summary' in selected_sections:
            stable_parts.append({
                'content': f"## 历史对话摘要\n{assembly.dag_summary}",
                'priority': priority_map.get('dag_summary', 4),
                'type': 'dag_summary'
            })

        if assembly.fresh_tail and 'tail' in selected_sections:
            tail_text = "\n".join(
                f"{'用户' if m['role']=='user' else '助手'}: {m['content'][:100]}"
                for m in assembly.fresh_tail
            )
            dynamic_parts.append({
                'content': f"## 最近对话\n{tail_text}",
                'priority': priority_map.get('tail', 3),
                'type': 'tail'
            })

        if assembly.handoff_pack and 'handoff_pack' in selected_sections:
            dynamic_parts.append({
                'content': assembly.handoff_pack,
                'priority': priority_map.get('handoff_pack', 5),
                'type': 'handoff_pack'
            })

        stable_budget = int(max_tokens * float(plan.get('stable_budget_ratio', 0.72) or 0.72))
        dynamic_budget = max(120, max_tokens - stable_budget)
        stable_prefix = ContextCompressor(max_tokens=stable_budget).smart_truncate(stable_parts) if stable_parts else ""
        dynamic_suffix = ContextCompressor(max_tokens=dynamic_budget).smart_truncate(dynamic_parts) if dynamic_parts else ""
        return {
            'context_route': str(plan.get('context_route', 'default')),
            'selected_sections': list(plan.get('selected_sections', []) or []),
            'stable_prefix': stable_prefix,
            'dynamic_suffix': dynamic_suffix,
            'combined': "\n\n".join(part for part in [stable_prefix, dynamic_suffix] if part),
        }

    def format_for_prompt(self, assembly: ContextAssembly, max_tokens: int = 2000, profile: Optional[Dict[str, object]] = None) -> str:
        """智能格式化，按优先级裁剪。"""
        sections = self.build_prompt_sections(assembly, max_tokens=max_tokens, profile=profile)
        assembly.stable_prefix = sections.get('stable_prefix', '')
        assembly.dynamic_suffix = sections.get('dynamic_suffix', '')
        return sections.get('combined', '')
