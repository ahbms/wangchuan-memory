from __future__ import annotations

"""WangChuan runtime accessor helpers.

这一层承接 memory_api 中低风险运行时访问器：
- sqlite connection accessor
- local vector lazy accessor
- entity linker lazy accessor
"""

from typing import Any
import importlib
import os
import sqlite3

try:
    from wangchuan.v3.local_vector_search import LocalMemoryVectorSearch
except ImportError:
    from wangchuan.v3.local_vector_search import LocalMemoryVectorSearch

try:
    from wangchuan.v3.llm_memory import EntityLinker
except ImportError:
    from wangchuan.v3.llm_memory import EntityLinker


def get_local_vector(memory_obj: Any) -> LocalMemoryVectorSearch:
    """获取本地向量搜索引擎（懒加载）。"""
    if memory_obj._local_vector is None:
        memory_obj._local_vector = LocalMemoryVectorSearch(memory_obj.db_path)
        memory_obj._local_vector.ensure_table()
    return memory_obj._local_vector


def get_entity_linker(memory_obj: Any) -> EntityLinker:
    """获取实体链接器（懒加载）。"""
    if memory_obj._entity_linker is None:
        memory_obj._entity_linker = EntityLinker(memory_obj.db_path)
    return memory_obj._entity_linker


def _initialize_fresh_database(db_path: str) -> None:
    """Create a fresh WangChuan database with the baseline schema.

    Standalone users often start with an empty data directory. The public
    Memory API should not require a manual migration step for the first write
    or status check.
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        baseline = importlib.import_module("wangchuan.migrations.001_baseline")
        baseline.up(conn)
        conn.commit()
    finally:
        conn.close()


def conn(memory_obj: Any):
    """获取数据库连接。不存在时自动初始化 baseline schema。"""
    if not os.path.exists(memory_obj.db_path):
        _initialize_fresh_database(memory_obj.db_path)
    return sqlite3.connect(memory_obj.db_path)

