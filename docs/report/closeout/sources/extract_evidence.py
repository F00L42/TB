"""Read pinned Git blobs and build the small closeout review evidence package.

No checkout, model call, tokenizer download, or presentation generation occurs.
Run from any directory: python docs/report/closeout/sources/extract_evidence.py
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "docs/report/closeout/evidence"
HISTORY = "32ab2268335e00d79e0aeda4395c6ecdc06d81eb"
DELIVERY = "f5512aa35152a13a9ee164a64928674badc26ceb"
OBSERVER = "7ccd6b99eb7a435c2d0d1f66dce24affe0728a0d"
OLD = "docs/report/dtb_closeout/evidence/"
RUN = "docs/log/perf/noemate_guardrail_main_20260922T070000Z/"
REGISTRY: dict[str, dict] = {}
OUTPUTS: list[str] = []


def blob(source_id: str, path: str, commit: str = HISTORY) -> bytes:
    data = subprocess.check_output(
        ["git", "show", f"{commit}:{path}"], cwd=ROOT,
        stdin=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    REGISTRY[source_id] = {
        "kind": "git_blob", "commit": commit, "path": path,
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "url": f"https://github.com/F00L42/TB/blob/{commit}/{path}",
    }
    return data


def read_json(source_id: str, path: str) -> dict | list:
    return json.loads(blob(source_id, path))


def write_json(name: str, data: object) -> None:
    (OUT / name).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    OUTPUTS.append(name)


def write_csv(name: str, rows: list[dict]) -> None:
    with (OUT / name).open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    OUTPUTS.append(name)


def close(a: float, b: float) -> None:
    assert math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6), (a, b)


def distribution() -> dict:
    frozen = read_json("S01", OLD + "distribution.json")
    raw = [json.loads(line) for line in blob(
        "S02", "docs/log/perf/noemate_analysis_60f2d7/request_features.jsonl"
    ).splitlines() if line.strip()]
    roles = read_json("S03", "docs/log/perf/noemate_analysis_60f2d7/prompt_roles.json")
    main_hashes = {r["system_sha256"] for r in roles if r["request_parameters"].get("stream")}
    main = [r for r in raw if r["system_sha256"] in main_hashes]
    valid = sorted([r for r in main if not r["exclusions"]],
                   key=lambda r: (r["started_at_utc"], r["request_id"]))
    assert len(raw) == len({r["request_id"] for r in raw}) == 12214
    assert len(main) == 8306 and len(valid) == 8277
    assert {r["request_id"] for r in valid} == {r["request_id"] for r in frozen["requests"]}
    frozen_by_id = {r["request_id"]: r for r in frozen["requests"]}
    points = []
    for sequence, r in enumerate(valid, 1):
        old = frozen_by_id[r["request_id"]]
        assert r["reasoning_tokens_local"] == old["reasoning_tokens"]
        points.append({"sequence": sequence, "request_id": r["request_id"],
                       "started_at_utc": r["started_at_utc"],
                       "reasoning_tokens": r["reasoning_tokens_local"],
                       "above_750": r["reasoning_tokens_local"] > 750})
    values = sorted(p["reasoning_tokens"] for p in points)
    assert sum(values) == 2322694
    assert sum(v > 750 for v in values) == 415
    assert sum(max(0, v - 750) for v in values) == 68167
    assert [values[math.ceil(q * len(values)) - 1] for q in (.5, .95, .99)] == [207, 753, 1002]
    write_csv("noemate_distribution.csv", points)
    return frozen["summary"]


def noemate() -> tuple[list[dict], list[dict]]:
    paired = read_json("S04", OLD + "paired.json")
    semantic = read_json("S05", OLD + "semantic_review.json")
    write_csv("alarm_reference.csv", semantic["alarm_source"])
    summaries, exported_pairs, raw_by_id = [], [], {}
    for line in [178, 259, 319, 353, 399]:
        folder = f"long_l{line}_measured/"
        rows = [json.loads(s) for s in blob(f"N{line}S", RUN + folder + "summary.jsonl").splitlines() if s.strip()]
        manifest = read_json(f"N{line}M", RUN + folder + "manifest.json")
        assert manifest["selected_lines"] == [line] and manifest["repetitions"] == 10
        assert manifest["variant_order"] == "alternating"
        assert manifest["request_overrides"]["fixed_budget_tokens"] == 750
        assert len(rows) == len({r["client_request_id"] for r in rows}) == 20
        raw_by_id.update({r["client_request_id"]: r for r in rows})
        pairs = [p for p in paired["pairs"] if p["group"] == "long" and p["line"] == line]
        assert len(pairs) == 10 and {p["attempt"] for p in pairs} == set(range(1, 11))
        for p in pairs:
            close(p["v_ms"], raw_by_id[p["v_id"]]["elapsed_ms"])
            close(p["d_ms"], raw_by_id[p["d_id"]]["elapsed_ms"])
            exported_pairs.append(dict(p,
                vanilla_finish=raw_by_id[p["v_id"]]["finish_reasons"],
                dtb_finish=raw_by_id[p["d_id"]]["finish_reasons"]))
        v = sum(p["v_ms"] for p in pairs) / 10
        d = sum(p["d_ms"] for p in pairs) / 10
        old = next(r for r in paired["rows"] if r["folder"] == folder.rstrip("/"))
        close(v, old["v_e2e_ms"]); close(d, old["d_e2e_ms"])
        summaries.append({"line": line, "pairs": 10,
            "vanilla_e2e_s": v / 1000, "dtb_e2e_s": d / 1000,
            "reduction_pct": (v - d) / v * 100,
            "faster_pairs": sum(p["d_ms"] < p["v_ms"] for p in pairs),
            "vanilla_reasoning_mean": old["v_reasoning"], "dtb_reasoning_mean": old["d_reasoning"],
            "vanilla_length": old["outcomes"]["vanilla"]["length"],
            "dtb_length": old["outcomes"]["dtb"]["length"],
            "source_id": f"N{line}S", "token_source_id": "S04"})
    write_json("noemate_long_pairs.json", exported_pairs)
    write_csv("noemate_long_summary.csv", summaries)
    cards = []
    for source_id, name in [("S06", "l178_a1"), ("S07", "l178_a3"), ("S08", "l399_a10")]:
        card = read_json(source_id, OLD + "cards/" + name + ".json")
        for variant, key in [("vanilla", "v_id"), ("dtb", "d_id")]:
            raw = raw_by_id[card["pair"][key]]
            for field in ["content", "reasoning", "elapsed_ms", "finish_reasons"]:
                assert card[variant][field] == raw[field]
            card[variant] = {k: raw[k] for k in ["client_request_id", "server_request_id",
                "elapsed_ms", "content", "reasoning", "finish_reasons", "usage"]}
        card["source_id"] = source_id
        cards.append(card)
    request = read_json("S09", OLD + "review/requests/l178.json")
    for alarm in semantic["alarm_source"]:
        original = json.dumps(request["messages"][alarm["message_index"]], ensure_ascii=False)
        for k in ["id", "name", "severity", "time", "ne"]:
            assert alarm[k] in original, (alarm["id"], k)
        assert all(alarm[k] in cards[0]["dtb"]["content"] for k in ["id", "name", "time", "ne"])
    write_json("noemate_cases.json", {"cards": cards,
        "alarm_review_all_attempts": semantic["all_178_dtb_attempts"],
        "reviewer": semantic["reviewer"], "319": semantic["319"]})
    return summaries, cards


def development() -> dict:
    frozen = read_json("S10", "docs/report/sum0911/sources/evidence.json")
    analysis = read_json("S11", "docs/log/perf/agent_run07_08/analysis.json")
    closed = read_json("S12", "docs/log/perf/agent_run06_closed/analysis.json")
    points = frozen["vanilla_chronological_requests"]
    raw_requests = {r["request_id"]: r for r in analysis["variants"]["vanilla"]["requests"]}
    assert len(points) == len({r["request_id"] for r in points}) == 391
    assert set(raw_requests) - {r["request_id"] for r in points} == {"7774ec46a9f245c9"}
    assert [r["sequence"] for r in points] == list(range(1, 392))
    for point in points:
        raw = raw_requests[point["request_id"]]
        assert point["reasoning_tokens"] == raw["metrics"]["reasoning_tokens_local"]
        assert point["case"] == raw["case"] and point["role"] == raw["role"]
    tail = [r for r in points if r["reasoning_tokens"] > 850]
    assert len(tail) == 11 and sum(r["role"] == "auxiliary" for r in tail) == 9
    assert sum(r["reasoning_tokens"] for r in points) == 52112
    assert sum(r["reasoning_tokens"] for r in tail) == 20263
    assert max(r["reasoning_tokens"] for r in points) == 3482
    write_csv("development_distribution.csv", [dict(r, above_850=r["reasoning_tokens"] > 850) for r in points])
    totals, task_rows = {}, []
    for variant in ["vanilla", "run08"]:
        original = analysis["variants"][variant]
        reqs = original["requests"]
        assert len(reqs) == len({r["request_id"] for r in reqs})
        target = frozen["variants"][variant]["totals"]
        assert len(reqs) == target["request_count"]
        close(sum(r["metrics"]["e2e_ms"] for r in reqs) / 1000, target["llm_e2e_s"])
        assert sum((r["metrics"].get("reasoning_tokens_local") or 0) for r in reqs) == target["reasoning_tokens_local"]
        totals[variant] = target
        for task in frozen["variants"][variant]["tasks"]:
            raw_task = next(t for t in original["tasks"].values() if t["case"] == task["case"])
            close(task["llm_e2e_s"], sum(r["metrics"]["e2e_ms"] for r in reqs if r["case"] == task["case"]) / 1000)
            dt = (raw_task.get("report") or {}).get("dt_results", {})
            label = (f"{dt['passed_tests']}/{dt['total_tests']}" if "total_tests" in dt else "未执行 / 缺报告")
            if variant == "vanilla" and task["case"] == "35":
                label = "36/36（原始测试，缺正式报告）"
            task_rows.append(dict(task, variant=variant, dt_result=label,
                pass_for_report=task["case"] in frozen["quality_policy"]["vanilla_pass" if variant == "vanilla" else "run08_pass"]))
    for case in {r["case"] for r in task_rows}:
        assert len({r["root_prompt_hash"] for r in task_rows if r["case"] == case}) == 1
    write_csv("development_tasks.csv", task_rows)
    c37 = frozen["case37"]
    raw_path = "proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed/full_log/proxy_session_795d073fadb0/part-00001.jsonl"
    raw_case = json.loads(blob("S20", raw_path).splitlines()[19])
    assert raw_case["request_id"] == c37["request_id"]
    close(raw_case["metrics"]["e2e_ms"], c37["metrics"]["e2e_ms"])
    for fragment in c37["reasoning_quote_fragments"]:
        assert raw_case["response_data"]["reasoning"][fragment["start_char"]:fragment["end_char"]] == fragment["text"]
    phases = [{k: p.get(k) for k in ["phase", "wall_ms", "generated_tokens", "visible", "confidence", "accepted", "stop_reason"]} for p in c37["phases"]]
    mechanism = {"source_id": "S10", "request_id": c37["request_id"],
        "parent_request_id": c37["summary"]["parent_request_id"],
        "metrics": c37["metrics"], "phases": phases,
        "reasoning_quote_fragments": c37["reasoning_quote_fragments"],
        "phase_wall_sum_ms": sum(p["wall_ms"] for p in phases),
        "model_work_complete": c37["summary"]["model_work_complete"],
        "recorded_model_tokens": c37["summary"]["total_model_generated_tokens"],
        "raw_source_id": "S20", "raw_source_line": 20,
        "response_data": raw_case["response_data"]}
    mechanism["e2e_minus_phase_ms"] = mechanism["metrics"]["e2e_ms"] - mechanism["phase_wall_sum_ms"]
    write_json("development_case37.json", mechanism)
    assert closed["intervention"] == {"triggered": 10, "disarmed_boundary": 353, "observing": 1}
    return {"totals": totals, "quality_policy": frozen["quality_policy"],
        "reductions_pct": {k: (totals["vanilla"][k] - totals["run08"][k]) / totals["vanilla"][k] * 100 for k in totals["vanilla"]},
        "closed_intervention": closed["intervention"], "closed_probe": closed["probe"],
        "distribution_850": frozen["vanilla_distribution"], "long_tail_850": frozen["long_tail"]}


def documentation_sources() -> None:
    for sid, path, rev in [
        ("S13", "docs/report/agent_dtb_demo_data_memo.md", HISTORY),
        ("S14", "docs/report/sum0911/工程复盘时间线_报错样例.md", HISTORY),
        ("S15", "docs/log/perf/agent_run07_08/RESULT.md", HISTORY),
        ("S16", "docs/report/dtb_closeout/README.md", HISTORY),
        ("S17", "docs/report/dtb_closeout/evidence/source_manifest.json", HISTORY),
        ("S18", "docs/report/dtb_closeout/evidence/index.json", HISTORY),
        ("S21", "docs/report/dtb_closeout/evidence/cards/l178_a1.md", HISTORY),
        ("S22", "docs/report/dtb_closeout/evidence/cards/l178_a3.md", HISTORY),
        ("S23", "docs/report/dtb_closeout/evidence/cards/l399_a10.md", HISTORY),
        ("V01", "docs/report/sum0911/统计图/思考长度长尾与850阈值.png", HISTORY),
        ("V02", "docs/report/dtb_closeout/evidence/figures/vanilla_distribution.png", HISTORY),
        ("C01", "dtb/README.md", DELIVERY),
        ("C02", "dtb/AGENTS.md", DELIVERY),
        ("C03", "docs/design/dtb_system_scene_native_routing.md", DELIVERY),
        ("C04", "RESULT.md", DELIVERY),
        ("C05", "dtb/reasoning/generation_states.py", DELIVERY),
        ("C06", "dtb/reasoning/deferred_observation.py", DELIVERY),
        ("C07", "dtb/config/budget_config.yaml", DELIVERY),
        ("C08", "dtb/reasoning/confidence_gates.py", DELIVERY),
        ("O01", "thinking_observer/README.md", OBSERVER),
        ("O02", "thinking_observer/docs/validation.md", OBSERVER),
    ]:
        blob(sid, path, rev)
    reference = ROOT / "docs/report/closeout/reference/动态思考预算控制方案.pptx"
    data = reference.read_bytes()
    REGISTRY["R01"] = {"kind": "user_reference", "path": reference.relative_to(ROOT).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
        "note": "User-supplied PPTX, not committed at extraction time; reference concept, not measured result."}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        slide = ET.fromstring(z.read("ppt/slides/slide1.xml"))
        text = "\n".join(t.text for t in slide.iter("{http://schemas.openxmlformats.org/drawingml/2006/main}t") if t.text)
    write_json("reference_slide_text.json", {"source_id": "R01", "slide": 1, "text": text})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    dist = distribution()
    long, _ = noemate()
    dev = development()
    documentation_sources()
    phase = read_json("S19", OLD + "phase.json")
    write_json("noemate_phase.json", {"source_id": "S19", "selected": phase["selected"]})
    write_json("display_data.json", {"schema_version": 1, "history_commit": HISTORY,
        "noemate_distribution": dist, "noemate_long": long, "development": dev,
        "boundaries": ["Historical measurements, not delivery-branch server validation",
            "Local re-encoded reasoning tokens are not total model work",
            "Noemate quality is sample-specific; 319 is pending independent review",
            "Nine-task equal pass counts do not establish per-task non-inferiority"]})
    write_json("source_manifest.json", {"schema_version": 1, "history_branch_at_selection": "debug_agent",
        "history_commit": HISTORY, "delivery_commit": DELIVERY, "observer_commit": OBSERVER,
        "hash_policy": "SHA-256 of exact Git blob bytes (no checkout CRLF conversion)",
        "sources": REGISTRY,
        "outputs": {name: {"sha256": hashlib.sha256((OUT / name).read_bytes()).hexdigest(),
                           "bytes": (OUT / name).stat().st_size} for name in OUTPUTS},
        "checks": ["8277 distribution rows agree with raw feature rows; totals and nearest-rank percentiles recomputed",
            "50 long pairs agree with all 100 raw summary records; 5 manifests fix inputs and alternating order",
            "Selected success/failure cards match raw reasoning, final, latency and finish fields",
            "12 alarm records agree with original request messages",
            "742 development requests sum to task and variant totals; 9 root prompt hashes match",
            "391 development distribution points match request ledger; 11 tails include 9 title requests",
            "Source observations were not rerun on a server; no PPT generated"]})
    print(json.dumps({"sources": len(REGISTRY), "files": len(OUTPUTS),
                      "bytes": sum((OUT / n).stat().st_size for n in OUTPUTS), "status": "PASS"}))


if __name__ == "__main__":
    main()
