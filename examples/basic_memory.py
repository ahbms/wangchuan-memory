#!/usr/bin/env python3
"""5-minute WangChuan Python demo.

Run from a source checkout:

    python3 examples/basic_memory.py

Or after install:

    python3 examples/basic_memory.py
"""

from __future__ import annotations

import json

from wangchuan import recall, remember, status


def main() -> int:
    preference = remember(
        "User prefers concise replies with short sections.",
        importance=0.9,
        tags=["preference", "style"],
    )
    fact = remember(
        "WangChuan stores memory in a local SQLite database.",
        importance=0.75,
        tags=["fact", "storage"],
    )

    rows = recall("How should I reply and where is memory stored?", limit=5)
    explains = [row.get("recall_explain", {}) for row in rows]

    payload = {
        "preference_written": bool(preference.get("success")),
        "fact_written": bool(fact.get("success")),
        "recall_count": len(rows),
        "top_contents": [row.get("content") for row in rows[:3]],
        "explain_samples": explains[:2],
        "status_message": status().get("message", ""),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["preference_written"] and payload["fact_written"] and payload["recall_count"] >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
