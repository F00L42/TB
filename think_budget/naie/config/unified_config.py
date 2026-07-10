"""统一预算配置加载器 — 从 budget_config.yaml 加载所有 Token 预算、难度、任务类型配置。

设计原则:
  - 所有配置集中在 budget_config.yaml，方便商业交接
  - 提供 Python API 供各模块调用，无需直接解析 YAML
  - 支持运行时热更新(reload)
  - 兼容现有 settings.py CONFIG 格式
"""
import os
from typing import Dict, Any, Optional, List
from enum import Enum

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


# ─── 配置文件路径 ───
_BUDGET_CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'budget_config.yaml')

# ─── 缓存 ───
_config_cache: Optional[Dict] = None
_config_mtime: Optional[float] = None


class TaskType(str, Enum):
    """任务类型枚举 — 系统支持的所有任务类型。

    新增任务类型只需:
      1. 在 budget_config.yaml 的 TASK_TYPES 下添加一项
      2. 在此枚举中添加对应的值
    系统会自动识别和加载新类型的 Token 预算配置。
    """
    RETRIEVAL = "retrieval"
    CLASSIFICATION = "classification"
    CODING = "coding"
    MATH = "math"
    REASONING = "reasoning"
    TOOL_CALL = "tool_call"
    FAULT_DIAGNOSIS = "fault_diagnosis"
    KNOWLEDGE_QA = "knowledge_qa"
    PLANNING = "planning"
    GENERAL = "general"

    @classmethod
    def values(cls) -> List[str]:
        """获取所有枚举值列表。"""
        return [e.value for e in cls]

    @classmethod
    def has_value(cls, value: str) -> bool:
        """检查某个字符串是否是有效的任务类型。"""
        return value in cls.values()

    @classmethod
    def from_str(cls, value: str) -> 'TaskType':
        """从字符串获取枚举值，未知类型返回 GENERAL。"""
        try:
            return cls(value)
        except ValueError:
            return cls.GENERAL


class DifficultyTier(str, Enum):
    """难度梯度枚举 — 系统支持的难度等级。

    每个等级的乘数倍率由 budget_config.yaml 的 DIFFICULTY_TIERS 控制，
    禁止在函数体内硬编码 token 计算。
    """
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    VERY_HARD = "very_hard"


def load_budget_config(config_path: Optional[str] = None, force_reload: bool = False) -> Dict[str, Any]:
    """加载预算配置 YAML 文件(带缓存和文件修改检测)。

    Args:
        config_path: YAML 文件路径，默认为 naie/config/budget_config.yaml
        force_reload: 是否强制重新加载(忽略缓存)

    Returns:
        dict: 完整的预算配置字典
    """
    global _config_cache, _config_mtime

    path = config_path or _BUDGET_CONFIG_PATH

    if not force_reload and _config_cache is not None:
        # 检查文件修改时间
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            if _config_mtime == mtime:
                return _config_cache

    if not os.path.exists(path):
        # 回退到默认值(不依赖 YAML 文件)
        return _get_default_config()

    if not _YAML_AVAILABLE:
        # PyYAML 未安装，回退到默认值
        import logging
        logging.getLogger(__name__).warning("PyYAML 未安装，使用内置默认配置。请 pip install pyyaml")
        return _get_default_config()

    try:
        with open(path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        _config_cache = config
        _config_mtime = os.path.getmtime(path) if os.path.exists(path) else None
        return config
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"加载预算配置失败({path}): {e}, 使用默认配置")
        return _get_default_config()


def get_task_type_budgets(config: Optional[Dict] = None) -> Dict[str, Dict[str, Any]]:
    """获取所有任务类型的 Token 预算参数。

    Args:
        config: 预算配置字典(可选，默认从 YAML 加载)

    Returns:
        {task_type: {typical_tokens, check_length, ...}}
    """
    cfg = config or load_budget_config()
    return cfg.get('TASK_TYPES', {})


def get_task_type_budget(task_type: str, config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取指定任务类型的 Token 预算参数。

    Args:
        task_type: 任务类型名称
        config: 预算配置字典(可选)

    Returns:
        dict: 该任务类型的 Token 预算参数
    """
    budgets = get_task_type_budgets(config)
    return dict(budgets.get(task_type, budgets.get('general', {})))


def get_difficulty_tier(difficulty: float, config: Optional[Dict] = None) -> Dict[str, Any]:
    """根据难度值获取对应的难度等级配置。

    Args:
        difficulty: 难度值 ∈ [0, 1]
        config: 预算配置字典(可选)

    Returns:
        dict: 难度等级配置(含 token_multiplier, check_length_factor 等)
    """
    cfg = config or load_budget_config()
    tiers = cfg.get('DIFFICULTY_TIERS', {})

    for tier_name, tier_config in tiers.items():
        r = tier_config.get('difficulty_range', [0.0, 1.0])
        if r[0] <= difficulty < r[1] or (tier_name == 'very_hard' and difficulty >= r[0]):
            return {'tier': tier_name, **tier_config}

    # 默认返回 medium
    return {'tier': 'medium', **tiers.get('medium', {})}


def get_difficulty_multiplier(tier_name: str, config: Optional[Dict] = None) -> float:
    """获取指定难度等级的 Token 乘数。

    Args:
        tier_name: 难度等级名称(easy/medium/hard/very_hard)
        config: 预算配置字典(可选)

    Returns:
        float: Token 乘数
    """
    cfg = config or load_budget_config()
    tiers = cfg.get('DIFFICULTY_TIERS', {})
    return tiers.get(tier_name, {}).get('token_multiplier', 1.0)


def get_human_controllable_params(config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取4个人工可控超参数。

    Returns:
        {
            'BUDGET_ENABLED': bool,
            'FIXED_BUDGET_TOKEN': int,
            'CONFIDENCE_THRESHOLD': float,
            'PERCENTILE_STEP': float,
        }
    """
    cfg = config or load_budget_config()
    return cfg.get('HUMAN_CONTROLLABLE', {})


def get_history_config(config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取历史轨迹预算策略配置(三段式泛化)。

    Returns:
        dict: 包含 HISTORY_NEW_TASK, HISTORY_WARM_START, HISTORY_HOT_START, AUTO_REGISTRATION
    """
    cfg = config or load_budget_config()
    return {
        'new_task': cfg.get('HISTORY_NEW_TASK', {}),
        'warm_start': cfg.get('HISTORY_WARM_START', {}),
        'hot_start': cfg.get('HISTORY_HOT_START', {}),
        'auto_registration': cfg.get('AUTO_REGISTRATION', {}),
    }


def get_task_type_priority(config: Optional[Dict] = None) -> List[str]:
    """获取任务类型分类优先级列表。"""
    cfg = config or load_budget_config()
    return cfg.get('TASK_TYPE_PRIORITY', [
        'coding', 'math', 'reasoning', 'planning',
        'fault_diagnosis', 'tool_call', 'classification',
        'retrieval', 'knowledge_qa', 'general',
    ])


def get_scene_type_tokens(scene_type: str, config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取场景类型的 typical_tokens 和 token_range。"""
    cfg = config or load_budget_config()
    scenes = cfg.get('SCENE_TYPES', {})
    return scenes.get(scene_type, scenes.get('general', {
        'typical_tokens': 3000,
        'token_range': [500, 15000],
    }))


def compute_difficulty_adjusted_budget(
    task_type: str,
    difficulty: float,
    config: Optional[Dict] = None,
) -> Dict[str, Any]:
    """根据任务类型基础预算 × 难度等级乘数计算最终 Token 预算。

    替代原有的硬编码 if difficulty == "hard": tokens = 8000 逻辑。
    所有计算由配置文件驱动。

    Args:
        task_type: 任务类型
        difficulty: 难度值 ∈ [0, 1]
        config: 预算配置字典(可选)

    Returns:
        dict: 调整后的 Token 预算参数
    """
    base_budget = get_task_type_budget(task_type, config)
    tier_config = get_difficulty_tier(difficulty, config)
    multiplier = tier_config.get('token_multiplier', 1.0)
    cl_factor = tier_config.get('check_length_factor', 0.5)
    stride_factor = tier_config.get('stride_factor', 0.5)
    nt_min = tier_config.get('num_thinking_min', 3)
    nt_max = tier_config.get('num_thinking_max', 10)

    typical = base_budget.get('typical_tokens', 3000)
    base_cl = base_budget.get('check_length', 500)
    base_stride = base_budget.get('think_stride', 200)

    adjusted_tokens = int(typical * multiplier)
    adjusted_cl = min(int(base_cl * cl_factor * 2), adjusted_tokens)  # 不超过总预算
    adjusted_stride = max(50, int(base_stride * stride_factor * 2))
    adjusted_nt = max(nt_min, min(nt_max, max(3, adjusted_tokens // max(adjusted_cl, 100))))

    return {
        'typical_tokens': adjusted_tokens,
        'check_length': adjusted_cl,
        'think_stride': adjusted_stride,
        'num_thinking': adjusted_nt,
        'min_think_tokens': max(50, int(adjusted_tokens * 0.2)),
        'check_answer_len': base_budget.get('check_answer_len', 3000),
        'prob_th': base_budget.get('prob_th', 0.93),
        'difficulty_tier': tier_config.get('tier', 'medium'),
        'difficulty_multiplier': multiplier,
    }


def _get_default_config() -> Dict[str, Any]:
    """YAML 加载失败时的默认配置(硬编码回退)。"""
    return {
        'HUMAN_CONTROLLABLE': {
            'BUDGET_ENABLED': True,
            'FIXED_BUDGET_TOKEN': 0,
            'CONFIDENCE_THRESHOLD': 0.95,
            'PERCENTILE_STEP': 0.75,
        },
        'TASK_TYPES': {
            'retrieval': {'description': '检索任务', 'typical_tokens': 500, 'check_length': 200, 'think_stride': 100, 'num_thinking': 3, 'min_think_tokens': 50, 'check_answer_len': 2000, 'prob_th': 0.90},
            'classification': {'description': '分类任务', 'typical_tokens': 800, 'check_length': 300, 'think_stride': 100, 'num_thinking': 4, 'min_think_tokens': 100, 'check_answer_len': 2000, 'prob_th': 0.90},
            'coding': {'description': '编码任务', 'typical_tokens': 12000, 'check_length': 2000, 'think_stride': 500, 'num_thinking': 15, 'min_think_tokens': 2000, 'check_answer_len': 4000, 'prob_th': 0.95},
            'math': {'description': '数学推理', 'typical_tokens': 8000, 'check_length': 1500, 'think_stride': 400, 'num_thinking': 12, 'min_think_tokens': 1500, 'check_answer_len': 4000, 'prob_th': 0.95},
            'reasoning': {'description': '逻辑推理', 'typical_tokens': 5000, 'check_length': 800, 'think_stride': 300, 'num_thinking': 8, 'min_think_tokens': 500, 'check_answer_len': 3000, 'prob_th': 0.93},
            'tool_call': {'description': '工具调用', 'typical_tokens': 1000, 'check_length': 400, 'think_stride': 200, 'num_thinking': 8, 'min_think_tokens': 100, 'check_answer_len': 4000, 'prob_th': 0.95},
            'fault_diagnosis': {'description': '故障诊断', 'typical_tokens': 1500, 'check_length': 500, 'think_stride': 200, 'num_thinking': 8, 'min_think_tokens': 200, 'check_answer_len': 4000, 'prob_th': 0.95},
            'knowledge_qa': {'description': '知识问答', 'typical_tokens': 3000, 'check_length': 600, 'think_stride': 200, 'num_thinking': 6, 'min_think_tokens': 200, 'check_answer_len': 3000, 'prob_th': 0.92},
            'planning': {'description': '任务规划', 'typical_tokens': 2500, 'check_length': 600, 'think_stride': 200, 'num_thinking': 8, 'min_think_tokens': 300, 'check_answer_len': 3000, 'prob_th': 0.93},
            'general': {'description': '兜底通用', 'typical_tokens': 3000, 'check_length': 500, 'think_stride': 200, 'num_thinking': 8, 'min_think_tokens': 200, 'check_answer_len': 3000, 'prob_th': 0.93},
        },
        'DIFFICULTY_TIERS': {
            'easy': {'difficulty_range': [0.0, 0.3], 'token_multiplier': 0.8, 'check_length_factor': 0.3, 'stride_factor': 0.3, 'num_thinking_min': 3, 'num_thinking_max': 6},
            'medium': {'difficulty_range': [0.3, 0.6], 'token_multiplier': 1.0, 'check_length_factor': 0.6, 'stride_factor': 0.6, 'num_thinking_min': 5, 'num_thinking_max': 10},
            'hard': {'difficulty_range': [0.6, 0.8], 'token_multiplier': 1.5, 'check_length_factor': 0.8, 'stride_factor': 0.8, 'num_thinking_min': 8, 'num_thinking_max': 15},
            'very_hard': {'difficulty_range': [0.8, 1.0], 'token_multiplier': 2.0, 'check_length_factor': 1.0, 'stride_factor': 1.0, 'num_thinking_min': 10, 'num_thinking_max': 20},
        },
        'TASK_TYPE_PRIORITY': ['coding', 'math', 'reasoning', 'planning', 'fault_diagnosis', 'tool_call', 'classification', 'retrieval', 'knowledge_qa', 'general'],
        'HISTORY_NEW_TASK': {'unlimited': True, 'unlimited_budget_placeholder': 99999, 'new_task_similarity_threshold': 0.3},
        'HISTORY_WARM_START': {'warmup_count': 3, 'multiplier': 1.2, 'strategy': 'moderate', 'high_variance_boost': {'conservative': 1.3, 'moderate': 1.1, 'aggressive': 1.0}},
        'HISTORY_HOT_START': {'stride_percentile': 0.75, 'low_success_boost': 1.3, 'high_failure_stride_factor': 0.8},
        'AUTO_REGISTRATION': {'auto_cluster_threshold': 5},
        'SCENE_TYPES': {
            'tool_call': {'typical_tokens': 1000, 'token_range': [500, 2000]},
            'fault_diagnosis': {'typical_tokens': 1500, 'token_range': [800, 3000]},
            'math_reasoning': {'typical_tokens': 8000, 'token_range': [3000, 22000]},
            'code_generation': {'typical_tokens': 12000, 'token_range': [5000, 20000]},
            'knowledge_qa': {'typical_tokens': 3000, 'token_range': [1000, 7000]},
            'retrieval': {'typical_tokens': 500, 'token_range': [200, 2000]},
            'classification': {'typical_tokens': 800, 'token_range': [300, 3000]},
            'planning': {'typical_tokens': 2500, 'token_range': [1000, 5000]},
            'general': {'typical_tokens': 3000, 'token_range': [500, 15000]},
        },
    }
