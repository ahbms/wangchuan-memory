from __future__ import annotations

"""WangChuan diagnostics / health helpers.

低风险拆分目标：
- 抽离 diagnostics / status / health 读面辅助簇
- 保持 Memory 的公开方法签名不变
- 不触碰 remember / recall 主链实现
"""

import re
import time
import json
from pathlib import Path
from typing import Any, Dict

from wangchuan.migrations import MigrationManager

try:
    from wangchuan._adapters.runtime_adapter import get_energy_state as get_runtime_energy_state
except ImportError:
    # 终极 fallback - 无 L4 可用
    def get_runtime_energy_state():
        return {"enabled": False, "state_label": "noop", "state": "noop"}

try:
    from wangchuan.paths import workspace_root
except ImportError:
    from wangchuan.paths import workspace_root


def task_resume(memory_obj: Any, board_path: str | None = None) -> Dict[str, Any]:
    """从实施任务板提取结构化恢复面。"""
    board = Path(board_path) if board_path else workspace_root() / "docs" / "记忆系统实施任务板-v1.md"
    if not board.exists():
        return {
            "status": "missing",
            "board_path": str(board),
            "summary": f"任务恢复面缺失：未找到任务板 {board}",
            "current_task": "",
            "next_step": "",
            "resume_steps": [],
            "checkpoint_items": [],
        }

    text = board.read_text(encoding="utf-8")

    def _first_section(*headings: str) -> str:
        for heading in headings:
            body = memory_obj._extract_markdown_section(text, heading)
            if body:
                return body
        return ""

    checkpoint_body = _first_section("8. checkpoint", "7. checkpoint", "checkpoint")
    next_step_body = _first_section("9. next step", "8. next step", "next step")
    done_ledger_body = _first_section(
        "7. 最近完成记录（Done Ledger）",
        "6. 最近完成记录（Done Ledger）",
        "最近完成记录（Done Ledger）",
    )

    current_task = memory_obj._extract_label_value(checkpoint_body, "- 当前最高优先未完成项")
    next_step = memory_obj._extract_label_value(next_step_body, "### 唯一下一步")
    if not next_step:
        match = re.search(r"\*\*(.+?)\*\*", next_step_body or "", re.S)
        next_step = match.group(1).strip() if match else ""

    checkpoint_items = memory_obj._extract_bullet_items(checkpoint_body, "当前 checkpoint", limit=10)
    resume_steps = memory_obj._extract_bullet_items(checkpoint_body, "如果此刻中断，恢复时先做什么", limit=8)

    recent_done_heading = ""
    for line in (done_ledger_body or "").splitlines():
        match = re.match(r"^###\s+(.+)$", line.strip())
        if match:
            recent_done_heading = match.group(1).strip()
            break
    recent_done = memory_obj._extract_bullet_items(done_ledger_body, recent_done_heading, limit=6) if recent_done_heading else []

    status = "ready" if current_task and next_step and resume_steps else "partial"
    summary = (
        f"任务恢复：当前={current_task or 'unknown'} | 下一步={next_step or 'unknown'} | "
        f"恢复步骤={len(resume_steps)} | checkpoint={len(checkpoint_items)}"
    )
    return {
        "status": status,
        "board_path": str(board),
        "current_task": current_task,
        "next_step": next_step,
        "resume_steps": resume_steps,
        "checkpoint_items": checkpoint_items,
        "recent_done": recent_done,
        "summary": summary,
    }


def write_gate_probe(memory_obj: Any) -> Dict[str, Any]:
    """主动探测 write gate 是否能拦截典型噪音样本。"""
    noise_cases = [
        ("HTTP promote candidate", ["http_api_test", "demo"]),
        ("情感事件: [cron] Agent OS 全量回归与放行闭环", ["ops"]),
        ("pytest fixture for memory pipeline", ["pytest"]),
        ("[startup context loaded by runtime]", []),
        ("情感事件: hello, how are you?", []),
        ("情感事件: heartbeat poll at 10:00", []),
    ]
    safe_cases = [
        ("情感事件: 配置文件是/tmp/cliproxyapi/config.yaml", []),
        ("用户长期偏好透明黑盒执行：交代任务后直接执行，关键节点汇报，少确认。", ["preference"]),
    ]
    outcomes = []
    blocked = 0
    safe_allowed = 0
    for content, tags in noise_cases:
        meta = memory_obj._build_memory_metadata(content, tags)
        gate = memory_obj._evaluate_write_gate(content, tags, meta)
        if not gate.get("allowed"):
            blocked += 1
        outcomes.append(
            {
                "content": content,
                "tags": tags,
                "allowed": bool(gate.get("allowed")),
                "reason": gate.get("reason"),
                "is_test_data": meta.get("is_test_data"),
            }
        )
    for content, tags in safe_cases:
        meta = memory_obj._build_memory_metadata(content, tags)
        gate = memory_obj._evaluate_write_gate(content, tags, meta)
        if gate.get("allowed"):
            safe_allowed += 1
        outcomes.append(
            {
                "content": content,
                "tags": tags,
                "allowed": bool(gate.get("allowed")),
                "reason": gate.get("reason"),
                "is_test_data": meta.get("is_test_data"),
            }
        )
    return {
        "ok": blocked == len(noise_cases) and safe_allowed == len(safe_cases),
        "blocked": blocked,
        "total": len(noise_cases),
        "safe_allowed": safe_allowed,
        "safe_total": len(safe_cases),
        "outcomes": outcomes,
    }


def history_search_healthcheck(memory_obj: Any) -> Dict[str, Any]:
    """阶段 2.3 最小历史搜索索引健康摘要。"""
    try:
        from wangchuan.v3.pipeline_v3 import WangchuanPipeline
        status = WangchuanPipeline(memory_obj.db_path).history_search_index_status()
    except Exception as e:
        status = {
            "reader": "gm_nodes_fts",
            "available": False,
            "total_nodes": 0,
            "fts_rows": 0,
            "coverage_ratio": 0.0,
            "status": "error",
            "degraded": True,
            "error": str(e),
        }
    return status


def _is_runtime_test_like_session(session_id: Any) -> bool:
    text = str(session_id or "").strip().lower()
    if not text:
        return False
    if text == "primary_health_runtime_probe":
        return False
    noisy_tokens = [
        "test",
        "regression",
        "synthetic",
        "fixture",
        "benchmark",
        "smoke",
        "debug",
        "probe",
        "contract",
        "audit",
    ]
    return any(token in text for token in noisy_tokens)


def resonance_runtime_healthcheck(memory_obj: Any, session_id: str | None = None) -> Dict[str, Any]:
    """共振主链运行健康摘要。"""
    try:
        from wangchuan._adapters.context_adapter import get_session_state_store
        store = get_session_state_store()
    except Exception as e:
        return {
            "status": "error",
            "ok": False,
            "error": str(e),
            "mode": "unknown",
            "degrade_level": "unknown",
        }

    def latest_event(event_name: str, preferred_session_id: str | None = None) -> Dict[str, Any]:
        rows = []
        for path in sorted((store.base_dir).glob("*/metrics.jsonl")):
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            item = json.loads(line)
                        except Exception:
                            continue
                        if not isinstance(item, dict) or item.get("event") != event_name:
                            continue
                        rows.append(item)
            except Exception:
                continue

        if not rows:
            return {}

        def pick_latest(candidates: list[dict[str, Any]]) -> Dict[str, Any]:
            latest: Dict[str, Any] = {}
            for candidate in candidates:
                if str(candidate.get("timestamp") or "") >= str(latest.get("timestamp") or ""):
                    latest = candidate
            return latest

        if preferred_session_id:
            exact_rows = [item for item in rows if str(item.get("session_id") or "") == preferred_session_id]
            if exact_rows:
                return pick_latest(exact_rows)

        default_rows = [
            item for item in rows
            if str(item.get("session_id") or "") in {"default", "main"}
            and not _is_runtime_test_like_session(item.get("session_id"))
        ]
        if default_rows:
            return pick_latest(default_rows)

        health_probe_rows = [item for item in rows if str(item.get("session_id") or "") == "primary_health_runtime_probe"]
        if health_probe_rows:
            return pick_latest(health_probe_rows)

        non_test_rows = [item for item in rows if not _is_runtime_test_like_session(item.get("session_id"))]
        if non_test_rows:
            return pick_latest(non_test_rows)

        return pick_latest(rows)

    last_recall = latest_event("recall_context_metrics", preferred_session_id=session_id)
    last_cache = latest_event("semantic_cache_metrics", preferred_session_id=session_id)
    last_runtime = latest_event("session_runtime_metrics", preferred_session_id=session_id)
    last_health = latest_event("wangchuan_runtime_health", preferred_session_id=session_id)

    primary_role = str(last_health.get("primary_role") or last_recall.get("decision_primary_role") or "")
    scope_route = str(last_health.get("scope_route") or last_recall.get("scope_route") or "")
    decision_block_len = int(last_recall.get("decision_block_len") or 0)
    scope_block_len = int(last_recall.get("scope_context_block_len") or 0)
    semantic_cache_status = str(last_cache.get("status") or "")
    current_mode = str(last_health.get("current_mode") or "resonance_mainline")
    fallback_mode = str(last_health.get("fallback_mode") or "")
    last_success_ts = str(last_health.get("last_success_ts") or "")
    last_degrade_reason = str(last_health.get("last_degrade_reason") or "")
    recovered_from_stage = str(last_health.get("recovered_from_stage") or "")
    degrade_stage = str(last_health.get("degrade_stage") or "")
    success_rate = float(last_health.get("success_rate") or 0.0)
    p95 = float(last_health.get("p95") or 0.0)
    backlog = int(last_health.get("backlog") or 0)
    consecutive_failures = int(last_health.get("consecutive_failures") or 0)

    checks = {
        "decision_block_is_present": decision_block_len > 0,
        "scope_block_is_present": scope_block_len > 0,
        "primary_role_is_visible": bool(primary_role),
        "scope_route_is_visible": bool(scope_route),
        "cache_state_is_visible": bool(semantic_cache_status),
        "runtime_health_is_available": bool(last_health),
    }
    passed = sum(1 for ok in checks.values() if ok)
    total = len(checks)
    health_status = str(last_health.get("status") or "")
    if health_status in {"ok", "healthy"}:
        status = "healthy"
    elif health_status == "degraded":
        status = "degraded"
    else:
        degraded = not checks["decision_block_is_present"] or not checks["primary_role_is_visible"]
        status = "healthy" if passed == total and not degraded else ("degraded" if passed >= 3 else "risky")

    if current_mode == "resonance_mainline":
        degrade_level = "none"
    elif current_mode == "foundation_recall":
        degrade_level = "partial"
    elif current_mode == "no_memory":
        degrade_level = "high"
    else:
        degrade_level = "none" if status == "healthy" else ("partial" if status == "degraded" else "high")

    return {
        "status": status,
        "ok": status == "healthy",
        "mode": "resonance_mainline_v1",
        "current_mode": current_mode,
        "degrade_level": degrade_level,
        "last_success_ts": last_success_ts,
        "success_rate": round(success_rate, 4),
        "p95": round(p95, 2),
        "backlog": backlog,
        "last_degrade_reason": last_degrade_reason,
        "consecutive_failures": consecutive_failures,
        "recovered_from_stage": recovered_from_stage,
        "degrade_stage": degrade_stage,
        "fallback_mode": fallback_mode,
        "checks": checks,
        "last_recall": {
            "timestamp": last_recall.get("timestamp"),
            "scope_route": scope_route,
            "memory_route": last_health.get("memory_route") or last_recall.get("memory_route"),
            "decision_primary_role": primary_role,
            "decision_primary_kind": last_health.get("primary_kind") or last_recall.get("decision_primary_kind"),
            "decision_block_len": decision_block_len,
            "scope_context_block_len": scope_block_len,
            "history_support_items": last_recall.get("history_support_items", 0),
        },
        "cache": {
            "status": semantic_cache_status,
            "semantic_family": last_cache.get("semantic_family", ""),
            "state_fingerprint": last_cache.get("state_fingerprint", ""),
        },
        "runtime": {
            "session_key": last_runtime.get("session_key", ""),
            "status": last_runtime.get("status", ""),
            "reason": last_runtime.get("reason", ""),
        },
        "runtime_health": {
            "timestamp": last_health.get("timestamp", ""),
            "status": str(last_health.get("status") or ""),
            "current_mode": current_mode,
            "last_success_ts": last_success_ts,
            "success_rate": round(success_rate, 4),
            "p95": round(p95, 2),
            "backlog": backlog,
            "last_degrade_reason": last_degrade_reason,
            "consecutive_failures": consecutive_failures,
            "recovered_from_stage": recovered_from_stage,
            "degrade_stage": degrade_stage,
            "fallback_mode": fallback_mode,
            "fallback_chain": list(last_health.get("fallback_chain") or []),
        },
    }


def user_healthcheck(memory_obj: Any) -> Dict[str, Any]:
    """用户视角体检。"""
    raw_probe = memory_obj.recall_raw("原话", limit=5)
    rule_probe = memory_obj.recall_scars("规则 教训 默认", limit=5)
    mixed_probe = memory_obj.recall("用户 规则 原话", limit=8)
    write_gate_events = memory_obj._read_recent_write_gate_events(limit=160)
    write_gate_probe_payload = write_gate_probe(memory_obj)
    schema_index = memory_obj.memory_schema_index_status()
    migration_status = MigrationManager(memory_obj.db_path).status()
    structured_overview = memory_obj.structured_memory_overview()
    canonical_profile = memory_obj.user_canonical_profile()
    history_search = history_search_healthcheck(memory_obj)
    resonance_runtime = resonance_runtime_healthcheck(memory_obj)
    recall_runtime = dict(getattr(memory_obj, "_last_recall_runtime", {}) or {})
    recall_error = dict(getattr(memory_obj, "_last_recall_error", {}) or {})

    raw_with_anchor = [item for item in raw_probe if item.get("source_anchor") and item.get("evidence_level") == "raw"]
    rule_like = [item for item in rule_probe if item.get("memory_type") in {"rule", "lesson", "decision"}]
    noisy_recall = [item for item in mixed_probe if item.get("is_test_data")]
    explained_items = [item for item in mixed_probe if item.get("source_anchor") or item.get("turn_signature")]
    hot_candidates = [item for item in mixed_probe if item.get("hot_memory_candidate")]

    allowed_events = [event for event in write_gate_events if event.get("result") == "allowed"]
    blocked_events = [event for event in write_gate_events if event.get("result") == "blocked"]
    recent_noise_blocks = []
    for event in blocked_events:
        reason = str(event.get("reason") or "")
        preview = str(event.get("content_preview") or "")
        if any(token in (reason + " " + preview).lower() for token in ["test", "pytest", "unittest", "cron", "http_api_test", "live_verify"]):
            recent_noise_blocks.append(event)

    checks = {
        "raw_recall_returns_raw_evidence": {
            "ok": bool(raw_with_anchor),
            "detail": f"raw_probe={len(raw_probe)} anchored_raw={len(raw_with_anchor)}",
        },
        "rule_recall_returns_rule_like_items": {
            "ok": bool(rule_like),
            "detail": f"rule_probe={len(rule_probe)} rule_like={len(rule_like)}",
        },
        "test_noise_not_floating_in_recall": {
            "ok": len(noisy_recall) == 0,
            "detail": f"mixed_probe={len(mixed_probe)} noisy={len(noisy_recall)}",
        },
        "results_have_explainable_anchor": {
            "ok": bool(explained_items),
            "detail": f"mixed_probe={len(mixed_probe)} anchored_or_turn={len(explained_items)}",
        },
        "write_gate_is_blocking_noise": {
            "ok": bool(recent_noise_blocks) or bool(blocked_events) or bool(write_gate_probe_payload.get("ok")),
            "detail": (
                f"allowed={len(allowed_events)} blocked={len(blocked_events)} noise_blocked={len(recent_noise_blocks)} "
                f"probe={write_gate_probe_payload.get('blocked', 0)}/{write_gate_probe_payload.get('total', 0)}"
            ),
        },
        "structured_index_is_not_legacy_heavy": {
            "ok": schema_index.get("derived_backfill", 0) == 0,
            "detail": (
                f"sidecar_backed={schema_index.get('sidecar_backed', 0)} "
                f"derived_backfill={schema_index.get('derived_backfill', 0)}"
            ),
        },
        "schema_version_is_visible": {
            "ok": bool(migration_status.get("current_version")),
            "detail": (
                f"current={migration_status.get('current_version') or 'missing'} "
                f"meta={migration_status.get('meta_schema_version') or 'missing'}"
            ),
        },
        "schema_version_matches_meta": {
            "ok": migration_status.get("version_matches_meta") is True,
            "detail": (
                f"current={migration_status.get('current_version') or 'missing'} "
                f"meta={migration_status.get('meta_schema_version') or 'missing'}"
            ),
        },
        "hot_memory_signal_present": {
            "ok": bool(hot_candidates) or structured_overview.get("hot_candidates", 0) > 0,
            "detail": (
                f"mixed_probe={len(mixed_probe)} hot_candidates={len(hot_candidates)} "
                f"structured_hot={structured_overview.get('hot_candidates', 0)}"
            ),
        },
        "structured_index_is_available": {
            "ok": schema_index.get("total", 0) > 0 and structured_overview.get("reader") == "memory_schema_index",
            "detail": (
                f"schema_total={schema_index.get('total', 0)} "
                f"sidecar_backed={schema_index.get('sidecar_backed', 0)} "
                f"derived_backfill={schema_index.get('derived_backfill', 0)} "
                f"reader={structured_overview.get('reader', 'none')}"
            ),
        },
        "structured_index_has_high_quality_signal": {
            "ok": structured_overview.get("high_quality", 0) > 0,
            "detail": (
                f"high_quality={structured_overview.get('high_quality', 0)} "
                f"by_type={len(structured_overview.get('by_memory_type', {}))}"
            ),
        },
        "user_canonical_profile_is_available": {
            "ok": canonical_profile.get("filled_slots", 0) >= 6,
            "detail": (
                f"filled={canonical_profile.get('filled_slots', 0)}/"
                f"{canonical_profile.get('total_slots', 0)} "
                f"missing={len(canonical_profile.get('missing_slots', []))}"
            ),
        },
        "user_canonical_profile_is_stable": {
            "ok": canonical_profile.get("status") == "stable",
            "detail": (
                f"status={canonical_profile.get('status', 'unknown')} "
                f"stable={canonical_profile.get('status_counts', {}).get('stable', 0)} "
                f"contended={canonical_profile.get('status_counts', {}).get('contended', 0)} "
                f"review={canonical_profile.get('status_counts', {}).get('needs_review', 0)}"
            ),
        },
        "user_canonical_profile_has_repair_path": {
            "ok": canonical_profile.get("status") == "stable" or len(canonical_profile.get("repair_suggestions", [])) > 0,
            "detail": (
                f"status={canonical_profile.get('status', 'unknown')} "
                f"repairs={len(canonical_profile.get('repair_suggestions', []))}"
            ),
        },
        "history_search_index_is_available": {
            "ok": history_search.get("available", False) or history_search.get("status") == "empty",
            "detail": (
                f"reader={history_search.get('reader', 'unknown')} "
                f"status={history_search.get('status', 'unknown')} "
                f"nodes={history_search.get('total_nodes', 0)} "
                f"fts={history_search.get('fts_rows', 0)} "
                f"coverage={history_search.get('coverage_ratio', 0.0)}"
            ),
        },
        "resonance_mainline_runtime_is_visible": {
            "ok": resonance_runtime.get("status") in {"healthy", "degraded"},
            "detail": (
                f"status={resonance_runtime.get('status', 'unknown')} "
                f"mode={resonance_runtime.get('mode', 'unknown')} "
                f"primary_role={resonance_runtime.get('last_recall', {}).get('decision_primary_role', '')}"
            ),
        },
        "recall_runtime_is_observable": {
            "ok": recall_runtime.get("status") in {"idle", "ok", "error"},
            "detail": (
                f"status={recall_runtime.get('status', 'unknown')} "
                f"reader={recall_runtime.get('reader', '')} "
                f"error_type={recall_error.get('error_type', '')}"
            ),
        },
    }

    passed = sum(1 for item in checks.values() if item["ok"])
    total = len(checks)

    core_check_names = [
        "raw_recall_returns_raw_evidence",
        "rule_recall_returns_rule_like_items",
        "test_noise_not_floating_in_recall",
        "results_have_explainable_anchor",
        "write_gate_is_blocking_noise",
        "structured_index_is_available",
        "structured_index_has_high_quality_signal",
        "history_search_index_is_available",
        "recall_runtime_is_observable",
    ]
    advisory_check_names = [
        "user_canonical_profile_is_available",
        "user_canonical_profile_is_stable",
        "user_canonical_profile_has_repair_path",
        "resonance_mainline_runtime_is_visible",
    ]
    core_passed = sum(1 for name in core_check_names if checks.get(name, {}).get("ok"))
    advisory_failed = sum(1 for name in advisory_check_names if not checks.get(name, {}).get("ok"))

    if core_passed == len(core_check_names):
        status = "healthy"
    elif core_passed >= max(6, len(core_check_names) - 1):
        status = "degraded"
    else:
        status = "risky"

    if status == "healthy" and advisory_failed >= len(advisory_check_names):
        status = "healthy"

    summary = (
        f"记忆体检：{passed}/{total} 项通过 | 状态={status} | "
        f"raw锚点={len(raw_with_anchor)} | 规则命中={len(rule_like)} | 噪声上浮={len(noisy_recall)} | "
        f"可解释结果={len(explained_items)} | gate拦截={len(blocked_events)} | "
        f"schema={schema_index.get('total', 0)} | structured_high={structured_overview.get('high_quality', 0)} | "
        f"history_fts={history_search.get('status', 'unknown')}"
    )

    return {
        "status": status,
        "passed": passed,
        "total": total,
        "summary": summary,
        "checks": checks,
        "probes": {
            "raw_probe": raw_probe,
            "rule_probe": rule_probe,
            "mixed_probe": mixed_probe,
        },
        "write_gate": {
            "allowed": len(allowed_events),
            "blocked": len(blocked_events),
            "noise_blocked": len(recent_noise_blocks),
            "probe": write_gate_probe_payload,
        },
        "migration_status": migration_status,
        "memory_schema_index": schema_index,
        "structured_memory": structured_overview,
        "user_canonical_profile": canonical_profile,
        "history_search_index": history_search,
        "resonance_runtime": resonance_runtime,
        "recall_runtime": recall_runtime,
        "recall_error": recall_error,
    }


def status(memory_obj: Any) -> Dict[str, Any]:
    """查看记忆系统当前状态。"""
    now = time.time()
    if memory_obj._status_cache["data"] and (now - memory_obj._status_cache["timestamp"]) < memory_obj._status_cache_ttl:
        return memory_obj._status_cache["data"]

    energy_report = get_runtime_energy_state()
    temporal_report = memory_obj.temporal.get_report()

    try:
        conn = memory_obj._conn()
        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        conn.close()
    except Exception:
        total = 0

    try:
        vector_stats = memory_obj._get_local_vector().get_stats()
    except Exception:
        vector_stats = {"indexed_memories": 0, "vocab_size": 0}

    health = user_healthcheck(memory_obj)
    task_resume_payload = task_resume(memory_obj)
    schema_index = memory_obj.memory_schema_index_status()
    migration_status = health.get("migration_status") or MigrationManager(memory_obj.db_path).status()
    structured_overview = memory_obj.structured_memory_overview()
    canonical_profile = memory_obj.user_canonical_profile()
    resonance_runtime = resonance_runtime_healthcheck(memory_obj)
    recall_runtime = dict(getattr(memory_obj, "_last_recall_runtime", {}) or {})
    foundation = {
        "status": health.get("status", "unknown"),
        "passed": health.get("passed", 0),
        "total": health.get("total", 0),
        "db_path": str(getattr(memory_obj, "db_path", "") or ""),
        "schema_version": migration_status.get("current_version") or "unknown",
        "schema_meta_version": migration_status.get("meta_schema_version") or "unknown",
        "schema_pending": migration_status.get("pending_count", 0),
        "schema_version_matches_meta": migration_status.get("version_matches_meta") is True,
        "schema_total": schema_index.get("total", 0),
        "structured_reader": structured_overview.get("reader", "?"),
        "high_quality": structured_overview.get("high_quality", 0),
        "hot_candidates": structured_overview.get("hot_candidates", 0),
        "canonical_filled": canonical_profile.get("filled_slots", 0),
        "canonical_total": canonical_profile.get("total_slots", 0),
        "recall_status": recall_runtime.get("status", "idle"),
        "recall_reader": recall_runtime.get("reader", "?") or "?",
        "summary": (
            f"foundation={health.get('status', 'unknown')} "
            f"{health.get('passed', 0)}/{health.get('total', 0)} | "
            f"schema_version={migration_status.get('current_version') or 'unknown'} "
            f"pending={migration_status.get('pending_count', 0)} | "
            f"schema={schema_index.get('total', 0)} | "
            f"structured={structured_overview.get('reader', '?')} "
            f"hq={structured_overview.get('high_quality', 0)} hot={structured_overview.get('hot_candidates', 0)} | "
            f"canonical={canonical_profile.get('filled_slots', 0)}/{canonical_profile.get('total_slots', 0)} | "
            f"recall={recall_runtime.get('status', 'idle')} via={recall_runtime.get('reader', '?') or '?'}"
        ),
    }
    resonance = {
        "status": resonance_runtime.get("status", "unknown"),
        "mode": resonance_runtime.get("mode", "unknown"),
        "degrade_level": resonance_runtime.get("degrade_level", "unknown"),
        "primary_role": resonance_runtime.get("last_recall", {}).get("decision_primary_role", "") or "?",
        "primary_kind": resonance_runtime.get("last_recall", {}).get("decision_primary_kind", "") or "?",
        "scope_route": resonance_runtime.get("last_recall", {}).get("scope_route", "") or "?",
        "memory_route": resonance_runtime.get("last_recall", {}).get("memory_route", "") or "?",
        "history_support_items": resonance_runtime.get("last_recall", {}).get("history_support_items", 0),
        "summary": (
            f"resonance={resonance_runtime.get('status', 'unknown')} "
            f"mode={resonance_runtime.get('mode', 'unknown')} "
            f"role={resonance_runtime.get('last_recall', {}).get('decision_primary_role', '') or '?'} "
            f"kind={resonance_runtime.get('last_recall', {}).get('decision_primary_kind', '') or '?'} "
            f"route={resonance_runtime.get('last_recall', {}).get('memory_route', '') or '?'}"
        ),
    }
    runtime_label = "standard"
    if isinstance(energy_report, dict) and energy_report.get("enabled") is False:
        runtime_label = "standard"
    elif isinstance(energy_report, dict):
        runtime_label = str(energy_report.get("state_label") or energy_report.get("state") or "standard")

    message = (
        f"🧭 runtime={runtime_label} | "
        f"⏱️ {temporal_report.get('phase_label', '?')} | "
        f"🧠 {total} 条记忆 | "
        f"📐 本地向量 {vector_stats.get('indexed_memories', 0)} | "
        f"🧱 {foundation['status']} {foundation['passed']}/{foundation['total']} | "
        f"🧬 schema={foundation['schema_version']} pending={foundation['schema_pending']} | "
        f"🎛️ {resonance['status']} role={resonance['primary_role']} | "
        f"🔎 recall={foundation['recall_status']} via={foundation['recall_reader']} | "
        f"🗂️ schema={schema_index.get('total', 0)} | "
        f"📚 structured={structured_overview.get('reader', '?')} "
        f"hq={structured_overview.get('high_quality', 0)} hot={structured_overview.get('hot_candidates', 0)} | "
        f"👤 canonical={canonical_profile.get('filled_slots', 0)}/{canonical_profile.get('total_slots', 0)} | "
        f"🧭 {task_resume_payload.get('current_task') or '?'}"
    )

    result = {
        "energy": energy_report,
        "temporal": temporal_report,
        "memories": total,
        "vector_index": vector_stats,
        "healthcheck": health,
        "task_resume": task_resume_payload,
        "migration_status": migration_status,
        "memory_schema_index": schema_index,
        "structured_memory": structured_overview,
        "user_canonical_profile": canonical_profile,
        "foundation": foundation,
        "resonance": resonance,
        "resonance_runtime": resonance_runtime,
        "recall_runtime": recall_runtime,
        "recall_error": dict(getattr(memory_obj, "_last_recall_error", {}) or {}),
        "message": message,
    }

    memory_obj._status_cache = {"data": result, "timestamp": time.time()}
    return result
