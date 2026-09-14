"""Recompute quality-selected task LLM E2E cohorts; no session-span metric."""
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GRADES = {"不合格": 0, "合格": 1, "良好": 2, "优秀": 3}


def quality(task):
    report = task["report"]
    if report is None:
        return dict(grade=None, outcome=None, all_pass=False, report_file=None)
    grade = report["evaluation_conclusion"]["grade"]
    assert grade in GRADES
    comp = report["compilation"]
    status = comp.get("status") or comp.get("main_code", {}).get("status")
    assert status in ("success", "failed"), comp
    dt = report["dt_results"]
    # Stage first; no test execution is not an observed zero-percent DT run.
    if status == "failed":
        outcome = [0, 0]
        passed = False
    else:
        total, count = dt["total_tests"], dt["passed_tests"]
        assert total > 0 and 0 <= count <= total
        outcome = [1, count / total]
        passed = count == total and all(
            dt.get(k, 0) == 0 for k in
            ("failed_tests", "errors", "error_tests", "skipped_tests"))
    return dict(grade=grade, outcome=outcome, all_pass=passed,
                report_file=task["report_file"])


def main():
    source = ROOT / "analysis.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    tasks = {v: {t["case"]: t for t in g["tasks"].values()}
             for v, g in data["variants"].items()}
    cases = sorted(tasks["vanilla"], key=int)
    assert set(cases) == set(tasks["dtb"])
    qualities = {c: {v: quality(g[c]) for v, g in tasks.items()} for c in cases}
    for c in cases:
        assert tasks["vanilla"][c]["root_prompt_hash"] == tasks["dtb"][c]["root_prompt_hash"]
    # Independently sum request metrics; reject missing/duplicate attribution.
    for variant, group in data["variants"].items():
        requests = group["requests"]
        assert len({r["request_id"] for r in requests}) == len(requests)
        assert all(r["case"] in cases for r in requests)
        for c, task in tasks[variant].items():
            selected = [r for r in requests if r["case"] == c]
            assert set(task["requests"]) == {r["request_id"] for r in selected}
            assert all(isinstance(r["metrics"].get("e2e_ms"), (int, float)) and
                       math.isfinite(r["metrics"]["e2e_ms"]) and
                       r["metrics"]["e2e_ms"] >= 0 for r in selected)
            assert math.isclose(sum(r["metrics"]["e2e_ms"] for r in selected) / 1000,
                                task["llm_e2e_s"], abs_tol=1e-8)

    formal = [c for c in cases if all(q["grade"] is not None for q in qualities[c].values())]
    noninferior = [c for c in formal if
                  qualities[c]["dtb"]["outcome"] >= qualities[c]["vanilla"]["outcome"]]
    grade_noninferior = [c for c in formal if
                        GRADES[qualities[c]["dtb"]["grade"]] >= GRADES[qualities[c]["vanilla"]["grade"]]]
    assert noninferior == grade_noninferior  # Same membership in this run, not a general equivalence.
    qualified = [c for c in formal if all(GRADES[q["grade"]] >= 1 for q in qualities[c].values())]
    excellent = [c for c in formal if all(q["grade"] == "优秀" for q in qualities[c].values())]
    fullpass = [c for c in formal if all(q["all_pass"] for q in qualities[c].values())]
    # Sensitivity only: verified raw 36/36 in the existing audit, not an invented report grade.
    assert qualities["35"]["vanilla"]["grade"] is None and qualities["35"]["dtb"]["all_pass"]
    assert data["aggregates"]["both_raw_all_pass"]["cases"] == sorted(fullpass + ["35"], key=int)
    specs = [
        ("all", "全量固定 9 任务", cases),
        ("noninferior", "DTB 非劣（正式报告可比）", noninferior),
        ("noninferior_raw35", "DTB 非劣＋case35 原始证据", sorted(noninferior + ["35"], key=int)),
        ("both_excellent", "两侧全优（均评级优秀）", excellent),
        ("both_qualified", "两侧全合格（均评级≥合格）", qualified),
        ("qualified_noninferior", "两侧合格且 DTB 非劣", [c for c in qualified if c in noninferior]),
        ("both_fullpass", "两侧 DT 全通过（正式报告）", fullpass),
        ("both_fullpass_raw35", "两侧 DT 全通过＋case35 原始证据", sorted(fullpass + ["35"], key=int)),
    ]
    results = []
    for key, label, selected in specs:
        assert selected
        totals = {v: sum(g[c]["llm_e2e_s"] for c in selected) for v, g in tasks.items()}
        reasoning = {v: sum(g[c]["reasoning_tokens_local"] for c in selected) for v, g in tasks.items()}
        role_totals = {v: {role: sum(r["metrics"]["e2e_ms"] / 1000
            for r in group["requests"] if r["case"] in selected and r["role"] == role)
            for role in ("main", "subagent", "auxiliary")}
            for v, group in data["variants"].items()}
        gains = [100 * (1 - tasks["dtb"][c]["llm_e2e_s"] / tasks["vanilla"][c]["llm_e2e_s"]) for c in selected]
        results.append(dict(id=key, label=label, cases=selected, llm_e2e_s=totals,
            saved_s=totals["vanilla"]-totals["dtb"],
            gain_pct=100*(1-totals["dtb"]/totals["vanilla"]),
            macro_gain_pct=sum(gains)/len(gains), faster_tasks=sum(x>0 for x in gains),
            reasoning_tokens_local=reasoning,
            role_llm_e2e_s=role_totals,
            reasoning_compression_pct=100*(1-reasoning["dtb"]/reasoning["vanilla"]),
            dtb_bad_tool_cases=[c for c in selected if c == "29"],
            vanilla_missing_sse_cases=[c for c in selected if c == "35"]))
    output = dict(source="analysis.json", source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        metric="Sum metrics.e2e_ms/1000 by task, including main/sub/auxiliary and retries; no session spans",
        quality_definition="Formal grade order and compilation-stage/DT-pass-rate noninferiority agree in this run; not statistical noninferiority",
        raw35_evidence="Existing audit RESULT.md section 5 and analysis.json aggregates.both_raw_all_pass; raw 36/36, formal report remains missing",
        selection_uses_latency=False, qualities=qualities, cohorts=results)
    (ROOT/"quality_cohorts.json").write_text(json.dumps(output, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    for r in results:
        print(f"{r['label']} | {','.join(r['cases'])} | {r['llm_e2e_s']['vanilla']:.3f} | {r['llm_e2e_s']['dtb']:.3f} | {r['saved_s']:+.3f} | {r['gain_pct']:+.2f}% | {r['macro_gain_pct']:+.2f}% | {r['faster_tasks']}/{len(r['cases'])} | {r['reasoning_compression_pct']:.2f}%")


if __name__ == "__main__":
    main()
