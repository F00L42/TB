"""历史轨迹驱动的预算推算 — 基于同类问题历史 token 消耗动态调整预算。

三段式泛化策略:
  ─── 阶段A: 新任务出现(零历史) ───
    当检测到全新的 task_type 时，系统不设限制(或赋予极大值如99999)，
    确保任务能正常执行，同时记录历史轨迹供后续推算。

  ─── 阶段B: 冷启动(1~warmup_count条历史) ───
    系统自动评估已有的少量Token消耗记录，推算合理的think_budget大小。
    算法: max(历史avg_tokens × multiplier, 历史max_tokens) 作为初始预算。
    变异系数(CV)感知: 低方差(cv<0.3)收紧乘数，高方差(cv>0.5)放大乘数。

  ─── 阶段C: 稳定期(>warmup_count条历史) ───
    切换为严格的百分比步长控制(Percentile Step)设定Budget。
    取历史消耗的P75分位数(由 PERCENTILE_STEP 控制)作为预算基准。
    低成功率时放大预算，高失败率时缩小步长(更精细步进)。

动态注册机制:
  - register_new_task_type(): 当历史轨迹中出现未知类型时自动调用
  - 不会抛出 KeyError，新类型自动纳入管理
  - 同类问题积累超过阈值(auto_cluster_threshold)自动聚类

历史百分位步长:
  - 取历史 token 消耗的 p{percentile*100}, 按 num_thinking 分摊为步长
  - 高失败率场景: 步长缩小(更精细步进)
"""
import os
import json
import math
from typing import Dict, Optional, List, Any

from naie.optimizations.history_module import (
    load_history, get_scene_stats, get_category_stats,
    get_task_type_stats, get_cv_for_task_type,
    record_inference,
)
from naie.optimizations.task_type_budget import (
    get_task_type_budget, register_custom_task_type, register_new_task_type,
    is_known_task_type, compute_num_thinking_from_budget, compute_stride_from_budget,
    TASK_TYPE_BUDGET_DEFAULTS,
)
from naie.config.unified_config import (
    get_history_config, TaskType,
)


def compute_history_budget(
    question: str,
    task_type: str = 'general',
    scene_type: str = '',
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """基于历史轨迹计算 token 预算 — 三段式泛化策略。

    ═══ 阶段A: 新任务出现(零历史) ═══
      当检测到全新的 task_type 时，系统不设限制(或赋予极大值如99999)，
      确保任务能正常执行，同时动态注册新类型到系统中。

    ═══ 阶段B: 冷启动(1~warmup_count条历史) ═══
      系统自动评估已有的少量Token消耗记录，推算合理的think_budget。
      算法: max(历史avg × multiplier, 历史max) 作为初始预算。
      变异系数(CV)感知: 低方差收紧，高方差放大。

    ═══ 阶段C: 稳定期(>warmup_count条历史) ═══
      切换为严格的百分比步长控制(Percentile Step)设定Budget。
      取历史消耗的P{PERCENTILE_STEP*100}分位数作为预算基准。

    Args:
        question: 问题文本
        task_type: 任务类型
        scene_type: 商用场景类型
        config: 超参数配置(来自 CONFIG, 可覆盖 YAML 默认)

    Returns:
        {
            'think_budget': int,        # 推算的总 thinking token 预算
            'check_length': int,        # 初始思考长度
            'think_stride': int,        # 步长
            'num_thinking': int,        # 最大轮次
            'min_think_tokens': int,    # 最低思考token
            'check_answer_len': int,    # 答案长度
            'prob_th': float,           # 置信度阈值
            'budget_mode': str,         # 'unlimited'/'warm_start'/'hot_start'/'cold_start'
            'history_count': int,       # 历史条数
            'history_avg_tokens': float,# 历史平均token
            'history_p75_tokens': float,# 历史p75 token
            'success_rate': float,      # 历史成功率
            'is_new_task': bool,        # 是否新任务
        }
    """
    # ─── 加载配置(优先 CONFIG 参数，回退到 YAML 统一配置) ───
    cfg = config or {}
    hist_cfg = get_history_config()

    # 阶段A: 新任务配置
    new_task_cfg = hist_cfg.get('new_task', {})
    new_task_unlimited = cfg.get('new_task_unlimited', new_task_cfg.get('unlimited', True))
    unlimited_placeholder = new_task_cfg.get('unlimited_budget_placeholder', 99999)
    similarity_threshold = new_task_cfg.get('new_task_similarity_threshold', 0.3)

    # 阶段B: warm-start配置
    warm_cfg = hist_cfg.get('warm_start', {})
    multiplier = cfg.get('history_budget_multiplier', warm_cfg.get('multiplier', 1.2))
    warmup_count = cfg.get('history_warmup_count', warm_cfg.get('warmup_count', 3))
    warmup_strategy = cfg.get('history_warmup_strategy', warm_cfg.get('strategy', 'moderate'))
    high_variance_boosts = warm_cfg.get('high_variance_boost', {
        'conservative': 1.3, 'moderate': 1.1, 'aggressive': 1.0,
    })

    # 阶段C: hot-start配置
    hot_cfg = hist_cfg.get('hot_start', {})
    stride_percentile = cfg.get('history_stride_percentile', hot_cfg.get('stride_percentile', 0.75))
    low_success_boost = cfg.get('history_low_success_boost', hot_cfg.get('low_success_boost', 1.3))
    high_failure_stride_factor = hot_cfg.get('high_failure_stride_factor', 0.8)

    # 获取基础预算(来自任务类型) — 如果类型未知，动态注册
    if not is_known_task_type(task_type):
        register_new_task_type(task_type, typical_tokens=3000,
                               description=f'动态注册 — {task_type}',
                               json_path=cfg.get('custom_task_types_path'))
    task_budget = get_task_type_budget(task_type, cfg.get('custom_task_types_path'))

    # 获取历史统计(使用 history_module 的聚合缓存+索引加速)
    cat_stats = get_task_type_stats(task_type, scene_type)
    history_count = cat_stats.get('count', 0)
    history_avg_tokens = cat_stats.get('avg_tokens', 0.0)
    history_p75_tokens = cat_stats.get('p75_tokens', 0.0)
    success_rate = cat_stats.get('success_rate', 0.0)
    failure_rate = cat_stats.get('failure_rate', 0.0)

    # ═══════════════════════════════════════════════════════════
    # 阶段A: 新任务出现(零历史) — 不设限制或使用极大值
    # ═══════════════════════════════════════════════════════════
    # "题面新颖"不等于"任务类型没有历史"。同一 task_type 的新表述仍应
    # 使用已积累的轨迹，否则 hot-start 永远无法覆盖真实线上请求。
    if history_count == 0:
        if new_task_unlimited:
            # 不设Budget限制，赋予极大值确保任务正常执行
            return {
                'think_budget': unlimited_placeholder,  # 不限制(99999)
                'check_length': task_budget.get('check_length', 500),
                'think_stride': task_budget.get('think_stride', 200),
                'num_thinking': 99,  # 不限制
                'min_think_tokens': task_budget.get('min_think_tokens', 0),
                'check_answer_len': task_budget.get('check_answer_len', 3000),
                'prob_th': task_budget.get('prob_th', 0.93),
                'budget_mode': 'unlimited',
                'history_count': history_count,
                'history_avg_tokens': 0.0,
                'history_p75_tokens': 0.0,
                'success_rate': 0.0,
                'is_new_task': True,
            }
        else:
            # 不设限制但非新任务: 使用任务类型默认值
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
                'is_new_task': True,
            }

    # ═══════════════════════════════════════════════════════════
    # 阶段B: 冷启动(1~warmup_count条历史) — 推算合理预算
    # 算法: max(历史avg × multiplier, 历史max) 作为初始预算
    # ═══════════════════════════════════════════════════════════
    if history_count <= warmup_count:
        # 方差感知策略: 根据历史 token 消耗的变异系数调整乘数
        cv = get_cv_for_task_type(task_type, scene_type)  # 变异系数(使用 history_module 索引加速)

        effective_multiplier = multiplier  # 默认 1.2
        if cv > 0.5:
            # 高方差: 增大余量(从统一配置获取)
            boost = high_variance_boosts.get(warmup_strategy, 1.1)
            effective_multiplier = multiplier * boost
        elif cv < 0.3:
            # 低方差: 可收紧
            if warmup_strategy == 'aggressive':
                effective_multiplier = max(1.0, multiplier * 0.9)
            elif warmup_strategy == 'moderate':
                effective_multiplier = multiplier

        # 阶段B核心算法: max(历史avg × multiplier, 历史max)
        # 确保预算不低于任何单次历史消耗
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
            'is_new_task': False,
            'warmup_cv': round(cv, 3),  # 变异系数, 供调试
            'warmup_multiplier_used': round(effective_multiplier, 3),
        }

    # ═══════════════════════════════════════════════════════════
    # 阶段C: 稳定期(>warmup_count条历史) — 百分比步长控制
    # 取历史消耗的P{PERCENTILE_STEP*100}分位数作为预算基准
    # ═══════════════════════════════════════════════════════════
    # 用百分位 token 消耗作为预算基准
    effective_tokens = history_p75_tokens if history_p75_tokens > 0 else history_avg_tokens

    # 低成功率放大预算
    if success_rate < 0.7:
        effective_tokens = int(effective_tokens * low_success_boost)

    # 高失败率时缩小步长(更精细步进) — 使用统一配置的因子
    stride_factor = 1.0
    if failure_rate > 0.3:
        stride_factor = high_failure_stride_factor

    num_thinking = compute_num_thinking_from_budget(effective_tokens, task_budget.get('check_length', 500),
                                                      min_rounds=3, max_rounds=20)
    stride = max(50, int((effective_tokens / num_thinking) * stride_factor))

    # 用百分位步长: 取历史 stride_percentile 位的 token 消耗
    # 分摊到 num_thinking 轮
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
        'is_new_task': False,
    }


def detect_new_task_type(question: str, task_type: str = 'general',
                          scene_type: str = '', threshold: float = 0.3) -> bool:
    """检测是否为新任务类型(与历史记录低相似度)。

    判定逻辑:
      - 与同 task_type 的历史记录计算文本相似度(Jaccard)
      - 最相似的历史记录 sim < threshold → 判为新任务
      - 无任何历史 → 也判为新任务(冷启动)

    Args:
        question: 问题文本
        task_type: 任务类型
        scene_type: 场景类型
        threshold: 相似度阈值(默认0.3, 来自统一配置)

    Returns:
        bool: 是否为新任务
    """
    history = load_history()
    if not history:
        return True

    # 过滤同 task_type
    candidates = [e for e in history if e.get('task_type') == task_type]
    if not candidates:
        # 尝试 scene_type
        if scene_type:
            candidates = [e for e in history if e.get('scene_type') == scene_type]
        if not candidates:
            return True

    # 计算最大相似度
    max_sim = 0.0
    q_chars = set(question[:200])
    for e in candidates:
        ep = e.get('question_preview', '')
        if not ep:
            continue
        ep_chars = set(ep[:200])
        # Jaccard 相似度
        intersection = len(q_chars & ep_chars)
        union = len(q_chars | ep_chars)
        if union > 0:
            sim = intersection / union
            max_sim = max(max_sim, sim)

    # 最相似 < threshold → 新任务
    return max_sim < threshold


def register_task_type_from_history(
    task_type: str,
    scene_type: str = '',
    json_path: Optional[str] = None,
    min_count: int = 5,
) -> bool:
    """从历史记录自动注册新任务类型。

    条件: 同 task_type 的历史条数 >= min_count
    计算该类型的 token 消耗统计, 注册到自定义任务类型

    Args:
        task_type: 任务类型名称
        scene_type: 场景类型
        json_path: 自定义任务类型 JSON 路径
        min_count: 最小历史条数阈值

    Returns:
        bool: 是否注册成功
    """
    stats = get_task_type_stats(task_type, scene_type)
    if stats.get('count', 0) < min_count:
        return False

    # 基于历史统计推算预算参数
    avg_tokens = stats.get('avg_tokens', 3000)
    p75_tokens = stats.get('p75_tokens', 5000)

    # 简单的预算推算
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
        'registered_task_type': task_type,
        'scene_type': scene_type or 'general',
        'source_stats': {
            'count': stats['count'],
            'avg_tokens': round(stats['avg_tokens'], 1),
            'p75_tokens': round(stats.get('p75_tokens', 0), 1),
            'success_rate': round(stats.get('success_rate', 0), 3),
            'task_type': task_type,
            'scene_type': scene_type or 'general',
        },
    }

    return register_custom_task_type(task_type, budget, json_path)


