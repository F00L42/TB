"""
Ablation Experiment Runner
每次只启用一个优化, 与 baseline 和纯 budget 截断对比

Usage:
    python run_ablation.py --max_cases 10
    python run_ablation.py --max_cases 5

Runs:
    0. baseline           - Chat API 自然调用 (不截断)
    1. pure_budget        - BudgetForcing 截断, 所有优化OFF
    2. +consecutive_same  - P2.2 连续相同答案早停
    3. +value_min_conf    - P3.1 Value最低概率
    4. +clean_context     - P3.3 Clean Context (OFF验证)
    5. +safety_gate       - 安全门(超时回退baseline)
    6. all_on             - 全部有效优化启用
"""
import naie._bootstrap  # sys.path + NO_PROXY

import json
import time
from datetime import datetime

import argparse
from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47, print_config
from naie.evaluation.runner import (
    load_datasets, evaluate_baseline, evaluate_budget, calculate_metrics
)


ABLATION_RUNS = [
    {
        "name": "0_baseline",
        "mode": "baseline",
        "desc": "Chat API 自然调用(不截断)",
    },
    {
        "name": "1_pure_budget",
        "mode": "budget",
        "desc": "BudgetForcing截断, 所有优化OFF",
        "config": {
            "enable_consecutive_same_stop": False,
            "enable_value_min_confidence": False,
            "enable_clean_context": True,
            "enable_safety_gate": False,
        },
    },
    {
        "name": "2_consecutive_same",
        "mode": "budget",
        "desc": "P2.2 连续相同答案早停(仅此优化ON)",
        "config": {
            "enable_consecutive_same_stop": True,
            "enable_value_min_confidence": False,
            "enable_clean_context": True,
            "enable_safety_gate": False,
        },
    },
    {
        "name": "3_value_min_conf",
        "mode": "budget",
        "desc": "P3.1 Value最低概率(仅此优化ON)",
        "config": {
            "enable_consecutive_same_stop": False,
            "enable_value_min_confidence": True,
            "enable_clean_context": True,
            "enable_safety_gate": False,
        },
    },
    {
        "name": "4_clean_context",
        "mode": "budget",
        "desc": "P3.3 Clean Context(此轮OFF验证关闭后的差异)",
        "config": {
            "enable_consecutive_same_stop": False,
            "enable_value_min_confidence": False,
            "enable_clean_context": False,
            "enable_safety_gate": False,
        },
        "note": "此轮clean_context=OFF, 其他轮clean_context=ON(默认). 对比1_pure_budget可知clean_context效果",
    },
    {
        "name": "5_safety_gate",
        "mode": "budget",
        "desc": "安全门超时回退(仅此优化ON,基于pure_budget)",
        "config": {
            "enable_consecutive_same_stop": False,
            "enable_value_min_confidence": False,
            "enable_clean_context": True,
            "enable_safety_gate": True,
            "baseline_time_limit": 40.0,
        },
    },
    {
        "name": "6_all_on",
        "mode": "budget",
        "desc": "全部有效优化启用",
        "config": {
            "enable_consecutive_same_stop": True,
            "enable_value_min_confidence": True,
            "enable_clean_context": True,
            "enable_safety_gate": True,
            "baseline_time_limit": 40.0,
        },
    },
]


def main():
    parser = argparse.ArgumentParser(description='Ablation Experiment Runner')
    parser.add_argument('--max_cases', type=int, default=10, help='每个实验跑N条')
    parser.add_argument('--skip_baseline', action='store_true', help='跳过baseline(已跑过)')
    parser.add_argument('--skip_completed', type=str, default=None,
                        help='跳过已完成的实验名称(逗号分隔)')
    args = parser.parse_args()

    result_dir = os.path.join(os.path.dirname(__file__), 'data', 'naie', 'results_ablation')
    result_dir = os.path.abspath(result_dir)
    os.makedirs(result_dir, exist_ok=True)

    datasets = load_datasets(NAIE_CONFIG['dataset_dir'])
    print(f"已加载 {len(datasets)} 个数据集")

    if args.max_cases > 0:
        trimmed = []
        for ds in datasets:
            trimmed.append({'name': ds['name'], 'data': ds['data'].head(args.max_cases)})
        datasets = trimmed

    skip_set = set()
    if args.skip_completed:
        skip_set = {s.strip() for s in args.skip_completed.split(',') if s.strip()}

    summary_rows = []
    wall_total_start = time.time()

    for run_def in ABLATION_RUNS:
        run_name = run_def["name"]
        if args.skip_baseline and run_def["mode"] == "baseline" and run_name == "0_baseline":
            print(f"\n{'='*70}")
            print(f"  跳过: {run_name} — {run_def['desc']}")
            print(f"{'='*70}")
            continue
        if run_name in skip_set:
            print(f"\n{'='*70}")
            print(f"  跳过已完成: {run_name} — {run_def['desc']}")
            print(f"{'='*70}")
            continue

        print(f"\n{'='*70}")
        print(f"  消融实验: {run_name}")
        print(f"  描述:     {run_def['desc']}")
        print(f"  模式:     {run_def['mode']}")
        if run_def.get('config'):
            print(f"  配置:     {json.dumps(run_def['config'], ensure_ascii=False)}")
        if run_def.get('note'):
            print(f"  注意:     {run_def['note']}")
        print(f"{'='*70}")

        run_start = time.time()

        if run_def["mode"] == "baseline":
            results = evaluate_baseline(datasets, result_dir)
        else:
            base_cfg = {
                "check_length": 500,
                "think_stride": 100,
                "prob_th": 0.85,
                "enable_budget": True,
                "num_thinking": 6,
                "check_answer_len": 50,
                "max_tokens": 2000,
                "enable_consecutive_same_stop": False,
                "enable_value_min_confidence": False,
                "enable_clean_context": True,
                "enable_safety_gate": False,
            }
            if run_def.get("config"):
                base_cfg.update(run_def["config"])
            results = evaluate_budget(datasets, result_dir, base_cfg)

        run_elapsed = time.time() - run_start
        metrics = calculate_metrics(results)

        row = {
            "run_name": run_name,
            "desc": run_def['desc'],
            "mode": run_def['mode'],
            "accuracy": metrics['accuracy'],
            "correct": metrics['correct'],
            "total": metrics['total'],
            "avg_latency_s": metrics['avg_elapsed_time'],
            "avg_think_tok": metrics['avg_thinking_tokens'],
            "avg_api_calls": metrics.get('avg_api_calls', '-'),
            "run_time_s": round(run_elapsed, 1),
            "config": run_def.get("config", {}),
        }

        if results and 'accept_reason_cn' in results[0]:
            accept_reasons = {}
            for r in results:
                ar = r.get('accept_reason_cn', r.get('accept_reason', 'unknown'))
                accept_reasons[ar] = accept_reasons.get(ar, 0) + 1
            row["accept_reasons"] = accept_reasons

        summary_rows.append(row)

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        summary_file = os.path.join(result_dir, f"ablation_{run_name}_{ts}.json")
        with open(summary_file, 'w', encoding='utf-8') as f:
            json.dump({"run": row, "cases": [
                {k: v for k, v in r.items() if k not in ('raw_content', 'question')}
                for r in results
            ]}, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n  [{run_name}] 完成: acc={metrics['accuracy']:.2%}, "
              f"latency={metrics['avg_elapsed_time']:.1f}s, "
              f"think_tok={metrics['avg_thinking_tokens']:.0f}, "
              f"time={run_elapsed:.0f}s")

    wall_total = time.time() - wall_total_start

    summary_file = os.path.join(result_dir, "ablation_summary.json")
    with open(summary_file, 'w', encoding='utf-8') as f:
        json.dump({
            "timestamp": datetime.now().isoformat(),
            "max_cases": args.max_cases,
            "total_wall_time_s": round(wall_total, 1),
            "runs": summary_rows,
        }, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n\n{'='*90}")
    print(f"  消融实验汇总 (max_cases={args.max_cases})")
    print(f"{'='*90}")
    print(f"  {'实验名':<24s} {'准确率':>6s} {'平均延迟':>8s} {'思考tok':>8s} {'API次数':>7s} {'耗时':>6s}  描述")
    sep = '-' * 24
    print(f"  {sep:s} {'------':>6s} {'--------':>8s} {'--------':>8s} {'-------':>7s} {'------':>6s}  {'--------------------':s}")
    for row in summary_rows:
        acc_str = f"{row['accuracy']:.1%}"
        lat_str = f"{row['avg_latency_s']:.1f}s"
        think_str = f"{row['avg_think_tok']:.0f}"
        api_str = str(row['avg_api_calls'])
        time_str = f"{row['run_time_s']:.0f}s"
        print(f"  {row['run_name']:<24s} {acc_str:>6s} {lat_str:>8s} {think_str:>8s} {api_str:>7s} {time_str:>6s}  {row['desc']}")
    print(f"{'='*90}")
    print(f"  总耗时: {wall_total/60:.1f}min")
    print(f"  详细结果: {result_dir}")


if __name__ == '__main__':
    main()
