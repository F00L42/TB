"""任务类型判断与 token 预算映射。

从 think_budget/optimizations/task_type_budget.py 迁移，
import 路径调整为 think_budget_vllm_adapter 内部模块。
"""
import re
import os
import json
from typing import Dict, Optional, Any, List

from think_budget_vllm_adapter.config.unified_config import (
    TaskType, get_task_type_budgets, get_task_type_budget as _get_unified_budget,
    get_task_type_priority,
)


def _load_defaults_from_unified_config():
    try:
        budgets = get_task_type_budgets()
        if budgets:
            return budgets
    except Exception:
        pass
    return {
        'retrieval': {
            'description': '检索任务', 'typical_tokens': 500, 'check_length': 200, 'think_stride': 100,
            'num_thinking': 3, 'min_think_tokens': 50, 'check_answer_len': 2000, 'prob_th': 0.90,
        },
        'classification': {
            'description': '分类任务', 'typical_tokens': 800, 'check_length': 300, 'think_stride': 100,
            'num_thinking': 4, 'min_think_tokens': 100, 'check_answer_len': 2000, 'prob_th': 0.90,
        },
        'coding': {
            'description': '编码任务', 'typical_tokens': 12000, 'check_length': 2000, 'think_stride': 500,
            'num_thinking': 15, 'min_think_tokens': 2000, 'check_answer_len': 4000, 'prob_th': 0.95,
        },
        'math': {
            'description': '数学推理', 'typical_tokens': 8000, 'check_length': 1500, 'think_stride': 400,
            'num_thinking': 12, 'min_think_tokens': 1500, 'check_answer_len': 4000, 'prob_th': 0.95,
        },
        'reasoning': {
            'description': '逻辑推理', 'typical_tokens': 5000, 'check_length': 800, 'think_stride': 300,
            'num_thinking': 8, 'min_think_tokens': 500, 'check_answer_len': 3000, 'prob_th': 0.93,
        },
        'tool_call': {
            'description': '工具调用', 'typical_tokens': 1000, 'check_length': 400, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 100, 'check_answer_len': 4000, 'prob_th': 0.95,
        },
        'fault_diagnosis': {
            'description': '故障诊断', 'typical_tokens': 1500, 'check_length': 500, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 200, 'check_answer_len': 4000, 'prob_th': 0.95,
        },
        'knowledge_qa': {
            'description': '知识问答', 'typical_tokens': 3000, 'check_length': 600, 'think_stride': 200,
            'num_thinking': 6, 'min_think_tokens': 200, 'check_answer_len': 3000, 'prob_th': 0.92,
        },
        'planning': {
            'description': '任务规划', 'typical_tokens': 2500, 'check_length': 600, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 300, 'check_answer_len': 3000, 'prob_th': 0.93,
        },
        'general': {
            'description': '兜底通用', 'typical_tokens': 3000, 'check_length': 500, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 200, 'check_answer_len': 3000, 'prob_th': 0.93,
        },
    }


TASK_TYPE_BUDGET_DEFAULTS = _load_defaults_from_unified_config()

VALID_TASK_TYPES: List[str] = TaskType.values()

# ─── 关键词集合 ───
_RETRIEVAL_STRONG = {
    '查询', '检索', '查找', '获取', '搜索', 'query', 'retrieve', 'search',
    'find', 'lookup', 'fetch', 'get info', '查找出', '查出', '查出所有',
    '列出', 'list all', '列出所有', '显示', 'show me', '帮我查', '查一下',
}
_RETRIEVAL_WEAK = {
    '什么', '哪个', '哪些', 'where', 'which', 'what',
    '有没有', '是否存在', 'is there',
}

_CLASSIFICATION_STRONG = {
    '分类', '归类', '属于哪类', '属于哪种', 'classify', 'categorize',
    '哪个类别', '哪种类型', 'which category', 'which type',
    '判断对错', '正误判断', 'true or false', '对还是错',
    '选择题', '单选题', '多选题', 'A.', 'B.', 'C.', 'D.',
    'which of the following', '选项', '以下哪项',
}
_CLASSIFICATION_WEAK = {
    '判断', '选择', '区分', '辨别', 'identify',
    '是A', '是B', '是C', '是D',
}

_REASONING_STRONG = {
    '推理', '推导', '如果...那么', '前提', '结论', '推断',
    'infer', 'deduce', 'therefore', 'hence', 'conclude',
    '必然', '一定', '可以推出', '根据...可以',
    '逻辑判断', '逻辑推理', 'logically',
}
_REASONING_WEAK = {
    '分析', '判断', '因为', '所以', '由于',
    'because', 'since', 'so', 'thus',
    '首先', '然后', '最后', '因果关系',
}

_SCENE_TO_TASK_TYPE = {
    'code_generation': 'coding',
    'math_reasoning': 'math',
    'fault_diagnosis': 'fault_diagnosis',
    'tool_call': 'tool_call',
    'knowledge_qa': 'knowledge_qa',
    'planning': 'planning',
    'general': 'general',
}

_TASK_TYPE_PRIORITY = get_task_type_priority()

_IF_THEN_RE = re.compile(r'如果[^。？]{3,30}(?:那么|则|就)', re.IGNORECASE)
_CHOICE_RE = re.compile(r'[A-D][\.、\)）]', re.IGNORECASE)


def classify_task_type(question: str, scene_info: Optional[Dict] = None) -> Dict[str, Any]:
    """根据问题内容判断任务类型。"""
    q_lower = question.lower()

    if scene_info is None:
        from think_budget_vllm_adapter.optimizations.scene_classifier import classify_scene
        scene_info = classify_scene(question)
    scene_type = scene_info.get('scene_type', 'general')

    scores = {}

    mapped_type = _SCENE_TO_TASK_TYPE.get(scene_type, 'general')
    scene_confidence = scene_info.get('scene_confidence', 0.0)
    if scene_confidence > 0:
        scores[mapped_type] = scores.get(mapped_type, 0.0) + scene_confidence * 3.0

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
    """获取指定任务类型的 token 预算参数。"""
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


_custom_config_cache = {}
_custom_config_mtime = {}


def _load_custom_config(json_path: Optional[str] = None) -> Optional[Dict]:
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
    if json_path is None:
        data_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data')
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
    return task_type in TASK_TYPE_BUDGET_DEFAULTS or TaskType.has_value(task_type)


def compute_num_thinking_from_budget(think_budget: int, check_length: int = 500,
                                      min_rounds: int = 3, max_rounds: int = 15) -> int:
    import math
    effective_cl = max(check_length, 100)
    return max(min_rounds, min(math.ceil(think_budget / effective_cl), max_rounds))


def compute_stride_from_budget(think_budget: int, num_thinking: int,
                                min_stride: int = 50) -> int:
    return max(min_stride, think_budget // num_thinking)
