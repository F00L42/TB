"""Read-only source audit of 7249b3 replay results, including split chunk files."""
from collections import Counter, defaultdict
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import random
import statistics as st
import sys
from types import SimpleNamespace

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3]
DATA = ROOT / 'docs/log/perf/noemate_analysis_60f2d7/replay_dataset'
sys.path.insert(0, str(ROOT / 'tools/dtb'))
import replay_tool_calls as replay


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def lines(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def save(name, value):
    (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def quant(values, p):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * p) - 1)] if values else None


def summary(pairs):
    if not pairs:
        return {'n': 0}
    v = sum(p['v_ms'] for p in pairs); d = sum(p['d_ms'] for p in pairs)
    return {'n': len(pairs), 'v_mean_ms': v / len(pairs), 'd_mean_ms': d / len(pairs),
            'sum_gain_percent': 100 * (1 - d / v),
            'paired_gain_median_percent': st.median(p['gain_percent'] for p in pairs),
            'paired_delta_median_ms': st.median(p['d_ms'] - p['v_ms'] for p in pairs),
            'faster': sum(p['d_ms'] < p['v_ms'] for p in pairs),
            'v_p95_ms': replay.percentile([p['v_ms'] for p in pairs], .95), 'd_p95_ms': replay.percentile([p['d_ms'] for p in pairs], .95),
            'v_first_mean_ms': st.mean(p['v_first'] for p in pairs), 'd_first_mean_ms': st.mean(p['d_first'] for p in pairs),
            'v_after_first_mean_ms': st.mean(p['v_ms'] - p['v_first'] for p in pairs),
            'd_after_first_mean_ms': st.mean(p['d_ms'] - p['d_first'] for p in pairs),
            'v_reasoning_sum': sum(p['v_reasoning'] for p in pairs), 'd_reasoning_sum': sum(p['d_reasoning'] for p in pairs),
            'v_completion_sum': sum(p['v_completion'] for p in pairs), 'd_completion_sum': sum(p['d_completion'] for p in pairs)}


def bootstrap(pairs):
    grouped = defaultdict(list)
    for pair in pairs:
        grouped[pair['case_id']].append(pair)
    groups = [(sum(p['v_ms'] for p in rows), sum(p['d_ms'] for p in rows)) for rows in grouped.values()]
    rng = random.Random(72493); gains = []
    for _ in range(2000):
        sample = rng.choices(groups, k=len(groups))
        gains.append(100 * (1 - sum(d for v, d in sample) / sum(v for v, d in sample)))
    return [quant(gains, .025), quant(gains, .975)]


def audit(directory, source_name, tokenizer, index):
    manifest = read(directory / 'manifest.json')
    source = DATA / source_name
    assert hashlib.sha256(source.read_bytes().replace(b'\r\n', b'\n')).hexdigest() == manifest['input_sha256']
    cases = replay.load_cases(source, 1, 0)
    by_case = {c.case_id: c for c in cases}
    rows = list(lines(directory / 'summary.jsonl'))
    by_id = {r['client_request_id']: r for r in rows}
    assert len(rows) == len(by_id) == len(cases) * 4
    assert {(r['case_id'], r['attempt'], r['variant']) for r in rows} == {
        (c.case_id, a, v) for c in cases for a in (1, 2) for v in ('vanilla', 'dtb')}
    expected_order = [(c.case_id, a, v) for c in cases for a in (1, 2)
                      for v in (('vanilla', 'dtb') if a == 1 else ('dtb', 'vanilla'))]
    assert [(r['case_id'], r['attempt'], r['variant']) for r in rows] == expected_order
    assert replay.build_paired_summary([replay.Observation(**r) for r in rows]) == read(directory / 'paired_summary.json')
    args = SimpleNamespace(**manifest['request_overrides'], stream=manifest['stream'], model='')
    files = sorted(directory.glob('chunks_part*.jsonl')) or [directory / 'chunks.jsonl']
    if len(files) > 1:
        assert [f.name for f in files] == [f'chunks_part{i:02d}.jsonl' for i in range(1, len(files) + 1)]
    request_order = []; current = None; chunks = []; event_index = 0; done = 0
    models = Counter(); field_names = Counter(); created = []; chunk_total = 0; continuations = []

    def finish():
        if current is None:
            return
        row = by_id[current]; case = by_case[row['case_id']]
        assert done == 1, (current, 'DONE', done)
        rebuilt = asdict(replay.assemble_observation(
            **{k: row[k] for k in ('line_no', 'attempt', 'variant', 'client_request_id', 'status_code', 'elapsed_ms',
                                  'expected_tool_call', 'case_id', 'category', 'latency_eligible', 'error',
                                  'first_chunk_ms', 'first_output_ms', 'first_reasoning_ms', 'first_content_ms', 'first_tool_call_ms')},
            expected_tool_names=tuple(row['expected_tool_names']), request_tools=case.request_body.get('tools'), chunks=chunks))
        assert rebuilt == row, (current, [k for k in row if rebuilt[k] != row[k]])

    for path in files:
        for n, record in enumerate(lines(path), 1):
            if record.get('record_type') == 'request':
                finish()
                current = record['client_request_id']; request_order.append(current)
                row = by_id[current]; case = by_case[row['case_id']]
                assert record['case_id'] == row['case_id'] and record['variant'] == row['variant']
                body = replay.build_body(case.request_body, variant=row['variant'], client_request_id=current, args=args)
                assert replay.json_hash(body) == record['request_body_sha256'], current
                chunks = []; event_index = 0; done = 0
            else:
                if n == 1:
                    continuations.append(path.name)
                assert record['client_request_id'] == current
                event_index += 1; chunk_total += 1
                assert record['event_index'] == event_index
                assert not record['parse_error']
                parsed = replay.parse_sse_data(record['raw_sse_line'])
                assert parsed == record['chunk']
                if parsed is not None:
                    assert not done
                    chunks.append(parsed)
                    if parsed.get('model'): models[parsed['model']] += 1
                    if parsed.get('created'): created.append(parsed['created'])
                    for c in parsed.get('choices', []):
                        field_names.update((c.get('delta') or {}).keys())
                elif record['raw_sse_line'].strip() == 'data: [DONE]':
                    done += 1
    finish()
    assert request_order == list(by_id)
    print(directory.name, 'chunks audited', len(files), chunk_total, flush=True)
    for row in rows:
        row['reasoning_local'] = len(tokenizer.encode(row['reasoning'], add_special_tokens=False))
        row['content_local'] = len(tokenizer.encode(row['content'], add_special_tokens=False))
    grouped = defaultdict(dict)
    for row in rows:
        grouped[(row['case_id'], row['attempt'])][row['variant']] = row
    pairs = []
    for (cid, attempt), variants in grouped.items():
        v, d = variants['vanilla'], variants['dtb']
        p = {'case_id': cid, 'line_no': v['line_no'], 'attempt': attempt,
             'v_ms': v['elapsed_ms'], 'd_ms': d['elapsed_ms'], 'v_first': v['first_output_ms'], 'd_first': d['first_output_ms'],
             'gain_percent': 100 * (1 - d['elapsed_ms'] / v['elapsed_ms']),
             'v_reasoning': v['reasoning_local'], 'd_reasoning': d['reasoning_local'],
             'v_completion': v['usage']['completion_tokens'], 'd_completion': d['usage']['completion_tokens'],
             'v_protocol': v['protocol_anomaly_reasons'], 'd_protocol': d['protocol_anomaly_reasons'],
             'v_semantic': v['semantic_anomaly_reasons'], 'd_semantic': d['semantic_anomaly_reasons'],
             'v_content_tokens': v['content_local'], 'd_content_tokens': d['content_local'],
             'max_tokens': by_case[cid].request_body['max_tokens'], 'original_reasoning': index[cid]['reasoning_tokens_local'],
             'same_visible_output': (v['content'], [(t['name'], t['arguments']) for t in v['tool_calls']]) ==
                                    (d['content'], [(t['name'], t['arguments']) for t in d['tool_calls']])}
        pairs.append(p)
    names = {'all': pairs, 'protocol_clean': [p for p in pairs if not p['v_protocol'] and not p['d_protocol']],
             'fully_clean': [p for p in pairs if not any(p[k] for k in ('v_protocol','d_protocol','v_semantic','d_semantic'))],
             'attempt1_V_first': [p for p in pairs if p['attempt'] == 1], 'attempt2_D_first': [p for p in pairs if p['attempt'] == 2],
             'same_visible_output': [p for p in pairs if p['same_visible_output']],
             'vanilla_reasoning_le750': [p for p in pairs if p['v_reasoning'] <= 750],
             'vanilla_reasoning_gt750': [p for p in pairs if p['v_reasoning'] > 750],
             'vanilla_reasoning_gt1024': [p for p in pairs if p['v_reasoning'] > 1024],
             'vanilla_first_gt3000ms': [p for p in pairs if p['v_first'] > 3000],
             'vanilla_first_le3000ms': [p for p in pairs if p['v_first'] <= 3000]}
    for limit in (2048, 16384): names[f'limit_{limit}'] = [p for p in pairs if p['max_tokens'] == limit]
    overview = {'requests': len(rows), 'cases': len(cases), 'chunks_files': len(files), 'chunks_events': chunk_total,
                'shards_continuing_requests': continuations, 'all_rows_reassembled_exactly': True,
                'all_effective_request_hashes_match': True, 'models': dict(models), 'delta_fields': dict(field_names),
                'created_min': min(created), 'created_max': max(created), 'metrics': {k: summary(v) for k, v in names.items()},
                'cluster_bootstrap_95_sum_gain': bootstrap(pairs),
                'anomalies': {variant: dict(Counter(a for row in rows if row['variant'] == variant for a in row['anomaly_reasons']))
                              for variant in ('vanilla','dtb')},
                'finish': {variant: dict(Counter(a for row in rows if row['variant'] == variant for a in row['finish_reasons']))
                           for variant in ('vanilla','dtb')}}
    save(directory.name + '_audit.json', overview)
    save(directory.name + '_pairs.json', pairs)
    save(directory.name + '_anomalies.json', [r for r in rows if r['anomaly_reasons']])
    save(directory.name + '_length_cases.json', [{k: r[k] for k in ('case_id', 'line_no', 'attempt', 'variant', 'finish_reasons', 'usage')}
                                               for r in rows if 'length' in r['finish_reasons']])
    return overview, pairs


def followups(pool, pilot):
    warm = [p for p in pool if p['attempt'] == 2]
    pilot_ids = {p['case_id'] for p in pilot}
    sensitivity = {}
    for label, subset in [('pool_warm', warm), ('pilot_warm', [p for p in pilot if p['attempt'] == 2]),
                          ('pool_same_pilot', [p for p in pool if p['case_id'] in pilot_ids])]:
        sensitivity[label] = {**summary(subset), 'bootstrap_case_95_sum_gain': bootstrap(subset)}
    selected = []
    required = pilot_ids | {p['case_id'] for p in warm if p['line_no'] in (230, 340, 347)}
    for lo, hi in [(0, 512), (512, 750), (750, 1024), (1024, 1000000)]:
        subset = [p for p in warm if lo < p['v_reasoning'] <= hi]
        sensitivity[f'warm_reasoning_{lo}_{hi}'] = summary(subset)
        mandatory = [p for p in subset if p['case_id'] in required]
        remaining = sorted([p for p in subset if p['case_id'] not in required],
                           key=lambda p: hashlib.sha256(p['case_id'].encode()).hexdigest())
        assert len(mandatory) <= 8
        selected.extend(mandatory + remaining[:8 - len(mandatory)])
    sensitivity['warm_all_clean'] = summary([p for p in warm if not any(p[k] for k in ('v_protocol', 'd_protocol', 'v_semantic', 'd_semantic'))])
    sensitivity['thought_increase_percent'] = 100 * (sum(p['d_reasoning'] for p in pool) / sum(p['v_reasoning'] for p in pool) - 1)
    sensitivity['completion_increase_percent'] = 100 * (sum(p['d_completion'] for p in pool) / sum(p['v_completion'] for p in pool) - 1)
    save('sensitivity.json', sensitivity)
    assert len(selected) == len({p['case_id'] for p in selected}) == 32
    selected.sort(key=lambda p: p['line_no'])
    (OUT / 'next_probe_lines.txt').write_text(','.join(str(p['line_no']) for p in selected) + '\n', encoding='utf-8')
    phase = {p['line_no'] for p in selected if p['line_no'] in (230, 340, 347)}
    for p in sorted(selected, key=lambda p: abs(p['d_ms'] - p['v_ms']), reverse=True):
        if len(phase) == 8: break
        phase.add(p['line_no'])
    (OUT / 'phase_probe_lines.txt').write_text(','.join(map(str, sorted(phase))) + '\n', encoding='utf-8')
    save('next_probe_manifest.json', {
        'selection': '8 per attempt2 Vanilla reasoning stratum; include six pilot and lines 230/340/347; remaining case SHA256 order',
        'purpose': 'post-hoc calibration and diagnostics; not an independent holdout or population-weighted sample',
        'cases': selected, 'phase_probe_lines': sorted(phase), 'inference_executed': False,
        'source_pool_input_sha256': read(OUT / 'pool_h750/manifest.json')['input_sha256']})


def main():
    from transformers import AutoTokenizer
    tokenizer_path = ROOT / 'docs/tokenizer/Qwen3.6-27B'
    hasher = hashlib.sha256()
    for name in ('tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.json', 'merges.txt', 'tokenizer.model'):
        path = tokenizer_path / name
        if path.exists():
            hasher.update(name.encode()); hasher.update(path.read_bytes())
    expected = read(DATA.parent / 'tokenizer_validation.json')['files_sha256']
    assert hasher.hexdigest() == expected
    save('tokenizer_identity.json', {'files_sha256': expected, 'path': tokenizer_path.relative_to(ROOT).as_posix(),
                                     'add_special_tokens': False, 'scope': 'locally re-encoded visible text, not hidden generation'})
    tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, local_files_only=True, trust_remote_code=False)
    index = {r['case_id']: r for r in lines(DATA / 'index.jsonl')}
    all_results = {}; pair_results = {}
    for folder, name in ((OUT.parent/'noemate_replay_20260920T010816Z/pilot_h512', 'pilot.jsonl'),
                         (OUT/'diagnostic_h750', 'diagnostic.jsonl'), (OUT/'pool_h750', 'pool.jsonl')):
        all_results[folder.name], pair_results[folder.name] = audit(folder, name, tokenizer, index)
    save('audit_summary.json', all_results)
    followups(pair_results['pool_h750'], pair_results['pilot_h512'])


if __name__ == '__main__': main()
