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
ranked = [{'rank':i+1, 'request_id':r['request_id'], 'role':r['role'],
           'reasoning_tokens':r['metrics']['reasoning_tokens_local'],
           'above_threshold_tokens':max(0,r['metrics']['reasoning_tokens_local']-850)}
          for i,r in enumerate(sorted(valid_requests,key=lambda r:(r['metrics']['reasoning_tokens_local'],r['request_id'])))]
assert not any(r['reasoning_tokens'] == 850 for r in ranked)
assert sum(r['above_threshold_tokens'] for r in ranked) == 10913
chronological = [{'sequence':i+1, 'request_id':r['request_id'], 'case':r['case'],
                  'role':r['role'], 'timestamp':r['timestamp'],
                  'reasoning_tokens':r['metrics']['reasoning_tokens_local']}
                 for i,r in enumerate(sorted(valid_requests,key=lambda r:(r['timestamp'],r['request_id'])))]
task_blocks = []
for r in chronological:
    if not task_blocks or task_blocks[-1]['case'] != r['case']:
        task_blocks.append({'case':r['case'], 'first_sequence':r['sequence'], 'last_sequence':r['sequence']})
    else:
        task_blocks[-1]['last_sequence'] = r['sequence']
assert len(task_blocks) == 9 and len({b['case'] for b in task_blocks}) == 9
assert {r['request_id'] for r in chronological} == {r['request_id'] for r in ranked}
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
sub_shard = 'proxy/results/qwen/local-task29/local-apiserver/dtb/run-06/full_log/proxy_session_1c94a63f8a4f/part-00001.jsonl'
sub_record = json.loads((ROOT/sub_shard).read_text(encoding='utf-8').splitlines()[0])
sub_trace = next(t for t in read('docs/log/perf/agent_run06/probe_analysis.json')['traces']
                 if t['request_id'] == '0aa42c61e0a5411e')
assert sub_record['request_id'] == sub_trace['request_id'] and sub_trace['role'] == 'subagent'
assert sub_trace['thinking_tokens'] == 851 and len(sub_trace['probes']) == 1
assert sub_trace['probes'][0]['accepted'] and sub_trace['probes'][0]['confidence'] == .985006
sub_reasoning = sub_record['response_data']['reasoning']
sub_final = sub_record['response_data']['content']
sub_fragments = ['Entities/Classes mentioned:', '- `SceneInfo` - has `sceneId`, `sceneCode`',
                 'From `ResponseAssemblerTest.java`:', '- `init()` (Before method)',
                 'Now let me look more carefully at the test method signatures from the diff:',
                 'From ResponseAssemblerTest.java:', '- `void init()` - @Before method']
assert all(t in sub_reasoning for t in sub_fragments)
final_fragment = '"void init() // ResponseAssemblerTest - @Before"'
assert final_fragment in sub_final and '"test_methods": [' in sub_final
sub_json = json.loads(sub_final.strip().removeprefix('```json').removesuffix('```').strip())
assert set(['file_info','data_structures','test_methods']) <= set(sub_json)
sources.extend([sub_shard, 'docs/log/perf/agent_run06/probe_analysis.json'])
subagent_case = {'case':'28', 'request_id':sub_trace['request_id'], 'parent_request_id':sub_trace['parent_request_id'],
    'source_file':sub_shard, 'source_line':1, 'trace':sub_trace, 'metrics':sub_record['metrics'],
    'reasoning_fragments':[{'text':t,'start_char':sub_reasoning.index(t)} for t in sub_fragments],
    'final_fragment':final_fragment, 'final_text':sub_final, 'reasoning_text':sub_reasoning,
    'quality_boundary':'Task final DT 12/12; no field-by-field JSON correctness audit.',
    'comparison_boundary':'Vanilla counterpart has 344 reasoning tokens vs DTB 851. Actual early-exit mechanism only, not a measured per-request compression gain.'}
out = {'generated_for':'sum0911, updated 2026-09-14', 'variants':variants,
       'quality_policy':{'denominator':9,'vanilla_pass':['28','34','35','36'],
                         'run08_pass':['2','28','34','35'],
                         'note':'Vanilla case35 explicitly uses raw 36/36; formal report absent. See blog section 3.2.'},
       'vanilla_distribution':dist, 'vanilla_distribution_bins':bins,
       'vanilla_ranked_requests':ranked,
       'vanilla_chronological_requests':chronological, 'vanilla_task_blocks':task_blocks,
       'long_tail':{'count':tail['above'],'tokens':tail_tokens,'request_share':tail['above']/391,
                    'token_share':tail_tokens/52112,'residual_tokens':tail['residual_tokens']},
       'subagent_case':subagent_case,
       'case37':{'request_id':record['request_id'],'metrics':record['metrics'],
                 'reasoning_quote_fragments':quotes,
                 'phases':phases,'summary':summary,'phase_sum_s':sum(p['wall_ms'] for p in phases)/1000},
       'sources':[{'path':p,'sha256':hashlib.sha256((ROOT/p).read_bytes()).hexdigest()} for p in sources]}
(OUT/'evidence.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
print(json.dumps({'totals':{k:v['totals'] for k,v in variants.items()},'tail_tokens':tail_tokens,
                  'case37_phase_sum_s':out['case37']['phase_sum_s']},ensure_ascii=False))
