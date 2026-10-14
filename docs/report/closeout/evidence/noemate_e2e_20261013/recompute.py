"""Recompute descriptive/standardized comparison from pinned capture features.

No new model requests. Bootstrap intervals condition on observed cases and runs;
they are exploratory and do not absorb temporal/configuration confounding.
"""
import argparse
import csv
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import heapq
import gzip
import json
import math
from pathlib import Path
import random
import statistics

ROOT=Path(__file__).resolve().parent
INPUT_ROOT=ROOT
OUTPUT_ROOT=Path.cwd()/'artifacts'/'perf'/'noemate_e2e_20261013_recomputed'
BOOTSTRAPS=5000
SEED=20261013


def read(name):
    path=INPUT_ROOT/(name+'.gz')
    with gzip.open(path,'rt',encoding='utf-8') as source:
        return [json.loads(line) for line in source if line.strip()]


def nearest(xs,p):
    return sorted(xs)[max(0,math.ceil(len(xs)*p)-1)] if xs else None


def percentile(xs,p):
    xs=sorted(xs)
    at=(len(xs)-1)*p
    lo=math.floor(at); hi=math.ceil(at)
    return xs[lo]+(xs[hi]-xs[lo])*(at-lo)


def latency_ok(row):
    return row['status_code']==200 and row['transport_status']=='completed' and row['e2e_ms'] is not None and row['e2e_ms']>0


def describe(rows):
    reasoning=[r['reasoning_tokens_local'] for r in rows if r['reasoning_tokens_local'] is not None and not r['exclusions']]
    result={'requests':len(rows),'reasoning_eligible':len(reasoning),
            'http_status':dict(Counter(str(r['status_code']) for r in rows)),
            'parse_status':dict(Counter(r['parse_status'] for r in rows)),
            'finish_reasons':dict(Counter(str(r['finish_reasons']) for r in rows)),
            'exclusions':dict(Counter(x for r in rows for x in r['exclusions'])),
            'latency_eligible':sum(latency_ok(r) for r in rows)}
    for name in ('e2e_ms','ttfb_ms','first_semantic_delta_ms','prompt_tokens_api','completion_tokens_api','content_tokens_local','tool_tokens_local','active_at_start'):
        values=[r[name] for r in rows if isinstance(r.get(name),(int,float))]
        result[name]={'n':len(values),'mean':statistics.mean(values) if values else None,
                      'p50':nearest(values,.5),'p95':nearest(values,.95),'p99':nearest(values,.99),
                      'sum':sum(values),'max':max(values) if values else None}
    result['reasoning']={'mean':statistics.mean(reasoning) if reasoning else None,
        'p50':nearest(reasoning,.5),'p95':nearest(reasoning,.95),'p99':nearest(reasoning,.99),
        'max':max(reasoning) if reasoning else None,
        'over_750':sum(r>750 for r in reasoning),'over_800':sum(r>800 for r in reasoning),
        'over_1000':sum(r>1000 for r in reasoning),
        'from_749_to_755':sum(749<=r<=755 for r in reasoning),
        'over_750_tokens':sum(r for r in reasoning if r>750),
        'excess_above_750_tokens':sum(max(0,r-750) for r in reasoning)}
    return result


def overlap(a,b,field):
    ca=Counter(r[field] for r in a); cb=Counter(r[field] for r in b)
    keys=sorted((ca.keys()&cb.keys())-{None})
    return {'common_cases':len(keys),'vanilla_unique':len(ca),'dtb_unique':len(cb),
            'vanilla_rows':sum(ca[k] for k in keys),'dtb_rows':sum(cb[k] for k in keys)}


def summarize_cases(cases,metric):
    pairs=[]
    for case in cases:
        va=[r[metric] for r in case['vanilla'] if r.get(metric) is not None]
        da=[r[metric] for r in case['dtb'] if r.get(metric) is not None]
        if va and da:pairs.append((statistics.mean(va),statistics.mean(da)))
    n=len(pairs)
    if not n:return {'cases':0}
    v=[p[0] for p in pairs]; d=[p[1] for p in pairs]
    rng=random.Random(SEED)
    ratios=[]; differences=[]
    for _ in range(BOOTSTRAPS):
        indices=rng.choices(range(n),k=n)
        vv=sum(v[i] for i in indices)/n
        dd=sum(d[i] for i in indices)/n
        ratios.append(dd/vv if vv else None)
        differences.append(dd-vv)
    ratios=[r for r in ratios if r is not None]
    return {'cases':n,'vanilla_mean':statistics.mean(v),'dtb_mean':statistics.mean(d),
            'ratio':sum(d)/sum(v) if sum(v) else None,
            'ratio_ci95':[percentile(ratios,.025),percentile(ratios,.975)] if ratios else None,
            'difference':statistics.mean(d)-statistics.mean(v),
            'difference_ci95':[percentile(differences,.025),percentile(differences,.975)],
            'faster_cases':sum(dd<vv for vv,dd in pairs)}


def grouped_cases(a,b,key):
    va=defaultdict(list); da=defaultdict(list)
    for r in a:
        if latency_ok(r) and r[key] is not None:va[r[key]].append(r)
    for r in b:
        if latency_ok(r) and r[key] is not None:da[r[key]].append(r)
    return [{'key':k,'vanilla':va[k],'dtb':da[k]} for k in sorted(va.keys()&da.keys())]


def compare_cases(cases):
    result={'case_count':len(cases),'vanilla_rows':sum(len(c['vanilla']) for c in cases),
            'dtb_rows':sum(len(c['dtb']) for c in cases),'scope':'exploratory_observed_cases_only'}
    for metric in ('e2e_ms','ttfb_ms','reasoning_tokens_local','prompt_tokens_api','completion_tokens_api'):
        result[metric]=summarize_cases(cases,metric)
    return result


def initial(row):
    return row['root_question_index'] is not None and row['message_count']==row['root_question_index']+1


def write_case_csv(cases,path):
    fields=['case_hash','question','vanilla_n','dtb_n','vanilla_reasoning_median','dtb_reasoning_mean',
            'vanilla_e2e_ms','dtb_e2e_ms','vanilla_prompt_tokens','dtb_prompt_tokens',
            'vanilla_finish','dtb_finish']
    with path.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fields);writer.writeheader()
        for c in cases:
            def mean(side,metric):
                values=[r[metric] for r in c[side] if r.get(metric) is not None]
                return statistics.mean(values) if values else None
            rs=[r['reasoning_tokens_local'] for r in c['vanilla'] if r['reasoning_tokens_local'] is not None]
            writer.writerow({'case_hash':c['key'],'question':c['vanilla'][0]['root_question_preview'],
                'vanilla_n':len(c['vanilla']),'dtb_n':len(c['dtb']),
                'vanilla_reasoning_median':statistics.median(rs) if rs else None,
                'dtb_reasoning_mean':mean('dtb','reasoning_tokens_local'),
                'vanilla_e2e_ms':mean('vanilla','e2e_ms'),'dtb_e2e_ms':mean('dtb','e2e_ms'),
                'vanilla_prompt_tokens':mean('vanilla','prompt_tokens_api'),
                'dtb_prompt_tokens':mean('dtb','prompt_tokens_api'),
                'vanilla_finish':str(Counter(str(r['finish_reasons']) for r in c['vanilla'])),
                'dtb_finish':str(Counter(str(r['finish_reasons']) for r in c['dtb']))})


def add_observed_concurrency(rows):
    # Gateway overlap is observable; engine scheduling and external traffic are not.
    active=[]
    for row in sorted(rows,key=lambda r:r['started_at_utc']):
        start=datetime.fromisoformat(row['started_at_utc']).timestamp()
        while active and active[0]<=start:heapq.heappop(active)
        row['active_at_start']=len(active)+1
        if row['completed_at_utc']:
            end=datetime.fromisoformat(row['completed_at_utc']).timestamp()
            if end>start:heapq.heappush(active,end)


def main():
    global INPUT_ROOT,OUTPUT_ROOT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=ROOT)
    parser.add_argument('--output-dir',type=Path,default=OUTPUT_ROOT)
    parser.add_argument('--verify',action='store_true',help='Verify frozen hashes and all statistical subtrees')
    args=parser.parse_args()
    INPUT_ROOT=args.data_dir.resolve()
    OUTPUT_ROOT=args.output_dir.resolve()
    if OUTPUT_ROOT==INPUT_ROOT:
        parser.error('Use a separate --output-dir; do not overwrite frozen evidence')
    OUTPUT_ROOT.mkdir(parents=True,exist_ok=True)
    if args.verify:
        manifest=json.loads((INPUT_ROOT/'bundle_manifest.json').read_text(encoding='utf-8'))
        for name,expected in manifest['files'].items():
            actual=hashlib.sha256((INPUT_ROOT/name).read_bytes()).hexdigest()
            if actual!=expected['sha256']:
                raise ValueError('Frozen evidence hash mismatch: '+name)
    samples={n:read(f'{n}_features.jsonl') for n in ('vanilla','dtb')}
    for rows in samples.values():add_observed_concurrency(rows)
    result={'scope':'gateway_model_request_E2E_not_verified_Agent_task_E2E',
        'user_confirmed_conditions':{'same_model_weights':True,'formal_DTB_observability':'off','compression_function_verified':True},
        'bootstrap':{'repetitions':BOOTSTRAPS,'seed':SEED,'sampling':'paired_case_cluster_means','ci':'percentile_95_two_sided',
            'limitation':'conditional_on_two_historical_runs; no run-level/confounder uncertainty'},
        'short_noninferiority_confirmed':False,'long_speedup_causally_identified':False,
        'descriptive':{},'overlap':{},'normalization':{},'initial_root_comparison':{}}
    for name,rows in samples.items():
        result['descriptive'][name]={scene:describe([r for r in rows if r['scene']==scene])
            for scene in ('main','followup','compaction','other') if any(r['scene']==scene for r in rows)}
    for scene in ('main','followup','compaction'):
        a=[r for r in samples['vanilla'] if r['scene']==scene]
        b=[r for r in samples['dtb'] if r['scene']==scene]
        result['overlap'][scene]={key:overlap(a,b,key) for key in ('request_hash','comparable_hash','messages_tools_hash','system_hash','root_question_hash')}
    a=[r for r in samples['vanilla'] if r['scene']=='followup']
    b=[r for r in samples['dtb'] if r['scene']=='followup']
    native=grouped_cases(a,b,'comparable_hash')
    result['native_same_input_control']=compare_cases(native)
    write_case_csv(native,OUTPUT_ROOT/'native_same_input_cases.csv')
    a=[r for r in samples['vanilla'] if r['scene']=='main' and initial(r)]
    b=[r for r in samples['dtb'] if r['scene']=='main' and initial(r)]
    cases=grouped_cases(a,b,'root_question_hash')
    write_case_csv(cases,OUTPUT_ROOT/'initial_root_cases.csv')
    short=[]; veryshort=[]; middle=[]; long=[]; unknown=[]
    for case in cases:
        rs=[r['reasoning_tokens_local'] for r in case['vanilla'] if r['reasoning_tokens_local'] is not None]
        if not rs:unknown.append(case);continue
        median=statistics.median(rs)
        (short if median<=750 else long).append(case)
        if median<=512:veryshort.append(case)
        elif median<=750:middle.append(case)
    for name,subset in [('short_le750',short),('short_le512',veryshort),('middle_513_750',middle),('long_gt750',long),('unknown',unknown)]:
        result['initial_root_comparison'][name]=compare_cases(subset)
    result['initial_root_comparison']['coverage']={'common_roots':len(cases),'vanilla_initial_rows':len(a),'dtb_initial_rows':len(b),
        'common_vanilla_rows':sum(len(c['vanilla']) for c in cases),'common_dtb_rows':sum(len(c['dtb']) for c in cases),
        'literal_same_input_pairs':0,'definition':'first non-memory/prefs user in message history; not QA_ID or full request equivalence'}
    a=[r for r in samples['vanilla'] if r['scene']=='main']; b=[r for r in samples['dtb'] if r['scene']=='main']
    result['normalization']['overlap']=overlap(a,b,'normalized_runtime_hash')
    result['normalization']['definition']='only two system runtime date/model lines and top-level model alias; all memory/tools/history retained'
    normalized=grouped_cases(a,b,'normalized_runtime_hash')
    result['normalization']['matched_cases']=compare_cases(normalized)
    write_case_csv(normalized,OUTPUT_ROOT/'runtime_normalized_main_cases.csv')
    result['source_hashes']={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in INPUT_ROOT.glob('*features.manifest.json')}
    result['observational_closeout']=json.loads((ROOT/'acceptance.json').read_text(encoding='utf-8'))
    if args.verify:
        reference=json.loads((ROOT/'comparison.json').read_text(encoding='utf-8'))
        for key in ('descriptive','overlap','normalization','initial_root_comparison','native_same_input_control'):
            if result[key]!=reference[key]:
                raise ValueError('Statistical result differs from frozen snapshot: '+key)
        for name in ('initial_root_cases.csv','native_same_input_cases.csv'):
            if (OUTPUT_ROOT/name).read_bytes()!=(ROOT/name).read_bytes():
                raise ValueError('Case CSV differs from frozen snapshot: '+name)
    (OUTPUT_ROOT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    print(json.dumps({'native_cases':len(native),'initial_roots':len(cases),'initial_short_cases':len(short),
                      'initial_long_cases':len(long),'normalized_main_cases':len(normalized),
                      'verification_passed':args.verify,'output':str(OUTPUT_ROOT)},ensure_ascii=False))


if __name__=='__main__':main()
