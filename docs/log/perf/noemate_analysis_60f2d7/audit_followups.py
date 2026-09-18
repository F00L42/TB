"""Audit observed concurrency, output-limit retry candidates, and sample tokenizer counts."""
from pathlib import Path
from datetime import datetime
import difflib
import hashlib
import importlib.metadata
import json

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]
REPORT=ROOT/'proxy/results/noemate/agent_20260917T123724Z_7bac2ba7/derived/27c30b983f78c49b86f3'
def read(p):return json.loads(p.read_text(encoding='utf-8'))
def save(name,value):(OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def main():
    rows=[json.loads(s) for s in (OUT/'request_features.jsonl').read_text(encoding='utf-8').splitlines()]
    concurrency={}
    for name,group in [('all',rows),('main_stream',[r for r in rows if r['request_stream']]),('aux_nonstream',[r for r in rows if not r['request_stream']])]:
        events=sorted([(r['started_at_utc'],1) for r in group]+[(r['completed_at_utc'],-1) for r in group])
        current=peak=0
        for _,delta in events:current+=delta;peak=max(peak,current)
        concurrency[name]=peak
    pairs=[]
    for high in [r for r in rows if r['max_tokens_request']==16384]:
        low=max((r for r in rows if r['finish_reasons']==['length'] and r['completed_at_utc']<high['started_at_utc']),key=lambda r:r['completed_at_utc'])
        a=read(REPORT/low['detail_json'])['request'];b=read(REPORT/high['detail_json'])['request']
        assert {k:v for k,v in a.items() if k not in {'messages','max_tokens'}}=={k:v for k,v in b.items() if k not in {'messages','max_tokens'}}
        encode=lambda m:json.dumps(m,sort_keys=True,ensure_ascii=False)
        diff=difflib.SequenceMatcher(a=list(map(encode,a['messages'])),b=list(map(encode,b['messages'])),autojunk=False)
        edits=[]
        for tag,i,j,u,v in diff.get_opcodes():
            if tag=='equal':continue
            edits.append({'operation':tag,'old_indices':[i,j],'new_indices':[u,v],
                'old_messages':[{'role':m['role'],'preview':str(m.get('content'))[:250]} for m in a['messages'][i:j]],
                'new_messages':[{'role':m['role'],'preview':str(m.get('content'))[:250]} for m in b['messages'][u:v]]})
        pairs.append({'low_limit_id':low['request_id'],'high_limit_id':high['request_id'],
            'relation':'same_input_except_output_limit' if not edits else 'changed_context_candidate_retry',
            'gap_seconds':(datetime.fromisoformat(high['started_at_utc'])-datetime.fromisoformat(low['completed_at_utc'])).total_seconds(),
            'low_reasoning':low['reasoning_tokens_local'],'high_reasoning':high['reasoning_tokens_local'],
            'low_completion':low['completion_tokens_api'],'high_completion':high['completion_tokens_api'],
            'low_e2e_ms':low['e2e_ms'],'high_e2e_ms':high['e2e_ms'],'message_edits':edits})
    valid=[r for r in rows if not r['exclusions']]
    total=sum(r['reasoning_tokens_local']+r['content_tokens_local']+r['tool_tokens_local'] for r in valid)
    save('limit_retry_audit.json',{'concurrency_peaks':concurrency,'pairs':pairs,
        'same_input_pair_count':sum(not p['message_edits'] for p in pairs),
        'main_eligible_fraction':len(valid)/sum(r['request_stream'] for r in rows),
        'tail850_without_output_tools':sum(r['reasoning_tokens_local']>850 and not r['tool_names'] for r in valid),
        'local_output_component_tokens':total,'excess850_fraction_of_local_output_components':35952/total})
    from transformers import AutoTokenizer
    path=ROOT/'docs/tokenizer/Qwen3.6-27B';hasher=hashlib.sha256()
    for name in ('tokenizer.json','tokenizer_config.json','special_tokens_map.json','vocab.json','merges.txt','tokenizer.model'):
        file=path/name
        if file.is_file():hasher.update(name.encode());hasher.update(file.read_bytes())
    assert hasher.hexdigest()==read(REPORT/'analysis_manifest.json')['tokenizers']['run']['files_sha256']
    tokenizer=AutoTokenizer.from_pretrained(path,local_files_only=True,trust_remote_code=False)
    checks=[]
    for sample in read(OUT/'selected_samples.json'):
        choices=read(ROOT/sample['source_detail'])['response']['choices']
        if not choices or choices[0]['reasoning'] is None:continue
        n=len(tokenizer.encode(choices[0]['reasoning'],add_special_tokens=False))
        assert n==sample['reasoning_tokens_local']
        checks.append({'request_id':sample['request_id'],'tokens':n,'match':True})
    save('tokenizer_validation.json',{'files_sha256':hasher.hexdigest(),
        'local_transformers':importlib.metadata.version('transformers'),'server_transformers':'5.5.4','checks':checks})
    print(f'Concurrency: {concurrency}; pairs: {len(pairs)}; token counts validated: {len(checks)}')

if __name__=='__main__':main()
