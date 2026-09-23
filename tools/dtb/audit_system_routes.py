"""Offline identity coverage using the production router, without fixed overrides.

Run from TB: python -m tools.dtb.audit_system_routes --help
This measures routing coverage, not model quality or live serving performance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from dtb.config.loader import load_dtb_service_config
from dtb.request_controller import DtbRequestController


def audit(config_path: Path, details: Path) -> dict:
    service = load_dtb_service_config(config_path)
    if service.routing.mode != "system_prefix" or service.observability.mode.value != "off":
        raise ValueError("audit requires system_prefix/off configuration")
    if any(getattr(service.algorithm, name) for name in vars(service.algorithm) if name.startswith("fixed_")):
        raise ValueError("automatic audit requires all service fixed_* = 0")
    controller = DtbRequestController(service_config=service)
    counts = Counter()
    groups = {}
    sources = hashlib.sha256()
    max_messages = 0
    total = 0
    for path in sorted(details.glob("*.json")):
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        sources.update(f"{path.name}:{sha}\n".encode())
        data = json.loads(raw)
        body = data["request"]
        if any(value for name, value in body.items() if name.startswith("fixed_")):
            raise ValueError(f"{path.name}: fixed override would invalidate automatic coverage")
        request = SimpleNamespace(**{**body, "enable_think_budget": True})
        prepared = controller.prepare_request_route(request, [])
        route = prepared.route
        messages = body.get("messages", [])
        max_messages = max(max_messages, len(messages))
        system = messages[0].get("content") if messages and messages[0].get("role") == "system" else None
        system_sha = hashlib.sha256(system.encode()).hexdigest() if isinstance(system, str) else None
        key = (system_sha, route.scene, route.action, route.reason)
        if key not in groups:
            groups[key] = dict(system_sha256=system_sha, scene=route.scene,
                               matched=route.matched, action=route.action, reason=route.reason,
                               threshold=prepared.threshold.tokens if prepared.threshold else None,
                               count=0, examples=[])
        group = groups[key]
        group["count"] += 1
        if len(group["examples"]) < 3:
            group["examples"].append({"request_id": path.stem, "detail_sha256": sha})
        counts[f"{route.scene}/{route.action}/{route.reason}"] += 1
        total += 1
    if not total:
        raise ValueError("no detail JSON files found")
    matched = sum(group["count"] for group in groups.values() if group["matched"])
    return {"schema": 1, "evidence": "offline routing of captured request bodies; not holdout or live generation",
            "config": config_path.as_posix(), "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
            "details": details.as_posix(), "sorted_detail_hashes_sha256": sources.hexdigest(),
            "hash_recipe": "SHA256 of sorted filename:SHA256(file_bytes) plus LF per file",
            "requests": total, "matched": matched, "rejected": total - matched,
            "max_messages": max_messages, "counts": dict(counts), "groups": list(groups.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--details", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.config, args.details)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("requests", "matched", "rejected", "counts")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
