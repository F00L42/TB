import json
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from naie.config.settings import NAIE_CONFIG
from naie.evaluation.trajectory_logger import load_trajectories, get_trajectory_summary


def analyze_results(data, name):
    data = [item for item in data if item.get('match_detail') != "Request timed out."]
    total = len(data)
    correct_count = sum(1 for item in data if item.get('correct', False))
    accuracy = correct_count / total * 100 if total > 0 else 0

    elapsed_times = [item.get('elapsed_time', 0) or 0 for item in data]
    thinking_tokens = [item.get('thinking_tokens', 0) or 0 for item in data
                       if item.get('thinking_tokens') is not None]

    avg_elapsed = sum(elapsed_times) / len(elapsed_times) if elapsed_times else 0
    avg_thinking = sum(thinking_tokens) / len(thinking_tokens) if thinking_tokens else 0
    total_thinking = sum(thinking_tokens)

    return {
        'name': name,
        'total': total,
        'correct': correct_count,
        'accuracy': accuracy,
        'avg_elapsed_time': avg_elapsed,
        'total_elapsed_time': sum(elapsed_times),
        'avg_thinking_tokens': avg_thinking,
        'total_thinking_tokens': total_thinking,
        'min_thinking_tokens': min(thinking_tokens) if thinking_tokens else 0,
        'max_thinking_tokens': max(thinking_tokens) if thinking_tokens else 0,
    }


def print_comparison(stats_think, stats_budget):
    print("=" * 80)
    print("NAIE Baseline(Think) vs BudgetForcing(Think+Budget) 对比报告".center(70))
    print("=" * 80)

    print("\n[1. 准确率对比]")
    print("-" * 40)
    print(f"{'指标':<20} {'Think':<20} {'Think+Budget':<20}")
    print(f"{'样本总数':<20} {stats_think['total']:<20} {stats_budget['total']:<20}")
    print(f"{'正确数':<20} {stats_think['correct']:<20} {stats_budget['correct']:<20}")
    print(f"{'准确率':<20} {stats_think['accuracy']:.2f}%{'':<15} {stats_budget['accuracy']:.2f}%")

    print("\n[2. 思考Token消耗对比]")
    print("-" * 40)
    print(f"{'指标':<20} {'Think':<20} {'Think+Budget':<20}")
    print(f"{'平均Token数':<20} {stats_think['avg_thinking_tokens']:.2f}{'':<14} {stats_budget['avg_thinking_tokens']:.2f}")
    print(f"{'最小Token数':<20} {stats_think['min_thinking_tokens']:<20} {stats_budget['min_thinking_tokens']:<20}")
    print(f"{'最大Token数':<20} {stats_think['max_thinking_tokens']:<20} {stats_budget['max_thinking_tokens']:<20}")
    print(f"{'总Token数':<20} {stats_think['total_thinking_tokens']:<20} {stats_budget['total_thinking_tokens']:<20}")

    token_reduction = (1 - stats_budget['avg_thinking_tokens'] / stats_think['avg_thinking_tokens']) * 100 if stats_think['avg_thinking_tokens'] > 0 else 0
    print(f"{'平均Token节省':<20} {stats_think['avg_thinking_tokens'] - stats_budget['avg_thinking_tokens']:.2f} (减少 {token_reduction:.2f}%)")

    print("\n[3. 时延对比]")
    print("-" * 40)
    print(f"{'指标':<20} {'Think':<20} {'Think+Budget':<20}")
    print(f"{'平均时延(s)':<20} {stats_think['avg_elapsed_time']:.2f}{'':<14} {stats_budget['avg_elapsed_time']:.2f}")
    print(f"{'总时延(s)':<20} {stats_think['total_elapsed_time']:.2f}{'':<14} {stats_budget['total_elapsed_time']:.2f}")

    time_reduction = (1 - stats_budget['avg_elapsed_time'] / stats_think['avg_elapsed_time']) * 100 if stats_think['avg_elapsed_time'] > 0 else 0
    print(f"{'平均时延变化':<20} {stats_budget['avg_elapsed_time'] - stats_think['avg_elapsed_time']:+.2f}s (减少 {time_reduction:.2f}%)")

    print("\n[4. 综合对比]")
    print("-" * 40)
    print(f"Think+Budget 相比 Think:")
    print(f"  - 准确率变化: {stats_budget['accuracy'] - stats_think['accuracy']:+.2f}%")
    print(f"  - 思考Token平均节省: {token_reduction:.2f}%")
    print(f"  - 时延平均变化: {stats_budget['avg_elapsed_time'] - stats_think['avg_elapsed_time']:+.2f}s ({time_reduction:+.2f}%)")

    print("\n" + "=" * 80)


def main():
    naie_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'naie')

    naie_files = [f for f in os.listdir(naie_dir) if f.startswith('evaluation_results') and f.endswith('.json')]

    think_file = None
    budget_file = None
    for f in naie_files:
        if 'think_budget' in f:
            budget_file = f
        elif f.startswith('evaluation_results_think_') and 'think_budget' not in f:
            think_file = f

    if not think_file or not budget_file:
        print("Could not find both think and think_budget result files")
        print(f"Found: {naie_files}")
        return

    data_think = json.load(open(os.path.join(naie_dir, think_file), 'r', encoding='utf-8'))
    data_budget = json.load(open(os.path.join(naie_dir, budget_file), 'r', encoding='utf-8'))

    stats_think = analyze_results(data_think, 'Think')
    stats_budget = analyze_results(data_budget, 'Think+Budget')

    print_comparison(stats_think, stats_budget)

    print("\n[5. 轨迹日志摘要]")
    traj_summary = get_trajectory_summary()
    if traj_summary['total'] > 0:
        print(f"  已记录轨迹: {traj_summary['total']} 条")
        print(f"  平均思考Token: {traj_summary['avg_thinking_tokens']:.0f}")
        print(f"  平均耗时: {traj_summary['avg_elapsed_time']:.2f}s")
        print(f"  平均得分: {traj_summary['avg_score']:.2f}")
    else:
        print("  暂无轨迹日志（需要运行评估来生成）")


if __name__ == '__main__':
    main()
