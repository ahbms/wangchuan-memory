#!/usr/bin/env python3
"""清理 memory_schema 旧快照目录。

将 memory_schema/ 下的旧快照归档到 memory_schema/_archive/ 目录，
并创建一个清单文件记录归档内容。

用法：
    python3 -m wangchuan.scripts.cleanup_snapshots [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path


def find_snapshot_dirs(base: Path) -> list[Path]:
    """查找 memory_schema 下的快照目录。"""
    schema_dir = base / "memory_schema"
    if not schema_dir.exists():
        return []

    # 查找模式: memory_schema/ 下的子目录（排除 _archive 本身）
    snapshots = []
    for child in sorted(schema_dir.iterdir()):
        if child.is_dir() and child.name != "_archive":
            snapshots.append(child)
    return snapshots


def archive_snapshots(base: Path, dry_run: bool = False) -> dict:
    """将快照目录归档。"""
    schema_dir = base / "memory_schema"
    archive_dir = schema_dir / "_archive"

    snapshots = find_snapshot_dirs(base)
    if not snapshots:
        return {"archived": 0, "message": "没有找到快照目录"}

    if not dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)

    archived = []
    for snap in snapshots:
        dest = archive_dir / snap.name
        if dry_run:
            archived.append({"from": str(snap), "to": str(dest), "size": _dir_size(snap)})
        else:
            if dest.exists():
                # 合并或跳过
                continue
            shutil.move(str(snap), str(dest))
            archived.append({"from": str(snap), "to": str(dest)})

    # 写清单
    if not dry_run and archived:
        manifest_path = archive_dir / "manifest.json"
        manifest = {
            "archived_at": datetime.now().isoformat(),
            "count": len(archived),
            "items": archived,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))

    return {
        "archived": len(archived),
        "dry_run": dry_run,
        "items": archived,
    }


def _dir_size(path: Path) -> int:
    """计算目录大小（字节）。"""
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            total += f.stat().st_size
    return total


def main():
    parser = argparse.ArgumentParser(description="清理忘川 memory_schema 快照")
    parser.add_argument("--dry-run", action="store_true", help="仅预览，不实际移动")
    parser.add_argument("--base", type=str, default=None, help="基础目录路径")
    args = parser.parse_args()

    if args.base:
        base = Path(args.base)
    else:
        # 默认路径
        from wangchuan.paths import data_root
        base = data_root()

    print(f"基础目录: {base}")
    snapshots = find_snapshot_dirs(base)
    print(f"找到 {len(snapshots)} 个快照目录")

    if snapshots:
        for s in snapshots[:10]:
            print(f"  - {s.name} ({_dir_size(s) / 1024:.1f} KB)")
        if len(snapshots) > 10:
            print(f"  ... 还有 {len(snapshots) - 10} 个")

    result = archive_snapshots(base, dry_run=args.dry_run)
    print(f"\n{'预览' if args.dry_run else '执行'}完成: 归档 {result['archived']} 个快照")


if __name__ == "__main__":
    main()
