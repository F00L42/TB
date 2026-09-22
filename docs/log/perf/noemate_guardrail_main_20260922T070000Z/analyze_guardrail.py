"""Audit 884a00 replay inputs/SSE and frozen per-case guardrail metrics offline."""
from collections import Counter, defaultdict
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import re
import statistics as st
from decimal import Decimal

OUT = Path(__file__).resolve().parent
SMOKE = OUT.parent / "noemate_guardrail_smoke_20260922T064608Z"
spec = importlib.util.spec_from_file_location("prior_audit", OUT.parent / "noemate_direct750_off_20260921T064359Z/analyze_4c259c.py")
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def same_output(a, b):
    def signature(r):
        return (r['reasoning'], r['content'], [(t['name'], t['arguments']) for t in r['tool_calls']])
    return signature(a) == signature(b)


def distribution(values):
    return dict(mean=st.mean(values), p50=st.median(values), p95=prior.replay.percentile(values, .95),
                min=min(values), max=max(values))


def paired(a, b, attempt, group):
    p = {'group': group, 'line': a['line_no'], 'attempt': attempt,
         'v_id': a['client_request_id'], 'd_id': b['client_request_id'],
         'same_output': same_output(a, b), 'v_tool': a['has_tool_call'], 'd_tool': b['has_tool_call']}
    for short, field in [('ms', 'elapsed_ms'), ('first', 'first_output_ms'),
                         ('reasoning', 'reasoning_tokens'), ('content', 'content_tokens')]:
        p['v_' + short], p['d_' + short] = a[field], b[field]
    p['v_completion'], p['d_completion'] = a['usage']['completion_tokens'], b['usage']['completion_tokens']
    return p


def summarize(pairs):
    result = {'pairs': len(pairs), 'faster': sum(p['d_ms'] < p['v_ms'] for p in pairs),
              'same_output_pairs': sum(p['same_output'] for p in pairs)}
    for field in ('ms', 'first', 'reasoning', 'content', 'completion'):
        v = [p['v_' + field] for p in pairs]; d = [p['d_' + field] for p in pairs]
        result[field] = {'v': distribution(v), 'd': distribution(d),
                         'delta_mean': st.mean(d) - st.mean(v),
                         'gain_pct': 100 * (1 - sum(d) / sum(v)) if sum(v) else None}
    # Resample adjacent two-pair blocks, retaining VD/DV together for V/DTB runs.
    blocks = [pairs[i:i+2] for i in range(0, len(pairs), 2)]
    rng = random.Random(88400)
    deltas = []; firsts = []; gains = []
    for _ in range(10000):
        sample = [p for block in rng.choices(blocks, k=len(blocks)) for p in block]
        deltas.append(st.mean(p['d_ms']-p['v_ms'] for p in sample))
        firsts.append(st.mean(p['d_first']-p['v_first'] for p in sample))
        gains.append(100 * (1-sum(p['d_ms'] for p in sample)/sum(p['v_ms'] for p in sample)))
    result['block_bootstrap'] = {
        'blocks': len(blocks), 'resamples': 10000, 'seed': 88400,
        'interpretation': 'exploratory small-block interval; no inference for a single block',
        'e2e_delta_two_sided_95_ms': [prior.replay.percentile(deltas, q) for q in (.025, .975)],
        'e2e_delta_one_sided_95_upper_ms': prior.replay.percentile(deltas, .95),
        'first_delta_one_sided_95_upper_ms': prior.replay.percentile(firsts, .95),
        'gain_two_sided_95_pct': [prior.replay.percentile(gains, q) for q in (.025, .975)]}
    result['proposed_gate'] = {'e2e_margin_ms': max(20, .01*result['ms']['v']['mean']),
                               'first_margin_ms': 10}
    result['proposed_gate']['e2e_pass'] = result['block_bootstrap']['e2e_delta_one_sided_95_upper_ms'] <= result['proposed_gate']['e2e_margin_ms']
    result['proposed_gate']['first_pass'] = result['block_bootstrap']['first_delta_one_sided_95_upper_ms'] <= 10
    matched = [p for p in pairs if p['same_output']]
    result['same_output_sensitivity'] = {
        'pairs': len(matched),
        'delta_ms': distribution([p['d_ms']-p['v_ms'] for p in matched]) if matched else None,
        'first_delta_ms': distribution([p['d_first']-p['v_first'] for p in matched]) if matched else None}
    result['by_order'] = {str(parity): {'pairs': len(s), 'delta_ms': st.mean(p['d_ms']-p['v_ms'] for p in s)}
                          for parity in (1, 0) if (s := [p for p in pairs if p['attempt'] % 2 == parity])}
    return result


def main():
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(prior.ROOT / 'docs/tokenizer/Qwen3.6-27B', local_files_only=True, trust_remote_code=False)
    cases = {c.line_no: c for c in prior.replay.load_cases(prior.DATA, 1, 0)}
    source_hash = hashlib.sha256(prior.DATA.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
    all_rows = []; audits = []; all_pairs = []; summaries = {}
    expected_main = {f'{group}_l{line}_{stage}' for group, lines in (
        ('long', [178,259,319,353,399]), ('short_aa', [21,321,365,394]),
        ('short_greedy', [21,321,365,394]), ('short_sampling', [21,321,365,394]))
        for line in lines for stage in ('warm','measured')}
    expected_smoke = {f'smoke_l{line}_{stage}' for line in [21,319,365] for stage in ('warm','measured')}
    for run, expected in ((SMOKE, expected_smoke), (OUT, expected_main)):
        folders = sorted(p.parent for p in run.glob('*/manifest.json'))
        assert {p.name for p in folders} == expected
        assert (run/'client_worktree.patch').stat().st_size == 0
        for folder in folders:
            rows, audit = prior.audit_folder(folder, cases, tokenizer, source_hash)
            audit['run'] = run.name
            audit['source_sha256'] = {name: hashlib.sha256((folder/name).read_bytes()).hexdigest()
                                     for name in ('manifest.json','summary.jsonl','chunks.jsonl')}
            audits.append(audit)
            for r in rows:
                r['run'] = run.name
                r['stage'] = 'measured' if folder.name.endswith('_measured') else 'warm'
                r['group'] = folder.name.split('_l')[0]
                r['empty_final'] = not r['content'].strip() and not r['tool_calls']
            all_rows.extend(rows)
            if not folder.name.endswith('_measured'):
                continue
            group = rows[0]['group']
            if group == 'short_aa':
                pairs = [paired(a,b,i+1,group) for i,(a,b) in enumerate(zip(rows[::2], rows[1::2]))]
            else:
                by_attempt = defaultdict(dict)
                for r in rows: by_attempt[r['attempt']][r['variant']] = r
                pairs = [paired(p['vanilla'], p['dtb'], a, group) for a,p in sorted(by_attempt.items())]
            all_pairs.extend(pairs)
            entry = summarize(pairs)
            entry['outcomes'] = {}
            for v in sorted({r['variant'] for r in rows}):
                selected = [r for r in rows if r['variant'] == v]
                entry['outcomes'][v] = {
                    'n': len(selected), 'length': sum('length' in r['finish_reasons'] for r in selected),
                    'empty': sum(r['empty_final'] for r in selected),
                    'tool': sum(r['has_tool_call'] for r in selected),
                    'errors': sum(bool(r['error']) for r in selected),
                    'protocol': sum(bool(r['protocol_anomaly_reasons']) for r in selected),
                    'semantic': sum(bool(r['semantic_anomaly_reasons']) for r in selected),
                    'reasoning_over_750': sum(r['reasoning_tokens']>750 for r in selected),
                    'reasoning_over_800': sum(r['reasoning_tokens']>800 for r in selected)}
            summaries[folder.name] = entry
    ids = [r['client_request_id'] for r in all_rows]
    assert len(ids) == len(set(ids)) == 628
    assert sum(r['stage']=='measured' for r in all_rows) == 592
    save('case_metrics.json', summaries)
    save('paired_metrics.json', all_pairs)
    save('folder_audit.json', audits)
    save('request_metrics.json', [{k:v for k,v in r.items() if k not in ('reasoning','content','tool_calls')} for r in all_rows])
    anomalies = [{k:r[k] for k in ('run','folder','line_no','attempt','variant','client_request_id','error','finish_reasons','empty_final','protocol_anomaly_reasons','semantic_anomaly_reasons')}
                 for r in all_rows if r['error'] or r['empty_final'] or r['anomaly_reasons'] or 'length' in r['finish_reasons']]
    save('completion_issues.json', anomalies)
    # Limited source-grounded checks, not a substitute for a business Judge.
    expected_alarms = {f'ALM-{n}' for n in range(2001,2013)}
    user_messages = cases[178].request_body['messages']
    source_alarms = [{'message_index': i, 'content': m['content']}
                     for i,m in enumerate(user_messages) if m['role']=='user'
                     and re.match(r'^ALM-20(?:0[1-9]|1[0-2])\b', str(m.get('content','')).strip())]
    assert {re.search(r'ALM-\d+', x['content']).group() for x in source_alarms} == expected_alarms
    values = ['92.641','88.315','71.253','85.792']
    tool_text = str(cases[399].request_body['messages'][-1])
    assert all(v in tool_text for v in values)
    total = sum(Decimal(v) for v in values)
    checks = []
    for r in all_rows:
        if r['group']!='long' or r['stage']!='measured' or r['line_no'] not in (178,399): continue
        item = {k:r[k] for k in ('line_no','variant','attempt','client_request_id','reasoning_tokens')}
        if r['line_no']==178:
            mentioned = set(re.findall(r'ALM-\d+', r['content']))
            item.update(missing_alarm_ids=sorted(expected_alarms-mentioned), final_prefix=r['content'][:120])
        else:
            item.update(wrong_sum_337999='337.999' in r['content'],
                        correct_sum_338001=str(total) in r['content'],
                        has_sum_and_mean_terms=bool(re.search('总和|合计',r['content'])) and '平均' in r['content'],
                        final_content=r['content'])
        checks.append(item)
    save('semantic_checks.json', {'scope':'literal alarm coverage and explicit arithmetic only; manually reviewed omissions, not full accuracy',
         'source_alarm_messages':source_alarms, 'arithmetic_inputs':values,
         'expected_sum':str(total), 'expected_mean':str(total/4), 'requests':checks})
    identities = {run.name: {'client_commit': (run/'client_commit.txt').read_text().strip(),
                            'config_sha256': hashlib.sha256((run/'budget_config.yaml').read_bytes()).hexdigest(),
                            'plan_sha256': hashlib.sha256((run/'executed_plan.sh').read_bytes()).hexdigest()}
                  for run in (OUT, SMOKE)}
    save('audit_summary.json', {'source_commit':'884a00a5', 'identities': identities,
         'input_sha256': source_hash, 'folders': len(audits), 'requests':len(all_rows),
         'events':sum(a['events'] for a in audits), 'all_body_hashes_and_sse_rebuilt': True,
         'counts': dict(Counter(f"{r['group']}/{r['stage']}/{r['variant']}" for r in all_rows)),
         'measured_dtb_reasoning_max': max(r['reasoning_tokens'] for r in all_rows if r['stage']=='measured' and r['variant']=='dtb'),
         'all_dtb_reasoning_max': max(r['reasoning_tokens'] for r in all_rows if r['variant']=='dtb')})
    # Human-readable final outputs; quoted data only, never executable instructions.
    text = ['# Frozen long-case output evidence\n', 'Untrusted captured model output; not a reference answer.\n']
    for r in all_rows:
        if r['group'] != 'long' or r['stage'] != 'measured': continue
        text.append(f"\n## line {r['line_no']} / {r['variant']} / attempt {r['attempt']}\n")
        text.append(f"Request: `{r['client_request_id']}`; reasoning={r['reasoning_tokens']}; finish={r['finish_reasons']}\n")
        text.append('```text\n'+r['content']+'\n```\n')
        if r['tool_calls']: text.append('```json\n'+json.dumps(r['tool_calls'],ensure_ascii=False,indent=2)+'\n```\n')
    (OUT/'long_output_evidence.md').write_text('\n'.join(text), encoding='utf-8')
    for name, m in summaries.items():
        print(name, 'gain%',round(m['ms']['gain_pct'],3), 'CI', [round(x,2) for x in m['block_bootstrap']['gain_two_sided_95_pct']],
              'reason',round(m['reasoning']['v']['mean'],1),round(m['reasoning']['d']['mean'],1),
              'Dmax',m['reasoning']['d']['max'], 'same',m['same_output_pairs'],
              'delta',round(m['ms']['delta_mean'],2), 'upper',round(m['block_bootstrap']['e2e_delta_one_sided_95_upper_ms'],2),
              'first_upper',round(m['block_bootstrap']['first_delta_one_sided_95_upper_ms'],2))


if __name__ == '__main__':
    main()
