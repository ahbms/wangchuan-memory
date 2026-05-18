#!/usr/bin/env python3
"""
忘川 v3.0 - 信号处理器
处理积压的信号（question/correction/error/completion）
将重要信号转化为知识存入知识图谱
并为进化主链生成结构化 candidate。
"""
from wangchuan.paths import workspace_root as _v3_ws_root

import logging
import sqlite3
import json
import os
import re
import sys
import fcntl
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

try:
    from wangchuan.paths import default_db_path, workspace_root
    WORKSPACE_ROOT = workspace_root()
    DB_PATH = str(default_db_path())
except ImportError:
    WORKSPACE_ROOT = _v3_ws_root()
    if str(WORKSPACE_ROOT) not in sys.path:
        sys.path.insert(0, str(WORKSPACE_ROOT))
    DB_PATH = str(WORKSPACE_ROOT / 'tiangong' / 'wangchuan' / '.index' / 'index.sqlite')
MEMORY_DIR = str(WORKSPACE_ROOT / 'memory')

_NOISE_SIGNAL_PATTERNS = [
    r'^system \(untrusted\):',
    r'^conversation info',
    r'^sender \(untrusted metadata\):',
    r'^\(system async result only\)',
    r'exec completed',
    r'exec failed',
    r'signal sigkill',
    r'an async command you ran earlier has completed',
]


def _normalize_signal_text(signal: Dict) -> str:
    return re.sub(r'\s+', ' ', str(signal.get('extracted_text') or signal.get('content') or '')).strip()


def is_noise_signal(signal: Dict) -> bool:
    text = _normalize_signal_text(signal)
    lowered = text.lower()
    if not lowered:
        return True
    if len(lowered) <= 2:
        return True
    if lowered in {'继续', '好的', 'ok', '1', 'unknown', 'default', 'closed'}:
        return True
    return any(re.search(pattern, lowered, re.IGNORECASE) for pattern in _NOISE_SIGNAL_PATTERNS)


def filter_noise_signals(signals: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    kept, noisy = [], []
    for signal in signals or []:
        (noisy if is_noise_signal(signal) else kept).append(signal)
    return kept, noisy


def get_unprocessed_signals(signal_type: str = None, limit: int = 100) -> List[Dict]:
    """获取未处理的信号"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    
    if signal_type:
        c.execute("""
            SELECT s.id, s.message_id, s.signal_type, s.confidence, 
                   s.extracted_text, s.timestamp, m.content, m.session_id, m.role
            FROM gm_signals s
            LEFT JOIN gm_messages m ON s.message_id = m.id
            WHERE s.processed = 0 AND s.signal_type = ?
            ORDER BY s.timestamp ASC
            LIMIT ?
        """, (signal_type, limit))
    else:
        c.execute("""
            SELECT s.id, s.message_id, s.signal_type, s.confidence,
                   s.extracted_text, s.timestamp, m.content, m.session_id, m.role
            FROM gm_signals s
            LEFT JOIN gm_messages m ON s.message_id = m.id
            WHERE s.processed = 0
            ORDER BY s.timestamp ASC
            LIMIT ?
        """, (limit,))
    
    rows = c.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def mark_processed(signal_ids: List[int]):
    """标记信号为已处理"""
    if not signal_ids:
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    placeholders = ','.join('?' * len(signal_ids))
    c.execute(f"UPDATE gm_signals SET processed = 1 WHERE id IN ({placeholders})", signal_ids)
    conn.commit()
    conn.close()


def process_errors(signals: List[Dict]) -> Dict:
    """处理错误信号 - 提取常见错误模式"""
    errors = []
    for s in signals:
        text = s.get('extracted_text') or s.get('content', '')
        if text:
            errors.append({
                'time': s['timestamp'],
                'text': text[:200],
                'confidence': s['confidence']
            })
    
    # 按相似度聚类（简化版：按关键词）
    error_patterns = defaultdict(list)
    for e in errors:
        # 提取关键词
        text = e['text'].lower()
        if 'attributeerror' in text:
            error_patterns['AttributeError'].append(e)
        elif 'import' in text or 'modulenotfound' in text:
            error_patterns['ImportError'].append(e)
        elif 'permission' in text or 'denied' in text:
            error_patterns['PermissionError'].append(e)
        elif 'timeout' in text:
            error_patterns['TimeoutError'].append(e)
        elif 'json' in text or 'parse' in text:
            error_patterns['JSON/ParseError'].append(e)
        else:
            error_patterns['Other'].append(e)
    
    return {
        'total': len(errors),
        'patterns': {k: len(v) for k, v in error_patterns.items()},
        'recent': errors[-5:] if errors else []
    }


def process_corrections(signals: List[Dict]) -> Dict:
    """处理纠错信号 - 提取被纠正的知识"""
    corrections = []
    for s in signals:
        text = s.get('extracted_text') or s.get('content', '')
        if text:
            corrections.append({
                'time': s['timestamp'],
                'text': text[:300],
                'confidence': s['confidence']
            })
    
    return {
        'total': len(corrections),
        'recent': corrections[-5:] if corrections else []
    }


def process_questions(signals: List[Dict]) -> Dict:
    """处理问题信号 - 识别高频问题类型"""
    questions = []
    for s in signals:
        text = s.get('extracted_text') or s.get('content', '')
        if text:
            questions.append({
                'time': s['timestamp'],
                'text': text[:200],
                'session': s.get('session_id', 'unknown')
            })
    
    # 按会话分组统计
    session_counts = defaultdict(int)
    for q in questions:
        session_counts[q['session']] += 1
    
    return {
        'total': len(questions),
        'top_sessions': dict(sorted(session_counts.items(), key=lambda x: -x[1])[:5]),
        'recent': questions[-5:] if questions else []
    }


def process_completions(signals: List[Dict]) -> Dict:
    """处理完成信号 - 记录重要完成事件"""
    completions = []
    for s in signals:
        text = s.get('extracted_text') or s.get('content', '')
        if text:
            completions.append({
                'time': s['timestamp'],
                'text': text[:200],
                'confidence': s['confidence']
            })
    
    return {
        'total': len(completions),
        'recent': completions[-5:] if completions else []
    }


def generate_summary() -> Dict:
    """生成信号汇总报告"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # 各类型统计
    c.execute("SELECT signal_type, COUNT(*) FROM gm_signals WHERE processed = 0 GROUP BY signal_type")
    pending = dict(c.fetchall())
    
    c.execute("SELECT signal_type, COUNT(*) FROM gm_signals WHERE processed = 1 GROUP BY signal_type")
    done = dict(c.fetchall())
    
    # 时间范围
    c.execute("SELECT MIN(timestamp), MAX(timestamp) FROM gm_signals WHERE processed = 0")
    time_range = c.fetchone()
    
    conn.close()
    
    return {
        'pending': pending,
        'processed': done,
        'pending_total': sum(pending.values()),
        'processed_total': sum(done.values()),
        'oldest_pending': time_range[0],
        'newest_pending': time_range[1]
    }


def build_evolution_candidates(limit: int = 50) -> List[Dict]:
    """把当前信号批次转成结构化进化候选。"""
    try:
        TrajectoryReflector = None  # optional Tiangong evolution component unavailable in standalone

        reflector = TrajectoryReflector(db_path=DB_PATH)
        return reflector.build_candidates(limit=limit)
    except Exception as e:
        logger.warning("【WangChuan】[SignalProcessor][Candidate] build failed: %s", e)
        return []


def build_evolution_candidates_from_signals(signals: List[Dict], limit: int = 50) -> List[Dict]:
    """仅基于当前批次非噪音 signal 生成 evolution candidates。"""
    try:
        TrajectoryReflector = None  # optional Tiangong evolution component unavailable in standalone

        reflector = TrajectoryReflector(db_path=DB_PATH)
        decision_hints = reflector.collect_decision_log_hints(limit=100)
        seen = set()
        candidates: List[Dict] = []
        for signal in signals or []:
            if len(candidates) >= limit:
                break
            if signal.get('signal_type') not in ('error', 'correction', 'completion'):
                continue
            content = _normalize_signal_text(signal)
            if not content:
                continue
            trace = {
                'signal_id': signal.get('id'),
                'message_id': signal.get('message_id'),
                'signal_type': signal.get('signal_type', ''),
                'confidence': float(signal.get('confidence') or 0.0),
                'content': content,
                'timestamp': signal.get('timestamp', ''),
                'session_id': signal.get('session_id', ''),
                'role': signal.get('role', ''),
            }
            inferred = reflector.infer_root_causes(trace, decision_hints=decision_hints)
            candidate = reflector.build_candidate(trace, inferred).to_dict()
            if candidate['candidate_id'] in seen:
                continue
            seen.add(candidate['candidate_id'])
            candidates.append(candidate)
        return candidates
    except Exception as e:
        logger.warning("【WangChuan】[SignalProcessor][Candidate] batch build failed: %s", e)
        return []


def run_processing(batch_size: int = 200) -> Dict:
    """运行信号处理（带文件锁防并发）"""
    lock_path = Path(MEMORY_DIR) / '.signal_processor.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(lock_path, 'w') as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            logger.warning("【WangChuan】[SignalProcessor] 另一个实例正在运行，跳过")
            return {'status': 'skipped', 'reason': 'another instance running'}
        
        try:
            return _run_processing_inner(batch_size)
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def _run_processing_inner(batch_size: int = 200) -> Dict:
    """实际信号处理逻辑"""
    logger.info("【WangChuan】[SignalProcessor] start")
    summary = generate_summary()
    logger.info("【WangChuan】[SignalProcessor] summary pending=%s processed=%s range=%s ~ %s",
                summary['pending_total'], summary['processed_total'], summary['oldest_pending'], summary['newest_pending'])
    
    results = {}
    all_processed_ids = []
    candidate_source_signals: List[Dict] = []
    noise_report = {
        'filtered_total': 0,
        'kept_total': 0,
        'by_type': {},
        'examples': [],
    }
    
    # 1. 处理 errors
    logger.info("【WangChuan】[SignalProcessor][Error] processing")
    errors_raw = get_unprocessed_signals('error', batch_size)
    errors, errors_noisy = filter_noise_signals(errors_raw)
    noise_report['filtered_total'] += len(errors_noisy)
    noise_report['kept_total'] += len(errors)
    noise_report['by_type']['error'] = len(errors_noisy)
    noise_report['examples'].extend([_normalize_signal_text(s)[:120] for s in errors_noisy[:2]])
    error_result = process_errors(errors)
    results['errors'] = error_result
    logger.info("【WangChuan】[SignalProcessor][Error] found=%s noisy=%s", error_result['total'], len(errors_noisy))
    for pattern, count in error_result['patterns'].items():
        logger.info("【WangChuan】[SignalProcessor][Error] pattern=%s count=%s", pattern, count)
    candidate_source_signals.extend(errors)
    all_processed_ids.extend([e['id'] for e in errors + errors_noisy])
    
    # 2. 处理 corrections
    logger.info("【WangChuan】[SignalProcessor][Correction] processing")
    corrections_raw = get_unprocessed_signals('correction', batch_size)
    corrections, corrections_noisy = filter_noise_signals(corrections_raw)
    noise_report['filtered_total'] += len(corrections_noisy)
    noise_report['kept_total'] += len(corrections)
    noise_report['by_type']['correction'] = len(corrections_noisy)
    noise_report['examples'].extend([_normalize_signal_text(s)[:120] for s in corrections_noisy[:2]])
    correction_result = process_corrections(corrections)
    results['corrections'] = correction_result
    logger.info("【WangChuan】[SignalProcessor][Correction] found=%s noisy=%s", correction_result['total'], len(corrections_noisy))
    candidate_source_signals.extend(corrections)
    all_processed_ids.extend([c['id'] for c in corrections + corrections_noisy])
    
    # 3. 处理 questions
    logger.info("【WangChuan】[SignalProcessor][Question] processing")
    questions_raw = get_unprocessed_signals('question', batch_size)
    questions, questions_noisy = filter_noise_signals(questions_raw)
    noise_report['filtered_total'] += len(questions_noisy)
    noise_report['kept_total'] += len(questions)
    noise_report['by_type']['question'] = len(questions_noisy)
    noise_report['examples'].extend([_normalize_signal_text(s)[:120] for s in questions_noisy[:2]])
    question_result = process_questions(questions)
    results['questions'] = question_result
    logger.info("【WangChuan】[SignalProcessor][Question] found=%s noisy=%s", question_result['total'], len(questions_noisy))
    all_processed_ids.extend([q['id'] for q in questions + questions_noisy])
    
    # 4. 处理 completions
    logger.info("【WangChuan】[SignalProcessor][Completion] processing")
    completions_raw = get_unprocessed_signals('completion', batch_size)
    completions, completions_noisy = filter_noise_signals(completions_raw)
    noise_report['filtered_total'] += len(completions_noisy)
    noise_report['kept_total'] += len(completions)
    noise_report['by_type']['completion'] = len(completions_noisy)
    noise_report['examples'].extend([_normalize_signal_text(s)[:120] for s in completions_noisy[:2]])
    completion_result = process_completions(completions)
    results['completions'] = completion_result
    logger.info("【WangChuan】[SignalProcessor][Completion] found=%s noisy=%s", completion_result['total'], len(completions_noisy))
    candidate_source_signals.extend(completions)
    all_processed_ids.extend([c['id'] for c in completions + completions_noisy])
    
    # 标记已处理
    logger.info("【WangChuan】[SignalProcessor] mark_processed=%s", len(all_processed_ids))
    mark_processed(all_processed_ids)
    
    # 保存报告
    report = {
        'timestamp': datetime.now().isoformat(),
        'summary': summary,
        'details': results,
        'noise_filter': {
            'filtered_total': noise_report['filtered_total'],
            'kept_total': noise_report['kept_total'],
            'by_type': noise_report['by_type'],
            'examples': noise_report['examples'][:6],
        },
        'processed_count': len(all_processed_ids)
    }

    candidates = build_evolution_candidates_from_signals(candidate_source_signals, limit=min(batch_size, 50))
    report['evolution_candidates'] = candidates
    report['candidate_count'] = len(candidates)
    if candidates:
        logger.info("【WangChuan】[SignalProcessor][Candidate] generated=%s", len(candidates))
    
    report_path = f"{MEMORY_DIR}/signal_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs(MEMORY_DIR, exist_ok=True)
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    logger.info("【WangChuan】[SignalProcessor] report=%s processed=%s", report_path, len(all_processed_ids))
    
    return report


if __name__ == '__main__':
    import sys
    import argparse

    parser = argparse.ArgumentParser(description='忘川 v3.0 信号处理器')
    parser.add_argument('batch', nargs='?', type=int, default=200, help='每类信号处理上限，默认 200')
    args = parser.parse_args()
    run_processing(args.batch)
