"""NAIE BudgetForcing Evaluation

Usage:
    python run_budget.py [options]

Core:
    --max_cases N               只跑前N条 (默认5, 0=全部)
    --model_name NAME           模型名称 (默认mg2hg)
    --check_length N            初始思考token数 (默认200)
    --think_stride N            每轮思考增量token (默认100)
    --prob_th FLOAT             置信度阈值 (默认0.95)
    --num_thinking N            最大尝试轮次 (默认10)
    --check_answer_len N        尝试答案长度 (默认4000)
    --max_tokens N              最大总token (默认65536)

Parallel:
    --parallel                  启用并行模式 (默认顺序执行)
    --concurrency N             并发数 (默认4, 仅 --parallel 时生效)

Switches (on|off):
    --enable_budget             BudgetForcing总开关 (默认ON)
    --enable_consecutive_same_stop 连续相同答案早停 (默认ON)
    --enable_value_min_confidence Value最低概率 (默认ON)
    --enable_clean_context      Clean Context (默认ON)
    --enable_safety_gate        安全门 (默认OFF)
    --enable_difficulty_estimator [P5] 难度估计器 (默认OFF)
    --enable_deer_trial         [P6] DEER风格trial answer (默认OFF)
    --enable_soft_truncation    软截断 (默认ON)

Task Type & History Budget:
    --enable_task_type_budget on|off   启用任务类型判断 (默认ON)
    --enable_history_budget on|off     启用历史轨迹预算 (默认ON)
    --task_type NAME                   手动覆盖任务类型
    --new_task_unlimited on|off        新任务类型不设限制 (默认ON)

Human Control:
    --fixed_budget_tokens N     固定budget token消耗 (0=动态计算)
    --fixed_num_thinking N      固定最大轮次 (0=动态计算)
    --fixed_think_stride N      固定步长 (0=动态计算)
    --fixed_check_length N      固定初始思考长度 (0=动态计算)
    --fixed_prob_th FLOAT       固定置信度阈值 (0.0=使用prob_th)
    --fixed_min_think_tokens N  固定最低思考token (0=动态计算)

History Budget Tuning:
    --history_budget_multiplier F   历史预算warm-start乘数 (默认1.2)
    --history_low_success_boost F   低成功率预算放大 (默认1.3)
    --history_stride_percentile F   步长历史百分位 (默认0.75)
    --history_warmup_count N        warm-start条数阈值 (默认3)
    --history_warmup_strategy STR   warm-start策略 (conservative/moderate/aggressive)
    --auto_cluster_threshold N      自动聚类新任务类型的历史条数阈值 (默认5)

Sub-params:
    --consecutive_same_stop N   连续相同次数阈值 (默认2)
    --value_min_bottom_k N      Value最低K个token取均值 (默认5)
    --min_think_tokens N        最低思考token硬门 (默认100)
    --max_api_calls N           单题最大API调用次数 (默认20)
    --baseline_time_limit FLOAT 安全门基线延迟s (默认90.0)
"""
import naie._bootstrap  # sys.path + NO_PROXY

import argparse
import time
from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47, print_config
from naie.evaluation.runner import load_datasets, evaluate_budget, calculate_metrics
from naie.evaluation.parallel_runner import evaluate_budget_parallel


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
        # 人工可控超参
        'fixed_budget_tokens', 'fixed_num_thinking', 'fixed_think_stride',
        'fixed_check_length', 'fixed_min_think_tokens',
        # 历史轨迹参数
        'history_warmup_count', 'auto_cluster_threshold',
    ]
    _FLOAT_PARAMS = [
        'prob_th', 'baseline_time_limit',
        # 人工可控超参
        'fixed_prob_th',
        # 历史轨迹参数
        'history_budget_multiplier', 'history_low_success_boost',
        'history_stride_percentile',
    ]
    _BOOL_PARAMS = [
        'enable_budget', 'enable_consecutive_same_stop',
        'enable_value_min_confidence',
        'enable_clean_context', 'enable_safety_gate',
        'enable_difficulty_estimator', 'enable_deer_trial',
        'enable_soft_truncation',
        # 任务类型 & 历史轨迹
        'enable_task_type_budget', 'enable_history_budget',
        'new_task_unlimited',
    ]
    _STR_PARAMS = [
        'task_type_override',
        'history_warmup_strategy',
        'default_task_type',
        'custom_task_types_path',
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

    for p in _STR_PARAMS:
        v = getattr(args, p, None)
        if v is not None and v:
            cfg[p] = v

    return cfg


def main():
    parser = argparse.ArgumentParser(description='NAIE BudgetForcing Evaluation')

    # 核心参数
    parser.add_argument('--max_cases', type=int, default=5, help='只跑前N条 (0=全部)')
    parser.add_argument('--model_name', type=str, default=None, help='模型名称')
    parser.add_argument('--check_length', type=int, default=None)
    parser.add_argument('--think_stride', type=int, default=None)
    parser.add_argument('--prob_th', type=float, default=None)
    parser.add_argument('--num_thinking', type=int, default=None)
    parser.add_argument('--check_answer_len', type=int, default=None)
    parser.add_argument('--max_tokens', type=int, default=None)

    # 开关
    parser.add_argument('--enable_budget', type=str, default=None)
    parser.add_argument('--enable_consecutive_same_stop', type=str, default=None)
    parser.add_argument('--consecutive_same_stop', type=int, default=None)
    parser.add_argument('--enable_value_min_confidence', type=str, default=None)
    parser.add_argument('--value_min_bottom_k', type=int, default=None)
    parser.add_argument('--enable_clean_context', type=str, default=None)
    parser.add_argument('--enable_safety_gate', type=str, default=None)
    parser.add_argument('--baseline_time_limit', type=float, default=None)
    parser.add_argument('--min_think_tokens', type=int, default=None)
    parser.add_argument('--max_api_calls', type=int, default=None)
    parser.add_argument('--enable_difficulty_estimator', type=str, default=None)
    parser.add_argument('--enable_deer_trial', type=str, default=None)
    parser.add_argument('--enable_soft_truncation', type=str, default=None)

    # 任务类型判断
    parser.add_argument('--enable_task_type_budget', type=str, default=None,
                        help='启用任务类型判断 (on|off)')
    parser.add_argument('--task_type', type=str, default=None,
                        help='手动覆盖任务类型 (retrieval/coding/math/...)')
    parser.add_argument('--default_task_type', type=str, default=None,
                        help='默认任务类型 — 无法判断时使用 (默认general)')
    parser.add_argument('--custom_task_types_path', type=str, default=None,
                        help='自定义任务类型配置路径 — None使用内置默认')
    parser.add_argument('--new_task_unlimited', type=str, default=None,
                        help='新任务类型不设限制 (on|off)')

    # 历史轨迹预算
    parser.add_argument('--enable_history_budget', type=str, default=None,
                        help='启用历史轨迹预算 (on|off)')
    parser.add_argument('--history_budget_multiplier', type=float, default=None,
                        help='历史预算warm-start乘数 (默认1.2)')
    parser.add_argument('--history_low_success_boost', type=float, default=None,
                        help='低成功率预算放大 (默认1.3)')
    parser.add_argument('--history_stride_percentile', type=float, default=None,
                        help='步长历史百分位 (默认0.75)')
    parser.add_argument('--history_warmup_count', type=int, default=None,
                        help='warm-start条数阈值 (默认3)')
    parser.add_argument('--history_warmup_strategy', type=str, default=None,
                        choices=['conservative', 'moderate', 'aggressive'],
                        help='warm-start策略 (默认moderate)')
    parser.add_argument('--auto_cluster_threshold', type=int, default=None,
                        help='自动聚类新任务类型的历史条数阈值 (默认5)')

    # 人工可控超参
    parser.add_argument('--fixed_budget_tokens', type=int, default=None,
                        help='固定budget token消耗 (0=动态计算)')
    parser.add_argument('--fixed_num_thinking', type=int, default=None,
                        help='固定最大轮次 (0=动态计算)')
    parser.add_argument('--fixed_think_stride', type=int, default=None,
                        help='固定步长 (0=动态计算)')
    parser.add_argument('--fixed_check_length', type=int, default=None,
                        help='固定初始思考长度 (0=动态计算)')
    parser.add_argument('--fixed_prob_th', type=float, default=None,
                        help='固定置信度阈值 (0.0=使用prob_th)')
    parser.add_argument('--fixed_min_think_tokens', type=int, default=None,
                        help='固定最低思考token (0=动态计算)')

    # 并行
    parser.add_argument('--parallel', action='store_true', help='启用并行模式')
    parser.add_argument('--concurrency', type=int, default=4, help='并发数 (默认4)')

    args = parser.parse_args()

    # 将 --task_type 映射到 task_type_override
    if args.task_type:
        args.task_type_override = args.task_type

    override_cfg = _build_override_config(args)

    for k, v in override_cfg.items():
        THINK_BUDGET_CONFIG_47[k] = v

    model_name = args.model_name or NAIE_CONFIG['model_name']

    print("=" * 70)
    print("NAIE BudgetForcing 评估")
    print("=" * 70)
    print_config()
    if override_cfg:
        print(f"\n  CLI覆盖参数: {override_cfg}")
    print(f"  用例数:       {args.max_cases or '全部'}")
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

    result_dir = os.path.join(os.path.dirname(__file__), 'data', 'naie', 'results')
    result_dir = os.path.abspath(result_dir)

    budget_config = dict(THINK_BUDGET_CONFIG_47)
    budget_config['max_cases'] = args.max_cases or 'all'
    if args.model_name:
        budget_config['model_name_cli'] = args.model_name

    wall_start = time.time()
    if args.parallel:
        results = evaluate_budget_parallel(datasets, result_dir, budget_config,
                                           concurrency=args.concurrency)
    else:
        results = evaluate_budget(datasets, result_dir, budget_config)
    wall_total = time.time() - wall_start

    metrics = calculate_metrics(results)

    print("\n" + "=" * 50)
    print("BudgetForcing 结果汇总")
    print("=" * 50)
    print(f"  总用例数:       {metrics['total']}")
    print(f"  正确数:         {metrics['correct']}")
    print(f"  准确率:         {metrics['accuracy']:.2%}")
    print(f"  平均单题延迟:   {metrics['avg_elapsed_time']:.2f}s")
    print(f"  端到端总延迟:   {wall_total:.1f}s")
    print(f"  平均思考token:  {metrics['avg_thinking_tokens']:.0f}")
    print(f"  平均API调用次数: {metrics['avg_api_calls']:.1f}")

    accept_reasons = {}
    for r in results:
        ar = r.get('accept_reason_cn', r.get('accept_reason', 'unknown'))
        accept_reasons[ar] = accept_reasons.get(ar, 0) + 1
    print(f"  采纳原因分布:   {accept_reasons}")
    print("=" * 50)


if __name__ == '__main__':
    main()
