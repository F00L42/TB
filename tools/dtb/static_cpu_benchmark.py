"""Compare local Python work with a Git baseline; never an inference benchmark.

Run from any directory. The baseline must be a trusted commit in this repository:
its two Python modules are executed in isolated module namespaces. No files in
the baseline or upstream checkouts are edited. All inputs are local/synthetic.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import platform
from pathlib import Path
import statistics
import subprocess
import sys
from time import perf_counter_ns
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from dtb.budgeting.scene_router import classify_scene
from dtb.config.loader import DEFAULT_CONFIG_PATH, load_dtb_service_config
from dtb.integration.vllm.response_adapter import aggregate_queue

BASELINE = "f342e1276a0d8b9c8b2c02f57917b19be936b799"
SOURCES = ("dtb/budgeting/scene_router.py", "dtb/integration/vllm/response_adapter.py")


def git(*args):
    return subprocess.run(
        ["git", *args], cwd=ROOT, stdin=subprocess.DEVNULL,
        capture_output=True, check=True,
    ).stdout


def baseline_module(path, revision):
    source = git("show", f"{revision}:{path}")
    module = ModuleType("_baseline_" + Path(path).stem)
    module.__file__ = f"{revision}:{path}"
    exec(compile(source, module.__file__, "exec"), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


def compare_calls(before, after, *, repeats, iterations):
    """Alternate order after warmup; report every sample, no timing assertions."""
    before()
    after()
    samples = {"baseline_us": [], "candidate_us": []}
    for index in range(repeats):
        calls = [("baseline_us", before), ("candidate_us", after)]
        if index % 2:
            calls.reverse()
        for name, call in calls:
            started = perf_counter_ns()
            for _ in range(iterations):
                call()
            samples[name].append((perf_counter_ns() - started) / iterations / 1000)
    before_us = statistics.median(samples["baseline_us"])
    after_us = statistics.median(samples["candidate_us"])
    return {"samples": samples, "baseline_median_us": before_us,
            "candidate_median_us": after_us, "ratio_candidate_to_baseline": after_us / before_us}


async def aggregate_batch(function, *, requests, chunks):
    async def one_request(index):
        queue = asyncio.Queue()
        for number in range(chunks):
            queue.put_nowait(SimpleNamespace(
                outputs=[SimpleNamespace(
                    text="正文" * 32, token_ids=[index, number],
                    finish_reason="stop" if number == chunks - 1 else None,
                    stop_reason="EOS" if number == chunks - 1 else None,
                )], finished=False,
            ))
        queue.put_nowait(None)
        output, = [item async for item in function(queue, SimpleNamespace(orchestration_error=None))]
        value = output.outputs[0]
        assert value.text == "正文" * (32 * chunks)
        assert value.token_ids == [token for n in range(chunks) for token in (index, n)]
        assert (value.finish_reason, value.stop_reason, output.finished) == ("stop", "EOS", True)

    await asyncio.gather(*(one_request(index) for index in range(requests)))


def replay_routes(baseline, config):
    directory = ROOT / "tests/fixtures/scenes/run_06_v3"
    paths = sorted(directory.glob("dtb_*.csv"))
    if len(paths) != 13:
        raise RuntimeError("route equivalence requires the 13 frozen run_06_v3 CSVs")
    count = 0
    files = {}
    for path in paths:
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
        with path.open(encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                text = row["问题"]
                if baseline(text, service_config=config) != classify_scene(text, service_config=config):
                    raise AssertionError(f"route differs: {path.name} / {row['QA_ID']}")
                count += 1
    if count != 633:
        raise RuntimeError(f"expected 633 frozen questions, got {count}")
    return {"equal_full_route_dicts": count, "input_sha256": files}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--replay", action="store_true", help="compare all route fields on 633 frozen inputs")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repeats < 1 or args.iterations < 1:
        parser.error("repeats and iterations must be positive")
    revision = git("rev-parse", "--verify", f"{args.baseline}^{{commit}}").decode().strip()
    route, route_hash = baseline_module(SOURCES[0], revision)
    response, response_hash = baseline_module(SOURCES[1], revision)
    config = load_dtb_service_config()
    report = {
        "schema_version": 1,
        "evidence": "local_synthetic_cpu_only",
        "limitations": "No vLLM/model/NPU, HTTP, inference queue, quality or E2E measurement. "
                       "Async batches contain prequeued independent responses; they do not model engine scheduling.",
        "python": sys.version, "platform": platform.platform(), "baseline_commit": revision,
        "candidate_head": git("rev-parse", "HEAD").decode().strip(),
        "candidate_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
        "baseline_source_sha256": dict(zip(SOURCES, (route_hash, response_hash))),
        "candidate_source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES},
        "config_sha256": hashlib.sha256(DEFAULT_CONFIG_PATH.read_bytes()).hexdigest(),
        "repeats": args.repeats, "iterations": args.iterations,
        "routing": [], "aggregation": [],
    }
    for size in (1000, 16000, 64000):
        text = ("分析 中文日志 search index step1 x_count y_count ```code```\n" * (size // 20 + 1))[:size]
        assert route.classify_scene(text, service_config=config) == classify_scene(text, service_config=config)
        timing = compare_calls(
            lambda: route.classify_scene(text, service_config=config),
            lambda: classify_scene(text, service_config=config),
            repeats=args.repeats, iterations=args.iterations,
        )
        report["routing"].append({"characters": size, **timing})
    for requests, chunks in ((1, 128), (1, 2048), (32, 128), (32, 2048)):
        timing = compare_calls(
            lambda: asyncio.run(aggregate_batch(response.aggregate_queue, requests=requests, chunks=chunks)),
            lambda: asyncio.run(aggregate_batch(aggregate_queue, requests=requests, chunks=chunks)),
            repeats=args.repeats, iterations=args.iterations,
        )
        report["aggregation"].append({"requests": requests, "chunks_per_request": chunks,
                                      "timing_includes_fixture_setup_and_assertions": True, **timing})
    if args.replay:
        report["route_equivalence"] = replay_routes(route.classify_scene, config)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
