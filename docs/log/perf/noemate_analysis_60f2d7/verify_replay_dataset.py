"""Offline checks for prepared replay data and documented CLI parameters."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import prepare_replay_dataset as dataset


def main():
    data = dataset.OUT / "replay_dataset"
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    for name, metadata in manifest["files"].items():
        assert dataset.sha(data / name) == metadata["sha256"], name
    plans = [("pilot.jsonl", h, 24) for h in (512, 750, 850)]
    plans += [("diagnostic.jsonl", 750, 8), ("pool.jsonl", 750, 1644)]
    results = []
    for name, checkpoint, expected in plans:
        argv = ["--file", str(data / name), "--url", "http://127.0.0.1:9001/v1/chat/completions",
                "--mode", "both", "--order", "alternating", "--stream", "--repetitions", "2",
                "--seed", "20260918", "--fixed-budget-tokens", "2048",
                "--fixed-check-length", str(checkpoint), "--fixed-think-stride", "256",
                "--fixed-num-thinking", "4", "--fixed-min-think-tokens", "1", "--fixed-prob-th", "0.95",
                "--timeout", "1800", "--request-id-run", f"offline_{name[:-6]}_h{checkpoint}"]
        completed = subprocess.run([sys.executable, str(dataset.ROOT / "tools/dtb/replay_tool_calls.py"),
                                    *argv, "--list-request-ids"], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, encoding="utf-8", check=True)
        ids = completed.stdout.splitlines()
        assert len(ids) == len(set(ids)) == expected
        args = dataset.replay.parse_args(argv)
        cases = dataset.replay.load_cases(data / name, 1, 0)
        for case in cases:
            original = dataset.replay.json_hash(case.request_body)
            bodies = [dataset.replay.build_body(case.request_body, variant=variant,
                                               client_request_id=variant, args=args)
                      for variant in ("vanilla", "dtb")]
            assert dataset.replay.json_hash(case.request_body) == original
            for body in bodies:
                assert body["messages"] == case.request_body["messages"]
                assert body.get("tools") == case.request_body.get("tools")
                assert body["max_tokens"] == case.request_body["max_tokens"]
                assert body["seed"] == 20260918
                assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
                for field in ("temperature", "top_p", "model", "tool_choice"):
                    assert body.get(field) == case.request_body.get(field)
            assert bodies[0]["enable_think_budget"] is False and bodies[1]["enable_think_budget"] is True
            ignored = {"request_id", "enable_think_budget", *dataset.replay.FIXED_FIELDS}
            assert {k: v for k, v in bodies[0].items() if k not in ignored} == {
                k: v for k, v in bodies[1].items() if k not in ignored}
        results.append({"file": name, "checkpoint": checkpoint, "cases": len(cases), "planned_requests": expected})
    all_sets = {name: {r["case_id"] for r in dataset.read_lines(data / name)}
                for name in ("pilot.jsonl", "pool.jsonl", "diagnostic.jsonl")}
    assert all_sets["pilot.jsonl"] <= all_sets["pool.jsonl"]
    assert not all_sets["diagnostic.jsonl"] & all_sets["pool.jsonl"]
    with tempfile.TemporaryDirectory() as scratch:
        path = Path(scratch) / "detail.json"
        lf = b'{\n  "request": {"stream": true}\n}\n'
        crlf = lf.replace(b"\n", b"\r\n")
        for expected_bytes in (lf, crlf):
            expected = hashlib.sha256(expected_bytes).hexdigest()
            for body in (lf, crlf):
                path.write_bytes(body)
                _, normalized = dataset.read_audited_detail(path, expected)
                assert normalized == hashlib.sha256(lf).hexdigest()
        path.write_bytes(lf.replace(b"true", b"false"))
        try:
            dataset.read_audited_detail(path, expected)
        except ValueError:
            pass
        else:
            raise AssertionError("content mutation was accepted")
        # Parse every Bash block in the executable section without running it.
        report = (dataset.OUT / "RESULT.md").read_text(encoding="utf-8").split("## 10.", 1)[1].split("## 11.", 1)[0]
        shell_source = "\n".join(re.findall(r"```bash\n(.*?)```", report, re.S))
        shell_file = Path(scratch) / "commands.sh"
        shell_file.write_text(shell_source, encoding="utf-8", newline="\n")
        bash = Path("C:/Program Files/Git/bin/bash.exe") if sys.platform == "win32" else Path("/bin/bash")
        subprocess.run([str(bash), "-n", str(shell_file)], stdin=subprocess.DEVNULL, check=True)
    dataset.write_json(data / "validation.json", {
        "inference_executed": False, "plans": results, "source_manifest_sha256": dataset.sha(data / "manifest.json"),
        "source_and_input_hashes": "passed", "pair_input_parity": "passed",
        "subset_and_diagnostic_separation": "passed", "line_ending_and_content_mutation_checks": "passed",
        "documented_bash_syntax": "passed"})
    print("Offline validation passed: 6 pilot, 2 diagnostic, 411 pool; no inference executed.")


if __name__ == "__main__":
    main()
