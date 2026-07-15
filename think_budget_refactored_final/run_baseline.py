"""NAIE Baseline Evaluation

Usage:
    python run_baseline.py [options]

Options:
    --max_cases N       只跑前N条 (默认5, 0=全部)
    --model_name NAME   模型名称 (默认mg2hg)
    --parallel          启用并行模式 (默认顺序执行)
    --concurrency N     并发数 (默认4, 仅 --parallel 时生效)
"""
import naie._bootstrap  # sys.path + NO_PROXY

import argparse
import os
import time
from naie.config.settings import NAIE_CONFIG
from naie.evaluation.runner import load_datasets, evaluate_baseline, calculate_metrics
from naie.evaluation.parallel_runner import evaluate_baseline_parallel


def main():
    parser = argparse.ArgumentParser(description='NAIE Baseline (think模式) 评估')
    parser.add_argument('--max_cases', type=int, default=5, help='只跑前N条 (0=全部)')
    parser.add_argument('--model_name', type=str, default=None, help='模型名称')
    parser.add_argument('--parallel', action='store_true', help='启用并行模式')
    parser.add_argument('--concurrency', type=int, default=4, help='并发数 (默认4)')
    args = parser.parse_args()

    model_name = args.model_name or NAIE_CONFIG['model_name']

    print("=" * 70)
    print("NAIE 基线评估 (think模式)")
    print("=" * 70)
    print(f"  模型:         {model_name}")
    print(f"  用例数:       {args.max_cases or '全部'}")
    print(f"  数据目录:     {NAIE_CONFIG['dataset_dir']}")
    if args.parallel:
        print(f"  并行模式:     ON (concurrency={args.concurrency})")
    else:
        print(f"  并行模式:     OFF (顺序执行)")
    print("=" * 70)

    datasets = load_datasets(NAIE_CONFIG['dataset_dir'])
    print(f"已加载 {len(datasets)} 个数据集")

    if args.max_cases > 0:
        trimmed = []
        for ds in datasets:
            trimmed.append({'name': ds['name'], 'data': ds['data'].head(args.max_cases)})
        datasets = trimmed

    result_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data', 'naie', 'results')
    result_dir = os.path.abspath(result_dir)

    hyperparams = {"max_cases": args.max_cases or 'all'}

    wall_start = time.time()
    if args.parallel:
        results = evaluate_baseline_parallel(datasets, result_dir, hyperparams,
                                             concurrency=args.concurrency)
    else:
        results = evaluate_baseline(datasets, result_dir, hyperparams)
    wall_total = time.time() - wall_start

    metrics = calculate_metrics(results)

    print("\n" + "=" * 50)
    print("基线结果汇总")
    print("=" * 50)
    print(f"  总用例数:     {metrics['total']}")
    print(f"  正确数:       {metrics['correct']}")
    print(f"  准确率:       {metrics['accuracy']:.2%}")
    print(f"  平均单题延迟: {metrics['avg_elapsed_time']:.2f}s")
    print(f"  端到端总延迟: {wall_total:.1f}s")
    print(f"  平均思考token: {metrics['avg_thinking_tokens']:.0f}")
    print("=" * 50)


if __name__ == '__main__':
    main()
