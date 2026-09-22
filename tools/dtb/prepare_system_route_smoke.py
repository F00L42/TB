"""Prepare five routing smoke cases; does not send requests or define an E2E dataset."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

from tools.dtb.replay_tool_calls import FIXED_FIELDS, load_cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--pool", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text(encoding="utf8"))
    rows = []
    seen = set()
    for group in audit["groups"]:
        if group["scene"] in seen:
            continue
        seen.add(group["scene"])
        example = group["examples"][0]
        path = Path(audit["details"]) / (example["request_id"] + ".json")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != example["detail_sha256"]:
            raise ValueError(f"source hash mismatch: {path}")
        detail = json.loads(raw)
        body = detail["request"]
        choices = detail.get("response", {}).get("choices", [])
        rows.append({"case_id": example["request_id"], "request_body": body,
                     "response_data": choices[0].get("message", {}) if choices else {},
                     "source": str(path), "source_sha256": example["detail_sha256"],
                     "route_expectation": {"scene": group["scene"], "action": group["action"]}})
    # Frozen long example used only to exercise budget activation, not certify quality.
    case = load_cases(args.pool, 1, 0, {319})[0]
    rows.append({"case_id": case.case_id, "request_body": case.request_body,
                 "source": f"{args.pool}:319", "source_sha256": hashlib.sha256(args.pool.read_bytes()).hexdigest(),
                 "route_expectation": {"scene": "noemate_main", "action": "dtb"}})
    unknown = copy.deepcopy(rows[0]["request_body"])
    unknown["messages"] = [{"role": "system", "content": "Unrecognized routing smoke identity."},
                           {"role": "user", "content": "Reply OK."}]
    unknown.pop("tools", None)
    unknown.pop("tool_choice", None)
    rows.append({"case_id": "synthetic_unknown", "request_body": unknown,
                 "source": "synthetic rejection control",
                 "route_expectation": {"scene": "general", "action": "native"}})
    for row in rows:
        row["request_body"]["enable_think_budget"] = True
        for field in FIXED_FIELDS:
            row["request_body"].pop(field, None)
        row["replay_expectation"] = {"category": "routing_smoke", "latency_eligible": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf8")
    load_cases(args.output, 1, 0)  # Validate tool/message history before any remote execution.
    print(f"prepared {len(rows)} cases; no requests sent")


if __name__ == "__main__":
    main()
