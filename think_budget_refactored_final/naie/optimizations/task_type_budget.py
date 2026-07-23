"""任务类型判断与 token 预算映射 — 根据问题内容判断任务类型并设置相应 token 预算。

核心原则:
  - 复用 scene_classifier 的场景分类, 增加 retrieval/classification/reasoning 三类
  - 每种任务类型有独立的 token 预算参数(统一由 budget_config.yaml 控制)
  - 计算耗时<1ms(纯规则无模型调用)
  - 新增任务类型: 只需在 budget_config.yaml 的 TASK_TYPES 下添加一项即可
  - 关键词由 budget_config.yaml TASK_TYPE_INDICATORS 统一管理
"""
import re
import os
import json
from typing import Dict, Optional, Any, List

# ─── 统一配置加载 ───
from naie.config.unified_config import (
    TaskType, get_task_type_budgets, get_task_type_budget as _get_unified_budget,
    get_task_type_priority, get_task_type_indicators,
)

# ─── 任务类型预算默认值(从统一配置加载) ───
def _load_defaults_from_unified_config():
    """从 budget_config.yaml 加载默认值。统一配置是唯一回退源。"""
    try:
        budgets = get_task_type_budgets()
        if budgets:
            return budgets
    except Exception:
        pass
    # 最小回退(仅 general)
    return {
        'general': {
            'description': '兜底通用',
            'typical_tokens': 3000, 'check_length': 500, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 200, 'check_answer_len': 3000, 'prob_th': 0.93,
        },
    }

TASK_TYPE_BUDGET_DEFAULTS = _load_defaults_from_unified_config()

# ─── 所有合法任务类型(从枚举获取) ───
VALID_TASK_TYPES: List[str] = TaskType.values()

# ─── 任务类型关键词(从统一配置加载) ───
def _load_task_type_indicators():
    """从 budget_config.yaml 加载任务类型关键词。"""
    try:
        indicators = get_task_type_indicators()
        if indicators:
            return indicators
    except Exception:
        pass
    # 内置最小回退
    return {
        'retrieval': {
            'strong': {'查询', '检索', '查找', '获取', '搜索', 'query', 'retrieve', 'search', 'find', 'lookup', 'fetch', '查出', '列出', '帮我查', '查一下'},
            'weak': {'什么', '哪个', '哪些', 'where', 'which', 'what', '有没有', '是否存在', 'is there'},
        },
        'classification': {
            'strong': {'分类', '归类', '属于哪类', 'classify', 'categorize', '判断对错', 'true or false', '选择题', 'A.', 'B.', 'C.', 'D.', 'which of the following', '选项'},
            'weak': {'判断', '选择', '区分', '辨别', 'identify'},
        },
        'reasoning': {
            'strong': {'推理', '推导', '前提', '结论', '推断', 'infer', 'deduce', 'therefore', 'hence', 'conclude', '必然', '逻辑判断', '逻辑推理', 'logically'},
            'weak': {'分析', '因为', '所以', '由于', 'because', 'since', 'so', 'thus', '首先', '然后', '最后', '因果关系'},
        },
    }

_INDICATORS_CFG = _load_task_type_indicators()

# 将列表转为集合(性能优化: O(1) 查找)
_RETRIEVAL_STRONG = set(_INDICATORS_CFG.get('retrieval', {}).get('strong', []))
_RETRIEVAL_WEAK = set(_INDICATORS_CFG.get('retrieval', {}).get('weak', []))
_CLASSIFICATION_STRONG = set(_INDICATORS_CFG.get('classification', {}).get('strong', []))
_CLASSIFICATION_WEAK = set(_INDICATORS_CFG.get('classification', {}).get('weak', []))
_REASONING_STRONG = set(_INDICATORS_CFG.get('reasoning', {}).get('strong', []))
_REASONING_WEAK = set(_INDICATORS_CFG.get('reasoning', {}).get('weak', []))

# ─── scene_classifier 场景到任务类型的映射 ───
_SCENE_TO_TASK_TYPE = {
    'code_generation': 'coding',
    'math_reasoning': 'math',
    'fault_diagnosis': 'fault_diagnosis',
    'tool_call': 'tool_call',
    'knowledge_qa': 'knowledge_qa',
    'planning': 'planning',
    'general': 'general',
}

# ─── 任务类型优先级 (从统一配置加载) ───
_TASK_TYPE_PRIORITY = get_task_type_priority()

# 预编译正则
_IF_THEN_RE = re.compile(r'如果[^。？]{3,30}(?:那么|则|就)', re.IGNORECASE)
_CHOICE_RE = re.compile(r'[A-D][\.、\)）]', re.IGNORECASE)


_COUNT_FIELD_RE = re.compile(r'\b[a-zA-Z_]*count\s*:', re.IGNORECASE)


def _is_statistics_aggregation_task(question: str, q_lower: str) -> bool:
    count_fields = _COUNT_FIELD_RE.findall(question)
    has_many_statistics_rows = (
        q_lower.count('ne_count') >= 5
        or q_lower.count('_count:') >= 5
        or len(count_fields) >= 5
    )
    asks_aggregation = any(
        marker in q_lower
        for marker in (
            'how many', 'how much', 'count', 'total', 'sum', 'number of',
            '多少', '几个', '总数', '数量', '统计', '求和'
        )
    )
    has_statistics_context = (
        '### statistics' in q_lower
        or 'statistics' in q_lower
        or 'aggregated record' in q_lower
        or 'ne_count' in q_lower
    )
    return has_many_statistics_rows and asks_aggregation and has_statistics_context


def classify_task_type(question: str, scene_info: Optional[Dict] = None) -> Dict[str, Any]:
    """根据问题内容判断任务类型。

    分类策略:
      1. 先用 scene_classifier 推断场景(如果已提供)
      2. 新增检索/分类/推理三类判断(在 scene 分类基础上)
      3. 优先级由 budget_config.yaml TASK_TYPE_PRIORITY 控制
      4. 特殊规则: 纯检索/分类题即使 scene 判断为 tool_call/knowledge_qa 也不应给予高预算

    Args:
        question: 问题文本
        scene_info: scene_classifier 的输出(可选)

    Returns:
        {
            'task_type': str,
            'task_confidence': float,
            'scene_type': str,
            'scores': dict,
            'budget': dict,
        }
    """
    q_lower = question.lower()

    # Step 1: 获取 scene 分类
    if scene_info is None:
        from naie.optimizations.scene_classifier import classify_scene
        scene_info = classify_scene(question)
    scene_type = scene_info.get('scene_type', 'general')

    # Step 2: 计算各任务类型得分
    if _is_statistics_aggregation_task(question, q_lower):
        task_type = 'statistics_aggregation'
        budget = get_task_type_budget(task_type)
        return {
            'task_type': task_type,
            'task_confidence': 1.0,
            'scene_type': scene_type,
            'scores': {task_type: 1.0},
            'budget': budget,
        }

    scores = {}

    # 2a: 基于 scene 的映射得分
    mapped_type = _SCENE_TO_TASK_TYPE.get(scene_type, 'general')
    scene_confidence = scene_info.get('scene_confidence', 0.0)
    if scene_confidence > 0:
        scores[mapped_type] = scores.get(mapped_type, 0.0) + scene_confidence * 3.0

    # 2b: 检索任务得分
    retrieval_score = 0.0
    for kw in _RETRIEVAL_STRONG:
        if kw.lower() in q_lower:
            retrieval_score += 3.0
    for kw in _RETRIEVAL_WEAK:
        if kw.lower() in q_lower:
            retrieval_score += 1.0
    if retrieval_score > 0 and len(question) < 200:
        retrieval_score += 2.0
    scores['retrieval'] = scores.get('retrieval', 0.0) + retrieval_score

    # 2c: 分类任务得分
    classification_score = 0.0
    for kw in _CLASSIFICATION_STRONG:
        if kw.lower() in q_lower:
            classification_score += 3.0
    for kw in _CLASSIFICATION_WEAK:
        if kw.lower() in q_lower:
            classification_score += 1.0
    choice_matches = _CHOICE_RE.findall(question)
    if len(choice_matches) >= 3:
        classification_score += 5.0
    scores['classification'] = scores.get('classification', 0.0) + classification_score

    # 2d: 推理任务得分(排除 coding/math 已命中的)
    reasoning_score = 0.0
    for kw in _REASONING_STRONG:
        if kw.lower() in q_lower:
            reasoning_score += 3.0
    for kw in _REASONING_WEAK:
        if kw.lower() in q_lower:
            reasoning_score += 1.0
    if_then_matches = _IF_THEN_RE.findall(question)
    if len(if_then_matches) >= 2:
        reasoning_score += 4.0
    elif len(if_then_matches) >= 1:
        reasoning_score += 2.0
    coding_score = scores.get('coding', 0.0)
    math_score = scores.get('math', 0.0)
    if coding_score > 3.0 or math_score > 3.0:
        reasoning_score *= 0.3
    scores['reasoning'] = scores.get('reasoning', 0.0) + reasoning_score

    # Step 3: 按优先级选最高分
    best_type = 'general'
    best_score = 0.0
    for task_type in _TASK_TYPE_PRIORITY:
        s = scores.get(task_type, 0.0)
        if s > best_score:
            best_score = s
            best_type = task_type

    if not TaskType.has_value(best_type):
        best_type = TaskType.GENERAL.value

    total_score = sum(scores.values())
    if total_score > 0:
        confidence = best_score / total_score
    else:
        confidence = 0.0
        best_type = 'general'

    budget = get_task_type_budget(best_type)

    return {
        'task_type': best_type,
        'task_confidence': round(confidence, 3),
        'scene_type': scene_type,
        'scores': {k: round(v, 2) for k, v in scores.items()},
        'budget': budget,
    }


def get_task_type_budget(task_type: str, json_path: Optional[str] = None) -> Dict[str, Any]:
    """获取指定任务类型的 token 预算参数。

    优先级: 外部JSON > 统一配置YAML > 内置默认
    """
    custom_config = _load_custom_config(json_path)
    if custom_config and task_type in custom_config:
        return custom_config[task_type]

    try:
        unified = _get_unified_budget(task_type)
        if unified:
            return unified
    except Exception:
        pass

    return dict(TASK_TYPE_BUDGET_DEFAULTS.get(task_type, TASK_TYPE_BUDGET_DEFAULTS.get('general', {})))


def get_all_task_type_budgets(json_path: Optional[str] = None) -> Dict[str, Dict]:
    """获取所有任务类型的 token 预算参数(含自定义覆盖)。"""
    result = {}
    for task_type, budget in TASK_TYPE_BUDGET_DEFAULTS.items():
        result[task_type] = dict(budget)

    custom_config = _load_custom_config(json_path)
    if custom_config:
        for task_type, budget in custom_config.items():
            if task_type.startswith('_'):
                continue
            if not isinstance(budget, dict):
                continue
            if task_type in result:
                result[task_type].update(budget)
            else:
                result[task_type] = budget
    return result


# ─── 内部缓存 ───
_custom_config_cache = {}
_custom_config_mtime = {}


def _load_custom_config(json_path: Optional[str] = None) -> Optional[Dict]:
    """加载自定义任务类型配置(带缓存和文件修改检测)。"""
    if json_path is None:
        return None
    if not os.path.exists(json_path):
        return None
    mtime = os.path.getmtime(json_path)
    if json_path in _custom_config_cache and _custom_config_mtime.get(json_path) == mtime:
        return _custom_config_cache[json_path]
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        _custom_config_cache[json_path] = config
        _custom_config_mtime[json_path] = mtime
        return config
    except (json.JSONDecodeError, IOError):
        return None


def register_custom_task_type(task_type: str, budget: Dict[str, Any],
                               json_path: Optional[str] = None) -> bool:
    """注册自定义任务类型到 JSON 文件。"""
    if json_path is None:
        data_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'naie')
        json_path = os.path.join(data_dir, 'custom_task_types.json')

    existing = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing = {}

    existing[task_type] = budget

    try:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        _custom_config_cache.pop(json_path, None)
        _custom_config_mtime.pop(json_path, None)
        TASK_TYPE_BUDGET_DEFAULTS[task_type] = budget
        return True
    except IOError:
        return False


def register_new_task_type(task_type: str, typical_tokens: int = 3000,
                            description: str = '', json_path: Optional[str] = None,
                            **kwargs) -> bool:
    """动态注册新任务类型 — 当历史轨迹中出现未知类型时自动调用。"""
    check_length = kwargs.get('check_length', min(typical_tokens // 5, 2000))
    think_stride = kwargs.get('think_stride', max(50, typical_tokens // 15))
    num_thinking = kwargs.get('num_thinking', max(3, min(15, typical_tokens // max(check_length, 100))))
    min_think_tokens = kwargs.get('min_think_tokens', max(50, typical_tokens // 5))
    check_answer_len = kwargs.get('check_answer_len', 3000)
    prob_th = kwargs.get('prob_th', 0.93)

    budget = {
        'description': description or f'动态注册任务类型 — {task_type}',
        'typical_tokens': typical_tokens,
        'check_length': check_length,
        'think_stride': think_stride,
        'num_thinking': num_thinking,
        'min_think_tokens': min_think_tokens,
        'check_answer_len': check_answer_len,
        'prob_th': prob_th,
        'auto_registered': True,
    }

    return register_custom_task_type(task_type, budget, json_path)


def is_known_task_type(task_type: str) -> bool:
    """检查任务类型是否已在系统中注册。"""
    return task_type in TASK_TYPE_BUDGET_DEFAULTS or TaskType.has_value(task_type)


def compute_num_thinking_from_budget(think_budget: int, check_length: int = 500,
                                      min_rounds: int = 3, max_rounds: int = 15) -> int:
    """从总预算和初始思考长度计算最大轮次。"""
    import math
    effective_cl = max(check_length, 100)
    return max(min_rounds, min(math.ceil(think_budget / effective_cl), max_rounds))


def compute_stride_from_budget(think_budget: int, num_thinking: int,
                                min_stride: int = 50) -> int:
    """从总预算和轮次计算步长。"""
    return max(min_stride, think_budget // num_thinking)
