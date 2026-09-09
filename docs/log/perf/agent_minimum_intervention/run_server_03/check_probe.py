"""Read-only phase/HTTP probe coverage check; not full G1 or quality acceptance."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def inspect_probe(run_dir, logs):
    rows = [json.loads(line) for line in (Path(run_dir)/"summary.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    clients = {r["server_request_id"]: r for r in rows}
    if len(clients) != len(rows):
        raise ValueError("duplicate client parent IDs")
    phases, summaries = defaultdict(list), defaultdict(list)
    for file in logs:
        with Path(file).open(encoding="utf-8") as stream:
            for line in stream:
                for marker, target in (("DTB_PHASE ", phases), ("DTB_REQUEST_SUMMARY ", summaries)):
                    if marker in line:
                        record = json.loads(line.split(marker, 1)[1])
                        if record.get("parent_request_id") in clients:
                            target[record["parent_request_id"]].append(record)
                        break
    details, covered, blocked = [], 0, False
    for parent, client in clients.items():
        protocol_ok = (client["status_code"] == 200 and client["terminal_count"] == 1
                       and not client.get("protocol_anomaly_reasons") and not client.get("error"))
        blocked |= not protocol_ok
        if client["variant"] != "dtb":
            continue
        records = summaries[parent]
        if len(records) != 1:
            blocked = True
            details.append({"parent":parent,"error":"need exactly one DTB_REQUEST_SUMMARY", "count":len(records)})
            continue
        summary = records[0]
        trace = summary.get("intervention", {})
        probes = [p for p in phases[parent] if p.get("phase") == "confidence_probe"]
        observation = [p for p in phases[parent] if p.get("phase") == "native_passthrough"]
        finals = [p for p in phases[parent] if p.get("phase") == "final_answer"]
        hidden = sum(p.get("generated_tokens", 0) for p in probes)
        success = protocol_ok and summary.get("completion_status") == "success" and not summary.get("error")
        probe_ok = (success and trace.get("intervention_status") == "triggered"
                    and trace.get("post_drain_branch") == "confidence_probe"
                    and trace.get("abort_count") == 1 and trace.get("abort_terminal_seen") is True
                    and trace.get("abort_lost_to_native_terminal") is False
                    and len(observation) == 1 and len(probes) >= 1 and len(finals) == 1
                    and hidden > 0 and hidden == summary.get("hidden_probe_tokens")
                    and summary.get("final_terminal_count") == 1)
        covered += bool(probe_ok)
        blocked |= not success or (bool(probes) and not probe_ok)
        details.append({"parent":parent,"probe_chain_observed":bool(probe_ok),
            "intervention_status":trace.get("intervention_status"),
            "post_drain_branch":trace.get("post_drain_branch"),
            "probe_phases":len(probes),"hidden_probe_tokens":hidden,
            "semantic_anomalies":client.get("semantic_anomaly_reasons",[]),
            "protocol_ok":protocol_ok})
    return {"covered_probe_requests":covered,"blocked":blocked,"requests":details,
            "full_g1_accepted":False,"exit_code":1 if blocked else (0 if covered else 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("server_logs", type=Path, nargs="+")
    args = parser.parse_args()
    result = inspect_probe(args.run_dir, args.server_logs)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
