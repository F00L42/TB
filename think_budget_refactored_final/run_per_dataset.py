"""NAIE Per-Dataset Evaluation — 按数据集分开统计, 单一CSV输出。

同时跑 baseline + budget, 每条case实时打印结果, 超时不记录延迟。

用法:
    python run_per_dataset.py                    # 默认每数据集1条
    python run_per_dataset.py --max_cases 10     # 每数据集10条
    python run_per_dataset.py --mode baseline    # 只跑baseline
    python run_per_dataset.py --mode budget      # 只跑budget

CSV格式: dataset, id, mode, elapsed_time
"""
import naie._bootstrap  # sys.path + NO_PROXY

import argparse
import time
import json
import csv
import math
from datetime import datetime

import pandas as pd
from tqdm import tqdm
from transformers import AutoTokenizer

from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47
from naie.evaluation.runner import (
    load_datasets, _evaluate_one, _translate_accept_reason,
    parse_json_string,
)
from naie.evaluation.trajectory_logger import log_trajectory, build_trajectory_entry
from naie.models.budget_forcing import call_model as bf_call_model
from naie.inference.think_budget import call_model
from naie.optimizations.history_module import record_inference, get_category_stats, get_scene_stats
from naie.optimizations.scene_classifier import classify_scene
from naie.optimizations.task_type_budget import classify_task_type


def _on_off(val):
    if isinstance(val, bool):
        return val
    s = str(val).lower()
    if s in ('on', 'true', '1', 'yes'):
        return True
    if s in ('off', 'false', '0', 'no'):
        return False
    raise ValueError(f"Cannot convert '{val}' to bool")


def _build_override_config(args):
    cfg = {}
    _INT_PARAMS = [
        'check_length', 'think_stride', 'num_thinking', 'check_answer_len',
        'max_tokens', 'consecutive_same_stop', 'value_min_bottom_k',
        'min_think_tokens', 'max_api_calls',
    ]
    _FLOAT_PARAMS = [
        'prob_th', 'baseline_time_limit',
    ]
    _BOOL_PARAMS = [
        'enable_budget', 'enable_consecutive_same_stop',
        'enable_value_min_confidence',
        'enable_clean_context', 'enable_safety_gate',
        'enable_difficulty_estimator', 'enable_deer_trial',
        'enable_soft_truncation',
    ]
    for p in _INT_PARAMS:
        v = getattr(args, p, None)
        if v is not None:
            cfg[p] = int(v)
    for p in _FLOAT_PARAMS:
        v = getattr(args, p, None)
        if v is not None:
            cfg[p] = float(v)
    for p in _BOOL_PARAMS:
        v = getattr(args, p, None)
        if v is not None:
            cfg[p] = _on_off(v)
    return cfg


def _get_history_stats(question, task_type='', scene_type=''):
    """获取历史统计 — 自动推断 task_type 而非硬编码 'naie'。"""
    try:
        if not task_type:
            scene_info = classify_scene(question)
            scene_type = scene_type or scene_info.get('scene_type', 'general')
            task_info = classify_task_type(question, scene_info)
            task_type = task_info.get('task_type', 'general')
        return get_category_stats(question[:100], task_type=task_type, scene_type=scene_type)
    except Exception:
        return {'avg_tokens': 0.0, 'failure_rate': 0.0, 'count': 0,
                'scene_type': scene_type, 'scene_match_count': 0}


def _is_timeout_or_error(result):
    ar = result.get('accept_reason', '')
    return ar.startswith('error:') or ar == 'safety_gate_fallback_no_answers'


def _compute_dataset_metrics(results):
    valid = [r for r in results if not _is_timeout_or_error(r)]
    total = len(results)
    total_valid = len(valid)
    correct = sum(1 for r in valid if r['correct'])
    accuracy = correct / total_valid if total_valid > 0 else 0
    times = [r['elapsed_time'] for r in valid]
    avg_time = sum(times) / len(times) if times else 0
    think_vals = [r['thinking_tokens'] for r in valid if r.get('thinking_tokens')]
    avg_think = sum(think_vals) / len(think_vals) if think_vals else 0
    api_counts = [r.get('api_call_count', 0) for r in valid if r.get('api_call_count')]
    avg_api = sum(api_counts) / len(api_counts) if api_counts else 0
    scores = [r['score'] for r in valid]
    avg_score = sum(scores) / len(scores) if scores else 0
    return {
        'total': total,
        'timeout': total - total_valid,
        'valid': total_valid,
        'correct': correct,
        'accuracy': round(accuracy, 4),
        'avg_score': round(avg_score, 2),
        'avg_elapsed_time': round(avg_time, 2),
        'total_elapsed_time': round(sum(times), 2),
        'avg_thinking_tokens': round(avg_think, 1),
        'avg_api_calls': round(avg_api, 1),
    }


def _get_response_fn(mode, max_token, budget_cfg):
    if mode == 'baseline':
        def fn(question, reference):
            return bf_call_model(question, max_token, enable_confidence=False, model_name='mg2hg')
        return fn
    else:
        max_tokens = budget_cfg.get('max_tokens', 2000)
        def fn(question, reference):
            ref = str(reference) if not pd.isna(reference) else ''
            scene_info = classify_scene(question)
            scene_type = scene_info.get('scene_type', 'general')
            cat_stats = _get_history_stats(question, scene_type=scene_type)
            scene_stats = get_scene_stats(scene_type)
            cfg_local = dict(budget_cfg)
            cfg_local['history_avg_tokens'] = cat_stats.get('avg_tokens', 0) or scene_stats.get('avg_tokens', 0)
            cfg_local['history_failure_rate'] = cat_stats.get('failure_rate', 0) or scene_stats.get('failure_rate', 0)
            cfg_local['history_category_avg_tokens'] = cat_stats.get('avg_tokens', 0)
            cfg_local['history_category_failure_rate'] = cat_stats.get('failure_rate', 0)
            cfg_local['history_count'] = cat_stats.get('count', 0) or scene_stats.get('count', 0)
            return call_model(question, max_tokens, budget_config=cfg_local, reference=ref)
        return fn


def main():
    parser = argparse.ArgumentParser(description='NAIE Per-Dataset Evaluation (按数据集分开统计)')
    parser.add_argument('--mode', type=str, default='both', choices=['baseline', 'budget', 'both'],
                        help='评估模式 (默认: both)')
    parser.add_argument('--max_cases', type=int, default=1, help='每个数据集只跑前N条 (0=全部)')
    parser.add_argument('--output_dir', type=str, default=None,
                        help='输出目录 (默认 data/naie/results_per_dataset)')
    parser.add_argument('--check_length', type=int, default=None)
    parser.add_argument('--think_stride', type=int, default=None)
    parser.add_argument('--prob_th', type=float, default=None)
    parser.add_argument('--num_thinking', type=int, default=None)
    parser.add_argument('--check_answer_len', type=int, default=None)
    parser.add_argument('--max_tokens', type=int, default=None)
    parser.add_argument('--min_think_tokens', type=int, default=None)
    parser.add_argument('--max_api_calls', type=int, default=None)
    parser.add_argument('--baseline_time_limit', type=float, default=None)
    parser.add_argument('--enable_budget', type=str, default=None)
    parser.add_argument('--enable_value_min_confidence', type=str, default=None)
    parser.add_argument('--enable_clean_context', type=str, default=None)
    parser.add_argument('--enable_safety_gate', type=str, default=None)
    parser.add_argument('--enable_soft_truncation', type=str, default=None)
    parser.add_argument('--enable_consecutive_same_stop', type=str, default=None)
    parser.add_argument('--consecutive_same_stop', type=int, default=None)
    parser.add_argument('--enable_difficulty_estimator', type=str, default=None)
    parser.add_argument('--enable_deer_trial', type=str, default=None)
    args = parser.parse_args()

    max_token = NAIE_CONFIG['max_token']

    budget_cfg = dict(THINK_BUDGET_CONFIG_47)
    override = _build_override_config(args)
    budget_cfg.update(override)

    if args.output_dir:
        output_dir = args.output_dir
    else:
        output_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'data', 'naie', 'results_per_dataset',
        )
    os.makedirs(output_dir, exist_ok=True)

    datasets = load_datasets(NAIE_CONFIG['dataset_dir'])

    modes = ['baseline', 'budget'] if args.mode == 'both' else [args.mode]

    print("=" * 70)
    print(f"NAIE Per-Dataset Evaluation — {args.mode.upper()} 模式")
    print("=" * 70)
    print(f"  数据集数量:   {len(datasets)}")
    print(f"  每集最大用例: {args.max_cases or '全部'}")
    print(f"  运行模式:     {modes}")
    print(f"  输出目录:     {output_dir}")
    print(f"  Budget参数:   prob_th={budget_cfg.get('prob_th')}, "
          f"check_length={budget_cfg.get('check_length')}, "
          f"min_think_tokens={budget_cfg.get('min_think_tokens')}")
    print("=" * 70, flush=True)

    all_mode_data = {}
    wall_start = time.time()

    for mode in modes:
        response_fn = _get_response_fn(mode, max_token, budget_cfg)
        all_valid_rows = []
        all_dataset_metrics = []

        target_valid = args.max_cases if args.max_cases > 0 else 0

        for ds_idx, ds in enumerate(datasets):
            dataset_name = ds['name']
            safe_name = os.path.splitext(dataset_name)[0]
            safe_name = safe_name.replace(' ', '_').replace('/', '_').replace('\\', '_')

            df_all = ds['data']

            if target_valid > 0:
                print(f"\n[{mode.upper()}] 数据集 [{ds_idx+1}/{len(datasets)}]: {dataset_name} "
                      f"(目标{target_valid}条有效)", flush=True)
            else:
                print(f"\n[{mode.upper()}] 数据集 [{ds_idx+1}/{len(datasets)}]: {dataset_name} "
                      f"(全部{len(df_all)}条)", flush=True)

            results = []
            valid_count = 0
            attempted = 0

            for idx, row in df_all.iterrows():
                if target_valid > 0 and valid_count >= target_valid:
                    break

                question = row.get('问题', row.get('question', ''))
                reference = row.get('参考答案', row.get('reference', ''))
                if pd.isna(question):
                    continue

                attempted += 1
                result = _evaluate_one(question, reference, 'mg2hg', response_fn)
                result['id'] = idx + 1
                result['accept_reason_cn'] = _translate_accept_reason(result.get('accept_reason', ''))
                results.append(result)

                is_err = _is_timeout_or_error(result)
                elapsed = result['elapsed_time']
                mark = 'X' if not result['correct'] else 'O'
                err_tag = ' [TIMEOUT]' if is_err else ''
                print(f"  #{attempted:>2d}(row{idx+1})  {mark}  score={result['score']:>2d}  "
                      f"elapsed={elapsed:.1f}s  think={result.get('thinking_tokens') or 0}tok  "
                      f"reason={result.get('accept_reason','')[:30]}{err_tag}", flush=True)

                if not is_err:
                    valid_count += 1
                    all_valid_rows.append({
                        'dataset': safe_name,
                        'id': idx + 1,
                        'mode': mode,
                        'elapsed_time': round(elapsed, 2),
                    })

            ds_metrics = _compute_dataset_metrics(results)
            ds_metrics['dataset'] = dataset_name
            all_dataset_metrics.append(ds_metrics)

            to_str = f" timeout={ds_metrics['timeout']}" if ds_metrics['timeout'] > 0 else ''
            print(f"  => {dataset_name}: {ds_metrics['correct']}/{ds_metrics['valid']} correct "
                  f"({ds_metrics['accuracy']:.1%}), avg={ds_metrics['avg_elapsed_time']:.1f}s{to_str}"
                  f" (tried {attempted} rows for {valid_count} valid)", flush=True)

        all_mode_data[mode] = {
            'valid_rows': all_valid_rows,
            'dataset_metrics': all_dataset_metrics,
        }

    wall_total = time.time() - wall_start

    # Single CSV: dataset, id, mode, elapsed_time
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_path = os.path.join(output_dir, f"latency_{ts}.csv")
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['dataset', 'id', 'mode', 'elapsed_time'])
        for mode in modes:
            for r in all_mode_data[mode]['valid_rows']:
                writer.writerow([r['dataset'], r['id'], r['mode'], r['elapsed_time']])

    # Summary JSON
    summary = {
        'modes': modes,
        'timestamp': datetime.now().isoformat(),
        'wall_elapsed': round(wall_total, 2),
        'budget_config': budget_cfg if 'budget' in modes else None,
        'per_mode': {},
    }
    for mode in modes:
        dm_list = all_mode_data[mode]['dataset_metrics']
        all_valid = all_mode_data[mode]['valid_rows']
        overall = _compute_dataset_metrics([
            {'correct': True, 'score': 10, 'elapsed_time': r['elapsed_time'],
             'thinking_tokens': 0, 'api_call_count': 0, 'accept_reason': ''}
            for r in all_valid
        ])
        summary['per_mode'][mode] = {
            'per_dataset': dm_list,
            'overall': overall,
        }

    summary_path = os.path.join(output_dir, f"summary_{ts}.json")
    with open(summary_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    # Grand summary
    print(f"\n{'═' * 70}")
    for mode in modes:
        dm_list = all_mode_data[mode]['dataset_metrics']
        total_valid = sum(dm['valid'] for dm in dm_list)
        total_correct = sum(dm['correct'] for dm in dm_list)
        total_timeout = sum(dm['timeout'] for dm in dm_list)

        print(f"  {mode.upper()} 汇总")
        print(f"  {'─' * 50}")
        print(f"  {'数据集':<35s}  {'OK':>3s}  {'TO':>3s}  {'准确率':>6s}  {'延迟':>7s}")
        print(f"  {'─'*35}  {'─'*3}  {'─'*3}  {'─'*6}  {'─'*7}")
        for dm in dm_list:
            print(f"  {dm['dataset'][:35]:<35s}  {dm['correct']:>3d}  {dm['timeout']:>3d}  "
                  f"{dm['accuracy']:>5.1%}  {dm['avg_elapsed_time']:>6.1f}s")
        if dm_list:
            avg_of_avgs = sum(dm['avg_elapsed_time'] for dm in dm_list) / len(dm_list)
            print(f"  {'跨数据集平均延迟':<35s}                    {avg_of_avgs:>6.1f}s")
        print(f"  总计: {total_correct}/{total_valid} = {total_correct/total_valid:.1%}" if total_valid else "  总计: N/A")
        if total_timeout > 0:
            print(f"  超时: {total_timeout} (不计入延迟)")
        print()

    print(f"  端到端总耗时: {wall_total:.1f}s")
    print(f"  CSV: {csv_path}")
    print(f"  JSON: {summary_path}")
    print(f"{'═' * 70}", flush=True)


if __name__ == '__main__':
    main()
