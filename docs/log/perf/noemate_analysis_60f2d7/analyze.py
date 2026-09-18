"""Read-only audit of the uploaded Noemate derived snapshot; run from TB root."""
from __future__ import annotations
import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))
OUT = Path(__file__).resolve().parent
CAP = ROOT / 'proxy/results/noemate/agent_20260917T123724Z_7bac2ba7'
REPORT = CAP / 'derived/27c30b983f78c49b86f3'

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def save(name, value):
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()

def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1048576), b''): h.update(block)
    return h.hexdigest()

def csv_file(name, values):
    if not values: return
    with (OUT/name).open('w', encoding='utf-8', newline='') as f:
        w=csv.DictWriter(f, fieldnames=list(values[0])); w.writeheader(); w.writerows(values)

def summary(rows):
    values=sorted(r['reasoning_tokens_local'] for r in rows if r['reasoning_tokens_local'] is not None)
    n=len(values); total=sum(values)
    return {'n':n,'sum_reasoning':total,'mean':total/n if n else None,
            **{f'p{p}':values[math.ceil(n*p/100)-1] if n else None for p in (50,75,90,95,99)},
            'max':max(values,default=None)}

def main():
    rows=[json.loads(s) for s in (REPORT/'requests.jsonl').read_text(encoding='utf-8').splitlines()]
    assert len(rows)==len({r['request_id'] for r in rows})
    integrity=read(REPORT/'integrity.json'); manifest=read(CAP/'manifest.json'); analysis=read(REPORT/'analysis_manifest.json')
    assert len(rows)==integrity['known_request_count']==integrity['selected_request_count']==12214
    assert manifest['config_hash']==digest(manifest['config'])==analysis['config_hash']
    assert analysis['capture_id']==manifest['capture_id']
    assert integrity==analysis['integrity']
    processes=[read(p) for p in (CAP/'processes').glob('*.json')]
    assert processes==integrity['processes']
    fields=('reasoning_tokens_local','content_tokens_local','tool_tokens_local','prompt_tokens_api','completion_tokens_api')
    old_csv={r['request_id']:r for r in csv.DictReader((REPORT/'token_usage.csv').open(encoding='utf-8'))}
    assert len(old_csv)==len(rows)
    for r in rows:
        for k in fields: assert old_csv[r['request_id']][k]==('' if r[k] is None else str(r[k]))
    source_files=[CAP/'manifest.json',CAP/'.gitignore',REPORT/'.gitignore',CAP/'shutdown_analysis.json',*sorted((CAP/'processes').glob('*.json'))]
    source_files += [REPORT/name for name in ('analysis_manifest.json','integrity.json','requests.jsonl','token_usage.csv','distribution.json','threshold_scan.csv','trajectory_edges.jsonl','trajectories.jsonl')]
    save('source_manifest.json',{'source_commit':'60f2d7c01faa3268874893dc42f850ad86086c7c',
        'capture_id':manifest['capture_id'],'analysis_id':analysis['analysis_id'],
        'raw_archive_present':(CAP/'archive').exists(),
        'ignored':{str(p.relative_to(CAP)):p.read_text(encoding='utf-8') for p in CAP.rglob('.gitignore')},
        'files':[{'path':p.relative_to(ROOT).as_posix(),'bytes':p.stat().st_size,'sha256':file_hash(p)} for p in source_files]})
    enriched=[]; warnings=Counter(); params=Counter(); cohorts=defaultdict(list)
    body_groups=defaultdict(list); user_groups=defaultdict(list); sys_groups=Counter(); detail_hashes=[]
    detail_token_mismatch=[]
    for idx,r in enumerate(rows):
        p=REPORT/r['detail_json']; data=p.read_bytes(); x=json.loads(data)
        detail_hashes.append({'request_id':r['request_id'],'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()})
        req=x['request']; response=x['response']; messages=req.get('messages',[])
        assert response['parse_status']==r['parse_status']
        tc=x['tokens'].get('choices',[])
        if len(tc)==1 and tc[0]['reasoning']['value']!=r['reasoning_tokens_local']: detail_token_mismatch.append(r['request_id'])
        warnings.update(x['warnings'])
        param={k:v for k,v in req.items() if k not in {'messages','tools'}}
        params[json.dumps(param,ensure_ascii=False,sort_keys=True)]+=1
        user=next((m.get('content') for m in reversed(messages) if m.get('role')=='user'),None)
        user_text=user if isinstance(user,str) else json.dumps(user,ensure_ascii=False)
        system=[m for m in messages if m.get('role') in {'system','developer'}]
        human=next((m.get('content') for m in reversed(messages) if m.get('role')=='user'
                    and isinstance(m.get('content'),str) and not m['content'].lstrip().startswith('<skill_content')),None)
        sys_key=digest(system); sys_groups[sys_key]+=1
        body_hash=digest(req); user_hash=digest(user)
        body_groups[body_hash].append(r['request_id']); user_groups[user_hash].append(r['request_id'])
        e={**r,'request_sha256':body_hash,'last_user_sha256':user_hash,'system_sha256':sys_key,
           'request_without_limit_sha256':digest({k:v for k,v in req.items() if k not in {'max_tokens','max_completion_tokens'}}),
           'last_non_skill_user_sha256':digest(human),'last_non_skill_user_preview':(human or '')[:250],
           'request_stream':req.get('stream',False),'max_tokens_request':req.get('max_tokens'),
           'message_count':len(messages),'tools_count':len(req.get('tools') or []),
           'tool_result_count':sum(m.get('role')=='tool' for m in messages),
           'last_user_preview':user_text[:200],'detail_bytes':len(data),
           'warnings':x['warnings'],'request_parameters':param}
        enriched.append(e)
        cohort=('stream' if e['request_stream'] else 'nonstream')+'/'+str(e['max_tokens_request'])
        cohorts[cohort].append(e)
        if idx%2000==0: print(f'details audited: {idx}/{len(rows)}',flush=True)
    assert not detail_token_mismatch
    save('detail_hashes.json',detail_hashes)
    with (OUT/'request_features.jsonl').open('w',encoding='utf-8',newline='\n') as f:
        for e in enriched: f.write(json.dumps(e,ensure_ascii=False)+'\n')
    eligible=[r for r in enriched if not r['exclusions']]
    reproduced=summary(eligible)
    original=next(iter(read(REPORT/'distribution.json').values()))
    for key in ('mean','max','p50','p75','p90','p95','p99'): assert reproduced[key]==original[key]
    assert len(eligible)==original['eligible_count']
    scans=[]
    for h in (256,512,600,650,750,800,850,1000,1024,1500,2048,4096):
        tail=[r for r in eligible if r['reasoning_tokens_local']>h]
        excess=sum(r['reasoning_tokens_local']-h for r in tail)
        scans.append({'threshold':h,'n':len(eligible),'tail_n':len(tail),'tail_fraction':len(tail)/len(eligible),
          'tail_token_share':sum(r['reasoning_tokens_local'] for r in tail)/reproduced['sum_reasoning'],
          'excess_tokens':excess,'excess_fraction':excess/reproduced['sum_reasoning'],
          'mean_excess_per_intervened_request':excess/len(tail) if tail else 0})
    for original_scan in original['threshold_scan']:
        actual=next(s for s in scans if s['threshold']==original_scan['threshold'])
        for k in ('n','tail_fraction','tail_token_share','excess_tokens'): assert actual[k]==original_scan[k]
    csv_file('threshold_scan.csv',scans)
    cohort_stats=[]
    for key,group in sorted(cohorts.items()):
        good=[r for r in group if not r['exclusions']]
        cohort_stats.append({'cohort':key,'total':len(group),'eligible':len(good),
            'parse_error':sum(r['parse_status']=='parse_error' for r in group),
            'completion_at_limit':sum(r['completion_tokens_api']==r['max_tokens_request'] for r in group if r['completion_tokens_api'] is not None),
            **{k:v for k,v in summary(good).items() if k!='n'}})
    csv_file('cohorts.csv',cohort_stats)
    hourly=[]
    for hour in sorted({r['bucket'] for r in enriched}):
        group=[r for r in enriched if r['bucket']==hour]; good=[r for r in group if not r['exclusions']]
        hourly.append({'hour_utc':hour,'total':len(group),'eligible':len(good),
                       'parse_error':sum(r['parse_status']=='parse_error' for r in group),**summary(good)})
    csv_file('hourly.csv',hourly)
    strata=[]
    def prompt_band(r):
        x=r['prompt_tokens_api']
        return 'unknown' if x is None else next((label for lim,label in ((4096,'0-4096'),(16384,'4097-16384'),(65536,'16385-65536')) if x<=lim),'>65536')
    for dimension,fn in [('prompt',prompt_band),('tools',lambda r:','.join(r['tool_names']) or 'no_tool'),('max_tokens',lambda r:str(r['max_tokens_request']))]:
        groups=defaultdict(list)
        for r in eligible: groups[fn(r)].append(r)
        for key,group in sorted(groups.items()): strata.append({'dimension':dimension,'value':key,**summary(group)})
    csv_file('strata.csv',strata)
    byid={r['request_id']:r for r in enriched}
    limit_pairs=[]
    for r in enriched:
        if r['max_tokens_request']!=16384: continue
        matches=[q for q in enriched if q['max_tokens_request']==2048
                 and q['request_without_limit_sha256']==r['request_without_limit_sha256']]
        limit_pairs.append({'high_limit_id':r['request_id'],'high_limit_reasoning':r['reasoning_tokens_local'],
          'high_limit_completion':r['completion_tokens_api'],'high_limit_e2e_ms':r['e2e_ms'],
          'matches':[{'request_id':q['request_id'],'finish':q['finish_reasons'],'reasoning':q['reasoning_tokens_local'],
                      'completion':q['completion_tokens_api'],'e2e_ms':q['e2e_ms'],
                      'gap_to_high_limit_start_seconds':(datetime.fromisoformat(r['started_at_utc'])-datetime.fromisoformat(q['completed_at_utc'])).total_seconds()}
                      for q in matches]})
    save('output_limit_pairs.json',limit_pairs)
    dedup=[byid[next(rid for rid in ids if not byid[rid]['exclusions'])] for ids in body_groups.values() if any(not byid[rid]['exclusions'] for rid in ids)]
    # Explicit sensitivity analysis: earliest valid realization per exact input, not task weights.
    unique_stats=summary(dedup)
    topic_stats=[]
    for key,ids in user_groups.items():
        group=[byid[rid] for rid in ids]; good=[r for r in group if not r['exclusions']]
        topic_stats.append({'last_user_sha256':key,'preview':group[0]['last_user_preview'],
            'total':len(group),'eligible':len(good),'n_gt_850':sum(r['reasoning_tokens_local']>850 for r in good),**summary(good)})
    save('last_user_groups.json',sorted(topic_stats,key=lambda r:-r['sum_reasoning']))
    t0=min(datetime.fromisoformat(r['started_at_utc']) for r in rows); t1=max(datetime.fromisoformat(r['completed_at_utc']) for r in rows)
    events=[]
    for r in rows: events += [(datetime.fromisoformat(r['started_at_utc']).timestamp(),1),(datetime.fromisoformat(r['completed_at_utc']).timestamp(),-1)]
    events.sort(); current=maximum=0; area=0.; prev=events[0][0]
    for t,delta in events: area+=current*(t-prev); current+=delta; maximum=max(maximum,current); prev=t
    top=sorted(eligible,key=lambda r:-r['reasoning_tokens_local'])
    selection=[]; seen=set()
    # Diverse long-case seeds by latest user content; this is not session attribution.
    for r in top:
        if r['last_non_skill_user_sha256'] not in seen:
            selection.append(r); seen.add(r['last_non_skill_user_sha256'])
        if len(selection)==12: break
    selection+=sorted([r for r in enriched if r['finish_reasons']==['length']],key=lambda r:-(r['reasoning_tokens_local'] or 0))[:3]
    selection += [next(r for r in enriched if r['parse_status']=='parse_error' and not r['request_stream']),
                  next(r for r in enriched if r['parse_status']=='parse_error' and r['request_stream'])]
    save('selected_samples.json',[{'request_id':r['request_id'],'reasoning_tokens_local':r['reasoning_tokens_local'],
         'completion_tokens_api':r['completion_tokens_api'],'prompt_tokens_api':r['prompt_tokens_api'],
         'e2e_ms':r['e2e_ms'],'max_tokens':r['max_tokens_request'],'tool_names':r['tool_names'],
         'exclusions':r['exclusions'],'last_user_preview':r['last_user_preview'],
         'last_non_skill_user_preview':r['last_non_skill_user_preview'],
         'source_detail':(REPORT/r['detail_json']).relative_to(ROOT).as_posix(),
         'source_readable':(REPORT/r['detail_markdown']).relative_to(ROOT).as_posix(),
         'request_sha256':r['request_sha256'],'replay_status':'not_run'} for r in selection])
    audit={'total':len(rows),'eligible':len(eligible),'eligible_fraction':len(eligible)/len(rows),
      'server_reported_integrity':integrity,'raw_bytes_independently_verified':False,
      'all_detail_files_audited':len(detail_hashes),'detail_total_bytes':sum(x['bytes'] for x in detail_hashes),
      'warnings':dict(warnings),'excluded_reasons':dict(Counter(e for r in rows for e in r['exclusions'])),
      'parameters':[{'parameters':json.loads(k),'n':n} for k,n in params.most_common()],
      'first_request_utc':t0.isoformat(),'last_completion_utc':t1.isoformat(),'observed_span_hours':(t1-t0).total_seconds()/3600,
      'observed_max_concurrent_requests':maximum,'observed_mean_inflight':area/(t1-t0).total_seconds(),
      'unique_request_bodies':len(body_groups),'repeated_body_groups':sum(len(v)>1 for v in body_groups.values()),
      'unique_last_user_contents':len(user_groups),'unique_system_prompts':len(sys_groups),
      'eligible_stats':reproduced,'earliest_valid_per_exact_body_stats':unique_stats,
      'top_1pct_reasoning_share':sum(r['reasoning_tokens_local'] for r in top[:math.ceil(len(top)*.01)])/reproduced['sum_reasoning'],
      'top_5pct_reasoning_share':sum(r['reasoning_tokens_local'] for r in top[:math.ceil(len(top)*.05)])/reproduced['sum_reasoning']}
    save('audit.json',audit)
    role_stats=[]
    for sys_key,n in sys_groups.most_common():
        group=[r for r in enriched if r['system_sha256']==sys_key]
        example=read(REPORT/group[0]['detail_json'])['request']['messages']
        preview='\n'.join(str(m.get('content','')) for m in example if m.get('role') in {'system','developer'})[:500]
        role_stats.append({'system_sha256':sys_key,'total':n,'prompt_excerpt':preview,
          'eligible':sum(not r['exclusions'] for r in group),
          'source_example_request_id':group[0]['request_id'],
          'request_parameters':group[0]['request_parameters'],
          **summary([r for r in group if not r['exclusions']])})
    save('prompt_roles.json',role_stats)
    from proxy.observation.chat_completions import parse_response
    response={'model':'fixture','choices':[{'index':0,'message':{'role':'assistant','content':'ok',
               'function_call':None,'tool_calls':None,'reasoning':'observable reasoning'},'finish_reason':'stop'}]}
    broken=parse_response(json.dumps(response).encode(),[['content-type','application/json']],['reasoning_content','reasoning'])
    response['choices'][0]['message'].pop('function_call'); response['choices'][0]['message'].pop('tool_calls')
    control=parse_response(json.dumps(response).encode(),[['content-type','application/json']],['reasoning_content','reasoning'])
    assert broken['parse_status']=='parse_error' and broken['choices'][0]['reasoning'] is None
    assert control['parse_status']=='complete' and control['choices'][0]['reasoning']=='observable reasoning'
    save('parser_reproduction.json',{'synthetic_not_raw_response':True,'null_optional_fields':broken,'omitted_optional_fields':control})
    save('excluded_request_ids.json',{key:[r['request_id'] for r in enriched if test(r)] for key,test in {
        'nonstream_parse_error':lambda r:r['parse_status']=='parse_error' and not r['request_stream'],
        'stream_parse_error':lambda r:r['parse_status']=='parse_error' and r['request_stream'],
        'length_truncated':lambda r:r['finish_reasons']==['length'],
        'missing_reasoning':lambda r:'reasoning_unknown' in r['exclusions'] and r['parse_status']=='complete'}.items()})
    print(json.dumps({k:v for k,v in audit.items() if k not in {'parameters','server_reported_integrity'}},ensure_ascii=True),flush=True)

if __name__=='__main__': main()
