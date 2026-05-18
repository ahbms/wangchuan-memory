#!/usr/bin/env python3
"""
忘川 v3 - Hook 桥接脚本（薄适配器）
被 OpenClaw 的 wangchuan-hook 调用
通过 stdin 接收内容，stdout 输出 JSON

边界说明：
- 本文件属于 Hook / Adapter Layer，只做事件桥接与最小结果整形
- 不在这里承载大业务逻辑；记忆主链优先交给 wangchuan.recall_service
- search 侧当前仍桥接到底层 retrieval 实现，后续可继续收口到语义化入口
- 输出中保留最小 trace 字段，便于后续把执行链做成可验证结构

用法: echo "消息内容" | python3 hook_bridge.py search
      echo "消息内容" | python3 hook_bridge.py extract
      echo "消息内容" | python3 hook_bridge.py ingest
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import logging
import sys
import os
import json
from pathlib import Path

logger = logging.getLogger(__name__)

WORKSPACE_ROOT = _v3_ws_root()
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

DB_PATH = os.environ.get(
    'WANGCHUAN_DB_PATH',
    str(WORKSPACE_ROOT / 'tiangong' / 'wangchuan' / '.index' / 'index.sqlite')
)
DEFAULT_SESSION_ID = os.environ.get('WANGCHUAN_SESSION_ID', '').strip() or os.environ.get('CONVERSATION_SESSION', '').strip() or 'default'
DEFAULT_CHANNEL = os.environ.get('WANGCHUAN_CHANNEL', '').strip() or os.environ.get('CONVERSATION_CHANNEL', '').strip()
DEFAULT_MESSAGE_ID = os.environ.get('WANGCHUAN_MESSAGE_ID', '').strip() or os.environ.get('CONVERSATION_MESSAGE_ID', '').strip()


def _make_trace(action: str) -> dict:
    return {
        'adapter': 'wangchuan_hook_bridge',
        'layer': 'hook_adapter',
        'action': action,
    }


def search(content: str) -> dict:
    """检索相关记忆。"""
    trace = _make_trace('search')
    try:
        # P5-05 延伸：对外 hook 搜索入口优先走统一 memory_api.recall()
        # 让结果默认消费 `memory_schema_index` + sidecar 真值层，而不是继续停留在
        # 仅 HybridRetriever 的旧式 `{content, confidence, type}` 口径。
        from wangchuan.memory_api import Memory

        memory = Memory(db_path=DB_PATH)
        rows = memory.recall(content, limit=3)
        memories = []
        for row in rows:
            memories.append({
                'memory_id': row.get('memory_id'),
                'content': row.get('content', ''),
                'confidence': row.get('score'),
                'type': row.get('memory_type') or row.get('type') or 'unknown',
                'memory_type': row.get('memory_type'),
                'source_layer': row.get('source_layer'),
                'lifecycle': row.get('lifecycle'),
                'promotion_state': row.get('promotion_state'),
                'recall_source_type': row.get('recall_source_type'),
                'schema_version': row.get('schema_version'),
                'reader': row.get('reader') or 'memory_api.recall',
            })

        if memories:
            trace['reader'] = rows[0].get('reader') or 'memory_api.recall'
            trace['structured'] = True
            return {'memories': memories, 'trace': trace}

        from wangchuan.v3.retrieval.hybrid import HybridRetriever
        retriever = HybridRetriever(DB_PATH)
        results = retriever.retrieve(content, top_k=3)

        fallback_memories = []
        for r in results:
            fallback_memories.append({
                'content': f"[{r.node_type}] {r.name}: {r.description}",
                'confidence': r.score,
                'type': r.node_type,
            })

        trace['reader'] = 'hybrid_retriever_fallback'
        trace['structured'] = False
        return {'memories': fallback_memories, 'trace': trace}
    except Exception as e:
        logger.warning("【WangChuan】[HookBridge][Search] failed: %s", e)
        return {'error': str(e), 'memories': [], 'trace': trace}


def extract(content: str) -> dict:
    """从对话中提取事实并存储。"""
    trace = _make_trace('extract')
    try:
        from wangchuan.recall_service import WangchuanPipeline
        pipe = WangchuanPipeline(DB_PATH)

        lines = content.strip().split('\n')
        session_id = DEFAULT_SESSION_ID
        ingested = 0

        for line in lines:
            line = line.strip()
            if line.startswith('用户:') or line.startswith('user:'):
                msg = line.split(':', 1)[1].strip()
                pipe.ingest(session_id, 'user', msg)
                ingested += 1
            elif line.startswith('AI:') or line.startswith('assistant:'):
                msg = line.split(':', 1)[1].strip()
                pipe.ingest(session_id, 'assistant', msg)
                ingested += 1
            elif len(line) > 5:
                pipe.ingest(session_id, 'user', line)
                ingested += 1

        return {
            'extracted': [{'content': '消息已存储', 'type': 'ingest'}],
            'ingested_rows': ingested,
            'trace': trace,
        }
    except Exception as e:
        logger.warning("【WangChuan】[HookBridge][Extract] failed: %s", e)
        return {'error': str(e), 'extracted': [], 'trace': trace}


def ingest(content: str) -> dict:
    """直接摄取消息，支持 assistant:/user:/AI:/用户: 前缀判别角色。"""
    trace = _make_trace('ingest')
    try:
        from wangchuan.recall_service import WangchuanPipeline
        pipe = WangchuanPipeline(DB_PATH)

        session_id = DEFAULT_SESSION_ID
        role = 'user'
        text = content.strip()
        lower = text.lower()

        if lower.startswith('assistant:'):
            role = 'assistant'
            text = text.split(':', 1)[1].strip()
        elif lower.startswith('ai:'):
            role = 'assistant'
            text = text.split(':', 1)[1].strip()
        elif lower.startswith('user:'):
            role = 'user'
            text = text.split(':', 1)[1].strip()
        elif text.startswith('用户:'):
            role = 'user'
            text = text.split(':', 1)[1].strip()

        if not text:
            return {'error': '内容为空', 'trace': trace}

        pipe.ingest(session_id, role, text)
        return {'success': True, 'role': role, 'session_id': session_id, 'channel': DEFAULT_CHANNEL, 'message_id': DEFAULT_MESSAGE_ID, 'trace': trace}
    except Exception as e:
        logger.warning("【WangChuan】[HookBridge][Ingest] failed: %s", e)
        return {'error': str(e), 'trace': trace}


def main():
    if len(sys.argv) < 2:
        print(json.dumps({'error': '需要指定 action: search/extract/ingest'}))
        sys.exit(1)

    action = sys.argv[1]
    content = sys.stdin.read().strip()

    if not content:
        print(json.dumps({'error': '内容为空'}))
        sys.exit(1)

    if action == 'search':
        result = search(content)
    elif action == 'extract':
        result = extract(content)
    elif action == 'ingest':
        result = ingest(content)
    else:
        result = {'error': f'未知 action: {action}', 'trace': _make_trace(action)}

    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
