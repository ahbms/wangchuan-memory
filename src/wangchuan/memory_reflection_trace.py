from __future__ import annotations

"""WangChuan reflection trace / dedupe helpers.

这一层承接 memory_api 中与 reflection_event 去重、trace 回填相关的低风险纯逻辑：
- exact reflection duplicate 查询
- reflection_event trace / dedupe 查询
- gm_messages trace 回填查询
- static-context candidate / lookup helper
- CLI mirror 偏好判断
- reflection/static context 文本归一化
- semantic token split / overlap helper

约束：
- 不定义新的记忆真值规则
- 仍由调用方（Memory）提供 DB / schema / trace helper 能力
- 优先保持与 memory_api 现有返回与判定口径一致
"""

from typing import Any, Dict, List
from difflib import SequenceMatcher
import os
import re
import sqlite3

try:
    from wangchuan.paths import workspace_root
except ImportError:
    from wangchuan.paths import workspace_root

try:
    from wangchuan.memory_rules import STATIC_CONTEXT_TRACE_RULES
except ImportError:
    from wangchuan.memory_rules import STATIC_CONTEXT_TRACE_RULES


def normalize_reflection_source_query(content: str) -> str:
    text = re.sub(r"\s+", " ", str(content or "").strip())
    text = re.sub(r"^(规则变更|偏好|纠错|里程碑|情感事件)\s*:\s*", "", text)
    text = text.split(" | 来源:", 1)[0]
    text = text.split("\n", 1)[0].strip()
    return text[:120]


def normalize_static_context_match_text(content: str) -> str:
    text = re.sub(r"\s+", " ", str(content or "").strip().lower())
    text = re.sub(r"^(规则变更|偏好|纠错|里程碑|情感事件)\s*:\s*", "", text)
    return text


def static_context_trace_candidates(memory_obj: Any) -> List[Dict[str, Any]]:
    if hasattr(memory_obj, '_cached_context_candidates'):
        return memory_obj._cached_context_candidates

    workspace = workspace_root()
    candidates: List[Dict[str, Any]] = []

    for rule in STATIC_CONTEXT_TRACE_RULES:
        rel_path = str(rule.get("path") or "").strip()
        if not rel_path:
            continue
        file_path = workspace / rel_path
        if not file_path.exists():
            continue

        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue

        markers = [str(marker).strip() for marker in rule.get("markers") or [] if str(marker).strip()]
        marker_lines = []
        for index, line in enumerate(lines, 1):
            normalized_line = str(line).strip()
            if normalized_line and any(marker in normalized_line for marker in markers):
                marker_lines.append(index)

        if not marker_lines:
            continue

        start_line = min(marker_lines)
        end_line = max(marker_lines)
        turn_signature = f"static:{rel_path}:{start_line}-{end_line}"
        source_anchor = f"workspace://{rel_path}#L{start_line}-L{end_line}"
        candidates.append({
            "rule_id": str(rule.get("rule_id") or rel_path),
            "path": rel_path,
            "source_session": "workspace_context",
            "source_anchor": source_anchor,
            "turn_signature": turn_signature,
            "provenance": source_anchor,
            "memory_types": {str(item).strip().lower() for item in (rule.get("memory_types") or []) if str(item).strip()},
            "required_tokens": [str(token).strip().lower() for token in (rule.get("required_tokens") or []) if str(token).strip()],
            "optional_tokens": [str(token).strip().lower() for token in (rule.get("optional_tokens") or []) if str(token).strip()],
            "markers": markers,
            "line_range": [start_line, end_line],
        })

    memory_obj._cached_context_candidates = candidates
    return candidates


def lookup_static_context_trace(memory_obj: Any, content: str, memory_type: str = "") -> Dict[str, Any]:
    normalized = memory_obj._normalize_static_context_match_text(content)
    if not normalized:
        return {}

    normalized_memory_type = str(memory_type or "").strip().lower()
    best: Dict[str, Any] = {}
    best_score = 0.0

    for candidate in static_context_trace_candidates(memory_obj):
        candidate_types = candidate.get("memory_types") or set()
        if candidate_types and normalized_memory_type and normalized_memory_type not in candidate_types:
            continue

        required_tokens = candidate.get("required_tokens") or []
        optional_tokens = candidate.get("optional_tokens") or []
        markers = candidate.get("markers") or []

        if required_tokens and not all(token in normalized for token in required_tokens):
            continue

        matched_optional = [token for token in optional_tokens if token in normalized]
        matched_markers = [marker for marker in markers if marker.lower() in normalized]
        lexical_hits = len(required_tokens) + len(matched_optional) + len(matched_markers)
        similarity = max(
            [SequenceMatcher(None, normalized, memory_obj._normalize_static_context_match_text(marker)).ratio() for marker in markers] or [0.0]
        )
        score = lexical_hits + similarity

        if lexical_hits <= 0 and similarity < 0.72:
            continue

        if score > best_score:
            best_score = score
            best = {
                "source_anchor": candidate.get("source_anchor") or "",
                "source_session": candidate.get("source_session") or "workspace_context",
                "turn_signature": candidate.get("turn_signature") or "",
                "provenance": candidate.get("provenance") or candidate.get("source_anchor") or "workspace_context",
                "trace_origin": "static_context",
                "trace_rule_id": candidate.get("rule_id") or "",
                "trace_score": round(float(score), 6),
            }

    return best


def split_semantic_match_tokens(content: str) -> List[str]:
    normalized = normalize_static_context_match_text(content)
    if not normalized:
        return []

    split_pattern = r"[\s,，。！？、:：;；()（）\[\]{}\-_/\\|]+|(?:并且|以及|还有|或者|重点关注|优先|默认模式|规则变更|长期偏好|用户明确要求|用户要求|用户偏好|用户长期偏好|偏好|规则)"
    stop_tokens = {"用户", "要求", "明确", "长期", "重点", "继续", "持续", "这个", "情况", "以后", "现在", "一条消息", "一个重点"}
    tokens: List[str] = []
    seen = set()
    for token in re.split(split_pattern, normalized):
        token = token.strip()
        if len(token) < 2 or token in stop_tokens or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def semantic_token_overlap_score(source: str, target: str) -> float:
    source_tokens = split_semantic_match_tokens(source)
    target_tokens = split_semantic_match_tokens(target)
    if not source_tokens or not target_tokens:
        return 0.0

    hits = 0
    for token in source_tokens:
        if any(token in candidate or candidate in token for candidate in target_tokens):
            hits += 1
    return hits / max(len(source_tokens), 1)


def lookup_related_memory_trace(memory_obj: Any, content: str, memory_type: str = "", exclude_memory_id: Any = None) -> Dict[str, Any]:
    normalized = memory_obj._normalize_static_context_match_text(content)
    if len(normalized) < 8:
        return {}

    if memory_obj.db_path != ":memory:" and not os.path.exists(memory_obj.db_path):
        return {}

    normalized_memory_type = str(memory_type or "").strip().lower()
    conn = None
    try:
        conn = memory_obj._conn()
        conn.row_factory = sqlite3.Row
        sql = (
            "SELECT m.id, m.content, COALESCE(msi.source_anchor, '') AS source_anchor, "
            "COALESCE(msi.source_session, '') AS source_session, COALESCE(msi.turn_signature, '') AS turn_signature, "
            "COALESCE(msi.memory_type, m.type, '') AS memory_type, COALESCE(msi.user_explicit, 0) AS user_explicit, "
            "COALESCE(msi.quality_score, 0) AS quality_score, COALESCE(msi.hotness_score, 0) AS hotness_score "
            "FROM memories m "
            "LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id "
            "WHERE COALESCE(msi.removed_at, '') = '' "
            "AND COALESCE(msi.source_anchor, '') != '' "
            "AND COALESCE(msi.source_session, '') != '' "
            "AND COALESCE(msi.turn_signature, '') != '' "
            "AND (COALESCE(msi.user_explicit, 0) = 1 OR COALESCE(msi.quality_score, 0) >= 0.85) "
        )
        params: List[Any] = []
        if normalized_memory_type:
            sql += "AND COALESCE(msi.memory_type, m.type, '') = ? "
            params.append(normalized_memory_type)
        if exclude_memory_id not in (None, ""):
            sql += "AND m.id != ? "
            params.append(int(exclude_memory_id))
        sql += "ORDER BY COALESCE(msi.quality_score, 0) DESC, COALESCE(msi.hotness_score, 0) DESC, m.id DESC LIMIT 400"
        candidates = conn.execute(sql, tuple(params)).fetchall()
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

    best: Dict[str, Any] = {}
    best_score = 0.0
    for row in candidates:
        candidate_text = memory_obj._normalize_static_context_match_text(row["content"])
        if not candidate_text:
            continue

        similarity = SequenceMatcher(None, normalized, candidate_text).ratio()
        overlap_score = memory_obj._semantic_token_overlap_score(normalized, candidate_text)
        containment_bonus = 0.15 if (normalized in candidate_text or candidate_text in normalized) else 0.0
        score = similarity + overlap_score + containment_bonus

        if similarity < 0.72 or overlap_score < 0.45:
            continue

        if score > best_score:
            best_score = score
            best = {
                "source_anchor": str(row["source_anchor"] or "").strip(),
                "source_session": str(row["source_session"] or "").strip(),
                "turn_signature": str(row["turn_signature"] or "").strip(),
                "provenance": str(row["source_anchor"] or row["source_session"] or "memory_neighbor").strip(),
                "trace_origin": "memory_neighbor",
                "trace_memory_id": int(row["id"]),
                "trace_score": round(float(score), 6),
            }

    return best


def has_preferred_non_cli_mirror(memory_obj: Any, candidate: sqlite3.Row, siblings: List[sqlite3.Row]) -> bool:
    candidate_session = str(candidate["session_id"] or "").strip()
    if not memory_obj._is_cli_mirror_session(candidate_session):
        return False

    candidate_text = memory_obj._normalize_reflection_source_query(candidate["content"])
    candidate_dt = memory_obj._parse_iso_dt(candidate["timestamp"])

    for sibling in siblings:
        if int(sibling["id"]) == int(candidate["id"]):
            continue

        sibling_session = str(sibling["session_id"] or "").strip()
        if memory_obj._is_cli_mirror_session(sibling_session):
            continue

        if memory_obj._normalize_reflection_source_query(sibling["content"]) != candidate_text:
            continue

        sibling_dt = memory_obj._parse_iso_dt(sibling["timestamp"])
        if candidate_dt and sibling_dt and abs((candidate_dt - sibling_dt).total_seconds()) > 180:
            continue

        return True

    return False


def lookup_message_trace(memory_obj: Any, content: str, created_at: str = "") -> Dict[str, Any]:
    needle = memory_obj._normalize_reflection_source_query(content)
    if len(needle) < 4:
        return {}

    if memory_obj.db_path != ":memory:" and not os.path.exists(memory_obj.db_path):
        return {}

    target_dt = memory_obj._parse_iso_dt(created_at)
    conn = None
    try:
        conn = memory_obj._conn()
        conn.row_factory = sqlite3.Row
        candidates = conn.execute(
            """
            SELECT id, session_id, COALESCE(message_id, '') AS message_id, role, content, timestamp
            FROM gm_messages
            WHERE role = 'user' AND content LIKE ?
            ORDER BY id DESC
            LIMIT 12
            """,
            (f"%{needle}%",),
        ).fetchall()

        if not candidates and len(needle) >= 8:
            prefix = needle[:80]
            candidates = conn.execute(
                """
                SELECT id, session_id, COALESCE(message_id, '') AS message_id, role, content, timestamp
                FROM gm_messages
                WHERE role = 'user' AND content LIKE ?
                ORDER BY id DESC
                LIMIT 12
                """,
                (f"%{prefix}%",),
            ).fetchall()

        best_row = None
        best_score = float("-inf")
        for row in candidates:
            row_text = str(row["content"] or "").strip()
            if not row_text:
                continue
            score = 0.0
            if needle and needle in row_text:
                score += 5.0
            if needle and row_text.startswith(needle):
                score += 2.0
            if needle[:24] and row_text[:24] == needle[:24]:
                score += 1.2
            if target_dt is not None:
                row_dt = memory_obj._parse_iso_dt(row["timestamp"])
                if row_dt is not None:
                    score -= abs((row_dt - target_dt).total_seconds()) / 3600.0 * 0.05
            if has_preferred_non_cli_mirror(memory_obj, row, candidates):
                score -= 1.0
            if score > best_score:
                best_score = score
                best_row = row

        if best_row is None:
            return {}

        session_id = str(best_row["session_id"] or "").strip()
        source_message_id = str(best_row["message_id"] or best_row["id"] or "").strip()
        timestamp = str(best_row["timestamp"] or "").strip()
        row_text = str(best_row["content"] or "")
        turn_signature = memory_obj._build_turn_signature_from_message(session_id, source_message_id, timestamp, row_text)
        source_anchor = memory_obj._build_message_anchor(session_id, source_message_id, turn_signature)
        return {
            "source_session": session_id,
            "turn_signature": turn_signature,
            "source_anchor": source_anchor,
            "provenance": source_anchor or session_id or "gm_messages",
        }
    except Exception:
        return {}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def find_existing_exact_reflection_memory(memory_obj: Any, content: str, metadata: Dict[str, Any] | None = None) -> int | None:
    metadata = dict(metadata or {})
    if str(metadata.get("promotion_reason") or "").strip().lower() != "reflection_event":
        return None

    memory_type = str(metadata.get("memory_type") or "").strip().lower()
    if memory_type not in {"rule", "correction"}:
        return None

    try:
        memory_obj._ensure_memory_schema_index_table()
        conn = memory_obj._conn()
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT m.id, m.created_at,
                       COALESCE(msi.source_anchor, '') AS source_anchor,
                       COALESCE(msi.source_session, '') AS source_session,
                       COALESCE(msi.turn_signature, '') AS turn_signature,
                       COALESCE(msi.quality_score, 0) AS quality_score,
                       COALESCE(msi.hotness_score, 0) AS hotness_score,
                       COALESCE(msi.last_confirmed_at, '') AS last_confirmed_at
                FROM memories m
                LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
                WHERE COALESCE(msi.removed_at, '') = ''
                  AND COALESCE(msi.promotion_reason, '') = 'reflection_event'
                  AND COALESCE(msi.memory_type, m.type, 'unknown') = ?
                  AND m.content = ?
                """,
                (memory_type, str(content or "").strip()),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return None

    keeper = memory_obj._pick_duplicate_memory_keeper(rows)
    return int(keeper["id"]) if keeper else None


def find_existing_reflection_memory(memory_obj: Any, content: str, metadata: Dict[str, Any] | None = None) -> int | None:
    metadata = dict(metadata or {})
    if str(metadata.get("promotion_reason") or "").strip().lower() != "reflection_event":
        return None

    source_anchor = str(metadata.get("source_anchor") or "").strip()
    turn_signature = str(metadata.get("turn_signature") or "").strip()
    dedupe_key = str(metadata.get("dedupe_key") or "").strip()

    try:
        memory_obj._ensure_memory_schema_index_table()
        conn = memory_obj._conn()
        conn.row_factory = sqlite3.Row
        try:
            if turn_signature or source_anchor:
                clauses = []
                params: List[Any] = []
                if turn_signature:
                    clauses.append("COALESCE(msi.turn_signature, '') = ?")
                    params.append(turn_signature)
                if source_anchor:
                    clauses.append("COALESCE(msi.source_anchor, '') = ?")
                    params.append(source_anchor)
                row = conn.execute(
                    "SELECT m.id FROM memories m "
                    "LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id "
                    "WHERE COALESCE(msi.promotion_reason, '') = 'reflection_event' "
                    f"AND ({' OR '.join(clauses)}) "
                    "ORDER BY m.id DESC LIMIT 1",
                    params,
                ).fetchone()
                if row:
                    return int(row["id"])

            if dedupe_key:
                row = conn.execute(
                    "SELECT m.id FROM memories m "
                    "LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id "
                    "WHERE COALESCE(msi.promotion_reason, '') = 'reflection_event' "
                    "AND COALESCE(msi.dedupe_key, '') = ? AND m.content = ? "
                    "ORDER BY m.id DESC LIMIT 1",
                    (dedupe_key, str(content or "").strip()),
                ).fetchone()
                if row:
                    return int(row["id"])
        finally:
            conn.close()
    except Exception:
        return None

    return None
