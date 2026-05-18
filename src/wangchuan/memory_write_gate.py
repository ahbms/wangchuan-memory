from __future__ import annotations

"""WangChuan memory write gate helpers.

这一层承接 memory_api 中低风险、纯规则型的写入门控逻辑：
- 空内容拦截
- blocked tag 拦截
- reflection_event + test/runtime noise 拦截
- 文本 pattern 拦截
- 短文本但无稳定记忆信号拦截
- write gate sidelog 读取

约束：
- 不触碰 remember 的数据库写入主链
- 仍由调用方（Memory）提供 tags normalize / bool coerce / 常量配置
"""

from typing import Any, Dict, List
import json
import re

try:
    from wangchuan.paths import state_root
except ImportError:
    from wangchuan.paths import state_root


def read_recent_write_gate_events(limit: int = 120) -> List[Dict[str, Any]]:
    side_path = state_root() / "memory_write_gate.log"
    if not side_path.exists():
        return []
    try:
        lines = side_path.read_text(encoding="utf-8").splitlines()[-limit:]
    except Exception:
        return []
    events: List[Dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events

try:
    from wangchuan.memory_rules import looks_like_reflection_runtime_noise as _looks_like_reflection_runtime_noise
except ImportError:
    from wangchuan.memory_rules import looks_like_reflection_runtime_noise as _looks_like_reflection_runtime_noise


def evaluate_write_gate(memory_obj: Any, content: str, tags: List[str] | None = None,
                        metadata: Dict[str, Any] | None = None) -> Dict[str, Any]:
    text = str(content or '').strip()
    normalized_tags = memory_obj._normalize_tags(tags)
    lowered = text.lower()
    lowered_tags = {t.lower() for t in normalized_tags}
    metadata = dict(metadata or {})
    promotion_reason = str(metadata.get("promotion_reason") or "").strip().lower()
    is_test_data = memory_obj._coerce_bool(metadata.get("is_test_data"))
    explicit_is_test_data = memory_obj._coerce_bool(metadata.get("is_test_data_explicit"))
    if not explicit_is_test_data and "is_test_data" in metadata and not metadata.get("test_data_reason"):
        explicit_is_test_data = memory_obj._coerce_bool(metadata.get("is_test_data"))

    if not text:
        return {
            'allowed': False,
            'reason': 'empty_content',
            'message': '❌ empty memory content',
            'normalized_tags': normalized_tags,
        }

    if any(tag in memory_obj.WRITE_GATE_BLOCK_TAGS for tag in lowered_tags):
        hit = next(tag for tag in lowered_tags if tag in memory_obj.WRITE_GATE_BLOCK_TAGS)
        return {
            'allowed': False,
            'reason': f'blocked_tag:{hit}',
            'message': f'⛔ rejected by MemoryWriteGate: blocked tag {hit}',
            'normalized_tags': normalized_tags,
        }

    if promotion_reason == 'reflection_event' and _looks_like_reflection_runtime_noise(text) and not explicit_is_test_data:
        return {
            'allowed': False,
            'reason': 'blocked_reflection_runtime_noise',
            'message': '⛔ rejected by MemoryWriteGate: reflection_event looks like runtime/wrapper noise',
            'normalized_tags': normalized_tags,
        }

    if promotion_reason == 'reflection_event' and is_test_data:
        return {
            'allowed': False,
            'reason': 'blocked_is_test_data',
            'message': '⛔ rejected by MemoryWriteGate: structured metadata marks this reflection_event as test/runtime noise',
            'normalized_tags': normalized_tags,
        }

    for pattern in memory_obj.WRITE_GATE_BLOCK_PATTERNS:
        if re.search(pattern, lowered, flags=re.IGNORECASE):
            return {
                'allowed': False,
                'reason': f'blocked_pattern:{pattern}',
                'message': '⛔ rejected by MemoryWriteGate: looks like test/demo/cron noise',
                'normalized_tags': normalized_tags,
            }

    is_user_explicit = any(tag in lowered_tags for tag in {'user', 'preference', 'rule', 'lesson', 'memory'})
    contains_allow_hint = any(hint in text for hint in memory_obj.WRITE_GATE_ALLOW_HINTS)
    if not is_user_explicit and not contains_allow_hint and len(text) < 8:
        return {
            'allowed': False,
            'reason': 'too_short_without_memory_signal',
            'message': '⛔ rejected by MemoryWriteGate: too short without stable memory signal',
            'normalized_tags': normalized_tags,
        }

    return {
        'allowed': True,
        'reason': 'allowed',
        'message': '✅ allowed by MemoryWriteGate',
        'normalized_tags': normalized_tags,
    }
