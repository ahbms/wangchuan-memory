"""忘川 Schema 迁移管理框架

提供版本化的数据库 schema 迁移管理，替代旧的 memory_schema 快照爆炸模式。

用法：
    from wangchuan.migrations import MigrationManager
    from wangchuan.paths import default_db_path

    mm = MigrationManager(str(default_db_path()))
    mm.run_migrations()  # 执行所有待执行的迁移

迁移文件约定：
    - 文件名格式: NNN_description.py (NNN 为三位数字序号)
    - 每个迁移文件必须定义 up(conn) 和 down(conn) 函数
    - up() 接收 sqlite3.Connection，执行迁移
    - down() 接收 sqlite3.Connection，回滚迁移
"""

from __future__ import annotations

import importlib
import logging
import os
import sqlite3
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent


class MigrationManager:
    """数据库 Schema 迁移管理器。

    跟踪 schema_version 表记录已应用的迁移，
    按文件名排序执行未应用的迁移。
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._ensure_version_table()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10.0)

    def _ensure_version_table(self) -> None:
        """确保 schema_version 表存在。"""
        conn = self._get_conn()
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    description TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    def get_current_version(self) -> Optional[str]:
        """获取当前 schema 版本（最后应用的迁移）。"""
        conn = self._get_conn()
        try:
            row = conn.execute(
                "SELECT version FROM schema_version ORDER BY applied_at DESC LIMIT 1"
            ).fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def get_applied_versions(self) -> List[str]:
        """获取所有已应用的迁移版本列表。"""
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT version FROM schema_version ORDER BY version"
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            conn.close()

    def _discover_migrations(self) -> List[str]:
        """发现所有迁移文件，按文件名排序。"""
        migrations = []
        for f in sorted(MIGRATIONS_DIR.glob("*.py")):
            if f.name.startswith("_") or f.name == "__init__.py":
                continue
            version = f.stem  # e.g. "001_baseline"
            migrations.append(version)
        return migrations

    def get_pending_migrations(self) -> List[str]:
        """获取待执行的迁移（未在 schema_version 中的）。"""
        applied = set(self.get_applied_versions())
        all_migrations = self._discover_migrations()
        return [m for m in all_migrations if m not in applied]

    def _load_migration_module(self, version: str):
        """动态加载迁移模块。"""
        module_name = f"wangchuan.migrations.{version}"
        try:
            return importlib.import_module(module_name)
        except ImportError as e:
            raise RuntimeError(f"无法加载迁移模块 {version}: {e}") from e

    def run_migrations(self) -> List[str]:
        """执行所有待执行的迁移。

        Returns:
            已执行的迁移版本列表
        """
        pending = self.get_pending_migrations()
        if not pending:
            logger.info("没有待执行的迁移")
            return []

        executed = []
        conn = self._get_conn()
        try:
            for version in pending:
                logger.info("执行迁移: %s", version)
                mod = self._load_migration_module(version)
                description = getattr(mod, "description", version)

                conn.execute("BEGIN IMMEDIATE")
                try:
                    mod.up(conn)
                    conn.execute(
                        "INSERT INTO schema_version (version, description) VALUES (?, ?)",
                        (version, description),
                    )
                    conn.commit()
                    executed.append(version)
                    logger.info("迁移 %s 完成", version)
                except Exception:
                    conn.rollback()
                    logger.exception("迁移 %s 失败，已回滚", version)
                    raise
        finally:
            conn.close()

        return executed

    def rollback(self, target_version: str) -> List[str]:
        """回滚到指定版本（不含该版本）。

        会按逆序执行 down()，直到 target_version 之前。

        Args:
            target_version: 回滚到此版本（不含）。传 "000" 回滚所有。

        Returns:
            已回滚的迁移版本列表（逆序）
        """
        applied = self.get_applied_versions()
        to_rollback = [v for v in reversed(applied) if v > target_version]

        if not to_rollback:
            logger.info("没有需要回滚的迁移")
            return []

        rolled_back = []
        conn = self._get_conn()
        try:
            for version in to_rollback:
                logger.info("回滚迁移: %s", version)
                mod = self._load_migration_module(version)

                conn.execute("BEGIN IMMEDIATE")
                try:
                    mod.down(conn)
                    conn.execute(
                        "DELETE FROM schema_version WHERE version = ?", (version,)
                    )
                    conn.commit()
                    rolled_back.append(version)
                    logger.info("回滚 %s 完成", version)
                except Exception:
                    conn.rollback()
                    logger.exception("回滚 %s 失败", version)
                    raise
        finally:
            conn.close()

        return rolled_back

    def status(self) -> dict:
        """获取迁移状态摘要。"""
        applied = self.get_applied_versions()
        pending = self.get_pending_migrations()
        return {
            "db_path": self.db_path,
            "current_version": self.get_current_version(),
            "applied_count": len(applied),
            "pending_count": len(pending),
            "applied": applied,
            "pending": pending,
        }
