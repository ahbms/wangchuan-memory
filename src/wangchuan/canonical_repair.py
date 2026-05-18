from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from .memory_api import Memory
from .paths import state_root

REPORT_DIR = state_root() / 'reports'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Inspect and safely repair WangChuan canonical profile conflicts')
    sub = parser.add_subparsers(dest='command', required=True)

    p_inspect = sub.add_parser('inspect', help='Show current canonical profile and repair suggestions')
    p_inspect.add_argument('--only-slot', default='', help='Optional slot filter, e.g. drink_preference')
    p_inspect.add_argument('--json', action='store_true', help='Emit JSON')

    p_exec = sub.add_parser('execute-safe', help='Execute only low-risk repair suggestions')
    p_exec.add_argument('--only-slot', default='', help='Optional slot filter, e.g. drink_preference')
    p_exec.add_argument('--json', action='store_true', help='Emit JSON')

    return parser


def _filter_profile(profile: Dict[str, Any], only_slot: str) -> Dict[str, Any]:
    if not only_slot:
        return profile
    slot = str(only_slot).strip()
    slots = profile.get('slots', {})
    if slot not in slots:
        return {
            'reader': profile.get('reader'),
            'status': 'missing_slot',
            'requested_slot': slot,
            'available_slots': sorted(slots.keys()),
            'repair_suggestions': [],
            'slots': {},
        }
    item = slots[slot]
    return {
        'reader': profile.get('reader'),
        'status': item.get('status'),
        'requested_slot': slot,
        'repair_suggestions': [s for s in profile.get('repair_suggestions', []) if s.get('slot') == slot],
        'slots': {slot: item},
    }


def _build_profile_summary(payload: Dict[str, Any]) -> str:
    requested_slot = str(payload.get('requested_slot') or '').strip()
    if requested_slot:
        slots = payload.get('slots', {}) or {}
        item = slots.get(requested_slot, {})
        if not item:
            return f'canonical repair inspect: slot={requested_slot} missing'
        return (
            f"canonical repair inspect: slot={requested_slot} status={item.get('status', 'unknown')} "
            f"reason={item.get('status_reason', 'n/a')} repairs={len(item.get('repair_suggestions', []) or [])}"
        )

    counts = payload.get('status_counts', {}) or {}
    repairs = len(payload.get('repair_suggestions', []) or [])
    return (
        f"canonical repair inspect: status={payload.get('status', 'unknown')} "
        f"stable={counts.get('stable', 0)} contended={counts.get('contended', 0)} "
        f"review={counts.get('needs_review', 0)} repairs={repairs}"
    )


def _safe_supersede(memory: Memory, winner_id: int, runner_up_id: int) -> Dict[str, Any]:
    conn = memory._conn()
    try:
        rows = conn.execute(
            '''
            SELECT m.id, m.content,
                   COALESCE(msi.memory_type, ''),
                   COALESCE(msi.subject_domain, ''),
                   COALESCE(msi.supersession_chain, ''),
                   COALESCE(msi.lifecycle, '')
            FROM memories m
            LEFT JOIN memory_schema_index msi ON m.id = msi.memory_id
            WHERE m.id IN (?, ?)
            ORDER BY m.id ASC
            ''',
            (winner_id, runner_up_id),
        ).fetchall()
    finally:
        conn.close()

    by_id = {int(row[0]): row for row in rows}
    winner = by_id.get(int(winner_id))
    runner = by_id.get(int(runner_up_id))
    if not winner or not runner:
        return {'success': False, 'reason': 'missing_memory'}

    winner_content = str(winner[1] or '')
    runner_content = str(runner[1] or '')
    winner_type = str(winner[2] or '')
    runner_type = str(runner[2] or '')
    winner_domain = str(winner[3] or '')
    runner_domain = str(runner[3] or '')

    if winner_type != runner_type or winner_domain != runner_domain:
        return {'success': False, 'reason': 'type_or_domain_mismatch'}

    comparable = runner_content == winner_content or (runner_content and runner_content in winner_content)
    if not comparable:
        return {'success': False, 'reason': 'runner_not_substring_of_winner'}

    now = datetime.now().isoformat(timespec='seconds')
    memory._update_memory_schema_fields(runner_up_id, {
        'lifecycle': 'superseded',
        'valid_until': now,
        'superseded_by': winner_id,
        'updated_at': now,
    })

    existing_chain = str(winner[4] or '')
    chain_parts = [p.strip() for p in existing_chain.split(',') if p.strip().isdigit()]
    runner_id_text = str(runner_up_id)
    if runner_id_text not in chain_parts:
        chain_parts.append(runner_id_text)
    memory._update_memory_schema_fields(winner_id, {
        'supersession_chain': ','.join(chain_parts) + (',' if chain_parts else ''),
        'updated_at': now,
    })

    return {
        'success': True,
        'winner_memory_id': winner_id,
        'runner_up_memory_id': runner_up_id,
        'reason': 'safe_supersede_applied',
    }


def inspect(memory: Memory, only_slot: str = '') -> Dict[str, Any]:
    profile = memory.user_canonical_profile()
    payload = _filter_profile(profile, only_slot)
    payload['summary'] = _build_profile_summary(payload)
    return payload


def _build_action_stats(actions: List[Dict[str, Any]]) -> Dict[str, int]:
    applied = sum(1 for action in actions if action.get('result', {}).get('success'))
    report_only = sum(1 for action in actions if action.get('result', {}).get('reason') == 'report_only')
    hard_fail = sum(1 for action in actions if not action.get('result', {}).get('success') and action.get('result', {}).get('reason') != 'report_only')
    return {
        'total': len(actions),
        'applied': applied,
        'report_only': report_only,
        'hard_fail': hard_fail,
    }


def _build_execute_summary(payload: Dict[str, Any]) -> str:
    stats = payload.get('stats', {}) or {}
    post_profile = payload.get('post_profile', {}) or {}
    counts = post_profile.get('status_counts', {}) or {}
    remaining_repairs = len(post_profile.get('repair_suggestions', []) or [])
    requested_slot = str(payload.get('requested_slot') or '').strip()
    if requested_slot and not counts:
        slot = (post_profile.get('slots') or {}).get(requested_slot, {})
        slot_status = str(slot.get('status') or 'unknown')
        counts = {
            'stable': 1 if slot_status == 'stable' else 0,
            'contended': 1 if slot_status == 'contended' else 0,
            'needs_review': 1 if slot_status == 'needs_review' else 0,
        }
    return (
        f"canonical repair execute-safe: applied={stats.get('applied', 0)}/{stats.get('total', 0)} "
        f"report_only={stats.get('report_only', 0)} hard_fail={stats.get('hard_fail', 0)} | "
        f"post={post_profile.get('status', 'unknown')} stable={counts.get('stable', 0)} "
        f"contended={counts.get('contended', 0)} review={counts.get('needs_review', 0)} "
        f"remaining_repairs={remaining_repairs}"
    )


def execute_safe(memory: Memory, only_slot: str = '') -> Dict[str, Any]:
    pre_profile = _filter_profile(memory.user_canonical_profile(), only_slot)
    slots = pre_profile.get('slots', {})
    actions: List[Dict[str, Any]] = []

    for slot_name, slot in slots.items():
        for suggestion in slot.get('repair_suggestions', []) or []:
            action = str(suggestion.get('action') or '')
            if action == 'review_runner_up':
                winner_id = suggestion.get('winner_memory_id')
                runner_up_id = suggestion.get('runner_up_memory_id')
                if winner_id and runner_up_id:
                    result = _safe_supersede(memory, int(winner_id), int(runner_up_id))
                else:
                    result = {'success': False, 'reason': 'missing_ids'}
            else:
                result = {'success': False, 'reason': 'report_only'}
            actions.append({
                'slot': slot_name,
                'suggestion': suggestion,
                'result': result,
            })

    post_profile = _filter_profile(memory.user_canonical_profile(), only_slot)
    stats = _build_action_stats(actions)
    payload = {
        'executed_at': datetime.now().astimezone().isoformat(timespec='seconds'),
        'requested_slot': only_slot or '',
        'pre_profile': pre_profile,
        'actions': actions,
        'stats': stats,
        'post_profile': post_profile,
    }
    payload['summary'] = _build_execute_summary(payload)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_path = REPORT_DIR / f'canonical_repair_tool_{stamp}.json'
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    payload['report_path'] = str(report_path)
    return payload


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    memory = Memory()

    if args.command == 'inspect':
        payload = inspect(memory, only_slot=args.only_slot)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload.get('summary', ''))
        return 0

    if args.command == 'execute-safe':
        payload = execute_safe(memory, only_slot=args.only_slot)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(payload.get('summary', ''))
            print(f"report: {payload.get('report_path', '')}")
        failures = [
            action for action in payload.get('actions', [])
            if not action.get('result', {}).get('success') and action.get('result', {}).get('reason') != 'report_only'
        ]
        return 1 if failures else 0

    parser.print_help()
    return 1
