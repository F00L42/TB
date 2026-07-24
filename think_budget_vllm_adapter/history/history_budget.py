"""历史轨迹驱动的预算推算 — 三段式泛化策略。

从 think_budget/history/history_budget.py 迁移，
import 路径调整为 think_budget_vllm_adapter 内部模块。
"""
import math
from typing import Dict, Optional, Any

from think_budget_vllm_adapter.history.history_module import (
    get_task_type_stats, get_cv_for_task_type,
)
from think_budget_vllm_adapter.optimizations.task_type_budget import (
    get_task_type_budget, register_custom_task_type,
    is_known_task_type, compute_num_thinking_from_budget, compute_stride_from_budget,
)
from think_budget_vllm_adapter.config.unified_config import get_history_config


def compute_history_budget(
    question: str,
    task_type: str = 'general',
    scene_type: str = '',
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """基于历史轨迹计算 token 预算 — 三段式泛化策略。"""
    cfg = config or {}
    hist_cfg = get_history_config()

    new_task_cfg = hist_cfg.get('new_task', {})
    new_task_unlimited = cfg.get('new_task_unlimited', new_task_cfg.get('unlimited', True))
    unlimited_placeholder = new_task_cfg.get('unlimited_budget_placeholder', 99999)

    warm_cfg = hist_cfg.get('warm_start', {})
    multiplier = cfg.get('history_budget_multiplier', warm_cfg.get('multiplier', 1.2))
    warmup_count = cfg.get('history_warmup_count', warm_cfg.get('warmup_count', 3))
    warmup_strategy = cfg.get('history_warmup_strategy', warm_cfg.get('strategy', 'moderate'))
    high_variance_boosts = warm_cfg.get('high_variance_boost', {
        'conservative': 1.3, 'moderate': 1.1, 'aggressive': 1.0,
    })

    hot_cfg = hist_cfg.get('hot_start', {})
    stride_percentile = cfg.get('history_stride_percentile', hot_cfg.get('stride_percentile', 0.75))
    low_success_boost = cfg.get('history_low_success_boost', hot_cfg.get('low_success_boost', 1.3))
    high_failure_stride_factor = hot_cfg.get('high_failure_stride_factor', 0.8)

    task_was_known = is_known_task_type(task_type)
    task_budget = get_task_type_budget(task_type, cfg.get('custom_task_types_path'))

    cat_stats = get_task_type_stats(task_type, scene_type)
    history_count = cat_stats.get('count', 0)
    history_avg_tokens = cat_stats.get('avg_tokens', 0.0)
    history_p75_tokens = cat_stats.get('p75_tokens', 0.0)
    success_rate = cat_stats.get('success_rate', 0.0)
    failure_rate = cat_stats.get('failure_rate', 0.0)
    quality_count = cat_stats.get('quality_count', 0)
    history_snapshot_id = (
        f"{task_type}:{scene_type or 'general'}:"
        f"{history_count}:{cat_stats.get('p75_tokens', 0)}"
    )

    # Once an unknown task has enough completed examples, replace its generic
    # cold-start entry with a history-derived task profile.  Registration is
    # deliberately deferred until five records so one noisy request cannot
    # permanently define the type.
    auto_cluster_threshold = int(cfg.get('auto_cluster_threshold', 5))
    if not task_was_known and history_count >= auto_cluster_threshold:
        register_task_type_from_history(
            task_type,
            scene_type=scene_type,
            json_path=cfg.get('custom_task_types_path'),
            min_count=auto_cluster_threshold,
        )
        task_budget = get_task_type_budget(task_type, cfg.get('custom_task_types_path'))

    # ═══ 阶段A: 当前任务类型没有历史 ═══
    # 题面措辞不同不代表任务类型是新的。以语义相似度判断会让同一
    # task_type 的新表述反复回到无限预算，破坏已积累的历史收敛性。
    if history_count == 0:
        if new_task_unlimited:
            return {
                'think_budget': unlimited_placeholder,
                'check_length': task_budget.get('check_length', 500),
                'think_stride': task_budget.get('think_stride', 200),
                'num_thinking': 99,
                'min_think_tokens': task_budget.get('min_think_tokens', 0),
                'check_answer_len': task_budget.get('check_answer_len', 3000),
                'prob_th': task_budget.get('prob_th', 0.93),
                'budget_mode': 'unlimited',
                'history_count': history_count,
                'history_avg_tokens': 0.0,
                'history_p75_tokens': 0.0,
                'success_rate': 0.0,
                'quality_count': 0,
                'is_new_task': True,
                'history_snapshot_id': history_snapshot_id,
            }
        else:
            return {
                'think_budget': task_budget.get('typical_tokens', 3000),
                'check_length': task_budget.get('check_length', 500),
                'think_stride': task_budget.get('think_stride', 200),
                'num_thinking': task_budget.get('num_thinking', 8),
                'min_think_tokens': task_budget.get('min_think_tokens', 200),
                'check_answer_len': task_budget.get('check_answer_len', 3000),
                'prob_th': task_budget.get('prob_th', 0.93),
                'budget_mode': 'cold_start',
                'history_count': 0,
                'history_avg_tokens': 0.0,
                'history_p75_tokens': 0.0,
                'success_rate': 0.0,
                'quality_count': 0,
                'is_new_task': True,
                'history_snapshot_id': history_snapshot_id,
            }

    # ═══ 阶段B: 冷启动(1~warmup_count条历史) ═══
    if history_count <= warmup_count:
        cv = get_cv_for_task_type(task_type, scene_type)

        effective_multiplier = multiplier
        if cv > 0.5:
            boost = high_variance_boosts.get(warmup_strategy, 1.1)
            effective_multiplier = multiplier * boost
        elif cv < 0.3:
            if warmup_strategy == 'aggressive':
                effective_multiplier = max(1.0, multiplier * 0.9)
            elif warmup_strategy == 'moderate':
                effective_multiplier = multiplier

        history_max_tokens = cat_stats.get('max_tokens', history_avg_tokens)
        think_budget = int(max(
            history_avg_tokens * effective_multiplier,
            history_max_tokens,
        ))

        num_thinking = compute_num_thinking_from_budget(think_budget, task_budget.get('check_length', 500))
        stride = compute_stride_from_budget(think_budget, num_thinking)

        return {
            'think_budget': think_budget,
            'check_length': min(think_budget, task_budget.get('check_length', 500)),
            'think_stride': stride,
            'num_thinking': num_thinking,
            'min_think_tokens': max(task_budget.get('min_think_tokens', 100), int(think_budget * 0.3)),
            'check_answer_len': task_budget.get('check_answer_len', 3000),
            'prob_th': task_budget.get('prob_th', 0.93),
            'budget_mode': 'warm_start',
            'history_count': history_count,
            'history_avg_tokens': round(history_avg_tokens, 1),
            'history_p75_tokens': round(history_p75_tokens, 1),
            'success_rate': round(success_rate, 3),
            'quality_count': quality_count,
            'is_new_task': False,
            'history_snapshot_id': history_snapshot_id,
            'warmup_cv': round(cv, 3),
            'warmup_multiplier_used': round(effective_multiplier, 3),
        }

    # ═══ 阶段C: 稳定期(>warmup_count条历史) ═══
    effective_tokens = history_p75_tokens if history_p75_tokens > 0 else history_avg_tokens

    if quality_count > 0 and success_rate < 0.7:
        effective_tokens = int(effective_tokens * low_success_boost)

    stride_factor = 1.0
    if quality_count > 0 and failure_rate > 0.3:
        stride_factor = high_failure_stride_factor

    num_thinking = compute_num_thinking_from_budget(effective_tokens, task_budget.get('check_length', 500),
                                                      min_rounds=3, max_rounds=20)
    stride = max(50, int((effective_tokens / num_thinking) * stride_factor))

    percentile_tokens = cat_stats.get(f'p{int(stride_percentile*100)}_tokens', effective_tokens)
    stride_from_percentile = max(50, int(percentile_tokens / num_thinking * stride_factor))
    stride = max(stride, stride_from_percentile)

    return {
        'think_budget': int(effective_tokens),
        'check_length': min(int(effective_tokens * 0.3), task_budget.get('check_length', 500)),
        'think_stride': stride,
        'num_thinking': num_thinking,
        'min_think_tokens': max(task_budget.get('min_think_tokens', 100), int(effective_tokens * 0.2)),
        'check_answer_len': task_budget.get('check_answer_len', 3000),
        'prob_th': task_budget.get('prob_th', 0.93),
        'budget_mode': 'hot_start',
        'history_count': history_count,
        'history_avg_tokens': round(history_avg_tokens, 1),
        'history_p75_tokens': round(history_p75_tokens, 1),
        'success_rate': round(success_rate, 3),
        'quality_count': quality_count,
        'is_new_task': False,
        'history_snapshot_id': history_snapshot_id,
    }
def register_task_type_from_history(
    task_type: str,
    scene_type: str = '',
    json_path: Optional[str] = None,
    min_count: int = 5,
) -> bool:
    """从历史记录自动注册新任务类型。"""
    stats = get_task_type_stats(task_type, scene_type)
    if stats.get('count', 0) < min_count:
        return False

    avg_tokens = stats.get('avg_tokens', 3000)
    p75_tokens = stats.get('p75_tokens', 5000)

    budget = {
        'description': f'自动聚类任务类型 — {task_type}',
        'typical_tokens': int(p75_tokens),
        'check_length': min(int(p75_tokens * 0.3), 2000),
        'think_stride': max(100, int(p75_tokens / 10)),
        'num_thinking': max(3, min(15, int(math.ceil(p75_tokens / max(avg_tokens * 0.3, 100))))),
        'min_think_tokens': max(50, int(avg_tokens * 0.2)),
        'check_answer_len': 3000,
        'prob_th': 0.93,
        'auto_registered': True,
        'source_stats': {
            'count': stats['count'],
            'avg_tokens': round(stats['avg_tokens'], 1),
            'p75_tokens': round(stats.get('p75_tokens', 0), 1),
            'success_rate': round(stats.get('success_rate', 0), 3),
        },
    }

    return register_custom_task_type(task_type, budget, json_path)
