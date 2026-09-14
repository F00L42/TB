"""Read-only recomputation for the 2026-09-14 report; no model calls."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent

def read(rel):
    return json.loads((ROOT / rel).read_text(encoding='utf-8-sig'))

analysis = read('docs/log/perf/agent_run07_08/analysis.json')
variants = {}
for name, variant in analysis['variants'].items():
    tasks = list(variant['tasks'].values())
    requests = variant['requests']
    assert len(tasks) == 9
    assert len({r['request_id'] for r in requests}) == len(requests)
    total = {k: sum(t[k] for t in tasks) for k in
             ['llm_e2e_s', 'reasoning_tokens_local', 'completion_tokens', 'request_count']}
    total['start_to_end_s'] = sum(t['xlsx']['start_to_end_s'] for t in tasks)
    total['create_to_end_s'] = sum(t['xlsx']['create_to_end_s'] for t in tasks)
    assert abs(total['llm_e2e_s'] - sum(r['metrics']['e2e_ms'] for r in requests) / 1000) < 1e-6
    variants[name] = {'totals': total, 'tasks': [
        {k: t[k] for k in ['case','llm_e2e_s','reasoning_tokens_local','completion_tokens','request_count','root_prompt_hash']}
        for t in sorted(tasks, key=lambda t: int(t['case']))]}
base_hash = {t['case']:t['root_prompt_hash'] for t in variants['vanilla']['tasks']}
assert all({t['case']:t['root_prompt_hash'] for t in v['tasks']} == base_hash for v in variants.values())
base = variants['vanilla']['totals']
for name in ['run07','run08']:
    variants[name]['reduction_percent'] = {k: 100*(1-v/base[k]) for k,v in variants[name]['totals'].items()}
assert base['reasoning_tokens_local'] == 52112
assert variants['run08']['totals']['reasoning_tokens_local'] == 39759

ev = read('docs/log/perf/agent_run06/analysis_evidence.json')
dist = [d for d in ev['thinking_distribution'] if d['variant'] == 'vanilla']
all_dist = next(d for d in dist if d['role'] == 'all')
tail = next(t for t in all_dist['thresholds'] if t['T'] == 850)
tail_tokens = tail['residual_tokens'] + tail['above'] * 850
assert tail_tokens == 20263
excluded = set(all_dist['excluded_request_ids'])
valid_requests = [r for r in analysis['variants']['vanilla']['requests'] if r['request_id'] not in excluded]
assert len(valid_requests) == 391 and all(r['terminal_count'] > 0 for r in valid_requests)
assert sum(r['metrics']['reasoning_tokens_local'] for r in valid_requests) == 52112
bins = []
for label, lower, upper in [('≤20', 0, 20), ('21–300', 21, 300), ('301–850', 301, 850), ('>850', 851, None)]:
    members = [r for r in valid_requests if r['metrics']['reasoning_tokens_local'] >= lower
               and (upper is None or r['metrics']['reasoning_tokens_local'] <= upper)]
    tokens = sum(r['metrics']['reasoning_tokens_local'] for r in members)
    bins.append({'label':label, 'lower_inclusive':lower, 'upper_inclusive':upper,
                 'request_count':len(members), 'reasoning_tokens':tokens,
                 'request_share':len(members)/391, 'token_share':tokens/52112,
                 'request_ids':[r['request_id'] for r in members]})
assert sum(b['request_count'] for b in bins) == 391
assert sum(b['reasoning_tokens'] for b in bins) == 52112
assert bins[-1]['request_count'] == 11 and bins[-1]['reasoning_tokens'] == tail_tokens

shard = 'proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed/full_log/proxy_session_795d073fadb0/part-00001.jsonl'
record = json.loads((ROOT / shard).read_text(encoding='utf-8').splitlines()[19])
assert record['request_id'] == '0480aca87728475f'
assert record['response_data']['reasoning'].startswith('I see the results:')
assert 'Tests run' in record['response_data']['content']
reasoning = record['response_data']['reasoning']
quote_fragments = ['I see the results:', '- Tests run: 3', '- Failures: 1', '- 2 tests passed:',
                   "Let me count: That's 31 indices total",
                   'But then the count should be 34, not 32', "I'm getting 31 items total"]
assert all(fragment in reasoning for fragment in quote_fragments)
quotes = [{'text':fragment, 'start_char':reasoning.index(fragment),
           'end_char':reasoning.index(fragment)+len(fragment)} for fragment in quote_fragments]
server = 'proxy/results/qwen/local-task29/local-apiserver/dtb/run-06-closed/server_filtered_part_01'
events = []
for line in (ROOT / server).read_text(encoding='utf-8').splitlines():
    if 'chatcmpl-a385fe220c683c95' in line and ('DTB_PHASE {' in line or 'DTB_REQUEST_SUMMARY {' in line):
        events.append(json.loads(line[line.index('{'):]))
phases = [e for e in events if 'phase' in e]
summary = next(e for e in events if 'completion_status' in e)
assert summary['thinking_tokens'] == 850 and summary['hidden_probe_tokens'] == 50
assert summary['final_answer_tokens'] == 174 and summary['model_work_complete'] is False
sources = ['docs/report/agent_dtb_demo_data_memo.md', 'docs/report/DTB_Agent集成验证_技术博客.md',
           'docs/log/perf/agent_run07_08/RESULT.md','docs/log/perf/agent_run07_08/analysis.json',
           'docs/log/perf/agent_run06/analysis_evidence.json','docs/log/perf/agent_run06_closed/RESULT.md',
           'docs/log/perf/agent_run06_closed/analysis.json', shard, server]
out = {'generated_for':'sum0911, updated 2026-09-14', 'variants':variants,
       'quality_policy':{'denominator':9,'vanilla_pass':['28','34','35','36'],
                         'run08_pass':['2','28','34','35'],
                         'note':'Vanilla case35 explicitly uses raw 36/36; formal report absent. See blog section 3.2.'},
       'vanilla_distribution':dist, 'vanilla_distribution_bins':bins,
       'long_tail':{'count':tail['above'],'tokens':tail_tokens,'request_share':tail['above']/391,
                    'token_share':tail_tokens/52112,'residual_tokens':tail['residual_tokens']},
       'case37':{'request_id':record['request_id'],'metrics':record['metrics'],
                 'reasoning_quote_fragments':quotes,
                 'phases':phases,'summary':summary,'phase_sum_s':sum(p['wall_ms'] for p in phases)/1000},
       'sources':[{'path':p,'sha256':hashlib.sha256((ROOT/p).read_bytes()).hexdigest()} for p in sources]}
(OUT/'evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'totals':{k:v['totals'] for k,v in variants.items()},'tail_tokens':tail_tokens,
                  'case37_phase_sum_s':out['case37']['phase_sum_s']},ensure_ascii=False))
