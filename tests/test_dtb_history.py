from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from dtb.history import store as history_module
from dtb.history.store import (
    AsyncHistoryRecorder,
    HistoryOutcome,
    get_scene_stats,
)


def outcome(
    request_id: str,
    *,
    status: str = "success",
    thinking_tokens: int = 100,
) -> HistoryOutcome:
    return HistoryOutcome.from_request(
        parent_request_id=request_id,
        question=f"question {request_id}",
        scene="general",
        decision_branch="history",
        budget_decision={"budget": {"check_length": 10}},
        thinking_tokens=thinking_tokens,
        hidden_probe_tokens=5,
        final_answer_tokens=10,
        elapsed_ms=20,
        completion_status=status,
    )


class HistoryRecorderTests(unittest.IsolatedAsyncioTestCase):
    def test_historical_budget_plan_key_is_normalized_on_read(self) -> None:
        entry = history_module._normalize_entry({
            "scene_type": "legacy",
            "budget_plan": {"budget": {"check_length": 10}},
        })
        self.assertEqual(entry["scene"], "legacy")
        self.assertIn("budget_decision", entry)
        self.assertNotIn("budget_plan", entry)

    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(
            dir=os.getcwd())
        self.old_path = history_module._HISTORY_FILE
        history_module._HISTORY_FILE = os.path.join(
            self.temp_dir.name, "history.jsonl")
        history_module._history_cache = None
        history_module._stats_cache.clear()
        history_module._scene_index.clear()
        history_module._parent_request_ids.clear()
        self.recorder = AsyncHistoryRecorder(max_queue_size=128)

    async def asyncTearDown(self) -> None:
        await self.recorder.flush()
        history_module._HISTORY_FILE = self.old_path
        history_module._history_cache = None
        history_module._stats_cache.clear()
        history_module._scene_index.clear()
        history_module._parent_request_ids.clear()
        self.temp_dir.cleanup()

    async def test_record_is_immediately_readable_and_persisted_once(self):
        self.assertTrue(self.recorder.record_outcome(outcome("p1")))
        immediate = get_scene_stats("general")
        self.assertEqual(immediate["count"], 1)
        self.assertEqual(immediate["quality_count"], 0)
        self.assertEqual(immediate["failure_rate"], 0.0)
        self.assertFalse(self.recorder.record_outcome(outcome("p1")))

        self.assertTrue(await self.recorder.flush())
        with open(
            history_module._HISTORY_FILE, "r", encoding="utf-8"
        ) as stream:
            lines = stream.readlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["parent_request_id"], "p1")

    async def test_errors_and_cancellations_do_not_pollute_token_percentiles(self):
        self.recorder.record_outcome(outcome("ok", thinking_tokens=120))
        self.recorder.record_outcome(
            outcome("error", status="error", thinking_tokens=900))
        self.recorder.record_outcome(
            outcome("cancel", status="cancelled", thinking_tokens=800))
        await self.recorder.flush()

        stats = get_scene_stats("general")
        self.assertEqual(stats["count"], 1)
        self.assertEqual(stats["avg_tokens"], 120)
        self.assertEqual(stats["reliability_count"], 3)
        self.assertEqual(stats["quality_count"], 0)

    async def test_many_enqueued_records_produce_valid_json_lines(self):
        for index in range(25):
            self.assertTrue(
                self.recorder.record_outcome(outcome(f"p{index}")))
        self.assertTrue(await self.recorder.flush())
        with open(
            history_module._HISTORY_FILE, "r", encoding="utf-8"
        ) as stream:
            records = [json.loads(line) for line in stream]
        self.assertEqual(len(records), 25)
        self.assertEqual(
            len({record["parent_request_id"] for record in records}), 25)

    async def test_writer_failure_is_contained(self):
        with patch.object(
            history_module,
            "_append_entry_sync",
            side_effect=OSError("disk unavailable"),
        ):
            self.assertTrue(
                self.recorder.record_outcome(outcome("write-failure")))
            self.assertTrue(await self.recorder.flush())
        self.assertIn("disk unavailable", self.recorder.last_error)
        self.assertEqual(get_scene_stats("general")["count"], 1)


if __name__ == "__main__":
    unittest.main()
