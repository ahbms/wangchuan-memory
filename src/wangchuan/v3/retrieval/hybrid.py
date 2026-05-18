#!/usr/bin/env python3
"""
忘川 v3.0 - 混合检索模块
整合: 图谱遍历 + 向量搜索 + FTS5全文检索
"""

import sqlite3
import json
import hashlib
import logging
from datetime import datetime
from typing import List, Dict, Optional, Set
from dataclasses import dataclass
from collections import defaultdict

from ...fts_utils import build_safe_fts_match_query, tokenize_search_terms

logger = logging.getLogger(__name__)

@dataclass
class RetrievalResult:
    """检索结果"""
    node_id: str
    node_type: str
    name: str
    description: str
    score: float              # 综合得分
    sources: List[str]        # 来源: graph/vector/fts


class HybridRetriever:
    """混合检索器"""
    
    def __init__(
        self,
        db_path: str,
        vector_engine=None,
        ppr_damping: float = 0.85,
        max_iterations: int = 100,
        iv_calculator=None
    ):
        self.db_path = db_path
        self.ppr_damping = ppr_damping
        self.max_iterations = max_iterations
        
        # IV 记忆价值计算器（可选）
        self.iv_calculator = iv_calculator
        if not self.iv_calculator:
            try:
                from ..iv_calculator import IVCalculator
                self.iv_calculator = IVCalculator(db_path)
            except ImportError as e:
                logger.warning("【WangChuan】[HybridRetriever][IV] calculator unavailable: %s", e)

        # 最近一次检索的最小可观测快照（供回归/排障使用）
        self.last_debug: Dict[str, object] = {}
        
        # 反馈闭环引擎
        try:
            from .feedback import FeedbackEngine
            self.feedback = FeedbackEngine(db_path)
        except Exception as e:
            logger.warning("【WangChuan】[HybridRetriever][Feedback] init failed: %s", e)
            self.feedback = None
        
        # 自动初始化向量引擎（优先讯飞，降级本地）
        if vector_engine:
            self.vector_engine = vector_engine
        else:
            import os
            emb_key = os.getenv('EMBEDDING_API_KEY')
            emb_url = os.getenv('EMBEDDING_BASE_URL', 'https://maas-api.cn-huabei-1.xf-yun.com/v2')
            emb_model = os.getenv('EMBEDDING_MODEL', 'xop3qwen8bembedding')
            
            if emb_key:
                try:
                    import sys
                    _graph_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'graph')
                    if _graph_dir not in sys.path:
                        sys.path.insert(0, _graph_dir)
                    from xfyun_vector import XfyunVectorEngine
                    self.vector_engine = XfyunVectorEngine(
                        db_path, api_key=emb_key, base_url=emb_url, model=emb_model
                    )
                    # 确保有向量数据
                    stats = self.vector_engine.get_stats()
                    if stats['xfyun_embeddings'] == 0:
                        logger.info("【WangChuan】[HybridRetriever][Vector] xfyun embeddings missing; generating")
                        self.vector_engine.embed_nodes()
                except Exception as e:
                    logger.warning("【WangChuan】[HybridRetriever][Vector] xfyun init failed: %s", e)
                    self.vector_engine = self._try_local_vector(db_path)
            else:
                self.vector_engine = self._try_local_vector(db_path)
    
    def _try_local_vector(self, db_path):
        """降级到本地向量引擎"""
        try:
            import sys, os
            _graph_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'graph')
            if _graph_dir not in sys.path:
                sys.path.insert(0, _graph_dir)
            from local_vector import LocalVectorEngine
            engine = LocalVectorEngine(db_path)
            stats = engine.get_stats()
            if stats['local_embeddings'] == 0:
                engine.embed_and_store()
            return engine
        except Exception as e:
            logger.warning("【WangChuan】[HybridRetriever][Vector] local fallback unavailable: %s", e)
            return None
    
    def retrieve(
        self,
        query: str,
        session_id: Optional[str] = None,
        top_k: int = 20,
        use_graph: bool = True,
        use_vector: bool = True,
        use_fts: bool = True
    ) -> List[RetrievalResult]:
        """
        混合检索
        
        Args:
            query: 查询文本
            session_id: 当前会话ID (用于个性化)
            top_k: 返回结果数
            use_graph: 是否使用图谱检索
            use_vector: 是否使用向量检索
            use_fts: 是否使用全文检索
        
        Returns:
            排序后的检索结果
        """
        debug = {
            "query": query,
            "session_id": session_id,
            "top_k": top_k,
            "flags": {
                "use_graph": use_graph,
                "use_vector": bool(use_vector and self.vector_engine),
                "use_fts": use_fts,
            },
            "expanded_queries": [query],
            "module_hits": {"graph": 0, "vector": 0, "fts": 0, "community": 0},
            "candidate_count": 0,
            "result_count": 0,
            "top_names": [],
        }
        self.last_debug = debug

        # 收集所有候选节点
        candidates = defaultdict(lambda: {
            'scores': {},
            'node_info': None
        })

        # 0. 查询扩展
        try:
            from .query_expansion import QueryExpander
            expander = QueryExpander()
            expanded_queries = expander.expand(query)
            debug["expanded_queries"] = expanded_queries
        except Exception as e:
            logger.warning("【WangChuan】[HybridRetriever][Expansion] query expansion failed: %s", e)
            expander = None
            expanded_queries = [query]
            debug["expansion_error"] = str(e)

        # 1. 图谱检索 (PPR) — 对每个扩展查询都搜索，合并去重
        if use_graph:
            seen_graph = set()
            for eq in expanded_queries:
                graph_results = self._retrieve_by_graph(eq, session_id)
                for node_id, score, node_info in graph_results:
                    if node_id not in seen_graph:
                        seen_graph.add(node_id)
                        candidates[node_id]['scores']['graph'] = max(
                            candidates[node_id]['scores'].get('graph', 0), score
                        )
                        candidates[node_id]['node_info'] = node_info
            debug["module_hits"]["graph"] = len(seen_graph)
        
        # 2. 向量检索 — 用扩展拼接查询
        if use_vector and self.vector_engine:
            vec_query = expander.expand_for_vector(query) if expander else query
            debug["vector_query"] = vec_query
            vector_results = self._retrieve_by_vector(vec_query)
            seen_vector = set()
            for node_id, score in vector_results:
                seen_vector.add(node_id)
                candidates[node_id]['scores']['vector'] = max(
                    candidates[node_id]['scores'].get('vector', 0), score
                )
                if not candidates[node_id]['node_info']:
                    candidates[node_id]['node_info'] = self._get_node_info(node_id)
            debug["module_hits"]["vector"] = len(seen_vector)
        
        # 3. 全文检索 (FTS5) — 对每个扩展查询都搜索，合并去重
        if use_fts:
            seen_fts = set()
            for eq in expanded_queries:
                fts_results = self._retrieve_by_fts(eq)
                for node_id, score in fts_results:
                    if node_id not in seen_fts:
                        seen_fts.add(node_id)
                        candidates[node_id]['scores']['fts'] = max(
                            candidates[node_id]['scores'].get('fts', 0), score
                        )
                        if not candidates[node_id]['node_info']:
                            candidates[node_id]['node_info'] = self._get_node_info(node_id)
            debug["module_hits"]["fts"] = len(seen_fts)
        
        # 4. 融合排序
        results = self._fuse_scores(candidates)
        debug["candidate_count"] = len(candidates)

        # 5. 社区扩展：对 top 结果补充同社区节点
        if len(results) > 0:
            top_ids = [r.node_id for r in results[:3]]
            expanded_ids = self.expand_by_community(top_ids, expand_ratio=0.3)
            # 把新增的社区成员加到结果中
            existing_ids = {r.node_id for r in results}
            community_added = 0
            for eid in expanded_ids:
                if eid not in existing_ids:
                    node_info = self._get_node_info(eid)
                    if node_info:
                        results.append(RetrievalResult(
                            node_id=eid,
                            node_type=node_info.get('node_type', 'UNKNOWN'),
                            name=node_info.get('name', ''),
                            description=node_info.get('description', ''),
                            score=0.05,
                            sources=['community']
                        ))
                        community_added += 1
            debug["module_hits"]["community"] = community_added

        # 6. 返回top_k
        
        # 7. 应用反馈权重
        if hasattr(self, "feedback") and self.feedback:
            try:
                result_dicts = [{"node_id": r.node_id, "score": r.score} for r in results]
                result_dicts = self.feedback.apply_weights_to_search(result_dicts)
                for i, rd in enumerate(result_dicts):
                    if i < len(results):
                        results[i].score = rd["score"]
                # 反馈权重会改变最终分数，必须重新排序，否则排序仍停留在加权前。
                results.sort(key=lambda x: -x.score)
            except Exception as e:
                logger.warning("【WangChuan】[HybridRetriever][Feedback] apply_weights failed: %s", e)
                debug["feedback_error"] = str(e)

        final_results = results[:top_k]
        debug["result_count"] = len(final_results)
        debug["top_names"] = [r.name for r in final_results[:5]]
        self.last_debug = debug
        return final_results
    
    def _retrieve_by_graph(
        self,
        query: str,
        session_id: Optional[str] = None
    ) -> List[tuple]:
        """
        基于图谱的检索 (PPR)
        
        步骤:
        1. 找到种子节点 (FTS5匹配查询)
        2. 从种子节点开始PPR扩散
        3. 返回排序后的节点
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            # 1. 找到种子节点（拆分 token 搜索，核心 token 优先）
            seed_nodes = {}
            core_tokens, expanded_tokens = self._split_query_tokens_with_priority(query)
            for token in core_tokens + expanded_tokens:
                try:
                    cursor.execute("""
                        SELECT n.node_id, n.pagerank_score
                        FROM gm_nodes n
                        JOIN gm_nodes_fts fts ON n.id = fts.rowid
                        WHERE gm_nodes_fts MATCH ?
                        ORDER BY n.pagerank_score DESC
                        LIMIT 5
                    """, (token,))
                    for row in cursor.fetchall():
                        if row['node_id'] not in seed_nodes:
                            seed_nodes[row['node_id']] = 1.0 if token in core_tokens else 0.7
                except sqlite3.OperationalError:
                    continue

            # LIKE 兜底：FTS5 对短中文查询可能无结果
            if not seed_nodes:
                like_pattern = f"%{query}%"
                cursor.execute("""
                    SELECT node_id, pagerank_score
                    FROM gm_nodes
                    WHERE name LIKE ? OR description LIKE ? OR content LIKE ?
                    ORDER BY pagerank_score DESC
                    LIMIT 5
                """, (like_pattern, like_pattern, like_pattern))
                seed_nodes = {row['node_id']: 0.8 for row in cursor.fetchall()}

            if not seed_nodes:
                # 跨会话兜底：取全局 PageRank 最高的节点
                cursor.execute("""
                    SELECT node_id, pagerank_score
                    FROM gm_nodes
                    WHERE pagerank_score > 0
                    ORDER BY pagerank_score DESC
                    LIMIT 5
                """)
                seed_nodes = {row['node_id']: 0.3 for row in cursor.fetchall()}

                if not seed_nodes:
                    # 最后兜底：最近访问
                    cursor.execute("""
                        SELECT node_id, pagerank_score
                        FROM gm_nodes
                        ORDER BY last_accessed DESC NULLS LAST
                        LIMIT 5
                    """)
                    seed_nodes = {row['node_id']: 0.1 for row in cursor.fetchall()}
            
            # 2. PPR扩散
            ppr_scores = self._compute_ppr(seed_nodes)
            
            # 3. 获取节点信息
            results = []
            for node_id, score in sorted(ppr_scores.items(), key=lambda x: -x[1])[:50]:
                node_info = self._get_node_info(node_id)
                if node_info:
                    results.append((node_id, score, node_info))
            
            return results
    
    def _compute_ppr(self, seed_nodes: Dict[str, float]) -> Dict[str, float]:
        """
        正确的 Personalized PageRank 实现

        公式: PR(v) = (1-d) * teleport(v) + d * Σ PR(u) * w(u,v) / out_degree(u)

        相比旧版 BFS 扩散，这能正确地做 2+ 跳传播并保证收敛。
        """
        # PPR 缓存检查
        seed_hash = hashlib.md5(json.dumps(sorted(seed_nodes.items())).encode()).hexdigest()[:16]
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT ppr_results FROM gm_ppr_cache WHERE query_hash = ? AND expires_at > datetime('now')",
                    (seed_hash,)
                ).fetchone()
                if row:
                    return json.loads(row[0])
        except sqlite3.OperationalError as e:
            logger.warning("【WangChuan】[HybridRetriever][PPR] cache read skipped: %s", e)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT node_id FROM gm_nodes")
            all_nodes = [row[0] for row in cursor.fetchall()]
            cursor.execute("SELECT source_node_id, target_node_id, weight FROM gm_edges")
            edges = cursor.fetchall()

        N = len(all_nodes)
        if N == 0:
            return {}

        # 构建邻接表（无向）
        adj = {n: [] for n in all_nodes}
        for src, tgt, w in edges:
            if src in adj and tgt in adj:
                adj[src].append((tgt, w))
                adj[tgt].append((src, w))

        # 归一化种子
        total_seed = sum(seed_nodes.values())
        if total_seed <= 0:
            total_seed = 1.0
        teleport = {n: seed_nodes.get(n, 0.0) / total_seed for n in all_nodes}

        # 初始化 PR
        pr = {n: 1.0 / N for n in all_nodes}

        degree_map = {
            node_id: sum(weight for _, weight in neighbors)
            for node_id, neighbors in adj.items()
        }

        # 迭代
        for _ in range(self.max_iterations):
            new_pr = {
                v: (1 - self.ppr_damping) * teleport.get(v, 0.0)
                for v in all_nodes
            }

            # 正确的边传播：从每个源节点 u 向其邻居分摊概率质量
            for u in all_nodes:
                degree = degree_map.get(u, 0.0)
                if degree <= 0:
                    continue
                base_mass = self.ppr_damping * pr[u]
                if base_mass == 0:
                    continue
                for neighbor, w in adj.get(u, []):
                    if w <= 0:
                        continue
                    new_pr[neighbor] += base_mass * (w / degree)

            # 检查收敛
            diff = sum(abs(new_pr[n] - pr[n]) for n in all_nodes)
            pr = new_pr
            if diff < 1e-6:
                break

        # 只返回分数 > 0 的节点
        scores = {n: s for n, s in pr.items() if s > 1e-8}

        # 写入缓存
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO gm_ppr_cache (query_hash, seed_nodes, ppr_results, expires_at)
                    VALUES (?, ?, ?, datetime('now', '+1 hour'))
                """, (seed_hash, json.dumps(seed_nodes), json.dumps(scores)))
                conn.commit()
        except Exception as e:
            logger.warning("【WangChuan】[HybridRetriever][PPR] cache write failed: %s", e)

        return scores
    
    def _retrieve_by_vector(self, query: str) -> List[tuple]:
        """向量检索（兼容 dict 和对象两种返回格式）"""
        if not self.vector_engine:
            return []

        try:
            results = self.vector_engine.search_similar(query, top_k=20, entity_type='node')
        except TypeError:
            # LocalVectorEngine 不需要 entity_type
            results = self.vector_engine.search_similar(query, top_k=20)

        if (not results) and getattr(self.vector_engine, 'last_error_kind', '') == 'timeout':
            logger.warning("【WangChuan】[HybridRetriever][Vector] xfyun timeout, fallback to local vector")
            fallback_engine = self._try_local_vector(self.db_path)
            if fallback_engine:
                try:
                    results = fallback_engine.search_similar(query, top_k=20)
                    self.last_debug['vector_fallback'] = 'local_after_timeout'
                except Exception as e:
                    logger.warning("【WangChuan】[HybridRetriever][Vector] local fallback after timeout failed: %s", e)
                    self.last_debug['vector_fallback_error'] = str(e)
        
        pairs = []
        for r in results:
            if isinstance(r, dict):
                pairs.append((r['entity_id'], r['similarity']))
            else:
                # 对象格式 (LocalEmbeddingResult)
                pairs.append((r.entity_id, r.similarity))
        return pairs
    
    def _retrieve_by_fts(self, query: str) -> List[tuple]:
        """全文检索 (FTS5 + LIKE 双通道)"""
        results = {}
        match_query = build_safe_fts_match_query(query, max_terms=10)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()

            # FTS5 搜索（英文/关键词有效）
            if match_query:
                try:
                    cursor.execute("""
                        SELECT n.node_id, rank
                        FROM gm_nodes n
                        JOIN gm_nodes_fts fts ON n.id = fts.rowid
                        WHERE gm_nodes_fts MATCH ?
                        ORDER BY rank
                        LIMIT 20
                    """, (match_query,))
                    for node_id, rank in cursor.fetchall():
                        score = 1.0 / (1.0 + abs(rank))
                        results[node_id] = max(results.get(node_id, 0), score)
                except Exception as e:
                    logger.warning("【WangChuan】[HybridRetriever][FTS] query failed: %s", e)

            # LIKE 搜索（中文有效）
            cursor.execute("""
                SELECT node_id, pagerank_score
                FROM gm_nodes
                WHERE name LIKE ? OR description LIKE ? OR content LIKE ?
                ORDER BY pagerank_score DESC
                LIMIT 20
            """, (f"%{query}%", f"%{query}%", f"%{query}%"))
            for node_id, pr in cursor.fetchall():
                # LIKE 匹配分数 = 0.5 + pagerank 权重
                like_score = 0.5 + (pr or 0)
                results[node_id] = max(results.get(node_id, 0), like_score)

        return list(results.items())

    @staticmethod
    def _split_query_tokens_with_priority(query: str):
        """
        拆分查询，返回 (core_tokens, expanded_tokens)

        core_tokens: 有语义的拆分段（中英文边界拆分后的段）
        expanded_tokens: 滑动窗口子串（仅用于扩大召回，权重低）
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
                # 滑动窗口作为扩展
                if re.match(r'^[\u4e00-\u9fff]+$', seed) and len(seed) >= 4:
                    for win in range(3, min(7, len(seed) + 1)):
                        for i in range(len(seed) - win + 1):
                            add_exp(seed[i:i+win])

        if not core and not expanded:
            core = tokenize_search_terms(query, min_len=1, max_terms=1) or [query.strip()]

        return core, expanded
    
    def _get_node_info(self, node_id: str) -> Optional[Dict]:
        """获取节点信息"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM gm_nodes WHERE node_id = ?
            """, (node_id,))
            
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def _get_temperature_weight(self, node_info: Dict) -> float:
        """
        温度加权：越新的记忆分数越高

        基于 last_accessed 时间差：
        - < 1天：1.5x (热记忆)
        - < 7天：1.2x (温记忆)
        - < 30天：1.0x (冷记忆)
        - > 30天：0.7x (冰记忆)
        """
        last_accessed = node_info.get('last_accessed')
        if not last_accessed:
            return 0.8  # 从未访问过的降权

        try:
            from datetime import datetime
            if isinstance(last_accessed, str):
                last = datetime.fromisoformat(last_accessed.replace('Z', '+00:00'))
            else:
                last = datetime.fromtimestamp(last_accessed)

            days_ago = (datetime.now() - last).days

            if days_ago <= 1:
                return 1.5  # 热
            elif days_ago <= 7:
                return 1.2  # 温
            elif days_ago <= 30:
                return 1.0  # 冷
            else:
                return 0.7  # 冰
        except Exception as e:
            logger.warning("【WangChuan】[HybridRetriever][Temperature] parse failed: %s", e)
            return 1.0

    def _get_iv_weight(self, node_info: Dict) -> float:
        """
        IV 加权：基于动态遗忘框架的信息价值

        使用 IVCalculator 计算记忆的保留价值：
        - IV > 0.5: 1.3x (高价值记忆)
        - IV > 0.3: 1.0x (中等价值)
        - IV > 0.15: 0.7x (低价值)
        - IV ≤ 0.15: 0.4x (建议遗忘)
        """
        if not self.iv_calculator:
            return 1.0  # 未启用 IV，不改变分数

        try:
            from ..iv_calculator import MemoryItem

            node_id = node_info.get('node_id', '')
            content = node_info.get('description', '') or node_info.get('content', '') or node_info.get('name', '')

            # 根据节点类型映射记忆类型
            node_type = node_info.get('node_type', 'UNKNOWN')
            type_map = {
                'LESSON': 'lesson',
                'PREFERENCE': 'preference',
                'PREFERENCE_RULE': 'preference',
                'EMOTION': 'emotional',
                'USER_DEFINED': 'user_defined',
            }
            memory_type = type_map.get(node_type, 'fact')

            memory = MemoryItem(
                id=node_id,
                content=content[:500],
                memory_type=memory_type,
                created_at=node_info.get('created_at', datetime.now().isoformat()),
                last_accessed=node_info.get('last_accessed', ''),
                access_count=node_info.get('access_count', 0),
                success_count=node_info.get('success_count', 0),
                failure_count=node_info.get('failure_count', 0),
                base_value=0.0,
            )

            result = self.iv_calculator.calculate(memory)

            # 分段加权
            iv = result.iv_score
            if iv > 0.5:
                return 1.3
            elif iv > 0.3:
                return 1.0
            elif iv > 0.15:
                return 0.7
            else:
                return 0.4
        except Exception as e:
            logger.warning("【WangChuan】[HybridRetriever][IV] weight calculation failed: %s", e)
            return 1.0  # 计算失败，保持原分数

    def _fuse_scores(self, candidates: Dict) -> List[RetrievalResult]:
        """
        融合多路检索分数

        使用加权求和:
        - 图谱: 0.4
        - 向量: 0.35
        - FTS: 0.25
        """
        weights = {
            'graph': 0.35,
            'vector': 0.25,
            'fts': 0.30,
            'like': 0.10
        }
        
        # 向量最低阈值（过滤低质量语义匹配）。
        # 本地 bigram TF-IDF 的余弦分布明显低于外部 embedding：
        # 精确命中常在 0.35+，有用的技术短语约 0.18~0.25，泛化噪声通常 <0.1。
        vector_min_sim = self._vector_min_similarity()
        
        results = []
        for node_id, data in candidates.items():
            node_info = data['node_info']
            if not node_info:
                continue
            
            # 过滤低质量向量结果
            filtered_scores = {}
            for source, score in data['scores'].items():
                if source == 'vector' and score < vector_min_sim:
                    continue
                filtered_scores[source] = score
            
            if not filtered_scores:
                continue
            
            # 计算加权分数
            total_score = 0
            total_weight = 0
            sources = []
            
            for source, score in filtered_scores.items():
                weight = weights.get(source, 0.2)
                total_score += score * weight
                total_weight += weight
                sources.append(source)
            
            # 多源加成：被多个检索通道命中的节点分数 x1.3
            if len(sources) >= 2:
                total_score *= 1.3
            
            if total_weight > 0:
                final_score = total_score / total_weight
            else:
                final_score = 0

            # 温度加权：新鲜记忆分数更高
            temp_weight = self._get_temperature_weight(node_info)
            final_score *= temp_weight

            # IV 加权：基于动态遗忘框架的信息价值
            iv_weight = self._get_iv_weight(node_info)
            final_score *= iv_weight

            results.append(RetrievalResult(
                node_id=node_id,
                node_type=node_info.get('node_type', 'UNKNOWN'),
                name=node_info.get('name', ''),
                description=node_info.get('description', ''),
                score=final_score,
                sources=sources
            ))
        
        # 去重：name 相似度 > 0.7 的节点只保留分数高的
        deduped = []
        for r in results:
            is_dup = False
            for existing in deduped:
                # 简单去重：name 包含关系
                if (r.name in existing.name or existing.name in r.name) and len(r.name) > 2:
                    is_dup = True
                    # 如果新结果分数更高，替换
                    if r.score > existing.score:
                        deduped.remove(existing)
                        deduped.append(r)
                    break
            if not is_dup:
                deduped.append(r)
        results = deduped

        # 按分数排序
        results.sort(key=lambda x: -x.score)

        return results

    def _vector_min_similarity(self) -> float:
        """Return quality threshold for the active vector backend."""
        engine_name = type(self.vector_engine).__name__ if self.vector_engine else ""
        if engine_name == "LocalVectorEngine":
            return 0.18
        return 0.35
    
    def expand_by_community(self, node_ids: List[str], expand_ratio: float = 0.3) -> List[str]:
        """
        社区扩展
        
        对于给定的节点，补充同社区的其他节点
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            
            # 获取输入节点的社区
            placeholders = ','.join('?' * len(node_ids))
            cursor.execute(f"""
                SELECT DISTINCT community_id FROM gm_nodes
                WHERE node_id IN ({placeholders})
                AND community_id IS NOT NULL
            """, node_ids)
            
            communities = [row[0] for row in cursor.fetchall()]
            
            if not communities:
                return node_ids
            
            # 获取同社区的其他节点
            comm_placeholders = ','.join('?' * len(communities))
            cursor.execute(f"""
                SELECT node_id FROM gm_nodes
                WHERE community_id IN ({comm_placeholders})
                AND node_id NOT IN ({placeholders})
                ORDER BY pagerank_score DESC
                LIMIT ?
            """, (*communities, *node_ids, int(len(node_ids) * expand_ratio)))
            
            expanded = [row[0] for row in cursor.fetchall()]
            
            return node_ids + expanded
