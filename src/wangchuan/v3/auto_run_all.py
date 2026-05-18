#!/usr/bin/env python3
"""
忘川 v3.0 - 全自动运行脚本
一键执行所有忘川功能，供heartbeat调用

功能：
1. 自动维护（遗忘衰减+清理）
2. 反思引擎（高重要性事件提取）
3. 身份快照（IDENTITY.md版本追踪）
4. 叙事一致性检查
5. 图数据库维护（PageRank+社区检测）
6. 运行态巡检（不含能量机制）
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)
WORKSPACE_ROOT = Path(os.getenv("OPENCLAW_WORKSPACE", str(Path(__file__).resolve().parents[3])))
sys.path.insert(0, str(WORKSPACE_ROOT))

DB_PATH = str(WORKSPACE_ROOT / "tiangong" / "wangchuan" / ".index" / "index.sqlite")
IDENTITY_PATH = str(WORKSPACE_ROOT / "IDENTITY.md")
MEMORY_DIR = str(WORKSPACE_ROOT / "memory")
GRAPH_DB = DB_PATH


def run_auto_maintenance():
    """1. 自动维护：遗忘衰减 + 清理"""
    try:
        from wangchuan.v3.auto_maintainer import AutoMaintainer
        m = AutoMaintainer(db_path=DB_PATH)
        result = m.daily_maintenance()
        return {"module": "auto_maintainer", "status": "ok", "result": result}
    except Exception as e:
        return {"module": "auto_maintainer", "status": "error", "error": str(e)}


def run_reflector():
    """2. 反思引擎：提取高重要性事件"""
    try:
        from wangchuan.v3.reflector import ReflectEngine
        engine = ReflectEngine()
        result = engine.reflect(since_hours=24)
        return {"module": "reflector", "status": "ok", "result": result}
    except Exception as e:
        return {"module": "reflector", "status": "error", "error": str(e)}


def run_identity_snapshot():
    """3. 身份快照：追踪IDENTITY.md变化"""
    try:
        from wangchuan._adapters.consciousness_adapter import get_identity_tracker as _get_identity_tracker
        IdentityTracker = type("_StubIdentityTracker", (object,), {})
        tracker = IdentityTracker(identity_path=IDENTITY_PATH, history_dir=f"{MEMORY_DIR}/identity_history")
        diff = tracker.diff_from_last()
        if diff:
            path = tracker.snapshot()
            return {"module": "identity_tracker", "status": "changed", "snapshot": path, "diff": diff[:200]}
        else:
            return {"module": "identity_tracker", "status": "unchanged"}
    except Exception as e:
        return {"module": "identity_tracker", "status": "error", "error": str(e)}


def run_consistency_check():
    """4. 叙事一致性检查"""
    try:
        from wangchuan.v3.consistency import ConsistencyChecker
        checker = ConsistencyChecker()
        return {"module": "consistency", "status": "ok", "result": "已加载，需对话触发"}
    except Exception as e:
        return {"module": "consistency", "status": "error", "error": str(e)}


def run_graph_maintenance():
    """5. 图数据库维护（PageRank + 社区检测）"""
    try:
        from wangchuan.v3.graph.maintenance import MaintenanceEngine
        from wangchuan.v3.graph.forget import ForgettingEngine
        
        results = {}
        
        # 遗忘衰减
        fe = ForgettingEngine(GRAPH_DB)
        forget_result = fe.decay_all()
        pruned = fe.prune_forgotten()
        results["forget"] = {"decay": forget_result, "pruned": pruned}
        
        # PageRank + 社区检测（每10次调用执行一次）
        me = MaintenanceEngine(GRAPH_DB)
        maint_result = me.run_maintenance()
        results["maintenance"] = maint_result
        
        return {"module": "graph", "status": "ok", "result": results}
    except Exception as e:
        return {"module": "graph", "status": "error", "error": str(e)}


def run_runtime_check():
    """6. 运行态巡检（兼容占位）"""
    return {"module": "runtime", "status": "ok", "result": {"mode": "standard", "energy_enabled": False}}


def run_curiosity():
    """7. 好奇心引擎：扫描信息源"""
    try:
        from wangchuan.curiosity_engine import CuriosityEngine
        ce = CuriosityEngine()
        result = ce.run()
        return {"module": "curiosity", "status": "ok", "result": str(result)[:200] if result else "无新发现"}
    except Exception as e:
        return {"module": "curiosity", "status": "error", "error": str(e)}


def main(argv: list[str] | None = None) -> int:
    """执行所有模块"""
    parser = argparse.ArgumentParser(description="Run WangChuan auto maintenance bundle")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO)
    logger.info("【WangChuan】[AutoRunAll] start ts=%s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    modules = [
        ("遗忘维护", run_auto_maintenance),
        ("反思引擎", run_reflector),
        ("身份快照", run_identity_snapshot),
        ("一致性检查", run_consistency_check),
        ("图数据库", run_graph_maintenance),
        ("运行态巡检", run_runtime_check),
        ("好奇心引擎", run_curiosity),
    ]

    results = {}
    has_error = False
    for name, func in modules:
        logger.info("【WangChuan】[AutoRunAll] module=%s status=running", name)
        result = func()
        results[name] = result
        status = result.get("status")
        if status == "ok":
            logger.info("【WangChuan】[AutoRunAll] module=%s status=ok result=%s", name, result.get("result", {}))
        elif status == "unchanged":
            logger.info("【WangChuan】[AutoRunAll] module=%s status=unchanged", name)
        elif status == "changed":
            logger.info("【WangChuan】[AutoRunAll] module=%s status=changed", name)
        else:
            has_error = True
            logger.info("【WangChuan】[AutoRunAll] module=%s status=error error=%s", name, result.get("error", "未知错误"))

    summary = {
        "ok": not has_error,
        "workspace_root": str(WORKSPACE_ROOT),
        "db_path": DB_PATH,
        "results": results,
    }

    logger.info("【WangChuan】[AutoRunAll] done ok=%s", summary["ok"])
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not has_error else 1


if __name__ == "__main__":
    raise SystemExit(main())
