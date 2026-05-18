#!/usr/bin/env python3
"""Fix confidence and importance distribution in memories table.

Root cause analysis:
- confidence values are clustered ~0.26-0.30 (likely from a similarity-based
  scoring that never reaches the 0.5 threshold)
- importance values are mostly 0.1 (hardcoded default from migration/insert)

This script recalculates both fields based on content analysis, type,
feedback, and recall history.
"""

import sqlite3
import re
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / ".index" / "index.sqlite"

# ── Importance rules ──────────────────────────────────────────────────
# Keywords/patterns for each importance tier
CORRECTION_KEYWORDS = [
    "纠正", "教训", "错误", "不对", "不要", "禁止", "别再", "记住了",
    "lesson", "correction", "mistake", "wrong", "never", "don't",
]
PREFERENCE_KEYWORDS = [
    "偏好", "喜欢", "不喜欢", "习惯", "总是", "从不", "prefer", "like",
    "dislike", "always", "never", "习惯用", "常用",
]
RULE_KEYWORDS = [
    "规则", "约束", "必须", "应该", "不能", "需要", "要求", "rule",
    "constraint", "must", "should", "require", "需要遵循",
]
EMOTION_KEYWORDS = [
    "情感", "开心", "难过", "生气", "感动", "失望", "高兴", "伤心",
    "emotion", "happy", "sad", "angry", "moved", "disappointed",
    "感动了", "生气了", "开心了",
]

# Type-based importance mapping
TYPE_IMPORTANCE = {
    "correction": 0.9,
    "lesson": 0.9,
    "rule": 0.7,
    "instruction": 0.7,
    "preference": 0.8,
    "identity": 0.7,
    "decision": 0.6,
    "milestone": 0.6,
    "evolution_lesson": 0.9,
    "technical": 0.5,
    "skill": 0.5,
    "fact": 0.5,
    "emotional": 0.6,
    "event": 0.5,
    "user_defined": 0.5,
    "habit": 0.6,
    "aversion": 0.6,
    "insight": 0.6,
    "knowledge": 0.5,
    "strategy": 0.5,
}


def classify_importance(content: str, mem_type: str) -> float:
    """Determine importance based on content keywords and memory type."""
    content_lower = content.lower()

    # Type-based priority (highest first)
    type_val = TYPE_IMPORTANCE.get(mem_type, 0.5)

    # Content-based overrides (can raise but not lower below type baseline)
    best = type_val

    # Check for correction/lesson content → highest importance
    if any(kw in content_lower for kw in CORRECTION_KEYWORDS):
        best = max(best, 0.9)

    # Check for rule/constraint content
    if any(kw in content_lower for kw in RULE_KEYWORDS):
        best = max(best, 0.7)

    # Check for preference content
    if any(kw in content_lower for kw in PREFERENCE_KEYWORDS):
        best = max(best, 0.8)

    # Check for emotional content
    if any(kw in content_lower for kw in EMOTION_KEYWORDS):
        best = max(best, 0.6)

    return round(best, 3)


def classify_confidence(
    content: str,
    mem_type: str,
    trigger_count: int,
    evidence_count: int,
    sentiment: str,
    existing_confidence: float,
) -> float:
    """Determine confidence based on recall history and feedback signals."""
    base = 0.5  # default for unclassified

    # Boost from recall frequency (trigger_count)
    if trigger_count >= 5:
        base = 0.8
    elif trigger_count >= 3:
        base = 0.7
    elif trigger_count >= 1:
        base = 0.6

    # Boost from evidence count
    if evidence_count >= 3:
        base = max(base, 0.7)
    elif evidence_count >= 2:
        base = max(base, 0.6)

    # Correction type gets lower confidence (uncertain/unverified)
    if mem_type in ("correction", "lesson", "evolution_lesson"):
        # These are high importance but lower confidence (lessons learned, not facts)
        base = min(base, 0.6)

    # Positive sentiment → slight boost
    if sentiment == "positive":
        base = max(base, 0.6)
    elif sentiment == "negative":
        base = min(base, 0.5)

    # Clamp to valid range
    base = max(0.3, min(1.0, base))
    return round(base, 3)


def main():
    db_path = DB_PATH
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row

        # ── Phase 1: Analyze before ──
        print("=" * 60)
        print("PHASE 1: Current distribution analysis")
        print("=" * 60)

        total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        print(f"Total memories: {total}")

        conf_below = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE confidence < 0.5"
        ).fetchone()[0]
        imp_below = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE importance < 0.3"
        ).fetchone()[0]
        print(f"confidence < 0.5: {conf_below}/{total} ({100*conf_below/total:.1f}%)")
        print(f"importance < 0.3: {imp_below}/{total} ({100*imp_below/total:.1f}%)")

        print("\nConfidence distribution (top 10):")
        for row in conn.execute(
            "SELECT confidence, COUNT(*) as cnt FROM memories "
            "GROUP BY ROUND(confidence, 2) ORDER BY cnt DESC LIMIT 10"
        ):
            print(f"  ~{row[0]:.2f}: {row[1]}")

        print("\nImportance distribution:")
        for row in conn.execute(
            "SELECT importance, COUNT(*) as cnt FROM memories GROUP BY importance ORDER BY importance"
        ):
            print(f"  {row[0]}: {row[1]}")

        # ── Phase 2: Recalculate ──
        print("\n" + "=" * 60)
        print("PHASE 2: Recalculating importance and confidence")
        print("=" * 60)

        rows = conn.execute(
            "SELECT id, content, type, confidence, importance, "
            "trigger_count, evidence_count, sentiment FROM memories"
        ).fetchall()

        updates = []
        for row in rows:
            mem_id = row["id"]
            content = row["content"] or ""
            mem_type = row["type"] or "user_defined"
            old_conf = row["confidence"] or 0.5
            old_imp = row["importance"] or 0.5
            trigger_count = row["trigger_count"] or 0
            evidence_count = row["evidence_count"] or 1
            sentiment = row["sentiment"] or "neutral"

            new_importance = classify_importance(content, mem_type)
            new_confidence = classify_confidence(
                content, mem_type, trigger_count, evidence_count, sentiment, old_conf
            )

            if new_importance != old_imp or new_confidence != old_conf:
                updates.append((new_confidence, new_importance, mem_id))

        print(f"Memories to update: {len(updates)} / {total}")

        # Batch update
        conn.executemany(
            "UPDATE memories SET confidence = ?, importance = ? WHERE id = ?",
            updates,
        )

        # Also update memory_schema_index to stay in sync
        schema_updates = []
        for new_conf, new_imp, mem_id in updates:
            schema_updates.append((new_conf, new_imp, mem_id))
        conn.executemany(
            "UPDATE memory_schema_index SET confidence = ?, importance = ? WHERE memory_id = ?",
            schema_updates,
        )

        conn.commit()

        # ── Phase 3: Verify ──
        print("\n" + "=" * 60)
        print("PHASE 3: Verification")
        print("=" * 60)

        conf_below_after = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE confidence < 0.5"
        ).fetchone()[0]
        imp_below_after = conn.execute(
            "SELECT COUNT(*) FROM memories WHERE importance < 0.3"
        ).fetchone()[0]
        print(f"confidence < 0.5: {conf_below_after}/{total} ({100*conf_below_after/total:.1f}%)")
        print(f"importance < 0.3: {imp_below_after}/{total} ({100*imp_below_after/total:.1f}%)")

        print("\nConfidence distribution (top 10):")
        for row in conn.execute(
            "SELECT ROUND(confidence, 2) as conf_bin, COUNT(*) as cnt FROM memories "
            "GROUP BY conf_bin ORDER BY cnt DESC LIMIT 10"
        ):
            print(f"  ~{row[0]:.2f}: {row[1]}")

        print("\nImportance distribution:")
        for row in conn.execute(
            "SELECT importance, COUNT(*) as cnt FROM memories GROUP BY importance ORDER BY importance"
        ):
            print(f"  {row[0]}: {row[1]}")

        print("\nDetailed breakdown:")
        for row in conn.execute(
            "SELECT "
            "  CASE WHEN confidence < 0.5 THEN '<0.5' ELSE '>=0.5' END as conf_range, "
            "  CASE WHEN importance < 0.3 THEN '<0.3' WHEN importance < 0.6 THEN '0.3-0.6' ELSE '>=0.6' END as imp_range, "
            "  COUNT(*) as cnt "
            "FROM memories GROUP BY conf_range, imp_range ORDER BY conf_range, imp_range"
        ):
            print(f"  conf={row[0]}, imp={row[1]}: {row[2]}")

    finally:
        conn.close()

    print("\n✅ Fix complete.")
    return {
        "total": total,
        "updated": len(updates),
    }


if __name__ == "__main__":
    main()
