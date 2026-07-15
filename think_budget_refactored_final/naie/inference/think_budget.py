"""NAIE Think Budget — 将 CONFIG 映射为 budget_forcing.call_model() 参数。

此模块是 naie config → shared budget_forcing 的适配层。
model_name 支持通过 budget_config['model_name'] 或 kwargs['model_name'] 传入，
未指定时使用默认模型名 (DEFAULT_MODEL)。

同步版: call_model
异步版: call_model_async
"""
from naie.models.budget_forcing import call_model as _base_call_model
from naie.models.budget_forcing import call_model_async as _base_call_model_async
from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47
from naie.config.unified_config import get_naie_key_names
from naie.optimizations.token_filter import set_task_key_names

# 默认模型名 — 未显式指定 model_name 时使用此值
DEFAULT_MODEL = 'mg2hg'

# NAIE 任务特有的 JSON 键名集合 — 从 budget_config.yaml NAIE_KEY_NAMES 加载
# 这些键名是可预测的结构 token (高 logprob)，不应参与置信度计算。
# 必须在入口处注入 token_filter，否则公共版 cal_value_min_confidence()
# 的 _DEFAULT_KEY_NAMES 为空，无法过滤键名。
_NAIE_KEY_NAMES = set(get_naie_key_names())

# 模块加载时即注入 — 确保后续所有 token_filter 调用均生效
set_task_key_names(_NAIE_KEY_NAMES)


def _cfg_to_params(cfg):
    """将扁平 CONFIG 字典映射为 budget_forcing.call_model() 参数。

    重要: 所有 .get() 默认值必须与 CONFIG 中的默认值一致，
    以避免因 CONFIG 缺少 key 而导致行为静默变化。
    """
    return {
        # ─── P0 总开关 ───
        'enable_confidence': cfg.get('enable_budget', True),

        # ─── P1 核心budget参数 ───
        'stop_condition': 'length',
        'check_length': cfg.get('check_length', 200),
        'think_stride': cfg.get('think_stride', 100),
        'num_thinking': cfg.get('num_thinking', 10),
        'check_answer_len': cfg.get('check_answer_len', 4000),
        'model_name': cfg.get('model_name', DEFAULT_MODEL),
        'prob_th': cfg.get('prob_th', 0.95),
        'max_tokens': cfg.get('max_tokens', 65536),

        # ─── P2.2 连续相同答案早停 ───
        'consecutive_same_stop': cfg.get('consecutive_same_stop', 2) if cfg.get('enable_consecutive_same_stop', True) else 999,

        # ─── P3.1 Value最低概率 ───
        'enable_value_min_confidence': cfg.get('enable_value_min_confidence', True),
        'value_min_bottom_k': cfg.get('value_min_bottom_k', 5),

        # ─── P3.4 完整链置信度 ───
        'enable_full_chain_confidence': cfg.get('enable_full_chain_confidence', False),
        'full_chain_min_percentile': cfg.get('full_chain_min_percentile', 0.1),

        # ─── P3.3 Clean Context ───
        'enable_clean_context': cfg.get('enable_clean_context', True),

        # ─── 安全门 ───
        'enable_safety_gate': cfg.get('enable_safety_gate', False),
        'baseline_time_limit': cfg.get('baseline_time_limit', 90.0),

        # ─── 路由参数 ───
        'min_think_tokens': cfg.get('min_think_tokens', 100),
        'max_api_calls': cfg.get('max_api_calls', 20),

        # ─── 任务类型判断 ───
        'enable_task_type_budget': cfg.get('enable_task_type_budget', True),
        'default_task_type': cfg.get('default_task_type', 'general'),
        'custom_task_types_path': cfg.get('custom_task_types_path', None),
        'task_type_override': cfg.get('task_type_override', ''),

        # ─── 历史轨迹预算 ───
        'enable_history_budget': cfg.get('enable_history_budget', True),
        'history_budget_multiplier': cfg.get('history_budget_multiplier', 1.2),
        'history_low_success_boost': cfg.get('history_low_success_boost', 1.3),
        'history_stride_percentile': cfg.get('history_stride_percentile', 0.75),
        'new_task_unlimited': cfg.get('new_task_unlimited', True),
        'auto_cluster_threshold': cfg.get('auto_cluster_threshold', 5),
        'history_warmup_count': cfg.get('history_warmup_count', 3),
        'history_warmup_strategy': cfg.get('history_warmup_strategy', 'moderate'),

        # ─── 人工可控超参 ───
        'fixed_budget_tokens': cfg.get('fixed_budget_tokens', 0),
        'fixed_num_thinking': cfg.get('fixed_num_thinking', 0),
        'fixed_think_stride': cfg.get('fixed_think_stride', 0),
        'fixed_check_length': cfg.get('fixed_check_length', 0),
        'fixed_prob_th': cfg.get('fixed_prob_th', 0.0),
        'fixed_min_think_tokens': cfg.get('fixed_min_think_tokens', 0),

        # ─── 统一预算配置 — 4个核心暴露超参数 ───
        'BUDGET_ENABLED': cfg.get('BUDGET_ENABLED', True),
        'FIXED_BUDGET_TOKEN': cfg.get('FIXED_BUDGET_TOKEN', 0),
        'CONFIDENCE_THRESHOLD': cfg.get('CONFIDENCE_THRESHOLD', 0.95),
        'PERCENTILE_STEP': cfg.get('PERCENTILE_STEP', 0.75),

        # ─── P5 难度估计器 ───
        'enable_difficulty_estimator': cfg.get('enable_difficulty_estimator', False),
        'difficulty_check_length_base': cfg.get('difficulty_check_length_base', 400),
        'difficulty_check_length_max': cfg.get('difficulty_check_length_max', 6000),
        'difficulty_min_think_base': cfg.get('difficulty_min_think_base', 100),
        'difficulty_min_think_max': cfg.get('difficulty_min_think_max', 6000),
        'difficulty_stride_base': cfg.get('difficulty_stride_base', 400),
        'difficulty_stride_max': cfg.get('difficulty_stride_max', 6000),
        'difficulty_num_thinking_base': cfg.get('difficulty_num_thinking_base', 10),
        'difficulty_num_thinking_max': cfg.get('difficulty_num_thinking_max', 20),
        'difficulty_answer_len_base': cfg.get('difficulty_answer_len_base', 2000),
        'difficulty_answer_len_max': cfg.get('difficulty_answer_len_max', 6000),

        # ─── P6 DEER trial ───
        'enable_deer_trial': cfg.get('enable_deer_trial', False),
        'deer_trial_stable_rounds': cfg.get('deer_trial_stable_rounds', 2),
        'deer_trial_answer_len': cfg.get('deer_trial_answer_len', 30),
        'deer_confidence_boost': cfg.get('deer_confidence_boost', 0.05),

        # ─── 历史统计(由 runner.py 注入) ───
        'history_avg_tokens': cfg.get('history_avg_tokens', 0.0),
        'history_failure_rate': cfg.get('history_failure_rate', 0.0),
        'history_category_avg_tokens': cfg.get('history_category_avg_tokens', 0.0),
        'history_category_failure_rate': cfg.get('history_category_failure_rate', 0.0),
        'history_count': cfg.get('history_count', 0),

        # ─── 软截断 ───
        'enable_soft_truncation': cfg.get('enable_soft_truncation', True),

        # ─── 双通道评估 ───
        'enable_dual_channel': cfg.get('enable_dual_channel', False),
        'reasoning_min_percentile': cfg.get('reasoning_min_percentile', 0.1),
    }


def call_model(prompt, max_tokens, budget_config=None, reference='', **kwargs):
    """NAIE BudgetForcing 入口 — 注入 NAIE 配置后调用 shared budget_forcing。

    model_name 优先级: kwargs['model_name'] > budget_config['model_name'] > DEFAULT_MODEL
    """
    if budget_config is not None:
        params = _cfg_to_params(budget_config)
    else:
        params = _cfg_to_params(THINK_BUDGET_CONFIG_47)
    params.update(kwargs)
    # kwargs 中的 model_name 覆盖 cfg 中的; 若都没有则保留 _cfg_to_params 的默认值
    if 'model_name' not in kwargs and 'model_name' not in (budget_config or {}):
        params['model_name'] = DEFAULT_MODEL
    params['reference'] = reference
    # max_tokens 已作为位置参数传入, 从 params 中移除避免重复
    params.pop('max_tokens', None)
    return _base_call_model(prompt, max_tokens, **params)


async def call_model_async(prompt, max_tokens, budget_config=None, reference='', **kwargs):
    """NAIE BudgetForcing 异步入口 — 注入 NAIE 配置后调用 shared budget_forcing.call_model_async。

    model_name 优先级: kwargs['model_name'] > budget_config['model_name'] > DEFAULT_MODEL
    参数与同步版 call_model 完全一致, 供并行评估使用。
    """
    if budget_config is not None:
        params = _cfg_to_params(budget_config)
    else:
        params = _cfg_to_params(THINK_BUDGET_CONFIG_47)
    params.update(kwargs)
    if 'model_name' not in kwargs and 'model_name' not in (budget_config or {}):
        params['model_name'] = DEFAULT_MODEL
    params['reference'] = reference
    # max_tokens 已作为位置参数传入, 从 params 中移除避免重复
    params.pop('max_tokens', None)
    return await _base_call_model_async(prompt, max_tokens, **params)
