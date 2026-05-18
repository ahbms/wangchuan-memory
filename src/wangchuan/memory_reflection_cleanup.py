from __future__ import annotations

"""WangChuan cleanup helpers.

这一层承接 memory_api 的低风险维护簇：
- generic recall noise cleanup
- duplicate keeper 选择
- duplicate reflection cleanup
- duplicate rule cleanup
- historical noise cleanup
- low-value emotional cleanup

约束：
- 不直接定义新的存储真值规则
- 仍由调用方（Memory）提供 DB / schema / sidecar 更新能力
- 优先保持与 memory_api 现有返回结构一致，避免破坏外部调用面
"""

import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

try:
    from wangchuan.memory_rules import (
        classify_historical_noise_memory,
        classify_low_value_emotional_memory,
        classify_questionish_rule,
        looks_like_questionish_rule_noise,
    )
except ImportError:
    from wangchuan.memory_rules import (
        classify_historical_noise_memory,
        classify_low_value_emotional_memory,
        classify_questionish_rule,
        looks_like_questionish_rule_noise,
    )

try:
    from wangchuan.paths import state_root
except ImportError:
    from wangchuan.paths import state_root


def pick_duplicate_memory_keeper(memory_obj: Any, rows: List[sqlite3.Row]) -> sqlite3.Row | None:
    if not rows:
        return None
    return max(rows, key=memory_obj._duplicate_memory_sort_key)


def _delete_memory_ids(memory_obj: Any, memory_ids: List[int], *, conn: sqlite3.Connection | None = None) -> int:
    if not memory_ids:
        return 0

    own_conn = conn is None
    active_conn = conn or memory_obj._conn()
    deleted = 0
    try:
        placeholders = ",".join("?" for _ in memory_ids)
        cursor = active_conn.execute(f"DELETE FROM memories WHERE id IN ({placeholders})", memory_ids)
        deleted = int(cursor.rowcount or 0)
        try:
            active_conn.execute(f"DELETE FROM fts_memories WHERE rowid IN ({placeholders})", memory_ids)
        except Exception:
            pass
        active_conn.commit()
    finally:
        if own_conn:
            try:
                active_conn.close()
            except Exception:
                pass

    memory_obj._batch_mark_memory_schema_removed(memory_ids)
    try:
        memory_obj._status_cache = {"data": None, "timestamp": 0}
    except Exception:
        pass
    return deleted


def cleanup_noise(memory_obj: Any, dry_run: bool = True, keep_emotions: bool = True) -> Dict[str, Any]:
    """清理 recall 噪声记忆，保持与 memory_api 既有返回结构一致。"""
    conn = None
    try:
        conn = memory_obj._conn()

        patterns = memory_obj.RECALL_NOISE_PATTERNS
        clean_patterns = [p.replace("\\b", "").replace("\\", "") for p in patterns[:5]]
        if not clean_patterns:
            return {"deleted": 0, "would_delete": 0, "samples": []}

        conditions = " OR ".join(["content LIKE ?" for _ in clean_patterns])
        params: List[str] = [f"%{pattern}%" for pattern in clean_patterns]

        if keep_emotions:
            conditions = f"({conditions}) AND content NOT LIKE ?"
            params.append("%情感事件:%")

        rows = conn.execute(
            f"SELECT id, content FROM memories WHERE {conditions} LIMIT 100",
            tuple(params),
        ).fetchall()

        would_delete = len(rows)
        samples = [str(row[1] or "")[:60] for row in rows[:5]]

        deleted = 0
        if not dry_run and would_delete > 0:
            target_ids = [int(row[0]) for row in rows]
            deleted = _delete_memory_ids(memory_obj, target_ids, conn=conn)

        return {
            "deleted": deleted,
            "would_delete": would_delete,
            "samples": samples,
            "dry_run": dry_run,
            "keep_emotions": keep_emotions,
        }
    except Exception as e:
        return {"error": str(e), "deleted": 0, "would_delete": 0}
    finally:
        try:
            conn.close()
        except Exception:
            pass


def cleanup_duplicate_reflections(memory_obj: Any, dry_run: bool = True) -> Dict[str, Any]:
    """清理 exact duplicate 的 rule/correction reflection_event 记忆，只保留一条最佳记录。"""
    memory_obj._ensure_memory_schema_index_table()
    log_dir = state_root() / "duplicate_reflection_cleanup"
    log_dir.mkdir(parents=True, exist_ok=True)

    conn = memory_obj._conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.created_at,
                   COALESCE(msi.memory_type, m.type, 'unknown') AS memory_type,
                   COALESCE(msi.source_anchor, '') AS source_anchor,
                   COALESCE(msi.source_session, '') AS source_session,
                   COALESCE(msi.turn_signature, '') AS turn_signature,
                   COALESCE(msi.promotion_reason, '') AS promotion_reason,
                   COALESCE(msi.quality_score, 0) AS quality_score,
                   COALESCE(msi.hotness_score, 0) AS hotness_score,
                   COALESCE(msi.last_confirmed_at, '') AS last_confirmed_at
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            WHERE COALESCE(msi.removed_at, '') = ''
              AND COALESCE(msi.promotion_reason, '') = 'reflection_event'
              AND COALESCE(msi.memory_type, m.type, 'unknown') IN ('rule', 'correction')
            ORDER BY m.content ASC, datetime(m.created_at) ASC, m.id ASC
            """
        ).fetchall()

        groups: Dict[tuple[str, str], List[sqlite3.Row]] = {}
        for row in rows:
            key = (str(row["memory_type"] or ""), str(row["content"] or ""))
            groups.setdefault(key, []).append(row)

        duplicate_groups = []
        remove_ids: List[int] = []
        by_type: Dict[str, int] = {}

        for (memory_type, content), group_rows in groups.items():
            if len(group_rows) <= 1:
                continue
            keeper = pick_duplicate_memory_keeper(memory_obj, group_rows)
            if keeper is None:
                continue
            dropped = [int(r["id"]) for r in group_rows if int(r["id"]) != int(keeper["id"])]
            if not dropped:
                continue
            remove_ids.extend(dropped)
            by_type[memory_type] = by_type.get(memory_type, 0) + len(dropped)
            duplicate_groups.append({
                "memory_type": memory_type,
                "content": content,
                "count": len(group_rows),
                "keep": {
                    "id": int(keeper["id"]),
                    "created_at": str(keeper["created_at"] or ""),
                    "source_session": str(keeper["source_session"] or ""),
                    "source_anchor": str(keeper["source_anchor"] or ""),
                    "turn_signature": str(keeper["turn_signature"] or ""),
                },
                "drop_ids": dropped,
            })

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": bool(dry_run),
            "matched_groups": len(duplicate_groups),
            "matched_rows": len(remove_ids),
            "by_type": by_type,
            "sample": duplicate_groups[:20],
        }
        report_path = log_dir / (
            f"cleanup_{datetime.now().strftime('%Y%m%d%H%M%S')}_{'dryrun' if dry_run else 'apply'}.json"
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        if dry_run or not remove_ids:
            return {
                "success": True,
                "dry_run": bool(dry_run),
                "matched_groups": len(duplicate_groups),
                "matched_rows": len(remove_ids),
                "removed": 0,
                "by_type": by_type,
                "report_path": str(report_path),
                "sample": duplicate_groups[:10],
            }

        deleted = _delete_memory_ids(memory_obj, remove_ids, conn=conn)
        conn = None

        return {
            "success": True,
            "dry_run": False,
            "matched_groups": len(duplicate_groups),
            "matched_rows": len(remove_ids),
            "removed": deleted,
            "by_type": by_type,
            "report_path": str(report_path),
            "sample": duplicate_groups[:10],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def cleanup_duplicate_rule_memories(memory_obj: Any, dry_run: bool = True) -> Dict[str, Any]:
    """清理 exact duplicate 的 rule 记忆，不限 promotion_reason，只保留一条最佳记录。"""
    memory_obj._ensure_memory_schema_index_table()
    log_dir = state_root() / "duplicate_rule_cleanup"
    log_dir.mkdir(parents=True, exist_ok=True)

    conn = memory_obj._conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.created_at,
                   COALESCE(msi.memory_type, m.type, 'unknown') AS memory_type,
                   COALESCE(msi.source_layer, '') AS source_layer,
                   COALESCE(msi.source_anchor, '') AS source_anchor,
                   COALESCE(msi.source_session, '') AS source_session,
                   COALESCE(msi.turn_signature, '') AS turn_signature,
                   COALESCE(msi.promotion_reason, '') AS promotion_reason,
                   COALESCE(msi.quality_score, 0) AS quality_score,
                   COALESCE(msi.hotness_score, 0) AS hotness_score,
                   COALESCE(msi.last_confirmed_at, '') AS last_confirmed_at
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            WHERE COALESCE(msi.removed_at, '') = ''
              AND COALESCE(msi.memory_type, m.type, 'unknown') = 'rule'
            ORDER BY m.content ASC, datetime(m.created_at) ASC, m.id ASC
            """
        ).fetchall()

        groups: Dict[str, List[sqlite3.Row]] = {}
        for row in rows:
            key = str(row["content"] or "")
            groups.setdefault(key, []).append(row)

        duplicate_groups = []
        remove_ids: List[int] = []

        for content, group_rows in groups.items():
            if len(group_rows) <= 1:
                continue
            keeper = pick_duplicate_memory_keeper(memory_obj, group_rows)
            if keeper is None:
                continue
            dropped = [int(r["id"]) for r in group_rows if int(r["id"]) != int(keeper["id"])]
            if not dropped:
                continue
            remove_ids.extend(dropped)
            duplicate_groups.append({
                "memory_type": "rule",
                "content": content,
                "count": len(group_rows),
                "keep": {
                    "id": int(keeper["id"]),
                    "created_at": str(keeper["created_at"] or ""),
                    "source_layer": str(keeper["source_layer"] or ""),
                    "source_session": str(keeper["source_session"] or ""),
                    "source_anchor": str(keeper["source_anchor"] or ""),
                    "turn_signature": str(keeper["turn_signature"] or ""),
                    "promotion_reason": str(keeper["promotion_reason"] or ""),
                },
                "drop_ids": dropped,
            })

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": bool(dry_run),
            "matched_groups": len(duplicate_groups),
            "matched_rows": len(remove_ids),
            "by_type": {"rule": len(remove_ids)},
            "sample": duplicate_groups[:20],
        }
        report_path = log_dir / (
            f"cleanup_{datetime.now().strftime('%Y%m%d%H%M%S')}_{'dryrun' if dry_run else 'apply'}.json"
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        if dry_run or not remove_ids:
            return {
                "success": True,
                "dry_run": bool(dry_run),
                "matched_groups": len(duplicate_groups),
                "matched_rows": len(remove_ids),
                "removed": 0,
                "by_type": {"rule": len(remove_ids)},
                "report_path": str(report_path),
                "sample": duplicate_groups[:10],
            }

        deleted = _delete_memory_ids(memory_obj, remove_ids, conn=conn)
        conn = None

        return {
            "success": True,
            "dry_run": False,
            "matched_groups": len(duplicate_groups),
            "matched_rows": len(remove_ids),
            "removed": deleted,
            "by_type": {"rule": len(remove_ids)},
            "report_path": str(report_path),
            "sample": duplicate_groups[:10],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def cleanup_historical_noise(memory_obj: Any, dry_run: bool = True) -> Dict[str, Any]:
    """清理已知历史脏记忆，只针对忘川历史噪音样本。"""
    memory_obj._ensure_memory_schema_index_table()
    log_dir = state_root() / "historical_noise_cleanup"
    log_dir.mkdir(parents=True, exist_ok=True)
    conn = memory_obj._conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.created_at,
                   COALESCE(msi.source_session, '') AS source_session,
                   COALESCE(msi.promotion_reason, '') AS promotion_reason
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            ORDER BY m.id ASC
            """
        ).fetchall()

        targets = []
        by_reason: Dict[str, int] = {}
        for row in rows:
            content = str(row["content"] or "")
            reason = classify_historical_noise_memory(content)
            if not reason:
                continue
            item = {
                "id": int(row["id"]),
                "content": content,
                "created_at": str(row["created_at"] or ""),
                "source_session": str(row["source_session"] or ""),
                "promotion_reason": str(row["promotion_reason"] or ""),
                "reason": reason,
            }
            targets.append(item)
            by_reason[reason] = by_reason.get(reason, 0) + 1

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": bool(dry_run),
            "matched": len(targets),
            "by_reason": by_reason,
            "sample": targets[:20],
        }
        report_path = log_dir / (
            f"cleanup_{datetime.now().strftime('%Y%m%d%H%M%S')}_{'dryrun' if dry_run else 'apply'}.json"
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")

        if dry_run or not targets:
            return {
                "success": True,
                "dry_run": bool(dry_run),
                "matched": len(targets),
                "removed": 0,
                "by_reason": by_reason,
                "report_path": str(report_path),
                "sample": targets[:10],
            }

        target_ids = [item["id"] for item in targets]
        deleted = _delete_memory_ids(memory_obj, target_ids, conn=conn)
        conn = None

        return {
            "success": True,
            "dry_run": False,
            "matched": len(targets),
            "removed": deleted,
            "by_reason": by_reason,
            "report_path": str(report_path),
            "sample": targets[:10],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def cleanup_question_like_rule_noise(memory_obj: Any, dry_run: bool = True) -> Dict[str, Any]:
    """清理低置信、低锚点的问句型假 rule，只处理最保守候选。"""
    memory_obj._ensure_memory_schema_index_table()
    log_dir = state_root() / "question_like_rule_cleanup"
    log_dir.mkdir(parents=True, exist_ok=True)

    conn = memory_obj._conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.created_at,
                   COALESCE(msi.memory_type, m.type, 'unknown') AS memory_type,
                   COALESCE(msi.source_layer, '') AS source_layer,
                   COALESCE(msi.source_anchor, '') AS source_anchor,
                   COALESCE(msi.source_session, '') AS source_session,
                   COALESCE(msi.turn_signature, '') AS turn_signature,
                   COALESCE(msi.promotion_reason, '') AS promotion_reason,
                   COALESCE(msi.user_explicit, 0) AS user_explicit,
                   COALESCE(msi.promotion_state, '') AS promotion_state,
                   COALESCE(msi.subject_domain, '') AS subject_domain,
                   COALESCE(msi.quality_score, m.confidence, 0) AS quality_score,
                   COALESCE(msi.hotness_score, 0) AS hotness_score
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            WHERE COALESCE(msi.removed_at, '') = ''
              AND COALESCE(msi.memory_type, m.type, 'unknown') = 'rule'
              AND COALESCE(msi.source_layer, '') = 'scar'
            ORDER BY m.id ASC
            """
        ).fetchall()

        targets = []
        for row in rows:
            content = str(row["content"] or "")
            if not looks_like_questionish_rule_noise(content):
                continue

            source_anchor = str(row["source_anchor"] or "").strip()
            source_session = str(row["source_session"] or "").strip()
            turn_signature = str(row["turn_signature"] or "").strip()
            promotion_reason = str(row["promotion_reason"] or "").strip().lower()
            promotion_state = str(row["promotion_state"] or "").strip().lower()
            subject_domain = str(row["subject_domain"] or "").strip().lower()
            user_explicit = bool(row["user_explicit"])
            try:
                quality_score = float(row["quality_score"] or 0.0)
            except Exception:
                quality_score = 0.0
            try:
                hotness_score = float(row["hotness_score"] or 0.0)
            except Exception:
                hotness_score = 0.0

            has_trace = bool(source_anchor or source_session or turn_signature)
            is_canonical = promotion_state in {"accepted", "promoted", "recalled"}
            is_reflection_event = promotion_reason == "reflection_event"
            is_user_profile_rule = subject_domain == "user" or "用户" in content

            allow_remove = (
                not user_explicit
                and not has_trace
                and not is_canonical
                and not is_user_profile_rule
                and (not is_reflection_event or quality_score <= 0.72)
                and hotness_score <= 0.31
            )

            if not allow_remove:
                continue

            targets.append({
                "id": int(row["id"]),
                "content": content,
                "created_at": str(row["created_at"] or ""),
                "promotion_reason": promotion_reason,
                "promotion_state": promotion_state,
                "source_anchor": source_anchor,
                "source_session": source_session,
                "turn_signature": turn_signature,
                "user_explicit": user_explicit,
                "quality_score": quality_score,
                "hotness_score": hotness_score,
                "subject_domain": subject_domain,
                "reason": "question_like_rule_noise",
            })

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": bool(dry_run),
            "matched": len(targets),
            "by_reason": {"question_like_rule_noise": len(targets)} if targets else {},
            "sample": targets[:30],
        }
        report_path = log_dir / (
            f"cleanup_{datetime.now().strftime('%Y%m%d%H%M%S')}_{'dryrun' if dry_run else 'apply'}.json"
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        if dry_run or not targets:
            return {
                "success": True,
                "dry_run": bool(dry_run),
                "matched": len(targets),
                "removed": 0,
                "by_reason": report["by_reason"],
                "report_path": str(report_path),
                "sample": targets[:10],
            }

        target_ids = [item["id"] for item in targets]
        deleted = _delete_memory_ids(memory_obj, target_ids, conn=conn)
        conn = None

        return {
            "success": True,
            "dry_run": False,
            "matched": len(targets),
            "removed": deleted,
            "by_reason": report["by_reason"],
            "report_path": str(report_path),
            "sample": targets[:10],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def audit_question_like_rules(memory_obj: Any, limit: int = 300) -> Dict[str, Any]:
    """输出 question-like rule 审计报表，区分噪音、保留和救援类型。"""
    memory_obj._ensure_memory_schema_index_table()
    log_dir = state_root() / "question_like_rule_audit"
    log_dir.mkdir(parents=True, exist_ok=True)

    conn = memory_obj._conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.created_at,
                   COALESCE(msi.memory_type, m.type, 'unknown') AS memory_type,
                   COALESCE(msi.source_layer, '') AS source_layer,
                   COALESCE(msi.source_anchor, '') AS source_anchor,
                   COALESCE(msi.source_session, '') AS source_session,
                   COALESCE(msi.turn_signature, '') AS turn_signature,
                   COALESCE(msi.promotion_reason, '') AS promotion_reason,
                   COALESCE(msi.user_explicit, 0) AS user_explicit,
                   COALESCE(msi.promotion_state, '') AS promotion_state,
                   COALESCE(msi.subject_domain, '') AS subject_domain,
                   COALESCE(msi.quality_score, m.confidence, 0) AS quality_score,
                   COALESCE(msi.hotness_score, 0) AS hotness_score
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            WHERE COALESCE(msi.removed_at, '') = ''
              AND COALESCE(msi.memory_type, m.type, 'unknown') = 'rule'
              AND COALESCE(msi.source_layer, '') = 'scar'
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (int(limit),)
        ).fetchall()

        items = []
        by_kind: Dict[str, int] = {}
        for row in rows:
            content = str(row["content"] or "")
            classification = classify_questionish_rule(content)
            if not classification.get("is_rule_event"):
                continue

            kind = str(classification.get("kind") or "not_question_like")
            source_anchor = str(row["source_anchor"] or "").strip()
            source_session = str(row["source_session"] or "").strip()
            turn_signature = str(row["turn_signature"] or "").strip()
            promotion_reason = str(row["promotion_reason"] or "").strip().lower()
            promotion_state = str(row["promotion_state"] or "").strip().lower()
            subject_domain = str(row["subject_domain"] or "").strip().lower()
            user_explicit = bool(row["user_explicit"])
            try:
                quality_score = float(row["quality_score"] or 0.0)
            except Exception:
                quality_score = 0.0
            try:
                hotness_score = float(row["hotness_score"] or 0.0)
            except Exception:
                hotness_score = 0.0

            has_trace = bool(source_anchor or source_session or turn_signature)
            is_canonical = promotion_state in {"accepted", "promoted", "recalled"}
            is_user_profile_rule = subject_domain == "user" or "用户" in content
            cleanup_candidate = (
                kind == "question_like_noise"
                and not user_explicit
                and not has_trace
                and not is_canonical
                and not is_user_profile_rule
                and hotness_score <= 0.31
                and (promotion_reason != "reflection_event" or quality_score <= 0.72)
            )

            item = {
                "id": int(row["id"]),
                "content": content,
                "created_at": str(row["created_at"] or ""),
                "kind": kind,
                "question_hint_hits": classification.get("question_hint_hits") or [],
                "stable_prefix": classification.get("stable_prefix") or "",
                "rescue_pattern": classification.get("rescue_pattern") or "",
                "promotion_reason": promotion_reason,
                "promotion_state": promotion_state,
                "subject_domain": subject_domain,
                "user_explicit": user_explicit,
                "has_trace": has_trace,
                "source_anchor": source_anchor,
                "source_session": source_session,
                "turn_signature": turn_signature,
                "quality_score": quality_score,
                "hotness_score": hotness_score,
                "cleanup_candidate": cleanup_candidate,
            }
            items.append(item)
            by_kind[kind] = by_kind.get(kind, 0) + 1

        cleanup_candidates = [item for item in items if item.get("cleanup_candidate")]
        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "scanned": len(rows),
            "audited": len(items),
            "by_kind": by_kind,
            "cleanup_candidates": len(cleanup_candidates),
            "summary": _build_question_like_rule_audit_summary(len(rows), len(items), by_kind, len(cleanup_candidates)),
            "sample": items[:40],
        }
        report_path = log_dir / f"audit_{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "success": True,
            "scanned": len(rows),
            "audited": len(items),
            "by_kind": by_kind,
            "cleanup_candidates": len(cleanup_candidates),
            "summary": report["summary"],
            "report_path": str(report_path),
            "sample": items[:12],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _build_question_like_rule_audit_summary(scanned: int, audited: int, by_kind: Dict[str, int], cleanup_candidates: int) -> str:
    noise = int(by_kind.get("question_like_noise", 0))
    rescued = int(by_kind.get("rescued_instruction_tail", 0))
    stable = int(by_kind.get("stable_prefix_keep", 0))
    explicit = int(by_kind.get("explicit_requirement_keep", 0))
    normal = int(by_kind.get("not_question_like", 0))
    return (
        f"question-like rule audit: scanned={scanned} | audited={audited} | "
        f"noise={noise} | rescued={rescued} | stable_prefix_keep={stable} | "
        f"explicit_requirement_keep={explicit} | normal={normal} | "
        f"cleanup_candidates={cleanup_candidates}"
    )


def cleanup_low_value_emotional_memories(memory_obj: Any, dry_run: bool = True) -> Dict[str, Any]:
    """清理低价值 emotional 记忆，只处理明确的 runtime/placeholder/cron 类噪音。"""
    memory_obj._ensure_memory_schema_index_table()
    log_dir = state_root() / "low_value_emotional_cleanup"
    log_dir.mkdir(parents=True, exist_ok=True)

    conn = memory_obj._conn()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT m.id, m.content, m.created_at,
                   COALESCE(msi.memory_type, m.type, 'unknown') AS memory_type,
                   COALESCE(msi.source_layer, '') AS source_layer,
                   COALESCE(msi.source_anchor, '') AS source_anchor,
                   COALESCE(msi.source_session, '') AS source_session,
                   COALESCE(msi.turn_signature, '') AS turn_signature,
                   COALESCE(msi.promotion_reason, '') AS promotion_reason,
                   COALESCE(msi.quality_score, m.confidence, 0) AS quality_score,
                   COALESCE(msi.importance, 0) AS importance,
                   COALESCE(msi.hotness_score, 0) AS hotness_score,
                   COALESCE(msi.user_explicit, 0) AS user_explicit,
                   COALESCE(msi.removed_at, '') AS removed_at
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            WHERE COALESCE(msi.removed_at, '') = ''
              AND COALESCE(msi.memory_type, m.type, 'unknown') = 'emotional'
            ORDER BY m.id ASC
            """
        ).fetchall()

        targets = []
        by_reason: Dict[str, int] = {}
        for row in rows:
            content = str(row["content"] or "")
            reason = classify_low_value_emotional_memory(content)
            if not reason:
                continue

            source_anchor = str(row["source_anchor"] or "").strip()
            source_session = str(row["source_session"] or "").strip()
            turn_signature = str(row["turn_signature"] or "").strip()
            promotion_reason = str(row["promotion_reason"] or "").strip().lower()
            try:
                importance = float(row["importance"] or 0.0)
            except Exception:
                importance = 0.0
            try:
                hotness_score = float(row["hotness_score"] or 0.0)
            except Exception:
                hotness_score = 0.0
            user_explicit = bool(row["user_explicit"])

            if user_explicit:
                continue

            if reason in {"media_placeholder", "cron_emotional"}:
                allow_remove = True
            else:
                allow_remove = (
                    promotion_reason == "reflection_event"
                    and importance <= 0.72
                    and hotness_score <= 0.31
                )

            if not allow_remove:
                continue

            item = {
                "id": int(row["id"]),
                "content": content,
                "created_at": str(row["created_at"] or ""),
                "source_layer": str(row["source_layer"] or ""),
                "source_anchor": source_anchor,
                "source_session": source_session,
                "turn_signature": turn_signature,
                "promotion_reason": promotion_reason,
                "importance": importance,
                "hotness_score": hotness_score,
                "quality_score": float(row["quality_score"] or 0.0),
                "reason": reason,
            }
            targets.append(item)
            by_reason[reason] = by_reason.get(reason, 0) + 1

        report = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "dry_run": bool(dry_run),
            "matched": len(targets),
            "by_reason": by_reason,
            "sample": targets[:30],
        }
        report_path = log_dir / (
            f"cleanup_{datetime.now().strftime('%Y%m%d%H%M%S')}_{'dryrun' if dry_run else 'apply'}.json"
        )
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        if dry_run or not targets:
            return {
                "success": True,
                "dry_run": bool(dry_run),
                "matched": len(targets),
                "removed": 0,
                "by_reason": by_reason,
                "report_path": str(report_path),
                "sample": targets[:10],
            }

        target_ids = [item["id"] for item in targets]
        deleted = _delete_memory_ids(memory_obj, target_ids, conn=conn)
        conn = None

        return {
            "success": True,
            "dry_run": False,
            "matched": len(targets),
            "removed": deleted,
            "by_reason": by_reason,
            "report_path": str(report_path),
            "sample": targets[:10],
        }
    finally:
        try:
            conn.close()
        except Exception:
            pass
