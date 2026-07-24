"""Online history feedback with non-blocking, idempotent JSONL persistence."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


_HISTORY_FILE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "think_budget",
    "inference_history.jsonl",
)
_HISTORY_LOCK = threading.RLock()
_history_cache: Optional[List[Dict[str, Any]]] = None
_task_type_index: Dict[str, List[int]] = {}
_scene_type_index: Dict[str, List[int]] = {}
_parent_request_ids: set[str] = set()
_stats_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

_MAX_HISTORY_ENTRIES = 5000
_MAX_HISTORY_AGE_DAYS = 7
_EVICT_EVERY_N_WRITES = 50
_STATS_CACHE_TTL = 60.0
_persisted_write_count = 0


def _stable_question_hash(text: str) -> str:
    return hashlib.sha256(text[:200].encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class HistoryOutcome:
    parent_request_id: str
    timestamp: float
    question_hash: str
    question_preview: str
    task_type: str
    scene_type: str
    decision_branch: str
    difficulty: float | None
    budget_plan: Dict[str, Any]
    thinking_tokens: int
    hidden_probe_tokens: int
    final_answer_tokens: int
    total_generated_tokens: int
    elapsed_ms: float
    completion_status: str
    quality_score: float | None = None
    quality_observed: bool = False
    token_data_complete: bool = True

    @classmethod
    def from_request(
        cls,
        *,
        parent_request_id: str,
        question: str,
        task_type: str,
        scene_type: str,
        decision_branch: str,
        difficulty: float | None,
        budget_plan: Dict[str, Any],
        thinking_tokens: int,
        hidden_probe_tokens: int,
        final_answer_tokens: int,
        elapsed_ms: float,
        completion_status: str,
    ) -> "HistoryOutcome":
        return cls(
            parent_request_id=parent_request_id,
            timestamp=time.time(),
            question_hash=_stable_question_hash(question),
            question_preview=question[:100],
            task_type=task_type,
            scene_type=scene_type or "general",
            decision_branch=decision_branch,
            difficulty=difficulty,
            budget_plan=budget_plan,
            thinking_tokens=max(0, thinking_tokens),
            hidden_probe_tokens=max(0, hidden_probe_tokens),
            final_answer_tokens=max(0, final_answer_tokens),
            total_generated_tokens=max(
                0,
                thinking_tokens + hidden_probe_tokens + final_answer_tokens,
            ),
            elapsed_ms=max(0.0, elapsed_ms),
            completion_status=completion_status,
            quality_score=None,
            quality_observed=False,
            token_data_complete=(completion_status == "success"),
        )

    def to_entry(self) -> Dict[str, Any]:
        values = asdict(self)
        values["difficulty_score"] = values.pop("difficulty")
        values["result"] = {
            "thinking_tokens": values.pop("thinking_tokens"),
            "hidden_probe_tokens": values.pop("hidden_probe_tokens"),
            "final_answer_tokens": values.pop("final_answer_tokens"),
            "total_generated_tokens": values.pop("total_generated_tokens"),
            "elapsed_ms": values.pop("elapsed_ms"),
            "completion_status": values.pop("completion_status"),
            "quality_score": values.pop("quality_score"),
            "quality_observed": values.pop("quality_observed"),
            "token_data_complete": values.pop("token_data_complete"),
        }
        values["_persisted"] = False
        return values


def _ensure_dir() -> None:
    os.makedirs(os.path.dirname(_HISTORY_FILE), exist_ok=True)


def _serializable_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in entry.items() if key != "_persisted"}


def _update_index_entry(entry: Dict[str, Any], index: int) -> None:
    task_type = entry.get("task_type", "")
    scene_type = entry.get("scene_type", "")
    if task_type:
        _task_type_index.setdefault(task_type, []).append(index)
    if scene_type:
        _scene_type_index.setdefault(scene_type, []).append(index)


def _rebuild_index_locked() -> None:
    _task_type_index.clear()
    _scene_type_index.clear()
    _parent_request_ids.clear()
    for index, entry in enumerate(_history_cache or []):
        _update_index_entry(entry, index)
        parent_request_id = entry.get("parent_request_id")
        if parent_request_id:
            _parent_request_ids.add(parent_request_id)


def _invalidate_stats_cache(entry: Dict[str, Any]) -> None:
    task_type = entry.get("task_type", "")
    scene_type = entry.get("scene_type", "")
    for key in list(_stats_cache):
        if key.startswith(f"{task_type}|") or key.endswith(f"|{scene_type}"):
            _stats_cache.pop(key, None)


def load_history() -> List[Dict[str, Any]]:
    global _history_cache
    if _history_cache is not None:
        return _history_cache
    entries: List[Dict[str, Any]] = []
    with _HISTORY_LOCK:
        if _history_cache is not None:
            return _history_cache
        if os.path.exists(_HISTORY_FILE):
            try:
                with open(
                    _HISTORY_FILE, "r", encoding="utf-8"
                ) as stream:
                    for line in stream:
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        entry["_persisted"] = True
                        entries.append(entry)
            except OSError as exc:
                print(
                    "TB_WARNING event=history_load_failed "
                    f"path={_HISTORY_FILE} error={exc}",
                    flush=True,
                )
        _history_cache = entries
        _rebuild_index_locked()
        return _history_cache


def _cache_entry(entry: Dict[str, Any]) -> bool:
    load_history()
    with _HISTORY_LOCK:
        parent_request_id = entry.get("parent_request_id")
        if parent_request_id and parent_request_id in _parent_request_ids:
            return False
        index = len(_history_cache or [])
        assert _history_cache is not None
        _history_cache.append(entry)
        _update_index_entry(entry, index)
        if parent_request_id:
            _parent_request_ids.add(parent_request_id)
        _invalidate_stats_cache(entry)
        return True


def _append_entry_sync(entry: Dict[str, Any]) -> None:
    global _persisted_write_count
    with _HISTORY_LOCK:
        _ensure_dir()
        with open(_HISTORY_FILE, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(
                _serializable_entry(entry),
                ensure_ascii=False,
                default=str,
            ) + "\n")
        entry["_persisted"] = True
        _persisted_write_count += 1
        if _persisted_write_count % _EVICT_EVERY_N_WRITES == 0:
            _evict_old_entries_locked()


def _evict_old_entries_locked() -> None:
    global _history_cache
    if not _history_cache:
        return
    cutoff = time.time() - (_MAX_HISTORY_AGE_DAYS * 86400)
    persisted = [
        entry for entry in _history_cache
        if entry.get("_persisted")
        and float(entry.get("timestamp", time.time())) >= cutoff
    ]
    unpersisted = [
        entry for entry in _history_cache if not entry.get("_persisted")
    ]
    if len(persisted) > _MAX_HISTORY_ENTRIES:
        persisted = persisted[-_MAX_HISTORY_ENTRIES:]
    survivors = [*persisted, *unpersisted]
    if len(survivors) == len(_history_cache):
        return
    _history_cache = survivors
    _ensure_dir()
    with open(_HISTORY_FILE, "w", encoding="utf-8") as stream:
        for entry in persisted:
            stream.write(json.dumps(
                _serializable_entry(entry),
                ensure_ascii=False,
                default=str,
            ) + "\n")
    _rebuild_index_locked()
    _stats_cache.clear()


class AsyncHistoryRecorder:
    """Small per-serving-instance queue; record submission never awaits I/O."""

    def __init__(self, max_queue_size: int = 1024) -> None:
        # Load once while the serving controller is constructed so the first
        # request never pays JSONL scan cost on its completion path.
        load_history()
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(
            maxsize=max_queue_size)
        self._worker_task: asyncio.Task | None = None
        self.last_error: str | None = None

    def record_outcome(self, outcome: HistoryOutcome) -> bool:
        entry = outcome.to_entry()
        if not _cache_entry(entry):
            return False
        try:
            loop = asyncio.get_running_loop()
            self._queue.put_nowait(entry)
            if self._worker_task is None or self._worker_task.done():
                self._worker_task = loop.create_task(self._drain())
            return True
        except (RuntimeError, asyncio.QueueFull) as exc:
            self.last_error = str(exc)
            print(
                "TB_WARNING event=history_enqueue_failed "
                f"request_id={outcome.parent_request_id} error={exc}",
                flush=True,
            )
            return False

    async def _drain(self) -> None:
        try:
            while True:
                try:
                    entry = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    await asyncio.to_thread(_append_entry_sync, entry)
                except Exception as exc:
                    self.last_error = str(exc)
                    print(
                        "TB_WARNING event=history_write_failed "
                        f"request_id={entry.get('parent_request_id')} error={exc}",
                        flush=True,
                    )
                finally:
                    self._queue.task_done()
        finally:
            self._worker_task = None
            if not self._queue.empty():
                self._worker_task = asyncio.create_task(self._drain())

    async def flush(self, timeout_s: float = 2.0) -> bool:
        try:
            await asyncio.wait_for(self._queue.join(), timeout=timeout_s)
            task = self._worker_task
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False


def record_inference(
    question: str,
    reference: str = "",
    task_type: str = "naie",
    difficulty_score: float = 0.0,
    budget_params: Optional[Dict[str, Any]] = None,
    result: Optional[Dict[str, Any]] = None,
    scene_type: str = "",
) -> None:
    """Compatibility entrypoint for offline evaluation writers."""
    result = dict(result or {})
    quality_observed = (
        "score" in result or "is_correct" in result
    )
    entry = {
        "timestamp": time.time(),
        "question_hash": _stable_question_hash(question),
        "question_preview": question[:100],
        "reference_preview": reference[:50] if reference else "",
        "task_type": task_type,
        "scene_type": scene_type or "general",
        "difficulty_score": difficulty_score,
        "budget_plan": budget_params or {},
        "result": {
            **result,
            "completion_status": result.get("completion_status", "success"),
            "quality_observed": quality_observed,
            "quality_score": result.get("score"),
            "token_data_complete": result.get("token_data_complete", True),
        },
        "_persisted": False,
    }
    if not _cache_entry(entry):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        loop.run_in_executor(None, _append_entry_sync, entry)
    else:
        _append_entry_sync(entry)


def _candidate_entries(
    task_type: str,
    scene_type: str = "",
) -> List[Dict[str, Any]]:
    history = load_history()
    if task_type in _task_type_index:
        candidates = [
            history[index]
            for index in _task_type_index[task_type]
            if index < len(history)
        ]
    else:
        candidates = [
            entry for entry in history
            if entry.get("task_type") == task_type
        ]
    if not candidates and scene_type:
        candidates = [
            history[index]
            for index in _scene_type_index.get(scene_type, [])
            if index < len(history)
        ]
    return candidates


def _thinking_tokens(entry: Dict[str, Any]) -> int:
    result = entry.get("result", {})
    value = (
        result.get("thinking_tokens")
        or result.get("total_tokens")
        or 0
    )
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def _token_record_is_valid(entry: Dict[str, Any]) -> bool:
    result = entry.get("result", {})
    status = result.get("completion_status")
    if status is not None and status != "success":
        return False
    if result.get("token_data_complete", True) is not True:
        return False
    return _thinking_tokens(entry) > 0


def _quality_label(entry: Dict[str, Any]) -> bool | None:
    result = entry.get("result", {})
    observed = result.get("quality_observed")
    if observed is None:
        observed = "score" in result or "is_correct" in result
    if not observed:
        return None
    if result.get("is_correct") is not None:
        return bool(result["is_correct"])
    score = result.get("quality_score", result.get("score"))
    if score is None:
        return None
    return float(score) >= 10


def get_task_type_stats(
    task_type: str,
    scene_type: str = "",
) -> Dict[str, Any]:
    cache_key = f"{task_type}|{scene_type}"
    now = time.time()
    cached = _stats_cache.get(cache_key)
    if cached and now - cached[1] < _STATS_CACHE_TTL:
        return dict(cached[0])

    candidates = _candidate_entries(task_type, scene_type)
    token_values = sorted(
        _thinking_tokens(entry)
        for entry in candidates
        if _token_record_is_valid(entry)
    )
    labels = [
        label for label in (_quality_label(entry) for entry in candidates)
        if label is not None
    ]
    successes = sum(1 for label in labels if label)
    failures = len(labels) - successes
    if token_values:
        count = len(token_values)
        result: Dict[str, Any] = {
            "count": count,
            "avg_tokens": round(sum(token_values) / count, 1),
            "p50_tokens": token_values[min(count - 1, int(count * 0.50))],
            "p75_tokens": token_values[
                min(count - 1, int(count * 0.75))
            ],
            "max_tokens": token_values[-1],
        }
    else:
        result = {
            "count": 0,
            "avg_tokens": 0.0,
            "p50_tokens": 0.0,
            "p75_tokens": 0.0,
            "max_tokens": 0.0,
        }
    quality_count = len(labels)
    result.update({
        "quality_count": quality_count,
        "success_rate": round(
            successes / quality_count, 3) if quality_count else 0.0,
        "failure_rate": round(
            failures / quality_count, 3) if quality_count else 0.0,
        "reliability_count": len(candidates),
    })
    _stats_cache[cache_key] = (dict(result), now)
    return result


def get_cv_for_task_type(task_type: str, scene_type: str = "") -> float:
    values = [
        _thinking_tokens(entry)
        for entry in _candidate_entries(task_type, scene_type)
        if _token_record_is_valid(entry)
    ]
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    if average == 0:
        return 0.0
    variance = sum((value - average) ** 2 for value in values) / len(values)
    return variance ** 0.5 / average


def get_category_stats(
    question_preview: str,
    task_type: str = "naie",
    scene_type: str = "",
    top_k: int = 10,
) -> Dict[str, Any]:
    candidates = _candidate_entries(task_type, scene_type)
    query_chars = set(question_preview[:100])
    scored = []
    for entry in candidates:
        preview = entry.get("question_preview", "")
        chars = set(preview)
        similarity = (
            len(chars & query_chars) / max(1, len(chars | query_chars))
        )
        if scene_type and entry.get("scene_type") == scene_type:
            similarity += 0.3
        scored.append((similarity, entry))
    selected = [
        entry for _, entry in sorted(
            scored, key=lambda item: item[0], reverse=True
        )[:top_k]
    ]
    valid_tokens = [
        _thinking_tokens(entry)
        for entry in selected
        if _token_record_is_valid(entry)
    ]
    labels = [
        label for label in (_quality_label(entry) for entry in selected)
        if label is not None
    ]
    return {
        "avg_tokens": (
            sum(valid_tokens) / len(valid_tokens) if valid_tokens else 0.0
        ),
        "failure_rate": (
            sum(1 for label in labels if not label) / len(labels)
            if labels else 0.0
        ),
        "count": len(valid_tokens),
        "quality_count": len(labels),
        "scene_type": scene_type,
        "scene_match_count": sum(
            1 for entry in selected
            if entry.get("scene_type") == scene_type
        ),
    }


def get_scene_stats(
    scene_type: str,
    task_type: str = "naie",
) -> Dict[str, Any]:
    return get_task_type_stats(task_type, scene_type)


def clear_history() -> None:
    global _history_cache, _persisted_write_count
    with _HISTORY_LOCK:
        if os.path.exists(_HISTORY_FILE):
            os.remove(_HISTORY_FILE)
        _history_cache = []
        _task_type_index.clear()
        _scene_type_index.clear()
        _parent_request_ids.clear()
        _stats_cache.clear()
        _persisted_write_count = 0
