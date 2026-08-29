from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from proxy.full_log_writer import FullLogShardWriter
from tools.dtb.correlate_lightcode_proxy import correlate
from tools.dtb.extract_tool_call_replay_cases import read_candidates


def encoded_size(record: dict) -> int:
    return len((
        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8"))


def read_records(root: Path) -> list[dict]:
    records = []
    for path in sorted(root.glob("proxy_session_*/part-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            records.append(json.loads(line))
    return records


class FullLogShardWriterTests(unittest.TestCase):
    def test_partitions_by_session_and_rolls_before_limit(self) -> None:
        first = {"proxy_session_id": "alpha", "request_id": "r1", "value": "x" * 10}
        second = {"proxy_session_id": "alpha", "request_id": "r2", "value": "y" * 10}
        other = {"proxy_session_id": "beta", "request_id": "r3"}
        max_bytes = encoded_size(first) + 1

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "full_log"
            writer = FullLogShardWriter(root, max_bytes=max_bytes)
            first_result = writer.append(first)
            second_result = writer.append(second)
            other_result = writer.append(other)

            self.assertEqual(first_result.path.name, "part-00001.jsonl")
            self.assertEqual(second_result.path.name, "part-00002.jsonl")
            self.assertEqual(first_result.path.parent.name, "proxy_session_alpha")
            self.assertEqual(other_result.path.parent.name, "proxy_session_beta")
            manifest = json.loads(
                (first_result.path.parent / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["first_request_id"], "r1")
            self.assertCountEqual(read_records(root), [first, second, other])

    def test_oversized_record_is_atomic_and_next_record_rolls(self) -> None:
        oversized = {"proxy_session_id": "alpha", "value": "大" * 100}
        normal = {"proxy_session_id": "alpha", "request_id": "r2"}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "full_log"
            writer = FullLogShardWriter(root, max_bytes=80)
            oversized_result = writer.append(oversized)
            normal_result = writer.append(normal)

            self.assertTrue(oversized_result.oversized)
            self.assertGreater(oversized_result.path.stat().st_size, 80)
            self.assertEqual(normal_result.path.name, "part-00002.jsonl")
            self.assertEqual(read_records(root), [oversized, normal])

    def test_restart_resumes_complete_part_and_skips_partial_tail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "full_log"
            first = {"proxy_session_id": "alpha", "request_id": "r1"}
            second = {"proxy_session_id": "alpha", "request_id": "r2"}
            first_result = FullLogShardWriter(root, 1024).append(first)
            second_result = FullLogShardWriter(root, 1024).append(second)

            self.assertEqual(first_result.path, second_result.path)
            self.assertEqual(second_result.byte_offset, encoded_size(first))

            partial_dir = root / "proxy_session_beta"
            partial_dir.mkdir(parents=True)
            (partial_dir / "part-00001.jsonl").write_bytes(b'{"truncated":')
            resumed = FullLogShardWriter(root, 1024).append({
                "proxy_session_id": "beta", "request_id": "r3",
            })
            self.assertEqual(resumed.path.name, "part-00002.jsonl")

    def test_concurrent_appends_produce_complete_bounded_lines(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "full_log"
            writer = FullLogShardWriter(root, max_bytes=300)

            def append(index: int) -> None:
                writer.append({
                    "proxy_session_id": "alpha",
                    "request_id": f"r{index}",
                    "payload": "x" * 20,
                })

            with ThreadPoolExecutor(max_workers=8) as executor:
                list(executor.map(append, range(100)))

            records = read_records(root)
            self.assertEqual(len(records), 100)
            self.assertEqual(
                {record["request_id"] for record in records},
                {f"r{index}" for index in range(100)},
            )
            for path in root.glob("proxy_session_alpha/part-*.jsonl"):
                self.assertLessEqual(path.stat().st_size, 300)

    def test_sharded_archive_is_accepted_by_replay_extractor(self) -> None:
        record = {
            "schema": 2,
            "timestamp": "2026-08-29T00:00:00",
            "request_id": "source-request",
            "proxy_session_id": "alpha",
            "proxy_turn_index": 0,
            "request_body": {
                "messages": [{
                    "role": "user",
                    "content": "- branch: branch-a\nrun",
                }],
            },
            "response_data": {
                "content": "l_call>\n<function=read>",
                "reasoning": "",
                "tool_calls": "",
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory) / "run"
            FullLogShardWriter(run_dir / "full_log", 1024).append(record)
            candidates = read_candidates(run_dir, {"branch-a"})

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].request_id, "source-request")
        self.assertEqual(candidates[0].source_file.name, "part-00001.jsonl")

    def test_rejects_unsafe_session_path_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            writer = FullLogShardWriter(directory, 1024)
            with self.assertRaises(ValueError):
                writer.append({"proxy_session_id": "../escape"})


class LightCodeProxyCorrelationTests(unittest.TestCase):
    @staticmethod
    def _proxy_record(
        session_id: str, request_id: str, prompt: str, turn_index: int = 0,
    ) -> dict:
        return {
            "schema": 2,
            "timestamp": f"2026-08-29T00:00:0{request_id[-1]}",
            "completed_at": f"2026-08-29T00:00:1{request_id[-1]}",
            "request_id": request_id,
            "proxy_session_id": session_id,
            "proxy_turn_index": turn_index,
            "request_body": {
                "messages": [{"role": "user", "content": prompt}],
            },
            "response_data": {
                "content": f"response-{request_id}",
                "reasoning": "",
                "tool_calls": [],
            },
            "metrics": {
                "e2e_ms": 100,
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "stop_reason": "stop",
            },
        }

    def test_exact_prompt_fingerprint_maps_main_segments_and_subagent(self) -> None:
        main_prompt = (
            "请使用code-production-evaluation对以下用例完成评测\n"
            "任务参数:\n- skill: code-production-evaluation\n- branch: branch-a"
        )
        child_prompt = "analyze patch for branch-a"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proxy_run = root / "proxy-run"
            writer = FullLogShardWriter(proxy_run / "full_log", 4096)
            writer.append(self._proxy_record("main-a", "r1", main_prompt))
            writer.append(self._proxy_record(
                "main-a", "r4", "Generate a title for this conversation:", 1,
            ))
            writer.append(self._proxy_record("main-b", "r2", main_prompt))
            writer.append(self._proxy_record("child-a", "r3", child_prompt))

            lightcode_run = root / "lightcode-run"
            task_dir = lightcode_run / "task_abc_r1" / "logs" / "sessions"
            task_dir.mkdir(parents=True)
            (lightcode_run / "evaluation_results.json").write_text(
                json.dumps([{
                    "file": "case-a.json",
                    "task_id": "task_abc_r1",
                    "status": "FINISHED",
                    "start_time": "2026-08-29T00:00:00",
                    "end_time": "2026-08-29T00:10:00",
                }]),
                encoding="utf-8",
            )
            session = {
                "sessionId": "ses-main",
                "title": "evaluation",
                "messages": [
                    {
                        "role": "user",
                        "content": main_prompt,
                        "timestamp": 1787913600000,
                    },
                    {
                        "role": "assistant",
                        "timestamp": 1787913601000,
                        "parts": [{
                            "type": "tool",
                            "tool": "task",
                            "callID": "call-child",
                            "state": {
                                "status": "completed",
                                "input": {
                                    "description": "Analyze patch",
                                    "prompt": child_prompt,
                                },
                                "metadata": {
                                    "parentSessionId": "ses-main",
                                    "sessionId": "ses-child",
                                },
                                "time": {
                                    "start": 1787913600200,
                                    "end": 1787913600800,
                                },
                            },
                        }],
                    },
                ],
            }
            (task_dir / "ses-main.json").write_text(
                json.dumps(session, ensure_ascii=False), encoding="utf-8"
            )

            output_dir = root / "correlation"
            result = correlate(proxy_run, lightcode_run, output_dir)

            self.assertEqual(result["official_agent_count"], 2)
            self.assertEqual(result["matched_agent_count"], 2)
            self.assertEqual(result["proxy_session_count"], 3)
            self.assertEqual(result["matched_proxy_session_count"], 3)
            self.assertEqual(result["matched_proxy_request_count"], 4)
            agent_rows = [
                json.loads(line)
                for line in (output_dir / "agent_map.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            main = next(row for row in agent_rows if row["role"] == "main")
            child = next(row for row in agent_rows if row["role"] == "subagent")
            self.assertEqual(main["proxy_session_ids"], ["main-a", "main-b"])
            self.assertEqual(child["proxy_session_ids"], ["child-a"])
            detail = (output_dir / "tasks" / "task_abc_r1.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Analyze patch", detail)
            self.assertIn("main-a", detail)
            self.assertIn("## 生命周期", detail)
            self.assertIn("report/result handoff", detail)


if __name__ == "__main__":
    unittest.main()
