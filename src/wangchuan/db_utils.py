"""
忘川统一数据库连接管理工具

提供标准化的 SQLite 连接上下文管理器，确保所有 DB 连接正确关闭。
"""

import sqlite3
import logging
from contextlib import contextmanager
from typing import Generator

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 10.0


@contextmanager
def get_connection(db_path: str, timeout: float = DEFAULT_TIMEOUT) -> Generator[sqlite3.Connection, None, None]:
    """
    统一的 SQLite 连接上下文管理器。

    使用方式：
        with get_connection(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT ...")

    Args:
        db_path: 数据库文件路径
        timeout: 连接超时秒数（默认 10s）

    Yields:
        sqlite3.Connection 对象，退出时自动 commit/rollback + close
    """
    conn = None
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        yield conn
        try:
            conn.commit()
        except Exception:
            pass
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


@contextmanager
def get_connection_rw(db_path: str, timeout: float = DEFAULT_TIMEOUT) -> Generator[sqlite3.Connection, None, None]:
    """
    读写模式连接（显式表示需要写入）。
    语义与 get_connection 相同，但调用方可以标注需要写入。
    """
    with get_connection(db_path, timeout=timeout) as conn:
        yield conn


@contextmanager
def get_connection_ro(db_path: str, timeout: float = DEFAULT_TIMEOUT) -> Generator[sqlite3.Connection, None, None]:
    """
    只读模式连接。用于不需要写入的查询。
    """
    conn = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", timeout=timeout, uri=True)
        yield conn
    except Exception:
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
