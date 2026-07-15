"""Baseline vs BudgetForcing 对比报告。

比较同一数据集上基线评估和预算评估的结果，
输出准确率、思考Token消耗、耗时等指标的对比表格。

用法:
    python run_compare.py
"""
import json
import os
import sys
import io
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

BASE = os.path.join(os.path.dirname(__file__), 'data', 'naie', 'results')


def analyze_naie(data, name):
    """分析单次评估结果。"""
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


def main():
    print("=" * 80)
    print("  BASELINE vs BUDGETFORCING COMPARISON REPORT")
    print("=" * 80)

    naie_files = [f for f in os.listdir(BASE) if f.endswith('.json')] if os.path.exists(BASE) else []

    # 查找 baseline 和 budget 结果文件
    think_file = None
    budget_file = None
    for f in naie_files:
        if 'baseline' in f.lower():
            think_file = f
        elif 'budget' in f.lower():
            budget_file = f

    if not think_file and not budget_file:
        # 尝试其他命名模式
        for f in naie_files:
            if 'think_budget' in f or 'budget' in f:
                budget_file = f
            elif 'think' in f and 'budget' not in f:
                think_file = f

    if think_file and budget_file:
        data_think = json.load(open(os.path.join(BASE, think_file), 'r', encoding='utf-8'))
        data_budget = json.load(open(os.path.join(BASE, budget_file), 'r', encoding='utf-8'))

        stats_think = analyze_naie(data_think, 'Baseline')
        stats_budget = analyze_naie(data_budget, 'BudgetForcing')

        print(f"\n{'指标':<25s} {'Baseline':<20s} {'BudgetForcing':<20s} {'变化':<15s}")
        print("-" * 80)
        print(f"{'样本总数':<25s} {stats_think['total']:<20d} {stats_budget['total']:<20d}")
        print(f"{'正确数':<25s} {stats_think['correct']:<20d} {stats_budget['correct']:<20d}")
        print(f"{'准确率':<25s} {stats_think['accuracy']:.2f}%{'':<14} {stats_budget['accuracy']:.2f}%"
              f"  {stats_budget['accuracy'] - stats_think['accuracy']:+.2f}%")
        print(f"{'平均思考Token':<25s} {stats_think['avg_thinking_tokens']:.0f}{'':<15} {stats_budget['avg_thinking_tokens']:.0f}"
              f"  {(1 - stats_budget['avg_thinking_tokens']/stats_think['avg_thinking_tokens'])*100 if stats_think['avg_thinking_tokens'] > 0 else 0:+.1f}%")
        print(f"{'总思考Token':<25s} {stats_think['total_thinking_tokens']:.0f}{'':<12} {stats_budget['total_thinking_tokens']:.0f}")
        print(f"{'平均耗时(s)':<25s} {stats_think['avg_elapsed_time']:.2f}{'':<14} {stats_budget['avg_elapsed_time']:.2f}"
              f"  {stats_budget['avg_elapsed_time'] - stats_think['avg_elapsed_time']:+.2f}s")

        token_reduction = (1 - stats_budget['avg_thinking_tokens'] / stats_think['avg_thinking_tokens']) * 100 if stats_think['avg_thinking_tokens'] > 0 else 0
        acc_change = stats_budget['accuracy'] - stats_think['accuracy']
        print(f"""
┌─────────────────────────────────────────────────────────────────────┐
│                    NAIE (Tool Calling)                              │
├─────────────────────────────────────────────────────────────────────┤
│  Baseline:      Acc={stats_think['accuracy']:.1f}%  AvgThinkTok={stats_think['avg_thinking_tokens']:.0f}          │
│  BudgetForcing: Acc={stats_budget['accuracy']:.1f}%  AvgThinkTok={stats_budget['avg_thinking_tokens']:.0f}          │
│  Δ:             Acc={acc_change:+.1f}%  TokenSave={token_reduction:.1f}%              │
└─────────────────────────────────────────────────────────────────────┘
""")
    else:
        print(f"  结果文件目录: {BASE}")
        print(f"  找到的文件: {naie_files}")
        print("  未找到 baseline 和 budget 结果文件进行对比")
        print("  请先运行: python run_baseline.py 和 python run_budget.py")


if __name__ == '__main__':
    main()
