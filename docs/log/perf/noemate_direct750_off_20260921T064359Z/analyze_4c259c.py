"""Audit 4c259c replay evidence and report per-case latency, not population wins."""
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import random
import statistics as st
import sys
from types import SimpleNamespace

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
PHASE = OUT.parent / "noemate_direct750_phase_20260921T062447Z"
DATA = OUT.parent / "noemate_analysis_60f2d7/replay_dataset/pool.jsonl"
sys.path.insert(0, str(ROOT / "tools/dtb"))
import replay_tool_calls as replay


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines(path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def summarize(pairs):
    if not pairs:
        return {"pairs": 0}
    result = {"pairs": len(pairs), "cases": len({p['line'] for p in pairs}),
              "faster": sum(p['d_ms'] < p['v_ms'] for p in pairs)}
    for field in ("ms", "first", "answer_start", "reasoning", "content", "completion"):
        v = [p['v_'+field] for p in pairs]; d = [p['d_'+field] for p in pairs]
        if any(x is None for x in v+d):
            continue
        result[field] = {"v_mean": st.mean(v), "d_mean": st.mean(d),
                         "delta_mean": st.mean(d)-st.mean(v),
                         "sum_gain_pct": 100*(1-sum(d)/sum(v)) if sum(v) else None,
                         "v_p95": replay.percentile(v, .95), "d_p95": replay.percentile(d, .95),
                         "v_max": max(v), "d_max": max(d)}
    result['median_paired_gain_pct'] = st.median(100*(1-p['d_ms']/p['v_ms']) for p in pairs)
    return result


def bootstrap(pairs):
    groups = defaultdict(list)
    for p in pairs:
        groups[p['line']].append(p)
    sums = [(sum(p['v_ms'] for p in g), sum(p['d_ms'] for p in g)) for g in groups.values()]
    if not sums:
        return None
    rng = random.Random(4259); values = []
    for _ in range(4000):
        sampled = rng.choices(sums, k=len(sums))
        values.append(100*(1-sum(d for v,d in sampled)/sum(v for v,d in sampled)))
    return [replay.percentile(values, .025), replay.percentile(values, .975)]


def audit_folder(folder, cases, tokenizer, source_hash):
    manifest = read(folder/'manifest.json')
    assert manifest['input_sha256'] == source_hash
    chosen = {line: cases[line] for line in manifest['selected_lines']}
    rows = list(lines(folder/'summary.jsonl'))
    expected = [(c.case_id, a, v) for c in chosen.values()
                for a in range(1,manifest['repetitions']+1)
                for v in (manifest['variants'][::-1] if a % 2 == 0 and manifest['variant_order']=='alternating' else manifest['variants'])]
    assert [(r['case_id'],r['attempt'],r['variant']) for r in rows] == expected, folder
    assert replay.build_paired_summary([replay.Observation(**r) for r in rows]) == read(folder/'paired_summary.json'), folder
    by_id = {r['client_request_id']: r for r in rows}
    assert len(by_id) == len(rows)
    args = SimpleNamespace(**manifest['request_overrides'], stream=manifest['stream'], model=manifest['model_override'] or '')
    current = None; events = []; event_index = done = total = 0; order = []
    def finish():
        if current is None:
            return
        row = by_id[current]
        assert done == 1, (current,done)
        rebuilt = asdict(replay.assemble_observation(
            **{k:row[k] for k in ('line_no','attempt','variant','client_request_id','status_code','elapsed_ms',
                                  'expected_tool_call','case_id','category','latency_eligible','error',
                                  'first_chunk_ms','first_output_ms','first_reasoning_ms','first_content_ms','first_tool_call_ms')},
            expected_tool_names=tuple(row['expected_tool_names']),
            request_tools=chosen[row['line_no']].request_body.get('tools'), chunks=events))
        assert rebuilt == row, (current,[k for k in row if rebuilt[k]!=row[k]])
    for record in lines(folder/'chunks.jsonl'):
        if record.get('record_type')=='request':
            finish(); current=record['client_request_id'];order.append(current)
            row=by_id[current]; case=chosen[row['line_no']]
            body=replay.build_body(case.request_body,variant=row['variant'],client_request_id=current,args=args)
            assert replay.json_hash(body)==record['request_body_sha256'], current
            events=[];event_index=done=0
        else:
            event_index+=1;total+=1
            assert record['client_request_id']==current and record['event_index']==event_index
            assert not record['parse_error']
            parsed=replay.parse_sse_data(record['raw_sse_line'])
            assert parsed==record['chunk']
            if parsed is not None:
                assert not done
                events.append(parsed)
            elif record['raw_sse_line'].strip()=='data: [DONE]':
                done+=1
    finish(); assert order==list(by_id)
    for row in rows:
        row['reasoning_tokens']=len(tokenizer.encode(row['reasoning'],add_special_tokens=False))
        row['content_tokens']=len(tokenizer.encode(row['content'],add_special_tokens=False))
        starts=[row[k] for k in ('first_content_ms','first_tool_call_ms') if row[k] is not None and row[k] > 0]
        row['answer_start_ms']=min(starts) if starts else None
        row['folder']=folder.name
    return rows, {'folder':folder.name,'requests':len(rows),'events':total,
                  'selected_lines':manifest['selected_lines'],'created_at':manifest['created_at'],
                  'overrides':manifest['request_overrides'],'all_chunks_rebuilt':True,'all_request_hashes_match':True}


def main():
    from transformers import AutoTokenizer
    tokenizer=AutoTokenizer.from_pretrained(ROOT/'docs/tokenizer/Qwen3.6-27B',local_files_only=True,trust_remote_code=False)
    cases={c.line_no:c for c in replay.load_cases(DATA,1,0)}
    source_hash=hashlib.sha256(DATA.read_bytes().replace(b'\r\n',b'\n')).hexdigest()
    selected=[int(x) for x in (OUT/'selected_lines.txt').read_text().strip().split(',')]
    selection={r['line_no']:r for r in read(OUT/'selection.json')['cases']}
    expected_folders={f'l{n}_{policy}_{stage}' for n in selected for policy in ('direct750','probe750') for stage in ('warm','measured')}
    actual_folders={f.name for f in OUT.iterdir() if f.is_dir() and (f/'manifest.json').exists()}
    assert expected_folders==actual_folders
    all_rows=[]; audits=[]; measured=[]; phase_rows=[]; phase_audits=[]
    for i,name in enumerate(sorted(actual_folders)):
        rows,audit=audit_folder(OUT/name,cases,tokenizer,source_hash)
        all_rows+=rows;audits.append(audit)
        if name.endswith('_measured'):
            policy=name.split('_')[1]
            expected_budget=750 if policy=='direct750' else 2048
            assert audit['overrides']==dict(max_tokens=None,temperature=None,top_p=None,seed=20260918,
                fixed_budget_tokens=expected_budget,fixed_check_length=750,fixed_think_stride=256,
                fixed_num_thinking=4,fixed_min_think_tokens=1,fixed_prob_th=.95)
            for r in rows:r['policy']=policy
            measured+=rows
        if (i+1)%32==0:print('audited folders',i+1,flush=True)
    assert len({r['client_request_id'] for r in all_rows})==len(all_rows)==576
    for folder in sorted(PHASE.iterdir()):
        if folder.is_dir() and (folder/'manifest.json').exists():
            rows,audit=audit_folder(folder,cases,tokenizer,source_hash)
            phase_rows+=rows;phase_audits.append(audit)
    grouped=defaultdict(dict)
    for r in measured:grouped[(r['policy'],r['line_no'],r['attempt'])][r['variant']]=r
    pairs=[]
    for (policy,line,attempt),g in grouped.items():
        v,d=g['vanilla'],g['dtb']
        p={'policy':policy,'line':line,'case_id':v['case_id'],'category':v['category'],'attempt':attempt,
           'historical_v_reasoning':selection[line]['v_reasoning'],'v_id':v['server_request_id'],'d_id':d['server_request_id']}
        for prefix,r in [('v',v),('d',d)]:
            p.update({prefix+'_ms':r['elapsed_ms'],prefix+'_first':r['first_output_ms'],prefix+'_answer_start':r['answer_start_ms'],
                      prefix+'_reasoning':r['reasoning_tokens'],prefix+'_content':r['content_tokens'],
                      prefix+'_completion':r['usage']['completion_tokens'],prefix+'_finish':r['finish_reasons'],
                      prefix+'_protocol':r['protocol_anomaly_reasons'],prefix+'_semantic':r['semantic_anomaly_reasons'],
                      prefix+'_error':r['error'],prefix+'_request_id':r['server_request_id']})
        p['same_final']=(v['content'],[(t['name'],t['arguments']) for t in v['tool_calls']])==(d['content'],[(t['name'],t['arguments']) for t in d['tool_calls']])
        p['same_trajectory']=p['same_final'] and v['reasoning']==d['reasoning']
        pairs.append(p)
    case_rows=[]
    for line in selected:
        row={'line':line,'case_id':cases[line].case_id,'historical_v_reasoning':selection[line]['v_reasoning']}
        lp=[p for p in pairs if p['line']==line]
        row['all8_v_le750']=all(p['v_reasoning']<=750 for p in lp)
        row['all8_v_gt750']=all(p['v_reasoning']>750 for p in lp)
        row['all8_d_le750']=all(p['d_reasoning']<=750 for p in lp)
        for policy in ('direct750','probe750'):
            ps=[p for p in lp if p['policy']==policy]
            row[policy]=summarize(ps)
            row[policy]['v_lengths']=[p['v_reasoning'] for p in ps]
            row[policy]['d_lengths']=[p['d_reasoning'] for p in ps]
            row[policy]['deltas_ms']=[p['d_ms']-p['v_ms'] for p in ps]
        row['direct_vs_probe_d_gain_pct']=100*(1-row['direct750']['ms']['d_mean']/row['probe750']['ms']['d_mean'])
        row['direct_vs_probe_v_gain_pct']=100*(1-row['direct750']['ms']['v_mean']/row['probe750']['ms']['v_mean'])
        case_rows.append(row)
    stable_short={r['line'] for r in case_rows if r['all8_v_le750']}
    stable_long={r['line'] for r in case_rows if r['all8_v_gt750']}
    result={'source_commit':'4c259cefd661250b7f7f47b8ef12295b6649ccb2',
            'user_confirmed_environment':{'apc':True,'observability':'off in both uploaded run directories',
                                          'loaded_dtb_fix':'fd60e41 direct-final gate fix',
                                          'server_phase_logs':'not enabled, not merely missing from upload'},
            'off_requests':len(all_rows),'off_measured':len(measured),'phase_requests':len(phase_rows),
            'off_events':sum(a['events'] for a in audits),'phase_folders':phase_audits,
            'stable_short_lines':sorted(stable_short),'stable_long_lines':sorted(stable_long),'policies':{}}
    for policy in ('direct750','probe750'):
        ps=[p for p in pairs if p['policy']==policy]
        cohorts={'all_background':ps,'historical_le512':[p for p in ps if p['historical_v_reasoning']<=512],
                 'historical_513_750':[p for p in ps if 512<p['historical_v_reasoning']<=750],
                 'historical_751_1024':[p for p in ps if 750<p['historical_v_reasoning']<=1024],
                 'historical_gt1024':[p for p in ps if p['historical_v_reasoning']>1024],
                 'current_stable_short_cases':[p for p in ps if p['line'] in stable_short],
                 'current_stable_long_cases':[p for p in ps if p['line'] in stable_long],
                 'both_reasoning_le750_POSTHOC':[p for p in ps if max(p['v_reasoning'],p['d_reasoning'])<=750],
                 'same_trajectory_POSTHOC':[p for p in ps if p['same_trajectory']],
                 'same_final_POSTHOC':[p for p in ps if p['same_final']]}
        result['policies'][policy]={name:{**summarize(values),'cluster_bootstrap_95_gain':bootstrap(values)} for name,values in cohorts.items()}
        result['policies'][policy]['anomalies']={}
        for variant in ('vanilla','dtb'):
            rs=[r for r in measured if r['policy']==policy and r['variant']==variant]
            result['policies'][policy]['anomalies'][variant]={
                'protocol':dict(Counter(a for r in rs for a in r['protocol_anomaly_reasons'])),
                'semantic':dict(Counter(a for r in rs for a in r['semantic_anomaly_reasons'])),
                'finish':dict(Counter(a for r in rs for a in r['finish_reasons'])),
                'errors':sum(bool(r['error']) or r['status_code']!=200 for r in rs),
                'empty_final_without_tools':sum(not r['content'].strip() and not r['tool_calls'] for r in rs),
                'empty_final_cases':[{'line':r['line_no'],'attempt':r['attempt'],'finish':r['finish_reasons']} for r in rs if not r['content'].strip() and not r['tool_calls']]}
    save('audit_4c259c.json',result);save('folder_audit.json',audits)
    save('case_metrics.json',case_rows);save('paired_metrics.json',pairs)
    save('request_metrics.json',[{k:v for k,v in r.items() if k not in ('reasoning','content','tool_calls')} for r in measured+phase_rows])
    print(json.dumps({k:v for k,v in result.items() if k!='policies'},ensure_ascii=True,indent=2))
    for r in case_rows:
        d=r['direct750']; print(r['line'], 'short',r['all8_v_le750'],'long',r['all8_v_gt750'],
          'direct gain',round(d['ms']['sum_gain_pct'],2),'faster',d['faster'],'V/D rea',d['v_lengths'],d['d_lengths'],flush=True)


if __name__=='__main__':main()
