"""推理历史模块 — 记录与查询推理历史, 支持商用场景分类过滤。

v4 改进:
  - 滑动窗口: 保留最近N条或最近7天记录, 自动淘汰过期/过量历史
  - 聚合统计缓存: 按 task_type 预计算统计(count/avg/p75/success_rate等), 60秒TTL
  - 内存聚合索引: task_type→[indices] 映射, 查询时只遍历相关子集
  - record_inference 支持 async 上下文: 自动检测事件循环，将文件 I/O
    卸载到线程池，避免阻塞协程
  - 淘汰触发: 每N次写入执行一次淘汰, 避免每次写入都全量扫描
"""
import json
import os
import time
import asyncio
import threading
import logging
from typing import Dict, List, Optional, Any, Tuple

logger = logging.getLogger(__name__)

_HISTORY_FILE = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'naie', 'inference_history.jsonl')
_HISTORY_LOCK = threading.Lock()
_history_cache: Optional[List[Dict]] = None

# ─── 滑动窗口参数 ───
_MAX_HISTORY_ENTRIES = 5000   # 最多保留5000条记录
_MAX_HISTORY_AGE_DAYS = 7     # 超过7天的记录自动淘汰
_EVICT_EVERY_N_WRITES = 50   # 每写入50条触发一次淘汰
_eviction_counter = 0         # 写入计数器

# ─── 聚合统计缓存 ───
_stats_cache: Dict[str, Tuple[Dict, float]] = {}  # key="task_type|scene_type", value=(stats, timestamp)
_STATS_CACHE_TTL = 60.0  # 缓存60秒

# ─── 内存聚合索引 ───
_task_type_index: Dict[str, List[int]] = {}  # task_type → [indices in _history_cache]
_scene_type_index: Dict[str, List[int]] = {}  # scene_type → [indices in _history_cache]


def _ensure_dir():
    d = os.path.dirname(_HISTORY_FILE)
    if not os.path.exists(d):
        os.makedirs(d, exist_ok=True)


def _record_inference_sync(entry: dict):
    """同步写入推理历史到文件 — 供线程池或同步上下文调用。"""
    global _history_cache, _eviction_counter
    with _HISTORY_LOCK:
        _ensure_dir()
        with open(_HISTORY_FILE, 'a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
        # cache 修改在锁内，消除数据竞争
        if _history_cache is not None:
            idx = len(_history_cache)
            _history_cache.append(entry)
            # 更新索引
            _update_index_entry(entry, idx)
            # 使统计缓存失效
            _invalidate_stats_cache(entry)
        # 淘汰计数
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
    """记录推理历史, 增加 scene_type 字段。

    自动检测调用上下文:
      - 如果在异步事件循环中调用，将文件 I/O 卸载到线程池，避免阻塞协程
      - 如果在同步上下文中调用，直接执行
    """
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

    # 检测是否在异步事件循环中
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # 异步上下文 — 卸载到线程池，不阻塞事件循环
        loop.run_in_executor(None, _record_inference_sync, entry)
    else:
        # 同步上下文 — 直接执行
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
    """获取同类题目的统计信息, 支持商用场景分类过滤。

    过滤策略:
      1. 优先按 scene_type 精确匹配(同商用场景)
      2. scene_type 匹配后, 再按文本相似度排序
      3. 如果 scene_type 匹配结果不足, 回退到全局相似度
      4. 兼容旧数据(scene_type 为空)

    Returns:
        {'avg_tokens': float, 'failure_rate': float, 'count': int,
         'scene_type': str, 'scene_match_count': int}
    """
    history = load_history()
    if not history:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0,
                'scene_type': scene_type, 'scene_match_count': 0}

    # Step 1: 使用索引按 task_type 过滤(比全量遍历快)
    if task_type in _task_type_index:
        candidates = [history[i] for i in _task_type_index[task_type]
                      if i < len(history)]
    else:
        candidates = history

    if not candidates:
        candidates = history

    # Step 2: 计算 scene + text 双重相似度
    q_hash = hash(question_preview[:200]) % (10**9)
    scored = []
    for e in candidates:
        sim = 0.0

        # 文本相似度 (原有逻辑)
        eh = e.get('question_hash', 0)
        if eh == q_hash:
            sim = 1.0
        ep = e.get('question_preview', '')
        if ep and question_preview:
            overlap = len(set(ep) & set(question_preview[:100]))
            total = len(set(ep) | set(question_preview[:100]))
            sim = max(sim, overlap / max(total, 1))

        # 场景匹配加分: 同场景 +0.3, 不同场景 -0.1
        e_scene = e.get('scene_type', '')
        if scene_type and e_scene:
            if e_scene == scene_type:
                sim += 0.3
            else:
                sim -= 0.1

        scored.append((sim, e))

    scored.sort(key=lambda x: x[0], reverse=True)

    # Step 3: 如果有 scene_type 筛选, 尝试优先取同场景的
    if scene_type:
        same_scene = [(s, e) for s, e in scored if e.get('scene_type', '') == scene_type]
        if len(same_scene) >= min(3, top_k):
            # 同场景数据足够: 用同场景数据
            top = same_scene[:top_k]
        else:
            # 同场景数据不足: 混合使用, 但同场景的排前面
            top = scored[:top_k]
            scene_match_count = len(same_scene)
    else:
        top = scored[:top_k]

    if not top:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0,
                'scene_type': scene_type, 'scene_match_count': 0}

    # Step 4: 计算统计
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
    """获取指定商用场景的聚合统计(不依赖单条问题文本)。

    用于冷启动时提供场景级先验。
    使用索引加速，只遍历相关记录。
    """
    history = load_history()
    if not history:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0}

    # 使用 task_type 索引缩小遍历范围
    if task_type in _task_type_index:
        candidates = [history[i] for i in _task_type_index[task_type]
                      if i < len(history)]
        # 再按 scene_type 过滤
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
    """获取指定任务类型的聚合统计(含百分位) — 供 history_budget.py 调用。

    使用聚合缓存加速: 60秒TTL内直接返回缓存结果。
    使用索引缩小遍历范围: 只遍历 task_type 匹配的记录。

    Args:
        task_type: 任务类型
        scene_type: 场景类型(可选，用于回退过滤)

    Returns:
        {
            'count': int,
            'avg_tokens': float,
            'p75_tokens': float,
            'p50_tokens': float,
            'max_tokens': float,
            'success_rate': float,
            'failure_rate': float,
        }
    """
    # 检查缓存
    cache_key = f"{task_type}|{scene_type}"
    now = time.time()
    if cache_key in _stats_cache:
        cached_stats, cached_time = _stats_cache[cache_key]
        if now - cached_time < _STATS_CACHE_TTL:
            return cached_stats

    # 缓存未命中 — 计算
    history = load_history()
    if not history:
        empty = {'count': 0, 'avg_tokens': 0.0, 'p75_tokens': 0.0,
                 'p50_tokens': 0.0, 'max_tokens': 0.0, 'success_rate': 0.0, 'failure_rate': 0.0}
        _stats_cache[cache_key] = (empty, now)
        return empty

    # 使用索引过滤
    if task_type in _task_type_index:
        candidates = [history[i] for i in _task_type_index[task_type]
                      if i < len(history)]
    else:
        candidates = [e for e in history if e.get('task_type', '') == task_type]

    if not candidates and scene_type:
        # 回退到 scene_type
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

    # 收集 token 消耗和成功/失败
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
    """计算指定任务类型历史 token 消耗的变异系数 (CV = std/mean)。

    使用索引缩小遍历范围，避免全量扫描。
    无历史或仅1条历史时返回 0.0 (无法计算方差)。
    """
    history = load_history()
    if not history:
        return 0.0

    # 使用索引过滤
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
# 内部函数 — 索引管理
# ═══════════════════════════════════════════════════════════════

def _rebuild_index():
    """重建内存聚合索引 — 遍历 _history_cache 一次，构建 task_type→[indices] 映射。"""
    global _task_type_index, _scene_type_index
    _task_type_index = {}
    _scene_type_index = {}
    if _history_cache is None:
        return
    for idx, entry in enumerate(_history_cache):
        tt = entry.get('task_type', '')
        st = entry.get('scene_type', '')
        if tt:
            _task_type_index.setdefault(tt, []).append(idx)
        if st:
            _scene_type_index.setdefault(st, []).append(idx)


def _update_index_entry(entry: dict, idx: int):
    """追加单条记录后更新索引（增量更新，无需重建）。"""
    tt = entry.get('task_type', '')
    st = entry.get('scene_type', '')
    if tt:
        _task_type_index.setdefault(tt, []).append(idx)
    if st:
        _scene_type_index.setdefault(st, []).append(idx)


def _invalidate_stats_cache(entry: dict):
    """写入记录后使相关统计缓存失效。"""
    tt = entry.get('task_type', '')
    st = entry.get('scene_type', '')
    # 删除以该 task_type 为 key 的缓存
    keys_to_remove = [k for k in _stats_cache if k.startswith(f"{tt}|")]
    for k in keys_to_remove:
        del _stats_cache[k]
    # 删除以该 scene_type 为 key 的缓存
    keys_to_remove2 = [k for k in _stats_cache if k.endswith(f"|{st}")]
    for k in keys_to_remove2:
        del _stats_cache[k]


# ═══════════════════════════════════════════════════════════════
# 内部函数 — 滑动窗口淘汰
# ═══════════════════════════════════════════════════════════════

def _evict_old_entries_locked():
    """淘汰过期/过量历史记录 — 必须在 _HISTORY_LOCK 内调用。

    策略:
      1. 删除超过 _MAX_HISTORY_AGE_DAYS 天的记录
      2. 如果仍超过 _MAX_HISTORY_ENTRIES 条，删除最旧的记录直到限额内
      3. 执行后同步更新 _history_cache 和磁盘文件(重写JSONL)
      4. 重建索引，清空统计缓存
    """
    global _history_cache, _stats_cache, _task_type_index, _scene_type_index
    # _HISTORY_LOCK 必须已由调用方持有

    if _history_cache is None or len(_history_cache) == 0:
        return

    now = time.time()
    cutoff_time = now - _MAX_HISTORY_AGE_DAYS * 86400

    # 阶段1: 按时间淘汰
    survived = [e for e in _history_cache if e.get('timestamp', now) >= cutoff_time]

    # 阶段2: 按数量淘汰（如果仍超过限额，保留最新的N条）
    if len(survived) > _MAX_HISTORY_ENTRIES:
        survived = survived[-_MAX_HISTORY_ENTRIES:]

    evicted_count = len(_history_cache) - len(survived)
    if evicted_count > 0:
        logger.info(f"历史淘汰: {evicted_count}条 (时间过期+数量超限), 保留{len(survived)}条")
        _history_cache = survived

        # 重写磁盘文件
        try:
            _ensure_dir()
            with open(_HISTORY_FILE, 'w', encoding='utf-8') as f:
                for entry in _history_cache:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + '\n')
        except Exception as e:
            logger.warning(f"历史淘汰后重写文件失败: {e}")

        # 重建索引
        _rebuild_index()
        # 清空统计缓存（数据已变化）
        _stats_cache = {}
