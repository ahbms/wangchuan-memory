"""
LlamaIndex Memory Adapter for Wangchuan.

Usage:
    from wangchuan.adapters.llamaindex import WangchuanLlamaIndexMemory
    
    memory = WangchuanLlamaIndexMemory(
        db_path="/path/to/index.sqlite"
    )
    
    # 在 LlamaIndex 索引中使用
    index = Index.from_documents(documents, storage_context=memory.storage_context())
"""

from typing import Any, Dict, List, Optional
from llama_index.core import Document
from llama_index.core.storage.docstore import BaseDocumentStore
from llama_index.core.schema import TextNode
import json


class WangchuanLlamaIndexMemory:
    """
    LlamaIndex 兼容的记忆适配器。
    
    将 Wangchuan 记忆系统作为 LlamaIndex 的 Document Store 使用。
    """
    
    def __init__(self, db_path: str):
        from wangchuan.memory_api import Memory
        self.memory = Memory(db_path=db_path)
        self._cache: Dict[str, Any] = {}
    
    def add_documents(self, nodes: List[TextNode]) -> None:
        """添加文档节点到记忆"""
        for node in nodes:
            self.memory.remember(
                content=node.text,
                importance=0.6,
                tags=node.metadata.get("tags", [])
            )
            self._cache[node.id_] = node
    
    def get_document(self, doc_id: str) -> Optional[TextNode]:
        """获取文档节点"""
        if doc_id in self._cache:
            return self._cache[doc_id]
        
        results = self.memory.recall(f"id:{doc_id}", limit=1)
        if results:
            node = TextNode(
                text=results[0].get("content", ""),
                id_=doc_id
            )
            self._cache[doc_id] = node
            return node
        return None
    
    def delete_document(self, doc_id: str) -> None:
        """删除文档"""
        self.memory.forget(f"id:{doc_id}")
        self._cache.pop(doc_id, None)
    
    def query(self, query_str: str, **kwargs) -> List[TextNode]:
        """查询记忆"""
        limit = kwargs.get("similarity_top_k", 5)
        results = self.memory.recall(query_str, limit=limit)
        
        nodes = []
        for r in results:
            node = TextNode(
                text=r.get("content", ""),
                id_=str(r.get("memory_id", "")),
                metadata={"score": r.get("score", 0)}
            )
            nodes.append(node)
        
        return nodes
    
    @property
    def docs(self) -> Dict[str, Any]:
        """返回所有文档"""
        return self._cache


class WangchuanLlamaIndexRetriever:
    """
    LlamaIndex Retriever 适配器。
    """
    
    def __init__(self, db_path: str, similarity_top_k: int = 5):
        from wangchuan.memory_api import Memory
        self.memory = Memory(db_path=db_path)
        self.similarity_top_k = similarity_top_k
    
    def retrieve(self, query_str: str) -> List[TextNode]:
        """检索相关节点"""
        results = self.memory.recall(query_str, limit=self.similarity_top_k)
        
        return [
            TextNode(
                text=r.get("content", ""),
                id_=str(r.get("memory_id", "")),
                metadata={
                    "score": r.get("score", 0),
                    "source_layer": r.get("source_layer")
                }
            )
            for r in results
        ]
    
    async def aretrieve(self, query_str: str) -> List[TextNode]:
        """异步检索"""
        return self.retrieve(query_str)
