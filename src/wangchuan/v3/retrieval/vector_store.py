#!/usr/bin/env python3
"""
忘川 v3.0 - 统一向量搜索抽象层
消除 4+ 个碎片化实现，提供统一的 search/insert/delete 接口

支持后端:
- LocalVectorStore: 本地 TF-IDF (gm_embeddings 表)
- XfyunVectorStore: 讯飞星火 embedding API (gm_embeddings 表)
- MemoryVectorStore: 本地 TF-IDF (memory_embeddings 表，兼容旧 memory_api)
- GenericVectorStore: 多后端 API (gm_embeddings 表)

用法:
    from wangchuan.v3.retrieval.vector_store import create_vector_store
    store = create_vector_store(db_path)
    results = store.search("Docker部署", top_k=5)
"""
from __future__ import annotations

from wangchuan.paths import workspace_root as _v3_ws_root

import json
import math
import os
import re
import sqlite3
import struct
import hashlib
import logging
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = _v3_ws_root()
DEFAULT_DB_PATH = str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite")


# ---------------------------------------------------------------------------
# Unified result type
# ---------------------------------------------------------------------------

@dataclass
class VectorSearchResult:
    """统一的向量搜索结果"""
    entity_id: str
    entity_type: str
    similarity: float
    metadata: Optional[Dict[str, Any]] = None  # 额外信息（如 content, name 等）

    # 向后兼容：支持 dict 风格访问
    def __getitem__(self, key: str) -> Any:
        if key == 'entity_id':
            return self.entity_id
        if key == 'entity_type':
            return self.entity_type
        if key == 'similarity':
            return self.similarity
        if self.metadata and key in self.metadata:
            return self.metadata[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class VectorStore(ABC):
    """向量存储抽象基类 — 所有后端必须实现这组接口"""

    @abstractmethod
    def search(
        self,
        query: str,
        top_k: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        """向量相似度搜索"""
        ...

    @abstractmethod
    def embed(
        self,
        entity_id: str,
        text: str,
        entity_type: str = "node",
    ) -> bool:
        """为文本生成向量并存储，成功返回 True"""
        ...

    @abstractmethod
    def delete(self, entity_id: str, entity_type: Optional[str] = None) -> bool:
        """删除向量索引，成功返回 True"""
        ...

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        ...

    @abstractmethod
    def ensure_ready(self) -> None:
        """确保引擎就绪（如有需要则生成向量）"""
        ...

    # ------------------------------------------------------------------
    # 兼容层：让旧代码中 search_similar / embed_and_store / embed_nodes
    # 继续工作，同时统一到新的 search / embed 接口。
    # ------------------------------------------------------------------

    def search_similar(
        self,
        query: str,
        top_k: int = 10,
        entity_type: str = "node",
    ) -> List[VectorSearchResult]:
        """兼容旧接口 → 转发到 search()"""
        return self.search(query, top_k=top_k, entity_type=entity_type)

    def embed_and_store(self, entity_type: str = "node") -> int:
        """兼容旧接口 → 全量重建向量索引，返回处理数"""
        return self._rebuild_index(entity_type)

    def embed_nodes(self) -> int:
        """兼容旧接口 → 全量重建向量索引，返回处理数"""
        return self._rebuild_index("node")

    @abstractmethod
    def _rebuild_index(self, entity_type: str = "node") -> int:
        """全量重建向量索引，子类必须实现"""
        ...


# ===========================================================================
# Helper: TF-IDF tokenizer / vector math
# ===========================================================================

def _tokenize_bigram(text: str) -> List[str]:
    """字符 bigram + unigram 分词（对中文友好）"""
    text = re.sub(r"\s+", " ", text.lower().strip())
    if len(text) < 2:
        return list(text)
    tokens = [text[i : i + 2] for i in range(len(text) - 1)]
    tokens.extend(list(text))
    return tokens


def _build_idf(docs: List[str]) -> Dict[str, float]:
    """从文档列表构建 IDF"""
    N = len(docs)
    if N == 0:
        return {}
    doc_freq: Counter = Counter()
    for doc in docs:
        for t in set(_tokenize_bigram(doc)):
            doc_freq[t] += 1
    return {t: math.log((N + 1) / (df + 1)) + 1 for t, df in doc_freq.items()}


def _text_to_tfidf(text: str, idf: Dict[str, float]) -> Dict[str, float]:
    """文本 → L2 归一化 TF-IDF 稀疏向量"""
    tokens = _tokenize_bigram(text)
    if not tokens:
        return {}
    tf = Counter(tokens)
    total = sum(tf.values())
    # 如果 IDF 为空或缺少 token，使用默认 IDF=1.0（纯 TF 模式）
    vec = {t: (c / total) * idf.get(t, 1.0) for t, c in tf.items()}
    norm = math.sqrt(sum(v * v for v in vec.values()))
    if norm > 0:
        vec = {k: v / norm for k, v in vec.items()}
    return vec


def _cosine_sparse(v1: Dict[str, float], v2: Dict[str, float]) -> float:
    """稀疏向量余弦相似度"""
    if not v1 or not v2:
        return 0.0
    common = set(v1) & set(v2)
    if not common:
        return 0.0
    return max(0.0, min(1.0, sum(v1[k] * v2[k] for k in common)))


def _cosine_dense(v1: List[float], v2: List[float]) -> float:
    """稠密向量余弦相似度"""
    if len(v1) != len(v2):
        min_len = min(len(v1), len(v2))
        v1, v2 = v1[:min_len], v2[:min_len]
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return max(0.0, dot / (n1 * n2))


# ===========================================================================
# LocalVectorStore — 本地 TF-IDF（gm_embeddings 表）
# ===========================================================================

class LocalVectorStore(VectorStore):
    """
    基于字符 bigram TF-IDF 的本地向量引擎
    纯 Python，无外部 API 依赖
    存储目标: gm_embeddings 表（JSON 格式稀疏向量）
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._idf_cache: Optional[Dict[str, float]] = None

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gm_embeddings (
                    embedding_id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    entity_id TEXT,
                    model_name TEXT,
                    dimensions INTEGER,
                    embedding BLOB
                )
            """)
            conn.commit()

    def _get_node_texts(self) -> List[Tuple[str, str, str, str]]:
        """返回 (node_id, name, description, content) 列表"""
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT node_id, name, description, COALESCE(content, '') FROM gm_nodes")
            return c.fetchall()

    def _rebuild_idf(self) -> Dict[str, float]:
        """重建 IDF 缓存"""
        rows = self._get_node_texts()
        docs = [f"{n} {d} {c}" for _, n, d, c in rows]
        self._idf_cache = _build_idf(docs)
        return self._idf_cache

    # -- 接口实现 --

    def search(
        self,
        query: str,
        top_k: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        if self._idf_cache is None:
            self._rebuild_idf()

        query_vec = _text_to_tfidf(query, self._idf_cache or {})
        if not query_vec:
            return []

        et = entity_type or "node"
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT entity_id, embedding FROM gm_embeddings WHERE entity_type = ?",
                (et,),
            )
            rows = c.fetchall()

        results: List[VectorSearchResult] = []
        for entity_id, vec_bytes in rows:
            try:
                stored = json.loads(vec_bytes.decode() if isinstance(vec_bytes, bytes) else vec_bytes)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            sim = _cosine_sparse(query_vec, stored)
            if sim > 0.01:
                results.append(VectorSearchResult(entity_id=entity_id, entity_type=et, similarity=sim))

        results.sort(key=lambda r: r.similarity, reverse=True)
        return results[:top_k]

    def embed(self, entity_id: str, text: str, entity_type: str = "node") -> bool:
        self._ensure_table()
        if self._idf_cache is None:
            self._rebuild_idf()
        vec = _text_to_tfidf(text, self._idf_cache)
        if not vec:
            return False
        embedding_id = f"local_{entity_id}"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO gm_embeddings
                   (embedding_id, entity_type, entity_id, model_name, dimensions, embedding)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (embedding_id, entity_type, entity_id, "local-bigram-tfidf", len(vec), json.dumps(vec).encode()),
            )
            conn.commit()
        return True

    def delete(self, entity_id: str, entity_type: Optional[str] = None) -> bool:
        embedding_id = f"local_{entity_id}"
        with sqlite3.connect(self.db_path) as conn:
            if entity_type:
                conn.execute(
                    "DELETE FROM gm_embeddings WHERE embedding_id = ? AND entity_type = ?",
                    (embedding_id, entity_type),
                )
            else:
                conn.execute("DELETE FROM gm_embeddings WHERE embedding_id = ?", (embedding_id,))
            conn.commit()
        return True

    def get_stats(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM gm_embeddings WHERE model_name LIKE 'local%'")
            local_count = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM gm_embeddings")
            total_count = c.fetchone()[0]
        return {
            "local_embeddings": local_count,
            "total_embeddings": total_count,
            "vocab_size": len(self._idf_cache) if self._idf_cache else 0,
        }

    def ensure_ready(self) -> None:
        self._ensure_table()
        stats = self.get_stats()
        if stats["local_embeddings"] == 0:
            self._rebuild_index()

    def _rebuild_index(self, entity_type: str = "node") -> int:
        self._ensure_table()
        self._rebuild_idf()
        rows = self._get_node_texts()
        count = 0
        for node_id, name, desc, content in rows:
            text = f"{name} {desc} {content}"
            vec = _text_to_tfidf(text, self._idf_cache)
            if vec:
                embedding_id = f"local_{node_id}"
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        """INSERT OR REPLACE INTO gm_embeddings
                           (embedding_id, entity_type, entity_id, model_name, dimensions, embedding)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (embedding_id, entity_type, node_id, "local-bigram-tfidf", len(vec), json.dumps(vec).encode()),
                    )
                    conn.commit()
                count += 1
        return count


# ===========================================================================
# XfyunVectorStore — 讯飞星火 embedding API（gm_embeddings 表）
# ===========================================================================

class XfyunVectorStore(VectorStore):
    """
    讯飞星火向量引擎
    使用 xop3qwen8bembedding 模型，768 维稠密向量
    存储目标: gm_embeddings 表（二进制 struct 格式）
    """

    def __init__(
        self,
        db_path: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        dimensions: int = 768,
    ):
        self.db_path = db_path
        self.api_key = api_key or os.getenv("EMBEDDING_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("EMBEDDING_API_KEY 环境变量未设置，请设置你的讯飞 API Key")
        self.base_url = base_url or os.getenv(
            "EMBEDDING_BASE_URL",
            "https://maas-api.cn-huabei-1.xf-yun.com/v2",
        )
        self.model = model or os.getenv("EMBEDDING_MODEL", "xop3qwen8bembedding")
        self.dimensions = dimensions
        self._query_cache: Dict[str, List[float]] = {}
        self.request_timeout = float(os.getenv("WANGCHUAN_XFYUN_TIMEOUT_SECONDS", "12"))
        self.last_error_kind = ""
        self.last_error_message = ""

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gm_embeddings (
                    embedding_id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    entity_id TEXT,
                    model_name TEXT,
                    dimensions INTEGER,
                    embedding BLOB
                )
            """)
            conn.commit()

    # -- API 调用 --

    def _call_api(self, text: str) -> Optional[List[float]]:
        import urllib.request
        import urllib.error
        import socket

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = json.dumps({"input": [text[:8000]], "model": self.model}).encode()

        self.last_error_kind = ""
        self.last_error_message = ""
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                result = json.loads(resp.read())
                if "data" in result and len(result["data"]) > 0:
                    return result["data"][0].get("embedding", [])
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            self.last_error_kind = "http_error"
            self.last_error_message = f"{e.code} {body}".strip()
            logger.warning("【WangChuan】[XfyunVectorStore] http error=%s body=%s", e.code, body)
        except Exception as e:
            text_err = str(e).lower()
            if "timeout" in text_err or isinstance(e, (TimeoutError, socket.timeout)):
                self.last_error_kind = "timeout"
            else:
                self.last_error_kind = "request_failed"
            self.last_error_message = str(e)[:240]
            logger.warning("【WangChuan】[XfyunVectorStore] error: %s", e)
        return None

    # -- 接口实现 --

    def search(
        self,
        query: str,
        top_k: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        cache_key = query[:100]
        query_emb = self._query_cache.get(cache_key)
        if query_emb is None:
            query_emb = self._call_api(query)
            if query_emb:
                self._query_cache[cache_key] = query_emb
        if not query_emb:
            return []

        et = entity_type or "node"
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute(
                "SELECT entity_id, embedding, dimensions FROM gm_embeddings WHERE entity_type = ? AND model_name = ?",
                (et, self.model),
            )
            rows = c.fetchall()

        results: List[VectorSearchResult] = []
        for entity_id, vec_bytes, dims in rows:
            try:
                stored = list(struct.unpack(f"{dims}f", vec_bytes))
            except struct.error:
                continue
            sim = _cosine_dense(query_emb, stored)
            if sim > 0.01:
                results.append(VectorSearchResult(entity_id=entity_id, entity_type=et, similarity=sim))

        results.sort(key=lambda r: r.similarity, reverse=True)
        return results[:top_k]

    def embed(self, entity_id: str, text: str, entity_type: str = "node") -> bool:
        self._ensure_table()
        emb = self._call_api(text)
        if not emb:
            return False
        embedding_id = f"xfyun_{entity_id}"
        vec_bytes = struct.pack(f"{len(emb)}f", *emb)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO gm_embeddings
                   (embedding_id, entity_type, entity_id, model_name, dimensions, embedding)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (embedding_id, entity_type, entity_id, self.model, len(emb), vec_bytes),
            )
            conn.commit()
        return True

    def delete(self, entity_id: str, entity_type: Optional[str] = None) -> bool:
        embedding_id = f"xfyun_{entity_id}"
        with sqlite3.connect(self.db_path) as conn:
            if entity_type:
                conn.execute(
                    "DELETE FROM gm_embeddings WHERE embedding_id = ? AND entity_type = ?",
                    (embedding_id, entity_type),
                )
            else:
                conn.execute("DELETE FROM gm_embeddings WHERE embedding_id = ?", (embedding_id,))
            conn.commit()
        return True

    def get_stats(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM gm_embeddings WHERE model_name = ?", (self.model,))
            count = c.fetchone()[0]
        return {"xfyun_embeddings": count, "model": self.model, "dimensions": self.dimensions}

    def ensure_ready(self) -> None:
        self._ensure_table()
        stats = self.get_stats()
        if stats["xfyun_embeddings"] == 0:
            self._rebuild_index()

    def _rebuild_index(self, entity_type: str = "node") -> int:
        self._ensure_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT node_id, name, description, COALESCE(content, '') FROM gm_nodes")
            nodes = c.fetchall()

        count = 0
        for node in nodes:
            text = f"{node['name']} {node['description']}"
            emb = self._call_api(text)
            if emb:
                embedding_id = f"xfyun_{node['node_id']}"
                vec_bytes = struct.pack(f"{len(emb)}f", *emb)
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        """INSERT OR REPLACE INTO gm_embeddings
                           (embedding_id, entity_type, entity_id, model_name, dimensions, embedding)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (embedding_id, entity_type, node["node_id"], self.model, len(emb), vec_bytes),
                    )
                    conn.commit()
                count += 1
                logger.info("  ✓ %s (%dd)", node["name"], len(emb))
        return count


# ===========================================================================
# MemoryVectorStore — 本地 TF-IDF（memory_embeddings 表，兼容 memory_api）
# ===========================================================================

class MemoryVectorStore(VectorStore):
    """
    本地 TF-IDF 向量引擎 — 面向 memory_api 的记忆表
    存储目标: memory_embeddings 表
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._idf_cache: Optional[Dict[str, float]] = None

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL,
                    embedding_model TEXT NOT NULL,
                    embedding_vector TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(memory_id, embedding_model)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_embeddings_model ON memory_embeddings(embedding_model)"
            )
            conn.commit()

    def _get_memory_texts(self) -> List[Tuple[int, str]]:
        """返回 (memory_id, content) 列表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT id, content FROM memories WHERE content IS NOT NULL")
            return [(row["id"], row["content"]) for row in c.fetchall()]

    def _rebuild_idf(self) -> Dict[str, float]:
        rows = self._get_memory_texts()
        docs = [content for _, content in rows]
        self._idf_cache = _build_idf(docs)
        return self._idf_cache

    # -- 接口实现 --

    def search(
        self,
        query: str,
        top_k: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        if self._idf_cache is None:
            self._rebuild_idf()

        query_vec = _text_to_tfidf(query, self._idf_cache or {})
        if not query_vec:
            return []

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT m.id, m.content, me.embedding_vector
                FROM memories m
                JOIN memory_embeddings me ON m.id = me.memory_id
                WHERE me.embedding_model = 'local-tfidf'
            """)
            rows = c.fetchall()

        results: List[VectorSearchResult] = []
        for row in rows:
            try:
                stored_vec = json.loads(row["embedding_vector"])
            except json.JSONDecodeError:
                continue
            sim = _cosine_sparse(query_vec, stored_vec)
            if sim > 0.01:
                results.append(
                    VectorSearchResult(
                        entity_id=str(row["id"]),
                        entity_type="memory",
                        similarity=sim,
                        metadata={"content": row["content"], "memory_id": row["id"]},
                    )
                )

        results.sort(key=lambda r: r.similarity, reverse=True)
        return results[:top_k]

    def embed(self, entity_id: str, text: str, entity_type: str = "memory") -> bool:
        self._ensure_table()
        if self._idf_cache is None:
            self._rebuild_idf()
        vec = _text_to_tfidf(text, self._idf_cache)
        if not vec:
            return False
        try:
            memory_id = int(entity_id)
        except (ValueError, TypeError):
            return False
        vec_json = json.dumps(vec)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO memory_embeddings
                   (memory_id, embedding_model, embedding_vector)
                   VALUES (?, ?, ?)""",
                (memory_id, "local-tfidf", vec_json),
            )
            conn.commit()
        return True

    def delete(self, entity_id: str, entity_type: Optional[str] = None) -> bool:
        try:
            memory_id = int(entity_id)
        except (ValueError, TypeError):
            return False
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))
            conn.commit()
        return True

    def get_stats(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM memory_embeddings WHERE embedding_model = 'local-tfidf'")
            count = c.fetchone()[0]
        return {
            "indexed_memories": count,
            "vocab_size": len(self._idf_cache) if self._idf_cache else 0,
        }

    def ensure_ready(self) -> None:
        self._ensure_table()

    def _rebuild_index(self, entity_type: str = "memory") -> int:
        self._ensure_table()
        self._rebuild_idf()
        rows = self._get_memory_texts()
        count = 0
        for memory_id, content in rows:
            vec = _text_to_tfidf(str(content), self._idf_cache)
            if vec:
                vec_json = json.dumps(vec)
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        """INSERT OR REPLACE INTO memory_embeddings
                           (memory_id, embedding_model, embedding_vector)
                           VALUES (?, ?, ?)""",
                        (memory_id, "local-tfidf", vec_json),
                    )
                    conn.commit()
                count += 1
        return count


# ===========================================================================
# GenericVectorStore — 多后端 API（gm_embeddings 表）
# ===========================================================================

class GenericVectorStore(VectorStore):
    """
    通用向量引擎 — 支持 OpenAI / 火山引擎 / 讯飞等后端
    存储目标: gm_embeddings 表（二进制 struct 格式）
    """

    def __init__(
        self,
        db_path: str,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        dimensions: int = 512,
    ):
        self.db_path = db_path
        self.api_key = api_key
        self.base_url = base_url or "https://api.openai.com/v1"
        self.model = model or "text-embedding-3-small"
        self.dimensions = dimensions

    def _ensure_table(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS gm_embeddings (
                    embedding_id TEXT PRIMARY KEY,
                    entity_type TEXT,
                    entity_id TEXT,
                    model_name TEXT,
                    dimensions INTEGER,
                    embedding BLOB
                )
            """)
            conn.commit()

    def _call_api(self, text: str) -> Optional[List[float]]:
        import urllib.request
        import urllib.error

        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        data = json.dumps({"input": text[:8000], "model": self.model}).encode()
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                if "data" in result and len(result["data"]) > 0:
                    return result["data"][0].get("embedding", [])
        except Exception as e:
            logger.warning("【WangChuan】[GenericVectorStore] api error: %s", e)
        return None

    def search(
        self,
        query: str,
        top_k: int = 10,
        entity_type: Optional[str] = None,
    ) -> List[VectorSearchResult]:
        if not self.api_key:
            return []
        query_vector = self._call_api(query)
        if not query_vector:
            return []

        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            if entity_type:
                c.execute(
                    "SELECT entity_id, entity_type, embedding, dimensions FROM gm_embeddings WHERE entity_type = ?",
                    (entity_type,),
                )
            else:
                c.execute("SELECT entity_id, entity_type, embedding, dimensions FROM gm_embeddings")
            rows = c.fetchall()

        results: List[VectorSearchResult] = []
        for eid, etype, vec_bytes, dims in rows:
            expected = dims * 4
            if not vec_bytes or len(vec_bytes) != expected:
                continue
            try:
                vec = list(struct.unpack(f"{dims}f", vec_bytes))
            except struct.error:
                continue
            sim = _cosine_dense(query_vector, vec)
            results.append(VectorSearchResult(entity_id=eid, entity_type=etype, similarity=sim))

        results.sort(key=lambda r: r.similarity, reverse=True)
        return results[:top_k]

    def embed(self, entity_id: str, text: str, entity_type: str = "node") -> bool:
        self._ensure_table()
        if not self.api_key:
            return False
        vector = self._call_api(text)
        if not vector:
            return False
        content = f"{entity_id}:{text[:100]}"
        embedding_id = f"emb_{hashlib.sha256(content.encode()).hexdigest()[:16]}"
        vec_bytes = struct.pack(f"{len(vector)}f", *vector)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO gm_embeddings
                   (embedding_id, entity_type, entity_id, model_name, dimensions, embedding)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (embedding_id, entity_type, entity_id, self.model, len(vector), vec_bytes),
            )
            conn.commit()
        return True

    def delete(self, entity_id: str, entity_type: Optional[str] = None) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            if entity_type:
                conn.execute(
                    "DELETE FROM gm_embeddings WHERE entity_id = ? AND entity_type = ?",
                    (entity_id, entity_type),
                )
            else:
                conn.execute("DELETE FROM gm_embeddings WHERE entity_id = ?", (entity_id,))
            conn.commit()
        return True

    def get_stats(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM gm_embeddings")
            total = c.fetchone()[0]
        return {"total_embeddings": total, "model": self.model, "dimensions": self.dimensions}

    def ensure_ready(self) -> None:
        self._ensure_table()

    def _rebuild_index(self, entity_type: str = "node") -> int:
        self._ensure_table()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT node_id, name, description, COALESCE(content, '') FROM gm_nodes")
            nodes = c.fetchall()

        count = 0
        for node in nodes:
            text = f"{node['name']} {node['description']}"
            if self.embed(node["node_id"], text, entity_type):
                count += 1
        return count


# ===========================================================================
# 工厂函数
# ===========================================================================

def create_vector_store(
    db_path: str = DEFAULT_DB_PATH,
    store_type: str = "auto",
    **kwargs: Any,
) -> VectorStore:
    """
    工厂函数：根据 store_type 创建向量存储

    store_type:
        "auto"   — 优先 XfyunVectorStore（有 API key），降级 LocalVectorStore
        "local"  — 本地 TF-IDF (gm_embeddings)
        "xfyun"  — 讯飞星火 API (gm_embeddings)
        "memory" — 本地 TF-IDF (memory_embeddings，兼容 memory_api)
        "generic"— 通用多后端 API (gm_embeddings)
    """
    if store_type == "auto":
        emb_key = kwargs.get("api_key") or os.getenv("EMBEDDING_API_KEY")
        if emb_key:
            return XfyunVectorStore(db_path, api_key=emb_key, **{k: v for k, v in kwargs.items() if k != "api_key"})
        return LocalVectorStore(db_path)
    elif store_type == "local":
        return LocalVectorStore(db_path)
    elif store_type == "xfyun":
        return XfyunVectorStore(db_path, **kwargs)
    elif store_type == "memory":
        return MemoryVectorStore(db_path)
    elif store_type == "generic":
        return GenericVectorStore(db_path, **kwargs)
    else:
        raise ValueError(f"Unknown store_type: {store_type!r}. Use 'auto', 'local', 'xfyun', 'memory', or 'generic'.")


# ===========================================================================
# 兼容旧的 dataclass 导出（某些旧代码 import 了它们）
# ===========================================================================

# 保留旧名称作为别名
LocalEmbeddingResult = VectorSearchResult
VectorResult = VectorSearchResult
EmbeddingResult = VectorSearchResult

__all__ = [
    "VectorStore",
    "VectorSearchResult",
    "LocalVectorStore",
    "XfyunVectorStore",
    "MemoryVectorStore",
    "GenericVectorStore",
    "create_vector_store",
    "LocalEmbeddingResult",  # compat alias
    "VectorResult",          # compat alias
    "EmbeddingResult",       # compat alias
]
