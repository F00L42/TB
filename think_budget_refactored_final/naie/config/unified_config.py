"""统一预算配置加载器 — 从 budget_config.yaml 加载所有 Token 预算、难度、任务类型配置。

设计原则:
  - 所有配置集中在 budget_config.yaml，方便商业交接
  - 提供 Python API 供各模块调用，无需直接解析 YAML
  - 支持运行时热更新(reload)
  - 兼容现有 settings.py CONFIG 格式
"""
import os
from typing import Dict, Any, Optional, List, Tuple
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
    STATISTICS_AGGREGATION = "statistics_aggregation"
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
    """难度梯度枚举 — 系统支持的难度等级。"""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    VERY_HARD = "very_hard"


def load_budget_config(config_path: Optional[str] = None, force_reload: bool = False) -> Dict[str, Any]:
    """加载预算配置 YAML 文件(带缓存和文件修改检测)。"""
    global _config_cache, _config_mtime

    path = config_path or _BUDGET_CONFIG_PATH

    if not force_reload and _config_cache is not None:
        if os.path.exists(path):
            mtime = os.path.getmtime(path)
            if _config_mtime == mtime:
                return _config_cache

    if not os.path.exists(path):
        return _get_default_config()

    if not _YAML_AVAILABLE:
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


# ═══════════════════════════════════════════════════════════════
# 现有配置加载函数 (保留原有)
# ═══════════════════════════════════════════════════════════════

def get_task_type_budgets(config: Optional[Dict] = None) -> Dict[str, Dict[str, Any]]:
    cfg = config or load_budget_config()
    return cfg.get('TASK_TYPES', {})


def get_task_type_budget(task_type: str, config: Optional[Dict] = None) -> Dict[str, Any]:
    budgets = get_task_type_budgets(config)
    return dict(budgets.get(task_type, budgets.get('general', {})))


def get_difficulty_tier(difficulty: float, config: Optional[Dict] = None) -> Dict[str, Any]:
    cfg = config or load_budget_config()
    tiers = cfg.get('DIFFICULTY_TIERS', {})
    for tier_name, tier_config in tiers.items():
        r = tier_config.get('difficulty_range', [0.0, 1.0])
        if r[0] <= difficulty < r[1] or (tier_name == 'very_hard' and difficulty >= r[0]):
            return {'tier': tier_name, **tier_config}
    return {'tier': 'medium', **tiers.get('medium', {})}


def get_difficulty_multiplier(tier_name: str, config: Optional[Dict] = None) -> float:
    cfg = config or load_budget_config()
    tiers = cfg.get('DIFFICULTY_TIERS', {})
    return tiers.get(tier_name, {}).get('token_multiplier', 1.0)


def get_human_controllable_params(config: Optional[Dict] = None) -> Dict[str, Any]:
    cfg = config or load_budget_config()
    return cfg.get('HUMAN_CONTROLLABLE', {})


def get_history_config(config: Optional[Dict] = None) -> Dict[str, Any]:
    cfg = config or load_budget_config()
    return {
        'new_task': cfg.get('HISTORY_NEW_TASK', {}),
        'warm_start': cfg.get('HISTORY_WARM_START', {}),
        'hot_start': cfg.get('HISTORY_HOT_START', {}),
        'auto_registration': cfg.get('AUTO_REGISTRATION', {}),
    }


def get_task_type_priority(config: Optional[Dict] = None) -> List[str]:
    cfg = config or load_budget_config()
    return cfg.get('TASK_TYPE_PRIORITY', [
        'coding', 'math', 'reasoning', 'planning',
        'fault_diagnosis', 'tool_call', 'statistics_aggregation', 'classification',
        'retrieval', 'knowledge_qa', 'general',
    ])


def get_scene_type_tokens(scene_type: str, config: Optional[Dict] = None) -> Dict[str, Any]:
    cfg = config or load_budget_config()
    scenes = cfg.get('SCENE_TYPES', {})
    return scenes.get(scene_type, scenes.get('general', {
        'typical_tokens': 3000, 'token_range': [500, 15000],
    }))


def compute_difficulty_adjusted_budget(
    task_type: str, difficulty: float, config: Optional[Dict] = None,
) -> Dict[str, Any]:
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
    adjusted_cl = min(int(base_cl * cl_factor * 2), adjusted_tokens)
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


# ═══════════════════════════════════════════════════════════════
# 新增配置加载函数
# ═══════════════════════════════════════════════════════════════

def get_model_client_config(config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取模型客户端配置(重试、超时、连接池、payload默认参数)。"""
    cfg = config or load_budget_config()
    return cfg.get('MODEL_CLIENT', {})


def get_safety_gate_config(config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取安全门配置(软/硬触发倍率、budget扩增参数)。"""
    cfg = config or load_budget_config()
    return cfg.get('SAFETY_GATE', {})


def get_budget_forcing_config(config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取BudgetForcing核心常量(答案token上限、自然断点、thinking_words等)。"""
    cfg = config or load_budget_config()
    return cfg.get('BUDGET_FORCING', {})


def get_prompt_format_config(config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取Prompt格式配置(think标签、chat template格式)。"""
    cfg = config or load_budget_config()
    return cfg.get('PROMPT_FORMAT', {})


def get_history_module_config(config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取历史模块配置(滑动窗口参数、缓存TTL)。"""
    cfg = config or load_budget_config()
    return cfg.get('HISTORY_MODULE', {})


def get_difficulty_estimator_config(config: Optional[Dict] = None) -> Dict[str, Any]:
    """获取难度估计器配置(特征权重、bins、映射参数)。"""
    cfg = config or load_budget_config()
    return cfg.get('DIFFICULTY_ESTIMATOR', {})


def get_naie_key_names(config: Optional[Dict] = None) -> List[str]:
    """获取NAIE结构Token键名列表(用于Value Min Confidence过滤)。"""
    cfg = config or load_budget_config()
    return cfg.get('NAIE_KEY_NAMES', [
        'tool', 'faultNumber', 'faultIMSI', 'faultPort', 'faultType',
        'fault', 'Number', 'IMSI', 'Url', 'Ip', 'APN', 'Time', 'Range',
        'Caller', 'result', 'answer', 'name', 'type', 'value',
        'faultUrl', 'faultIp', 'faultAPN', 'faultTimeRange',
        'isCaller', 'status', 'code', 'message', 'id', 'data',
        'error', 'success', 'query', 'response',
    ])


def get_scene_indicators(config: Optional[Dict] = None) -> Dict[str, Dict[str, List[str]]]:
    """获取场景指示词(strong/weak关键词列表)。

    Returns:
        {scene_type: {'strong': [...], 'weak': [...]}}
    """
    cfg = config or load_budget_config()
    return cfg.get('SCENE_INDICATORS', {})


def get_scene_full_config(config: Optional[Dict] = None) -> Dict[str, Dict[str, Any]]:
    """获取完整的场景配置(合并 SCENE_TYPES + SCENE_INDICATORS)。

    Returns:
        {scene_type: {typical_tokens, token_range, indicators_strong, indicators_weak, description}}
    """
    cfg = config or load_budget_config()
    scene_types = cfg.get('SCENE_TYPES', {})
    scene_indicators = cfg.get('SCENE_INDICATORS', {})

    # 合并: SCENE_TYPES 提供 typical_tokens/token_range, SCENE_INDICATORS 提供关键词
    result = {}
    for scene_name, scene_data in scene_types.items():
        indicators = scene_indicators.get(scene_name, {})
        result[scene_name] = {
            'typical_tokens': scene_data.get('typical_tokens', 3000),
            'token_range': tuple(scene_data.get('token_range', [500, 15000])),
            'indicators_strong': indicators.get('strong', []),
            'indicators_weak': indicators.get('weak', []),
        }

    # 补充 SCENE_INDICATORS 中有但 SCENE_TYPES 中没有的场景
    for scene_name, indicators in scene_indicators.items():
        if scene_name not in result:
            result[scene_name] = {
                'typical_tokens': 3000,
                'token_range': (500, 15000),
                'indicators_strong': indicators.get('strong', []),
                'indicators_weak': indicators.get('weak', []),
            }

    # general 兜底
    if 'general' not in result:
        result['general'] = {
            'typical_tokens': 3000,
            'token_range': (500, 15000),
            'indicators_strong': [],
            'indicators_weak': [],
        }

    return result


def get_task_type_indicators(config: Optional[Dict] = None) -> Dict[str, Dict[str, List[str]]]:
    """获取任务类型指示词(检索/分类/推理三类，其他类型用场景映射)。

    Returns:
        {task_type: {'strong': [...], 'weak': [...]}}
    """
    cfg = config or load_budget_config()
    return cfg.get('TASK_TYPE_INDICATORS', {})


# ═══════════════════════════════════════════════════════════════
# 默认配置回退 (YAML 加载失败时使用)
# ═══════════════════════════════════════════════════════════════

def _get_default_config() -> Dict[str, Any]:
    """YAML 加载失败时的默认配置(硬编码回退) — 唯一回退源。"""
    return {
        'HUMAN_CONTROLLABLE': {
            'BUDGET_ENABLED': True, 'FIXED_BUDGET_TOKEN': 0,
            'CONFIDENCE_THRESHOLD': 0.95, 'PERCENTILE_STEP': 0.75,
        },
        'TASK_TYPES': {
            'retrieval': {'description': '检索任务', 'typical_tokens': 500, 'check_length': 200, 'think_stride': 100, 'num_thinking': 3, 'min_think_tokens': 50, 'check_answer_len': 2000, 'prob_th': 0.90},
            'statistics_aggregation': {'description': '统计聚合/计数求和任务', 'typical_tokens': 4000, 'check_length': 800, 'think_stride': 300, 'num_thinking': 6, 'min_think_tokens': 600, 'check_answer_len': 4000, 'prob_th': 0.90},
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
        'TASK_TYPE_PRIORITY': ['coding', 'math', 'reasoning', 'planning', 'fault_diagnosis', 'tool_call', 'statistics_aggregation', 'classification', 'retrieval', 'knowledge_qa', 'general'],
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
        'MODEL_CLIENT': {
            'max_retries': 3, 'retry_delay': 2.0,
            'timeout': {'total': 180.0, 'connect': 30.0, 'read': 240.0, 'write': 30.0, 'pool': 30.0},
            'connection_pool': {
                'sync': {'max_connections': 16, 'max_keepalive_connections': 4, 'keepalive_expiry': 30.0},
                'async': {'max_connections': 32, 'max_keepalive_connections': 8, 'keepalive_expiry': 30.0},
            },
            'payload_defaults': {'temperature': 0, 'logprobs': True, 'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}},
        },
        'SAFETY_GATE': {
            'soft_trigger_multiplier': 1.0, 'hard_trigger_multiplier': 1.5,
            'budget_amplify_factor': 0.5, 'budget_amplify_cap': 4, 'budget_time_cap_multiplier': 3.0,
        },
        'BUDGET_FORCING': {
            'unlimited_answer_tokens': 30000,
            'trial_answer_tokens': 100,
            'natural_break_chars': ['。', '！', '？', '.', '!', '?', '\n', '}', '】', '」'],
            'natural_break_search_range': 500,
            'min_think_ratio_warm': 0.3, 'min_think_ratio_hot': 0.2,
            'thinking_words': ['等一下', '等等', '不过', '此外', '但是', 'Wait', 'wait', 'Hmm', 'hmm', 'Alternatively', 'alternatively', 'However', 'however'],
        },
        'PROMPT_FORMAT': {
            'think_open_tag': 'iesa', 'think_close_tag': 'eiraot',
            'user_prefix': '<|im_start|>user\n', 'user_suffix': '<|im_end|>\n<|im_start|>assistant\n', 'think_suffix': '\n',
        },
        'HISTORY_MODULE': {
            'max_entries': 5000, 'max_age_days': 7, 'evict_every_n_writes': 50, 'stats_cache_ttl': 60.0,
        },
        'DIFFICULTY_ESTIMATOR': {
            'repeat_phrase_threshold': 15, 'max_difficulty_score': 1.0, 'history_warmup_threshold': 10, 'hard_floor': 0.8,
            'question_len_bins': [[200, 0.02], [500, 0.05], [800, 0.08], [1500, 0.12], [3000, 0.16], [6000, 0.22], [10000, 0.28], [20000, 0.32]],
            'feature_weights': {'entity': {'per_count': 0.04, 'cap': 0.12}, 'constraint': {'per_count': 0.05, 'cap': 0.12}, 'ambiguity': {'per_count': 0.03, 'cap': 0.08}, 'multi_tool': 0.08, 'cross_turn': 0.06, 'validation': 0.05},
            'difficulty_mapping': {'segments': [0.3, 0.6], 'power_exponent': 0.7},
        },
        'NAIE_KEY_NAMES': ['tool', 'faultNumber', 'faultIMSI', 'faultPort', 'faultType', 'fault', 'Number', 'IMSI', 'Url', 'Ip', 'APN', 'Time', 'Range', 'Caller', 'result', 'answer', 'name', 'type', 'value', 'faultUrl', 'faultIp', 'faultAPN', 'faultTimeRange', 'isCaller', 'status', 'code', 'message', 'id', 'data', 'error', 'success', 'query', 'response'],
        'SCENE_INDICATORS': {
            'tool_call': {'strong': ['调用', 'call function', 'API调用', '参数填充', '参数值', 'SELECT ', 'INSERT ', 'UPDATE ', 'query', 'tool_call', 'function call', 'invoke', 'execute query'], 'weak': ['参数', 'parameter', 'argument', '接口', 'interface', '请求', 'request', '返回', 'return']},
            'fault_diagnosis': {'strong': ['告警', '故障', '诊断', '根因', 'alarm', 'fault', '运维', '自愈', '闭环', '排障', 'troubleshoot', 'ALM', '网元', 'KPI', '指标异常'], 'weak': ['网络', '设备', '基站', '核心网', '传输', '性能', '监测', '监控', '巡检']},
            'math_reasoning': {'strong': ['证明', '计算', '方程', 'integral', 'prove', 'equation', '$$', '\\frac', '\\sum', '\\int', '\\sqrt', '\\prod', 'polynomial', 'derivative', 'AIME', 'AMC', 'mathematical'], 'weak': ['求解', '化简', 'evaluate', 'determine the value', 'find the value', 'calculate', 'compute', '等于多少', '结果是多少']},
            'code_generation': {'strong': ['```', 'def ', 'class ', 'implement a function', 'write a program', 'program', 'python', 'java', 'javascript', 'cpp', 'algorithm', 'output', 'complete the code', 'import ', 'LiveCodeBench', 'LeetCode', 'competitive programming', '编写程序', '编写代码', '实现函数', '代码补全'], 'weak': ['编写', '编程', '实现', '代码', '输入格式', '输出格式', 'input format', 'output format', 'test case', '示例输入', '示例输出', 'return ']},
            'knowledge_qa': {'strong': ['which of the following', 'true or false', 'correct answer', '判断', '选择', '以下哪项', '下列说法', 'multiple choice', 'single choice'], 'weak': ['解释', '说明', '描述', 'explain', 'describe', 'define', 'what is', '是什么', '什么是']},
            'retrieval': {'strong': ['查询', '检索', '查找', '获取', '搜索', 'query', 'retrieve', 'search', 'find', 'lookup', 'fetch', '查出', '列出', '列出所有', '帮我查', '查一下'], 'weak': ['什么', '哪个', '哪些', '有没有', '是否存在', 'where', 'which', 'what', 'is there']},
            'classification': {'strong': ['分类', '归类', '属于哪类', '属于哪种', 'classify', 'categorize', '判断对错', '正误判断', 'true or false', '选项', '以下哪项正确', 'which of the following'], 'weak': ['判断', '选择', '区分', '辨别', 'identify', 'A.', 'B.', 'C.', 'D.']},
            'planning': {'strong': ['规划', '计划', '步骤', 'plan', 'workflow', '编排', 'orchestrate', '依次', '先后', '第一步', '第二步', 'step 1', 'step 2'], 'weak': ['流程', '方案', '策略', '方案设计', '执行顺序', '操作流程']},
            'general': {'strong': [], 'weak': []},
        },
        'TASK_TYPE_INDICATORS': {
            'retrieval': {'strong': ['查询', '检索', '查找', '获取', '搜索', 'query', 'retrieve', 'search', 'find', 'lookup', 'fetch', 'get info', '查找出', '查出', '查出所有', '列出', 'list all', '列出所有', '显示', 'show me', '帮我查', '查一下'], 'weak': ['什么', '哪个', '哪些', 'where', 'which', 'what', '有没有', '是否存在', 'is there']},
            'classification': {'strong': ['分类', '归类', '属于哪类', '属于哪种', 'classify', 'categorize', '哪个类别', '哪种类型', 'which category', 'which type', '判断对错', '正误判断', 'true or false', '对还是错', '选择题', '单选题', '多选题', 'A.', 'B.', 'C.', 'D.', 'which of the following', '选项', '以下哪项'], 'weak': ['判断', '选择', '区分', '辨别', 'identify', '是A', '是B', '是C', '是D']},
            'reasoning': {'strong': ['推理', '推导', '如果...那么', '前提', '结论', '推断', 'infer', 'deduce', 'therefore', 'hence', 'conclude', '必然', '一定', '可以推出', '根据...可以', '逻辑判断', '逻辑推理', 'logically'], 'weak': ['分析', '判断', '因为', '所以', '由于', 'because', 'since', 'so', 'thus', '首先', '然后', '最后', '因果关系']},
        },
    }
