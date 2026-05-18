#!/usr/bin/env python3
"""WangChuan public CLI / facade.

Use one canonical public path:
- package root: `wangchuan`
- matching CLI facade: `python3 -m wangchuan`

Stable everyday commands:
    python3 -m wangchuan status
    python3 -m wangchuan paths --json
    python3 -m wangchuan remember "User prefers concise replies" --importance 0.8 --tag preference --tag style
    python3 -m wangchuan recall "user preference" --limit 5
    python3 -m wangchuan recall-raw "what was the original wording" --limit 5
    python3 -m wangchuan recall-scars "rules and lessons" --limit 5
    python3 -m wangchuan task-resume --json
    python3 -m wangchuan healthcheck --json

Advanced maintenance commands stay on the same CLI path but are not the default learning surface:
    python3 -m wangchuan question-like-rule-audit --limit 300 --json
    python3 -m wangchuan question-like-rule-cleanup --dry-run --json
    python3 -m wangchuan recall-at "coffee preference" --as-of 2026-04-20T09:00:00 --limit 5 --json
    python3 -m wangchuan merge "用户喜欢热拿铁" "用户现在更喜欢冰美式" --importance 0.9 --json
    python3 -m wangchuan remember-rule "默认优先直接执行，再关键节点汇报" --tag rule --json
    python3 -m wangchuan remember-lesson '{"content":"配置变更前先做验证","status":"active"}' --json
    python3 -m wangchuan user-memories --user-id alice --limit 20 --json
    python3 -m wangchuan tag-search --tag preference --limit 10 --json
    python3 -m wangchuan history --query "冰美式" --limit 10 --json
    python3 -m wangchuan chain --memory-id 123 --json
    python3 -m wangchuan rollback --memory-id 456 --target-version 123 --json
    python3 -m wangchuan consolidate --session-id default --json
    python3 -m wangchuan agent-tools --json
    python3 -m wangchuan recent --limit 5 --json
    python3 -m wangchuan cleanup --dry-run
    python3 -m wangchuan canonical-repair inspect

Three-layer boundary:
1. Stable public facade: package root + stable everyday CLI commands
2. Advanced operations: maintenance / repair subcommands on the same CLI path, plus `scripts/wangchuan/debug_recall.py` and `scripts/wangchuan/primary_healthcheck.py`
3. Internal implementation: `wangchuan.v3.*` and related internals; compat lives under `wangchuan.compat`

Working language for the current architecture:
- Foundation = evidence-constrained structured memory mainline
- Resonance = memory resonance core
- Use Foundation outputs to answer whether the system stands up
- Use Resonance outputs to answer whether the system recalls with explanation
"""


from __future__ import annotations

import argparse
import json
from typing import Any

from . import (
    Memory,
    paths,
    task_resume,
    facade_capabilities,
    facade_health,
    facade_invoke,
    facade_version,
)
from .canonical_repair import execute_safe as canonical_repair_execute_safe, inspect as canonical_repair_inspect
from wangchuan._protocol import LayerRequest


def _print_payload(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    if isinstance(payload, (dict, list)):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="WangChuan public CLI (Foundation = mainline health/truth, Resonance = memory resonance core)",
        epilog=(
            "Stable everyday facade: remember, recall, recall-raw, recall-scars, status, paths, "
            "healthcheck, task-resume. "
            "Consumer contract facade: facade-version, facade-health, facade-capabilities, facade-invoke. "
            "Advanced maintenance stays on the same CLI path: remember-rule, remember-lesson, recall-at, merge, "
            "history, chain, rollback, user-memories, tag-search, consolidate, agent-tools, recent, cleanup, "
            "question-like-rule-audit, question-like-rule-cleanup, canonical-repair. "
            "Compat lives under wangchuan.compat; v3.* remains internal."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_remember = sub.add_parser("remember", help="Write one memory item")
    p_remember.add_argument("content", help="Memory content")
    p_remember.add_argument("--importance", type=float, default=0.6, help="Importance score, default 0.6")
    p_remember.add_argument("--tag", action="append", dest="tags", default=[], help="Repeatable memory tag")
    p_remember.add_argument("--json", action="store_true", help="Emit JSON")

    p_remember_rule = sub.add_parser("remember-rule", help="Advanced: write one rule/scar memory item")
    p_remember_rule.add_argument("content", help="Rule content")
    p_remember_rule.add_argument("--importance", type=float, default=0.8, help="Importance score, default 0.8")
    p_remember_rule.add_argument("--tag", action="append", dest="tags", default=[], help="Repeatable memory tag")
    p_remember_rule.add_argument("--json", action="store_true", help="Emit JSON")

    p_remember_lesson = sub.add_parser("remember-lesson", help="Advanced: write one lesson memory item")
    p_remember_lesson.add_argument("lesson", help="Lesson JSON string")
    p_remember_lesson.add_argument("--json", action="store_true", help="Emit JSON")

    for name, help_text in [
        ("recall", "General memory recall"),
        ("recall-raw", "Recall raw evidence and original records"),
        ("recall-scars", "Recall rules, lessons, and scars"),
    ]:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("query", help="Recall query")
        p.add_argument("--limit", type=int, default=5, help="Number of rows to return")
        p.add_argument("--json", action="store_true", help="Emit JSON")

    p_recall_at = sub.add_parser("recall-at", help="Advanced: recall memories that were valid at a specific time")
    p_recall_at.add_argument("query", help="Recall query")
    p_recall_at.add_argument("--as-of", required=True, help="ISO timestamp or date, e.g. 2026-04-20 or 2026-04-20T09:00:00")
    p_recall_at.add_argument("--limit", type=int, default=5, help="Number of rows to return")
    p_recall_at.add_argument("--json", action="store_true", help="Emit JSON")

    p_merge = sub.add_parser("merge", help="Advanced: supersede an old fact with a new current truth")
    p_merge.add_argument("old_query", help="Old fact selector, supports content text or id:<memory_id>")
    p_merge.add_argument("new_content", help="New fact content")
    p_merge.add_argument("--importance", type=float, default=0.8, help="Importance score for the new fact")
    p_merge.add_argument("--json", action="store_true", help="Emit JSON")

    p_history = sub.add_parser("history", help="Advanced: show version history for a fact or memory")
    p_history.add_argument("--memory-id", type=int, default=None, help="Specific memory id")
    p_history.add_argument("--query", default=None, help="Content keyword query")
    p_history.add_argument("--limit", type=int, default=10, help="Number of rows to return")
    p_history.add_argument("--json", action="store_true", help="Emit JSON")

    p_chain = sub.add_parser("chain", help="Advanced: show supersession chain for a memory id")
    p_chain.add_argument("--memory-id", type=int, required=True, help="Specific memory id")
    p_chain.add_argument("--json", action="store_true", help="Emit JSON")

    p_rollback = sub.add_parser("rollback", help="Advanced: rollback a memory to a prior version")
    p_rollback.add_argument("--memory-id", type=int, required=True, help="Current memory id")
    p_rollback.add_argument("--target-version", type=int, default=None, help="Target historical memory id")
    p_rollback.add_argument("--json", action="store_true", help="Emit JSON")

    p_user_memories = sub.add_parser("user-memories", help="Advanced: show memories visible to one user id")
    p_user_memories.add_argument("--user-id", required=True, help="User id")
    p_user_memories.add_argument("--limit", type=int, default=50, help="Number of rows to return")
    p_user_memories.add_argument("--json", action="store_true", help="Emit JSON")

    p_tag_search = sub.add_parser("tag-search", help="Advanced: search memories by tag")
    p_tag_search.add_argument("--tag", required=True, help="Tag name")
    p_tag_search.add_argument("--limit", type=int, default=10, help="Number of rows to return")
    p_tag_search.add_argument("--json", action="store_true", help="Emit JSON")

    p_consolidate = sub.add_parser("consolidate", help="Advanced: trigger one session consolidation")
    p_consolidate.add_argument("--session-id", default=None, help="Session id, default=default")
    p_consolidate.add_argument("--json", action="store_true", help="Emit JSON")

    p_agent_tools = sub.add_parser("agent-tools", help="Advanced: show standard agent memory tools mapping")
    p_agent_tools.add_argument("--json", action="store_true", help="Emit JSON")

    p_recent = sub.add_parser("recent", help="Advanced: show recent structured memories")
    p_recent.add_argument("--limit", type=int, default=10, help="Number of rows to return")
    p_recent.add_argument("--json", action="store_true", help="Emit JSON")

    p_status = sub.add_parser("status", help="Show WangChuan runtime status")
    p_status.add_argument("--json", action="store_true", help="Emit JSON")

    p_paths = sub.add_parser("paths", help="Show resolved WangChuan workspace/data/state paths")
    p_paths.add_argument("--json", action="store_true", help="Emit JSON")

    p_health = sub.add_parser("healthcheck", help="Run user-facing WangChuan memory healthcheck")
    p_health.add_argument("--json", action="store_true", help="Emit JSON")

    p_task_resume = sub.add_parser("task-resume", help="Show structured task resume summary")
    p_task_resume.add_argument("--board-path", default=None, help="Optional board path override")
    p_task_resume.add_argument("--json", action="store_true", help="Emit JSON")

    p_facade_version = sub.add_parser("facade-version", help="Stable consumer facade: print facade version")
    p_facade_version.add_argument("--json", action="store_true", help="Emit JSON")

    p_facade_health = sub.add_parser("facade-health", help="Stable consumer facade: print facade health")
    p_facade_health.add_argument("--json", action="store_true", help="Emit JSON")

    p_facade_capabilities = sub.add_parser("facade-capabilities", help="Stable consumer facade: print supported facade capabilities")
    p_facade_capabilities.add_argument("--json", action="store_true", help="Emit JSON")

    p_facade_invoke = sub.add_parser("facade-invoke", help="Stable consumer facade: invoke one whitelisted facade operation")
    p_facade_invoke.add_argument("operation", choices=["remember", "recall", "recall_raw", "recall_scars", "status", "healthcheck", "task_resume", "paths"], help="Facade operation")
    p_facade_invoke.add_argument("--content", default="", help="Content for remember")
    p_facade_invoke.add_argument("--query", default="", help="Query for recall operations")
    p_facade_invoke.add_argument("--limit", type=int, default=5, help="Recall limit")
    p_facade_invoke.add_argument("--importance", type=float, default=0.6, help="Importance for remember")
    p_facade_invoke.add_argument("--tag", action="append", dest="tags", default=[], help="Repeatable memory tag")
    p_facade_invoke.add_argument("--board-path", default="", help="Optional board path for task_resume")
    p_facade_invoke.add_argument("--trace-id", default="", help="Optional trace id")
    p_facade_invoke.add_argument("--session-id", default="default", help="Optional session id")
    p_facade_invoke.add_argument("--json", action="store_true", help="Emit JSON")

    p_cleanup = sub.add_parser("cleanup", help="Advanced: run historical noise cleanup")
    p_cleanup.add_argument("--dry-run", action="store_true", help="Preview historical noise cleanup without deleting")
    p_cleanup.add_argument("--json", action="store_true", help="Emit JSON")

    p_question_like_audit = sub.add_parser("question-like-rule-audit", help="Advanced: audit question-like rule memories")
    p_question_like_audit.add_argument("--limit", type=int, default=300, help="Number of scar/rule rows to audit")
    p_question_like_audit.add_argument("--json", action="store_true", help="Emit JSON")

    p_question_like_cleanup = sub.add_parser("question-like-rule-cleanup", help="Advanced: clean conservative question-like rule noise")
    p_question_like_cleanup.add_argument("--dry-run", action="store_true", help="Preview cleanup without deleting")
    p_question_like_cleanup.add_argument("--json", action="store_true", help="Emit JSON")

    p_canonical_repair = sub.add_parser("canonical-repair", help="Advanced: inspect and safely repair canonical profile conflicts")
    p_canonical_repair.add_argument("mode", choices=["inspect", "execute-safe"], help="Repair mode")
    p_canonical_repair.add_argument("--only-slot", default="", help="Optional slot filter, e.g. drink_preference")
    p_canonical_repair.add_argument("--json", action="store_true", help="Emit JSON")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    memory = Memory()

    if args.command == "remember":
        payload = memory.remember(args.content, importance=args.importance, tags=args.tags)
        _print_payload(payload, args.json)
        return 0 if payload.get("success") else 1

    if args.command == "remember-rule":
        payload = memory.remember(
            args.content,
            importance=args.importance,
            tags=list(dict.fromkeys(list(args.tags or []) + ["rule"])),
            metadata={"memory_type": "rule", "source_layer": "scar", "user_explicit": True},
        )
        _print_payload(payload, args.json)
        return 0 if payload.get("success") else 1

    if args.command == "remember-lesson":
        payload = memory.remember_lesson(json.loads(args.lesson))
        _print_payload(payload, args.json)
        return 0 if payload.get("success") else 1

    if args.command == "recall":
        _print_payload(memory.recall(args.query, limit=args.limit), args.json)
        return 0

    if args.command == "recall-raw":
        _print_payload(memory.recall_raw(args.query, limit=args.limit), args.json)
        return 0

    if args.command == "recall-scars":
        _print_payload(memory.recall_scars(args.query, limit=args.limit), args.json)
        return 0

    if args.command == "recall-at":
        _print_payload(memory.recall_at(args.query, as_of=args.as_of, limit=args.limit), args.json)
        return 0

    if args.command == "merge":
        payload = memory.merge(args.old_query, args.new_content, importance=args.importance)
        _print_payload(payload, args.json)
        return 0 if payload.get("success") else 1

    if args.command == "history":
        payload = memory.history(memory_id=args.memory_id, query=args.query, limit=args.limit)
        _print_payload(payload, args.json)
        return 0

    if args.command == "chain":
        payload = memory.get_supersession_chain(args.memory_id)
        _print_payload(payload, args.json)
        return 0

    if args.command == "rollback":
        payload = memory.rollback(args.memory_id, target_version=args.target_version)
        _print_payload(payload, args.json)
        return 0 if payload.get("success") else 1

    if args.command == "user-memories":
        _print_payload(memory.get_user_memories(args.user_id, limit=args.limit), args.json)
        return 0

    if args.command == "tag-search":
        _print_payload(memory.find_by_tag(args.tag, limit=args.limit), args.json)
        return 0

    if args.command == "consolidate":
        from .memory_api import consolidate as consolidate_memory
        payload = consolidate_memory(args.session_id)
        _print_payload(payload, args.json)
        return 0 if payload.get("success", True) else 1

    if args.command == "agent-tools":
        from .memory_api import agent_tools
        _print_payload(agent_tools(), True if args.json else False)
        return 0

    if args.command == "recent":
        _print_payload(memory.recent(limit=args.limit), args.json)
        return 0

    if args.command == "status":
        payload = memory.status()
        if args.json:
            _print_payload(payload, True)
        else:
            print(payload.get("message", ""))
        return 0

    if args.command == "paths":
        payload = paths()
        _print_payload(payload, True if args.json else False)
        return 0

    if args.command == "healthcheck":
        payload = memory.user_healthcheck()
        if args.json:
            _print_payload(payload, True)
        else:
            print(payload.get("summary", ""))
            for name, item in payload.get("checks", {}).items():
                mark = "PASS" if item.get("ok") else "FAIL"
                print(f"- [{mark}] {name}: {item.get('detail', '')}")
        return 0 if payload.get("status") == "healthy" else 1

    if args.command == "task-resume":
        payload = task_resume(args.board_path)
        if args.json:
            _print_payload(payload, True)
        else:
            print(payload.get("summary", ""))
            print(f"current_task: {payload.get('current_task') or '?'}")
            print(f"next_step: {payload.get('next_step') or '?'}")
            for step in payload.get("resume_steps", [])[:8]:
                print(f"- {step}")
        return 0

    if args.command == "facade-version":
        _print_payload({"version": facade_version()}, True if args.json else False)
        return 0

    if args.command == "facade-health":
        payload = facade_health().to_dict()
        _print_payload(payload, True if args.json else False)
        return 0 if payload.get("ok") else 1

    if args.command == "facade-capabilities":
        payload = facade_capabilities().to_dict()
        _print_payload(payload, True if args.json else False)
        return 0

    if args.command == "facade-invoke":
        build_payload: dict[str, object] = {}
        if args.operation == "remember":
            build_payload = {"content": args.content, "importance": args.importance, "tags": args.tags}
        elif args.operation in {"recall", "recall_raw", "recall_scars"}:
            build_payload = {"query": args.query, "limit": args.limit}
        elif args.operation == "task_resume":
            build_payload = {"board_path": args.board_path}
        response = facade_invoke(LayerRequest(
            layer="wangchuan",
            operation=args.operation,
            trace_id=args.trace_id,
            session_id=args.session_id,
            payload=build_payload,
        )).to_dict()
        _print_payload(response, True if args.json else False)
        return 0 if response.get("ok") else 1

    if args.command == "cleanup":
        payload = memory.cleanup_historical_noise(dry_run=args.dry_run)
        _print_payload(payload, args.json)
        return 0 if payload.get("success") else 1

    if args.command == "question-like-rule-audit":
        payload = memory.audit_question_like_rules(limit=args.limit)
        _print_payload(payload, args.json)
        return 0 if payload.get("success") else 1

    if args.command == "question-like-rule-cleanup":
        payload = memory.cleanup_question_like_rule_noise(dry_run=args.dry_run)
        _print_payload(payload, args.json)
        return 0 if payload.get("success") else 1

    if args.command == "canonical-repair":
        if args.mode == "inspect":
            payload = canonical_repair_inspect(memory, only_slot=args.only_slot)
            if args.json:
                _print_payload(payload, True)
            else:
                print(payload.get("summary", ""))
            return 0

        payload = canonical_repair_execute_safe(memory, only_slot=args.only_slot)
        if args.json:
            _print_payload(payload, True)
        else:
            print(payload.get("summary", ""))
            print(f"report: {payload.get('report_path', '')}")
        failures = [
            action for action in payload.get("actions", [])
            if not action.get("result", {}).get("success") and action.get("result", {}).get("reason") != "report_only"
        ]
        return 1 if failures else 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
