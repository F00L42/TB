"""Pure CPU routing microbenchmark; no JSON I/O in measured intervals."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path
from time import perf_counter_ns
from types import SimpleNamespace

from dtb.config.loader import load_dtb_service_config
from dtb.request_controller import DtbRequestController


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3000)
    args = parser.parse_args()
    service = load_dtb_service_config(args.config)
    if (service.routing.mode != "system_prefix" or service.observability.mode.value != "off"
            or args.repetitions < 100
            or any(value for name, value in vars(service.algorithm).items() if name.startswith("fixed_"))):
        parser.error("requires system_prefix/off, fixed_*=0, repetitions>=100")
    ctl = DtbRequestController(service_config=service)
    results = []
    for count, body_chars in ((3, 1), (3, 1_000_000), (393, 1), (4096, 1)):
        messages = [{"role": "system", "content": service.scene_rules[0].system_prefix},
                    {"role": "user", "content": "question"}]
        messages += [{"role": "tool", "content": "x" * body_chars}] * (count - 2)
        request = SimpleNamespace(messages=messages, tools=[], stream=True, enable_think_budget=True)
        for _ in range(500):
            ctl.prepare_request_route(request, [])
        times = []
        for _ in range(args.repetitions):
            start = perf_counter_ns()
            ctl.prepare_request_route(request, [])
            times.append((perf_counter_ns() - start) / 1_000_000)
        times.sort()
        results.append({"messages": count, "tool_body_chars": body_chars, "repetitions": len(times),
                        "p50_ms": times[int(.50 * (len(times) - 1))],
                        "p95_ms": times[int(.95 * (len(times) - 1))],
                        "p99_ms": times[int(.99 * (len(times) - 1))], "max_ms": times[-1]})
    output = {"scope": "local CPU only; target serving CPU and model E2E pending_remote",
              "python": sys.version, "platform": platform.platform(),
              "config_sha256": hashlib.sha256(args.config.read_bytes()).hexdigest(), "groups": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf8")
    print(json.dumps(results))


if __name__ == "__main__":
    main()
