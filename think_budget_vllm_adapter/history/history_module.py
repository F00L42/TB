"""推理历史模块 — 记录与查询推理历史, 支持商用场景分类过滤。

从 think_budget/history/history_module.py 迁移，调整路径。
"""
import json
import os
import time
import asyncio
import threading
from typing import Dict, List, Optional, Any, Tuple


_HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', 'data', 'think_budget', 'inference_history.jsonl')
_HISTORY_LOCK = threading.Lock()
_history_cache: Optional[List[Dict]] = None

_MAX_HISTORY_ENTRIES = 5000
_MAX_HISTORY_AGE_DAYS = 7
_EVICT_EVERY_N_WRITES = 50
_eviction_counter = 0

_stats_cache: Dict[str, Tuple[Dict, float]] = {}
_STATS_CACHE_TTL = 60.0

_task_type_index: Dict[str, List[int]] = {}
_scene_type_index: Dict[str, List[int]] = {}


def _ensure_dir():
    d = os.path.dirname(_HISTORY_FILE)
    if not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _record_inference_sync(entry: dict):
    """同步写入推理历史到文件。"""
    global _history_cache, _eviction_counter
    with _HISTORY_LOCK:
        _ensure_dir()
        with open(_HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
        if _history_cache is not None:
            idx = len(_history_cache)
            _history_cache.append(entry)
            _update_index_entry(entry, idx)
            _invalidate_stats_cache(entry)
        _eviction_counter += 1
        if _eviction_counter >= _EVICT_EVERY_N_WRITES:
            _eviction_counter = 0
            _evict_old_entries_locked()


def record_inference(question: str, reference: str = '',
                     task_type: str = 'naie',
                     difficulty_score: float = 0.0,
                     budget_params: Optional[Dict] = None,
                     result: Optional[Dict] = None,
                     scene_type: str = ''):
    """记录推理历史。"""
    entry = {
        'timestamp': time.time(),
        'question_hash': hash(question[:200]) % (10**9),
        'question_preview': question[:100],
        'reference_preview': reference[:50] if reference else '',
        'task_type': task_type,
        'scene_type': scene_type or 'general',
        'difficulty_score': difficulty_score,
        'budget_params': budget_params or {},
        'result': result or {},
    }

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        loop.run_in_executor(None, _record_inference_sync, entry)
    else:
        _record_inference_sync(entry)


def load_history() -> List[Dict]:
    """加载历史记录(带缓存和索引构建)。"""
    global _history_cache
    if _history_cache is not None:
        return _history_cache
    entries = []
    if not os.path.exists(_HISTORY_FILE):
        _history_cache = entries
        _rebuild_index()
        return entries
    with _HISTORY_LOCK:
        with open(_HISTORY_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    _history_cache = entries
    _rebuild_index()
    return entries


def get_category_stats(question_preview: str, task_type: str = 'naie',
                       scene_type: str = '', top_k: int = 10) -> Dict[str, float]:
    """获取同类题目的统计信息。"""
    history = load_history()
    if not history:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0,
                'scene_type': scene_type, 'scene_match_count': 0}

    if task_type in _task_type_index:
        candidates = [history[i] for i in _task_type_index[task_type]
                      if i < len(history)]
    else:
        candidates = history

    if not candidates:
        candidates = history

    q_hash = hash(question_preview[:200]) % (10**9)
    scored = []
    for e in candidates:
        sim = 0.0
        eh = e.get('question_hash', 0)
        if eh == q_hash:
            sim = 1.0
        ep = e.get('question_preview', '')
        if ep and question_preview:
            overlap = len(set(ep) & set(question_preview[:100]))
            total = len(set(ep) | set(question_preview[:100]))
            sim = max(sim, overlap / max(total, 1))
        e_scene = e.get('scene_type', '')
        if scene_type and e_scene:
            if e_scene == scene_type:
                sim += 0.3
            else:
                sim -= 0.1
        scored.append((sim, e))

    scored.sort(key=lambda x: x[0], reverse=True)

    if scene_type:
        same_scene = [(s, e) for s, e in scored if e.get('scene_type', '') == scene_type]
        if len(same_scene) >= min(3, top_k):
            top = same_scene[:top_k]
        else:
            top = scored[:top_k]
    else:
        top = scored[:top_k]

    if not top:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0,
                'scene_type': scene_type, 'scene_match_count': 0}

    tokens_list = []
    failures = 0
    scene_match_count = 0
    for sim, e in top:
        res = e.get('result', {})
        tok = res.get('thinking_tokens') or res.get('total_tokens', 0)
        if tok:
            tokens_list.append(tok)
        if res.get('score', 0) == 0:
            failures += 1
        if e.get('scene_type', '') == scene_type:
            scene_match_count += 1

    avg_tok = sum(tokens_list) / len(tokens_list) if tokens_list else 0.0
    fail_rate = failures / len(top) if top else 0.0
    return {
        'avg_tokens': avg_tok,
        'failure_rate': fail_rate,
        'count': len(top),
        'scene_type': scene_type,
        'scene_match_count': scene_match_count,
    }


def get_scene_stats(scene_type: str, task_type: str = 'naie') -> Dict[str, float]:
    """获取指定商用场景的聚合统计。"""
    history = load_history()
    if not history:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0}

    if task_type in _task_type_index:
        candidates = [history[i] for i in _task_type_index[task_type]
                      if i < len(history)]
        candidates = [e for e in candidates if e.get('scene_type', '') == scene_type]
    else:
        candidates = [e for e in history
                      if e.get('task_type', '') == task_type and e.get('scene_type', '') == scene_type]

    if not candidates:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0}

    tokens_list = []
    failures = 0
    for e in candidates:
        res = e.get('result', {})
        tok = res.get('thinking_tokens') or res.get('total_tokens', 0)
        if tok:
            tokens_list.append(tok)
        if res.get('score', 0) == 0:
            failures += 1

    avg_tok = sum(tokens_list) / len(tokens_list) if tokens_list else 0.0
    fail_rate = failures / len(candidates) if candidates else 0.0
    return {
        'avg_tokens': avg_tok,
        'failure_rate': fail_rate,
        'count': len(candidates),
    }


def get_task_type_stats(task_type: str, scene_type: str = '') -> Dict[str, float]:
    """获取指定任务类型的聚合统计(含百分位)。"""
    cache_key = f"{task_type}|{scene_type}"
    now = time.time()
    if cache_key in _stats_cache:
        cached_stats, cached_time = _stats_cache[cache_key]
        if now - cached_time < _STATS_CACHE_TTL:
            return cached_stats

    history = load_history()
    if not history:
        empty = {'count': 0, 'avg_tokens': 0.0, 'p75_tokens': 0.0,
                 'p50_tokens': 0.0, 'max_tokens': 0.0, 'success_rate': 0.0, 'failure_rate': 0.0}
        _stats_cache[cache_key] = (empty, now)
        return empty

    if task_type in _task_type_index:
        candidates = [history[i] for i in _task_type_index[task_type]
                      if i < len(history)]
    else:
        candidates = [e for e in history if e.get('task_type', '') == task_type]

    if not candidates and scene_type:
        if scene_type in _scene_type_index:
            candidates = [history[i] for i in _scene_type_index[scene_type]
                          if i < len(history)]
        else:
            candidates = [e for e in history if e.get('scene_type', '') == scene_type]

    if not candidates:
        empty = {'count': 0, 'avg_tokens': 0.0, 'p75_tokens': 0.0,
                 'p50_tokens': 0.0, 'max_tokens': 0.0, 'success_rate': 0.0, 'failure_rate': 0.0}
        _stats_cache[cache_key] = (empty, now)
        return empty

    tokens_list = []
    successes = 0
    failures = 0
    for e in candidates:
        res = e.get('result', {})
        tok = res.get('thinking_tokens') or res.get('total_tokens', 0)
        if tok and tok > 0:
            tokens_list.append(tok)
        score = res.get('score', 0)
        if score >= 10 or res.get('is_correct', False):
            successes += 1
        elif score == 0 or res.get('is_correct') is False:
            failures += 1

    if not tokens_list:
        result = {'count': len(candidates), 'avg_tokens': 0.0, 'p75_tokens': 0.0,
                  'p50_tokens': 0.0, 'max_tokens': 0.0, 'success_rate': 0.0, 'failure_rate': 0.0}
        _stats_cache[cache_key] = (result, now)
        return result

    tokens_sorted = sorted(tokens_list)
    n = len(tokens_sorted)
    avg_tok = sum(tokens_sorted) / n
    p50_tok = tokens_sorted[int(n * 0.50)]
    p75_tok = tokens_sorted[int(n * 0.75)] if n > 3 else tokens_sorted[-1]
    max_tok = tokens_sorted[-1]

    total_judged = successes + failures
    success_rate = successes / total_judged if total_judged > 0 else 0.0
    failure_rate = failures / total_judged if total_judged > 0 else 0.0

    result = {
        'count': len(candidates),
        'avg_tokens': round(avg_tok, 1),
        'p75_tokens': round(p75_tok, 1),
        'p50_tokens': round(p50_tok, 1),
        'max_tokens': max_tok,
        'success_rate': round(success_rate, 3),
        'failure_rate': round(failure_rate, 3),
    }
    _stats_cache[cache_key] = (result, now)
    return result


def get_cv_for_task_type(task_type: str, scene_type: str = '') -> float:
    """计算指定任务类型历史 token 消耗的变异系数。"""
    history = load_history()
    if not history:
        return 0.0

    if task_type in _task_type_index:
        candidates = [history[i] for i in _task_type_index[task_type]
                      if i < len(history)]
    else:
        candidates = [e for e in history if e.get('task_type', '') == task_type]

    if not candidates and scene_type:
        if scene_type in _scene_type_index:
            candidates = [history[i] for i in _scene_type_index[scene_type]
                          if i < len(history)]
        else:
            candidates = [e for e in history if e.get('scene_type', '') == scene_type]

    tokens_list = []
    for e in candidates:
        res = e.get('result', {})
        tok = res.get('thinking_tokens') or res.get('total_tokens', 0)
        if tok and tok > 0:
            tokens_list.append(tok)

    if len(tokens_list) < 2:
        return 0.0

    avg = sum(tokens_list) / len(tokens_list)
    if avg == 0:
        return 0.0

    variance = sum((t - avg) ** 2 for t in tokens_list) / len(tokens_list)
    std = variance ** 0.5
    return std / avg


def clear_history():
    """清空历史记录。"""
    global _history_cache, _stats_cache, _task_type_index, _scene_type_index
    with _HISTORY_LOCK:
        if os.path.exists(_HISTORY_FILE):
            os.remove(_HISTORY_FILE)
        _history_cache = []
        _stats_cache = {}
        _task_type_index = {}
        _scene_type_index = {}


# ═══════════════════════════════════════════════════════════════
# 内部函数
# ═══════════════════════════════════════════════════════════════

def _rebuild_index():
    global _task_type_index, _scene_type_index
    _task_type_index = {}
    _scene_type_index = {}
    if _history_cache is None:
        return
    for idx, entry in enumerate(_history_cache):
        _update_index_entry(entry, idx)


def _update_index_entry(entry: dict, idx: int):
    tt = entry.get('task_type', '')
    st = entry.get('scene_type', '')
    if tt:
        _task_type_index.setdefault(tt, []).append(idx)
    if st:
        _scene_type_index.setdefault(st, []).append(idx)


def _invalidate_stats_cache(entry: dict):
    tt = entry.get('task_type', '')
    st = entry.get('scene_type', '')
    keys_to_remove = [k for k in _stats_cache if k.startswith(f"{tt}|")]
    for k in keys_to_remove:
        del _stats_cache[k]
    keys_to_remove2 = [k for k in _stats_cache if k.endswith(f"|{st}")]
    for k in keys_to_remove2:
        del _stats_cache[k]


def _evict_old_entries_locked():
    """淘汰过期/过量历史记录。"""
    global _history_cache, _stats_cache, _task_type_index, _scene_type_index

    if _history_cache is None or len(_history_cache) == 0:
        return

    now = time.time()
    cutoff_time = now - _MAX_HISTORY_AGE_DAYS * 86400

    survived = [e for e in _history_cache if e.get('timestamp', now) >= cutoff_time]

    if len(survived) > _MAX_HISTORY_ENTRIES:
        survived = survived[-_MAX_HISTORY_ENTRIES:]

    evicted_count = len(_history_cache) - len(survived)
    if evicted_count > 0:
        print(
            f"TB_HISTORY event=evict evicted={evicted_count} retained={len(survived)}",
            flush=True,
        )
        _history_cache = survived

        try:
            _ensure_dir()
            with open(_HISTORY_FILE, 'w', encoding='utf-8') as f:
                for entry in _history_cache:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
        except Exception as e:
            print(
                f"TB_WARNING event=history_rewrite_failed error={e}",
                flush=True,
            )

        _rebuild_index()
        _stats_cache = {}
