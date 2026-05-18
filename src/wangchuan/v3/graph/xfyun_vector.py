#!/usr/bin/env python3
"""
忘川 v3.0 - 讯飞向量引擎
使用讯飞星火 embedding API 生成真实语义向量
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import sqlite3
import json
import os
import socket
import logging
from pathlib import Path
import math
import struct
import hashlib
import urllib.request
import urllib.error
from typing import List, Dict, Optional
from dataclasses import dataclass


WORKSPACE_ROOT = _v3_ws_root()
DEFAULT_DB_PATH = str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite")
logger = logging.getLogger(__name__)

@dataclass
class VectorResult:
    entity_id: str
    entity_type: str
    similarity: float


class XfyunVectorEngine:
    """
    讯飞星火向量引擎
    
    使用 xop3qwen8bembedding 模型，768 维
    """
    
    def __init__(self, db_path: str, api_key: str = None, 
                 base_url: str = None, model: str = None, dimensions: int = 768):
        self.db_path = db_path
        self.api_key = api_key or os.getenv("EMBEDDING_API_KEY", "")
        self.base_url = base_url or "https://maas-api.cn-huabei-1.xf-yun.com/v2"
        self.model = model or "xop3qwen8bembedding"
        self.dimensions = dimensions
        self._query_cache: Dict[str, List[float]] = {}  # 查询缓存
        self.request_timeout = float(os.getenv("WANGCHUAN_XFYUN_TIMEOUT_SECONDS", "12"))
        self.last_error_kind = ""
        self.last_error_message = ""

    def _mark_error(self, kind: str, message: str) -> None:
        self.last_error_kind = kind
        self.last_error_message = str(message or "")[:240]

    def _clear_error(self) -> None:
        self.last_error_kind = ""
        self.last_error_message = ""

    @staticmethod
    def _is_timeout_error(exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, socket.timeout)):
            return True
        if isinstance(exc, urllib.error.URLError) and isinstance(getattr(exc, "reason", None), socket.timeout):
            return True
        text = str(exc or "").lower()
        return "timed out" in text or "timeout" in text
    
    def _call_api(self, text: str) -> Optional[List[float]]:
        """调用讯飞 embedding API"""
        url = f"{self.base_url}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = json.dumps({
            "input": [text[:8000]],
            "model": self.model
        }).encode()
        
        self._clear_error()
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.request_timeout) as resp:
                result = json.loads(resp.read())
                if "data" in result and len(result["data"]) > 0:
                    return result["data"][0].get("embedding", [])
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="ignore")[:200]
            self._mark_error("http_error", f"{e.code} {body}".strip())
            logger.warning("【WangChuan】[XfyunVector] http error=%s body=%s", e.code, body)
        except Exception as e:
            if self._is_timeout_error(e):
                self._mark_error("timeout", str(e))
                logger.warning("【WangChuan】[XfyunVector] timeout after %ss", self.request_timeout)
            else:
                self._mark_error("request_failed", str(e))
                logger.warning("【WangChuan】[XfyunVector] request failed: %s", e)
        return None
    
    def _call_batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批量调用（逐个，讯飞不支持批量）"""
        results = []
        for text in texts:
            emb = self._call_api(text)
            results.append(emb)
        return results
    
    def embed_nodes(self) -> int:
        """为所有节点生成向量"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT node_id, name, description, COALESCE(content, '') FROM gm_nodes")
            nodes = c.fetchall()
        
        count = 0
        for node in nodes:
            text = f"{node['name']} {node['description']} {node[3]}"
            emb = self._call_api(text)
            
            if emb:
                vec_bytes = struct.pack(f"{len(emb)}f", *emb)
                embedding_id = f"xfyun_{node['node_id']}"
                
                with sqlite3.connect(self.db_path) as conn:
                    c = conn.cursor()
                    c.execute("""
                        INSERT OR REPLACE INTO gm_embeddings
                        (embedding_id, entity_type, entity_id, model_name, dimensions, embedding)
                        VALUES (?, 'node', ?, ?, ?, ?)
                    """, (embedding_id, node["node_id"], self.model, len(emb), vec_bytes))
                    conn.commit()
                
                count += 1
                print(f"  ✓ {node['name']} ({len(emb)}d)")
        
        return count
    
    def search_similar(self, query: str, top_k: int = 10) -> List[VectorResult]:
        """向量相似度搜索"""
        # 检查缓存
        cache_key = query[:100]
        if cache_key in self._query_cache:
            query_emb = self._query_cache[cache_key]
        else:
            query_emb = self._call_api(query)
            if query_emb:
                self._query_cache[cache_key] = query_emb

        if not query_emb:
            return []
        
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("""
                SELECT entity_id, embedding, dimensions FROM gm_embeddings
                WHERE entity_type = 'node' AND model_name = ?
            """, (self.model,))
            rows = c.fetchall()
        
        results = []
        for entity_id, vec_bytes, dims in rows:
            stored = list(struct.unpack(f"{dims}f", vec_bytes))
            sim = self._cosine(query_emb, stored)
            if sim > 0.01:
                results.append(VectorResult(entity_id=entity_id, entity_type="node", similarity=sim))
        
        results.sort(key=lambda x: x.similarity, reverse=True)
        return results[:top_k]
    
    def _cosine(self, v1: List[float], v2: List[float]) -> float:
        if len(v1) != len(v2):
            # 维度不匹配时截断到较短
            min_len = min(len(v1), len(v2))
            v1, v2 = v1[:min_len], v2[:min_len]
        
        dot = sum(a * b for a, b in zip(v1, v2))
        n1 = math.sqrt(sum(a * a for a in v1))
        n2 = math.sqrt(sum(b * b for b in v2))
        
        if n1 == 0 or n2 == 0:
            return 0.0
        return max(0.0, dot / (n1 * n2))
    
    def get_stats(self) -> Dict:
        with sqlite3.connect(self.db_path) as conn:
            c = conn.cursor()
            c.execute("SELECT COUNT(*) FROM gm_embeddings WHERE model_name = ?", (self.model,))
            count = c.fetchone()[0]
        return {"xfyun_embeddings": count, "model": self.model, "dimensions": self.dimensions}


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_PATH
    
    engine = XfyunVectorEngine(db)
    
    # 先检查已有向量
    stats = engine.get_stats()
    print(f"已有: {stats['xfyun_embeddings']} 个向量")
    
    if stats["xfyun_embeddings"] == 0:
        print("生成中...")
        count = engine.embed_nodes()
        print(f"✅ 已为 {count} 个节点生成向量")
    
    # 测试搜索
    for q in ["Docker", "权限", "Python环境"]:
        results = engine.search_similar(q, top_k=3)
        print(f"\n🔍 '{q}':")
        for r in results:
            print(f"  {r.entity_id[:25]} sim={r.similarity:.4f}")
