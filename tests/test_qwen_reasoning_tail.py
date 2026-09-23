from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools/dtb/reasoning_tail_fidelity.py"
REPLAY = ROOT / "tests/fixtures/parser_tail"


class QwenReasoningTailTests(unittest.TestCase):
    def run_gate(self, *, patched: bool, archived: bool = False, public=False):
        command = [sys.executable, "-X", "utf8", str(GATE)]
        if patched:
            command.append("--apply-repo-patch")
        if archived:
            command.extend(["--replay-dir", str(REPLAY)])
        if public:
            command.append("--require-public-fidelity")
        result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding="utf-8", check=False)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def test_patched_native_sources_satisfy_contract(self):
        code, result = self.run_gate(patched=True)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["failure_count"], 0)
        self.assertEqual(result["checks"], {
            "upstream_regression_functions": 10,
            "unchanged_controls": 10,
            "native_detokenizer_unified": 4,
        })

    def test_unpatched_source_is_a_failing_control(self):
        code, result = self.run_gate(patched=False)
        self.assertEqual(code, 1)
        self.assertEqual(result["failure_count"], 5)
        self.assertFalse(any(item["group"] == "unchanged_controls"
                             for item in result["failures"]))

    def test_all_archived_parser_chains_preserve_text_and_tools(self):
        code, result = self.run_gate(patched=True, archived=True)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["archived_dtb_requests"], 60)
        self.assertEqual(result["checks"]["archived_full_parser_chain"], 60)
        # New parsing must not masquerade as corrected historical server SSE.
        code, result = self.run_gate(patched=True, archived=True, public=True)
        self.assertEqual(code, 1)
        self.assertEqual(result["failure_count"], 47)
        self.assertTrue(all(item["error"] == "Public reasoning differs from source prefix"
                            for item in result["failures"]))


if __name__ == "__main__":
    unittest.main()
