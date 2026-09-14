"""Quality cohorts selected without latency; comparisons use one shared baseline."""
from collections import Counter
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
GRADE={"不合格":0,"合格":1,"良好":2,"优秀":3}


def quality(t):
    r=t["report"]
    if r is None:
        return dict(grade=None,compiled=None,all_pass=None,total=None,passed=None,classes=[])
    c=r["compilation"]
    statuses=[c.get("status")]
    for key in ("main_code","test_code"):
        value=c.get(key)
        statuses.append(value.get("status") if isinstance(value,dict) else value)
    statuses=[s for s in statuses if s in ("success","failed")]
    assert statuses
    compiled="failed" not in statuses
    dt=r["dt_results"]
    total,passed=dt.get("total_tests"),dt.get("passed_tests")
    assert not compiled or (isinstance(total,int) and total>0 and isinstance(passed,int))
    return dict(grade=r["evaluation_conclusion"]["grade"],compiled=compiled,
        all_pass=compiled and total==passed and all(dt.get(k,0)==0 for k in ("failed_tests","errors","error_tests","skipped_tests")),
        total=total,passed=passed,classes=sorted(s.split(".")[-1] for s in dt.get("test_classes",[])))


def main():
    data=json.loads((ROOT/"analysis.json").read_text(encoding="utf8"))
    tasks={v:{t["case"]:t for t in g["tasks"].values()} for v,g in data["variants"].items()}
    q={v:{c:quality(t) for c,t in g.items()} for v,g in tasks.items()}
    all_cases=sorted(tasks["vanilla"],key=int)
    selections={}
    for v in ("run07","run08"):
        known=[c for c in all_cases if q["vanilla"][c]["grade"] is not None]
        noninferior=[c for c in known if GRADE[q[v][c]["grade"]]>=GRADE[q["vanilla"][c]["grade"]]]
        excellent=[c for c in known if q[v][c]["grade"]==q["vanilla"][c]["grade"]=="优秀"]
        qualified=[c for c in known if min(GRADE[q[v][c]["grade"]],GRADE[q["vanilla"][c]["grade"]])>=1]
        comparable=[c for c in known if q[v][c]["compiled"] and q["vanilla"][c]["compiled"]
            and q[v][c]["total"]==q["vanilla"][c]["total"] and q[v][c]["classes"]==q["vanilla"][c]["classes"]]
        dtni=[c for c in comparable if q[v][c]["passed"]>=q["vanilla"][c]["passed"]]
        bothpass=[c for c in comparable if q[v][c]["all_pass"] and q["vanilla"][c]["all_pass"]]
        selections[v]=dict(all=all_cases,grade_noninferior=noninferior,both_excellent=excellent,
            both_qualified=qualified,qualified_grade_noninferior=[c for c in qualified if c in noninferior],
            comparable_test_noninferior=dtni,both_test_fullpass=bothpass,
            both_fullpass_raw35=sorted(bothpass+(["35"] if q[v]["35"]["all_pass"] else []),key=int))
    common_dtni=sorted(set(selections["run07"]["comparable_test_noninferior"]) & set(selections["run08"]["comparable_test_noninferior"]),key=int)
    common_pass=sorted(set(selections["run07"]["both_test_fullpass"]) & set(selections["run08"]["both_test_fullpass"]),key=int)
    common_bothni=[c for c in common_dtni if all(c in selections[v]["grade_noninferior"] for v in ("run07","run08"))]
    results={}
    for v,sets in selections.items():
        sets.update(common_test_noninferior=common_dtni,common_test_fullpass=common_pass,
                    common_grade_and_test_noninferior=common_bothni)
        results[v]={}
        for name,cases in sets.items():
            out=dict(cases=cases,metrics={})
            for metric in ("llm_e2e_s","start_to_end_s","create_to_end_s","reasoning_tokens_local","completion_tokens","request_count"):
                getter=lambda t: t["xlsx"][metric] if metric in ("start_to_end_s","create_to_end_s") else t[metric]
                vs=[getter(tasks["vanilla"][c]) for c in cases]
                ds=[getter(tasks[v][c]) for c in cases]
                out["metrics"][metric]=dict(vanilla=sum(vs),dtb=sum(ds),gain_pct=100*(1-sum(ds)/sum(vs)),
                    faster=sum(d<b for b,d in zip(vs,ds)),macro_gain_pct=sum(100*(1-d/b) for b,d in zip(vs,ds))/len(cases))
            results[v][name]=out
    totals={}
    for v,g in data["variants"].items():
        totals[v]=dict(grades=dict(Counter(x["grade"] for x in q[v].values())),
            reported_fullpass=sum(x["all_pass"] is True for x in q[v].values()),
            compiled=sum(x["compiled"] is True for x in q[v].values()),roles={})
        for role in ("main","subagent","auxiliary"):
            rs=[r for r in g["requests"] if r["role"]==role]
            totals[v]["roles"][role]=dict(requests=len(rs),e2e_s=sum(r["metrics"]["e2e_ms"] for r in rs)/1000,
                completion=sum(r["metrics"]["completion_tokens"] for r in rs),
                reasoning=sum(r["metrics"]["reasoning_tokens_local"] for r in rs))
    combined={}
    for name in ("all","common_test_noninferior","common_test_fullpass","common_grade_and_test_noninferior"):
        metric={}
        for m in results["run07"][name]["metrics"]:
            a,b=[results[v][name]["metrics"][m] for v in ("run07","run08")]
            assert a["vanilla"]==b["vanilla"]
            avg=(a["dtb"]+b["dtb"])/2
            metric[m]=dict(vanilla=a["vanilla"],dtb_mean=avg,gain_pct=100*(1-avg/a["vanilla"]))
        combined[name]=dict(cases=selections["run07"][name],metrics=metric)
    output=dict(quality=q,cohorts=results,totals=totals,combined=combined,
        caveats=["Class/count agreement is a coverage screen, not proof of unchanged test source or independent correctness.",
                 "Raw vanilla case35 36/36 is a separately verified prior-audit input, not a formal grade override.",
                 "The two DTB runs reuse one historical Vanilla run; do not count it as two independent baseline observations."])
    (ROOT/"cohorts.json").write_text(json.dumps(output,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    for v,sets in results.items():
        for name,s in sets.items():
            m=s["metrics"]
            print(v,name,','.join(s['cases']),*[round(m[k]['gain_pct'],3) for k in ('llm_e2e_s','start_to_end_s','create_to_end_s','reasoning_tokens_local','completion_tokens')])
    print('COMBINED',json.dumps(combined,ensure_ascii=False))
    print('TOTALS',json.dumps(totals,ensure_ascii=False))


if __name__=="__main__":
    main()
