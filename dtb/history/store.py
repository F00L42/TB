"""Scene-only online history with non-blocking JSONL persistence."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


_HISTORY_FILE = os.path.join(
    os.path.dirname(__file__),
    "..",
    "data",
    "history",
    "inference_history.jsonl",
)
_HISTORY_LOCK = threading.RLock()
_history_cache: Optional[List[Dict[str, Any]]] = None
_scene_index: Dict[str, List[int]] = {}
_parent_request_ids: set[str] = set()
_stats_cache: Dict[str, Tuple[Dict[str, Any], float]] = {}

_MAX_HISTORY_ENTRIES = 5000
_MAX_HISTORY_AGE_DAYS = 7
_EVICT_EVERY_N_WRITES = 50
_STATS_CACHE_TTL = 60.0
_persisted_write_count = 0
_LOGGER = logging.getLogger(__name__)


def configure_history_store(
    *,
    path: str,
    max_entries: int,
    max_age_days: int,
) -> None:
    """Apply the validated startup snapshot before history is enabled."""
    global _HISTORY_FILE, _MAX_HISTORY_ENTRIES, _MAX_HISTORY_AGE_DAYS
    resolved_path = os.path.abspath(path)
    with _HISTORY_LOCK:
        if _history_cache is not None and resolved_path != _HISTORY_FILE:
            raise RuntimeError("history store cannot be reconfigured after loading")
        _HISTORY_FILE = resolved_path
        _MAX_HISTORY_ENTRIES = max_entries
        _MAX_HISTORY_AGE_DAYS = max_age_days


def _stable_question_hash(text: str) -> str:
    return hashlib.sha256(text[:200].encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class HistoryOutcome:
    parent_request_id: str
    timestamp: float
    question_hash: str
    scene: str
    decision_branch: str
    budget_decision: Dict[str, Any]
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
        scene: str,
        decision_branch: str,
        budget_decision: Dict[str, Any],
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
            scene=scene or "general",
            decision_branch=decision_branch,
            budget_decision=budget_decision,
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


def _normalize_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize historical keys at the persistence boundary."""
    if not entry.get("scene"):
        entry["scene"] = entry.pop("scene_type", "general") or "general"
    if "budget_decision" not in entry and "budget_plan" in entry:
        entry["budget_decision"] = entry.pop("budget_plan")
    return entry


def _update_index_entry(entry: Dict[str, Any], index: int) -> None:
    scene = str(entry.get("scene", "general"))
    _scene_index.setdefault(scene, []).append(index)


def _rebuild_index_locked() -> None:
    _scene_index.clear()
    _parent_request_ids.clear()
    for index, entry in enumerate(_history_cache or []):
        _update_index_entry(entry, index)
        request_id = entry.get("parent_request_id")
        if request_id:
            _parent_request_ids.add(str(request_id))


def _invalidate_stats_cache(entry: Dict[str, Any]) -> None:
    _stats_cache.pop(str(entry.get("scene", "general")), None)


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
                with open(_HISTORY_FILE, "r", encoding="utf-8") as stream:
                    for line in stream:
                        try:
                            entry = _normalize_entry(json.loads(line))
                        except (json.JSONDecodeError, TypeError):
                            continue
                        entry["_persisted"] = True
                        entries.append(entry)
            except OSError as exc:
                _LOGGER.warning(
                    "DTB history load failed path=%s error=%s",
                    _HISTORY_FILE,
                    exc,
                )
        _history_cache = entries
        _rebuild_index_locked()
        return _history_cache


def _cache_entry(entry: Dict[str, Any]) -> bool:
    load_history()
    with _HISTORY_LOCK:
        request_id = entry.get("parent_request_id")
        if request_id and request_id in _parent_request_ids:
            return False
        assert _history_cache is not None
        index = len(_history_cache)
        _history_cache.append(entry)
        _update_index_entry(entry, index)
        if request_id:
            _parent_request_ids.add(str(request_id))
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
    ][-_MAX_HISTORY_ENTRIES:]
    pending = [
        entry for entry in _history_cache if not entry.get("_persisted")]
    survivors = [*persisted, *pending]
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
    """Record completion outcomes without adding file I/O to request latency."""

    def __init__(self, max_queue_size: int = 1024) -> None:
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
            _LOGGER.warning(
                "DTB history enqueue failed request_id=%s error=%s",
                outcome.parent_request_id,
                exc,
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
                    _LOGGER.warning(
                        "DTB history write failed request_id=%s error=%s",
                        entry.get("parent_request_id"),
                        exc,
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
            if self._worker_task is not None:
                await asyncio.wait_for(
                    asyncio.shield(self._worker_task), timeout=timeout_s)
            return True
        except asyncio.TimeoutError:
            return False


def _candidate_entries(scene: str) -> List[Dict[str, Any]]:
    history = load_history()
    return [
        history[index]
        for index in _scene_index.get(scene, [])
        if index < len(history)
    ]


def _thinking_tokens(entry: Dict[str, Any]) -> int:
    value = entry.get("result", {}).get("thinking_tokens", 0)
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def _token_record_is_valid(entry: Dict[str, Any]) -> bool:
    result = entry.get("result", {})
    return (
        result.get("completion_status", "success") == "success"
        and result.get("token_data_complete", True) is True
        and _thinking_tokens(entry) > 0
    )


def _quality_label(entry: Dict[str, Any]) -> bool | None:
    result = entry.get("result", {})
    if not result.get(
        "quality_observed",
        "score" in result or "is_correct" in result,
    ):
        return None
    if result.get("is_correct") is not None:
        return bool(result["is_correct"])
    score = result.get("quality_score", result.get("score"))
    return None if score is None else float(score) >= 10


def get_scene_stats(scene: str) -> Dict[str, Any]:
    now = time.time()
    cached = _stats_cache.get(scene)
    if cached and now - cached[1] < _STATS_CACHE_TTL:
        return dict(cached[0])
    candidates = _candidate_entries(scene)
    values = sorted(
        _thinking_tokens(entry)
        for entry in candidates
        if _token_record_is_valid(entry)
    )
    labels = [
        label for label in (_quality_label(entry) for entry in candidates)
        if label is not None
    ]
    if values:
        count = len(values)
        result: Dict[str, Any] = {
            "count": count,
            "avg_tokens": round(sum(values) / count, 1),
            "p50_tokens": values[min(count - 1, int(count * 0.50))],
            "p75_tokens": values[min(count - 1, int(count * 0.75))],
            "max_tokens": values[-1],
        }
    else:
        result = {
            "count": 0,
            "avg_tokens": 0.0,
            "p50_tokens": 0.0,
            "p75_tokens": 0.0,
            "max_tokens": 0.0,
        }
    successes = sum(1 for label in labels if label)
    quality_count = len(labels)
    result.update({
        "scene": scene,
        "quality_count": quality_count,
        "success_rate": round(
            successes / quality_count, 3) if quality_count else 0.0,
        "failure_rate": round(
            (quality_count - successes) / quality_count,
            3,
        ) if quality_count else 0.0,
        "reliability_count": len(candidates),
    })
    _stats_cache[scene] = (dict(result), now)
    return result


def get_cv_for_scene(scene: str) -> float:
    values = [
        _thinking_tokens(entry)
        for entry in _candidate_entries(scene)
        if _token_record_is_valid(entry)
    ]
    if len(values) < 2:
        return 0.0
    average = sum(values) / len(values)
    if average == 0:
        return 0.0
    variance = sum((value - average) ** 2 for value in values) / len(values)
    return variance ** 0.5 / average


def clear_history() -> None:
    global _history_cache, _persisted_write_count
    with _HISTORY_LOCK:
        if os.path.exists(_HISTORY_FILE):
            os.remove(_HISTORY_FILE)
        _history_cache = []
        _scene_index.clear()
        _parent_request_ids.clear()
        _stats_cache.clear()
        _persisted_write_count = 0
