"""
LangChain Memory Adapter for Wangchuan.

Usage:
    from wangchuan.adapters.langchain import WangchuanLangChainMemory
    
    memory = WangchuanLangChainMemory(
        db_path="/path/to/index.sqlite"
    )
    
    # 在 LangChain agent 中使用
    agent = Agent(
        memory=memory,
        ...
    )
"""

from typing import Any, Dict, List, Optional
from langchain.memory.chat_memory import BaseChatMemory
from langchain.schema import HumanMessage, AIMessage, SystemMessage
from langchain.schema.messages import BaseMessage
from pydantic import Field


class WangchuanLangChainMemory(BaseChatMemory):
    """
    LangChain 兼容的记忆适配器。
    
    将 Wangchuan 记忆系统作为 LangChain 的 ChatMemory 使用。
    """
    
    db_path: str = Field(default=None)
    memory: Any = Field(default=None, exclude=True)
    max_tokens: int = Field(default=2000)
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        db_path = kwargs.get("db_path")
        if db_path:
            from wangchuan.memory_api import Memory
            self.memory = Memory(db_path=db_path)
    
    @property
    def memory_variables(self) -> List[str]:
        return ["history"]
    
    def load_memory_variables(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """加载记忆历史"""
        query = inputs.get("query", "")
        if not query:
            query = "chat history"
        
        if self.memory:
            results = self.memory.recall(query, limit=10)
            history = "\n".join([
                r.get("content", "")[:200] 
                for r in results
            ])
        else:
            history = ""
        
        return {"history": history}
    
    def save_context(self, inputs: Dict[str, Any], outputs: Dict[str, Any]) -> None:
        """保存对话上下文到记忆"""
        input_str = str(inputs.get("input", ""))
        output_str = str(outputs.get("output", ""))
        
        if input_str and self.memory:
            self.memory.remember(
                content=f"用户: {input_str}",
                importance=0.5
            )
        
        if output_str and self.memory:
            self.memory.remember(
                content=f"AI: {output_str}",
                importance=0.5
            )
    
    def clear(self) -> None:
        """清空记忆"""
        pass


class WangchuanLangChainRetriever:
    """
    LangChain Retriever 适配器。
    
    将 Wangchuan 作为 LangChain Document Retriever 使用。
    """
    
    def __init__(self, db_path: str, search_kwargs: Optional[Dict] = None):
        from wangchuan.memory_api import Memory
        self.memory = Memory(db_path=db_path)
        self.search_kwargs = search_kwargs or {}
        self.k = self.search_kwargs.get("k", 5)
    
    def get_relevant_documents(self, query: str) -> List[Any]:
        """检索相关文档"""
        results = self.memory.recall(query, limit=self.k)
        
        from langchain.schema import Document
        docs = [
            Document(
                page_content=r.get("content", ""),
                metadata={
                    "memory_id": r.get("memory_id"),
                    "score": r.get("score", 0),
                    "source_layer": r.get("source_layer"),
                    "created_at": r.get("created_at")
                }
            )
            for r in results
        ]
        return docs
    
    async def aget_relevant_documents(self, query: str) -> List[Any]:
        """异步检索"""
        return self.get_relevant_documents(query)
