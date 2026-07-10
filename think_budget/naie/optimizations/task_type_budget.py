"""任务类型判断与 token 预算映射 — 根据问题内容判断任务类型并设置相应 token 预算。

任务类型分类 (由 budget_config.yaml TASK_TYPES 统一管理):
  - retrieval:    检索任务 — 简单查找/查询, 少推理
  - classification: 分类任务 — 选择题/判断题/归类
  - coding:       编码任务 — 编程题/代码生成/算法实现
  - math:         数学推理 — 证明/计算/方程求解
  - reasoning:    逻辑推理 — 多步推导/条件推理
  - tool_call:    工具调用 — API调用/参数填充
  - fault_diagnosis: 故障诊断 — 告警分析/根因定位
  - knowledge_qa: 知识问答 — 事实问答/解释说明
  - planning:     任务规划 — 多步骤规划/工作流编排
  - general:      兜底通用 — 无法判断时使用

核心原则:
  - 复用 scene_classifier 的场景分类, 增加 retrieval/classification/reasoning 三类
  - 每种任务类型有独立的 token 预算参数(统一由 budget_config.yaml 控制)
  - 计算耗时<1ms(纯规则无模型调用)
  - 新增任务类型: 只需在 budget_config.yaml 的 TASK_TYPES 下添加一项即可
"""
import re
import os
import json
from typing import Dict, Optional, Any, List

# ─── 统一配置加载 ───
from naie.config.unified_config import (
    TaskType, get_task_type_budgets, get_task_type_budget as _get_unified_budget,
    get_task_type_priority,
)

# ─── 兼容旧代码: 保留 TASK_TYPE_BUDGET_DEFAULTS (从统一配置加载) ───
def _load_defaults_from_unified_config():
    """从 budget_config.yaml 加载默认值，回退到内置硬编码。"""
    try:
        budgets = get_task_type_budgets()
        if budgets:
            return budgets
    except Exception:
        pass
    # 内置回退(与 budget_config.yaml 一致)
    return {
        'retrieval': {
            'description': '检索任务 — 简单查找/查询, 少推理',
            'typical_tokens': 500, 'check_length': 200, 'think_stride': 100,
            'num_thinking': 3, 'min_think_tokens': 50, 'check_answer_len': 2000, 'prob_th': 0.90,
        },
        'classification': {
            'description': '分类任务 — 选择题/判断题/归类',
            'typical_tokens': 800, 'check_length': 300, 'think_stride': 100,
            'num_thinking': 4, 'min_think_tokens': 100, 'check_answer_len': 2000, 'prob_th': 0.90,
        },
        'coding': {
            'description': '编码任务 — 编程题/代码生成/算法实现',
            'typical_tokens': 12000, 'check_length': 2000, 'think_stride': 500,
            'num_thinking': 15, 'min_think_tokens': 2000, 'check_answer_len': 4000, 'prob_th': 0.95,
        },
        'math': {
            'description': '数学推理 — 证明/计算/方程求解',
            'typical_tokens': 8000, 'check_length': 1500, 'think_stride': 400,
            'num_thinking': 12, 'min_think_tokens': 1500, 'check_answer_len': 4000, 'prob_th': 0.95,
        },
        'reasoning': {
            'description': '逻辑推理 — 多步推导/条件推理',
            'typical_tokens': 5000, 'check_length': 800, 'think_stride': 300,
            'num_thinking': 8, 'min_think_tokens': 500, 'check_answer_len': 3000, 'prob_th': 0.93,
        },
        'tool_call': {
            'description': '工具调用 — API调用/参数填充',
            'typical_tokens': 1000, 'check_length': 400, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 100, 'check_answer_len': 4000, 'prob_th': 0.95,
        },
        'fault_diagnosis': {
            'description': '故障诊断 — 告警分析/根因定位',
            'typical_tokens': 1500, 'check_length': 500, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 200, 'check_answer_len': 4000, 'prob_th': 0.95,
        },
        'knowledge_qa': {
            'description': '知识问答 — 事实问答/解释说明',
            'typical_tokens': 3000, 'check_length': 600, 'think_stride': 200,
            'num_thinking': 6, 'min_think_tokens': 200, 'check_answer_len': 3000, 'prob_th': 0.92,
        },
        'planning': {
            'description': '任务规划 — 多步骤规划/工作流编排',
            'typical_tokens': 2500, 'check_length': 600, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 300, 'check_answer_len': 3000, 'prob_th': 0.93,
        },
        'general': {
            'description': '兜底通用 — 无法判断时使用',
            'typical_tokens': 3000, 'check_length': 500, 'think_stride': 200,
            'num_thinking': 8, 'min_think_tokens': 200, 'check_answer_len': 3000, 'prob_th': 0.93,
        },
    }

TASK_TYPE_BUDGET_DEFAULTS = _load_defaults_from_unified_config()

# ─── 所有合法任务类型(从枚举获取) ───
VALID_TASK_TYPES: List[str] = TaskType.values()

# ─── 检索任务关键词 ───
_RETRIEVAL_STRONG = {
    '查询', '检索', '查找', '获取', '搜索', 'query', 'retrieve', 'search',
    'find', 'lookup', 'fetch', 'get info', '查找出', '查出', '查出所有',
    '列出', 'list all', '列出所有', '显示', 'show me', '帮我查', '查一下',
}
_RETRIEVAL_WEAK = {
    '什么', '哪个', '哪些', 'where', 'which', 'what',
    '有没有', '是否存在', 'is there',
}

# ─── 分类任务关键词 ───
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

# ─── 推理任务关键词(非数学/非代码的逻辑推理) ───
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
# 检索 > 分类 > 推理 的优先级: 如果同时匹配, 按优先级选
# coding/math 优先级高于 retrieval/classification(代码题也含查找)
_TASK_TYPE_PRIORITY = get_task_type_priority()

# 预编译正则
_IF_THEN_RE = re.compile(r'如果[^。？]{3,30}(?:那么|则|就)', re.IGNORECASE)
_CHOICE_RE = re.compile(r'[A-D][\.、\)）]', re.IGNORECASE)


def classify_task_type(question: str, scene_info: Optional[Dict] = None) -> Dict[str, Any]:
    """根据问题内容判断任务类型。

    分类策略:
      1. 先用 scene_classifier 推断场景(如果已提供)
      2. 新增检索/分类/推理三类判断(在 scene 分类基础上)
      3. 优先级: coding > math > reasoning > planning > fault_diagnosis > tool_call > classification > retrieval > knowledge_qa > general
      4. 特殊规则: 纯检索/分类题即使 scene 判断为 tool_call/knowledge_qa 也不应给予高预算

    Args:
        question: 问题文本
        scene_info: scene_classifier 的输出(可选, 如未提供则内部调用)

    Returns:
        {
            'task_type': str,          # 任务类型
            'task_confidence': float,  # 置信度 [0, 1]
            'scene_type': str,         # 原始场景类型
            'scores': dict,            # 各任务类型得分
            'budget': dict,            # 该类型的 token 预算参数
        }
    """
    q_lower = question.lower()

    # Step 1: 获取 scene 分类
    if scene_info is None:
        from naie.optimizations.scene_classifier import classify_scene
        scene_info = classify_scene(question)
    scene_type = scene_info.get('scene_type', 'general')

    # Step 2: 计算各任务类型得分
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
    # 检索任务特征: 短问题 + 查询类动词, 无推理要求
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
    # 选择题模式: A. B. C. D.
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
    # 条件推理模式: 如果...那么
    if_then_matches = _IF_THEN_RE.findall(question)
    if len(if_then_matches) >= 2:
        reasoning_score += 4.0
    elif len(if_then_matches) >= 1:
        reasoning_score += 2.0
    # 推理任务排除: 如果已明确是 coding 或 math, 不额外加推理分
    coding_score = scores.get('coding', 0.0)
    math_score = scores.get('math', 0.0)
    if coding_score > 3.0 or math_score > 3.0:
        reasoning_score *= 0.3  # 抑制: 代码/数学题的推理分量不应覆盖主分类
    scores['reasoning'] = scores.get('reasoning', 0.0) + reasoning_score

    # Step 3: 按优先级选最高分
    best_type = 'general'
    best_score = 0.0
    for task_type in _TASK_TYPE_PRIORITY:
        s = scores.get(task_type, 0.0)
        if s > best_score:
            best_score = s
            best_type = task_type

    # 验证类型合法性(使用枚举)
    if not TaskType.has_value(best_type):
        best_type = TaskType.GENERAL.value

    # 置信度: 归一化
    total_score = sum(scores.values())
    if total_score > 0:
        confidence = best_score / total_score
    else:
        confidence = 0.0
        best_type = 'general'

    # Step 4: 获取该类型的 token 预算
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

    Args:
        task_type: 任务类型名称
        json_path: 外部 JSON 配置路径(可选, 覆盖内置默认)

    Returns:
        dict: 该任务类型的 token 预算参数
    """
    # 尝试从外部 JSON 加载
    custom_config = _load_custom_config(json_path)
    if custom_config and task_type in custom_config:
        return custom_config[task_type]

    # 尝试从统一配置加载
    try:
        unified = _get_unified_budget(task_type)
        if unified:
            return unified
    except Exception:
        pass

    # 内置默认
    return dict(TASK_TYPE_BUDGET_DEFAULTS.get(task_type, TASK_TYPE_BUDGET_DEFAULTS.get('general', {})))


def get_all_task_type_budgets(json_path: Optional[str] = None) -> Dict[str, Dict]:
    """获取所有任务类型的 token 预算参数(含自定义覆盖)。

    Args:
        json_path: 外部 JSON 配置路径(可选)

    Returns:
        dict: {task_type: {budget_params}}
    """
    result = {}
    for task_type, budget in TASK_TYPE_BUDGET_DEFAULTS.items():
        result[task_type] = dict(budget)

    # 外部覆盖
    custom_config = _load_custom_config(json_path)
    if custom_config:
        for task_type, budget in custom_config.items():
            # Skip metadata keys (starting with _)
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

    # 检查文件修改时间
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
    """注册自定义任务类型到 JSON 文件。

    Args:
        task_type: 新任务类型名称
        budget: token 预算参数
        json_path: JSON 文件路径(默认 data/naie/custom_task_types.json)

    Returns:
        bool: 是否注册成功
    """
    if json_path is None:
        data_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'data', 'naie')
        json_path = os.path.join(data_dir, 'custom_task_types.json')

    # 读取已有配置
    existing = {}
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing = {}

    # 添加/更新
    existing[task_type] = budget

    # 写回
    try:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        # 清缓存
        _custom_config_cache.pop(json_path, None)
        _custom_config_mtime.pop(json_path, None)
        # 同步更新内存中的 TASK_TYPE_BUDGET_DEFAULTS
        TASK_TYPE_BUDGET_DEFAULTS[task_type] = budget
        return True
    except IOError:
        return False


def register_new_task_type(task_type: str, typical_tokens: int = 3000,
                            description: str = '', json_path: Optional[str] = None,
                            **kwargs) -> bool:
    """动态注册新任务类型 — 当历史轨迹中出现未知类型时自动调用。

    与 register_custom_task_type 不同，此方法接受更直观的参数，
    自动推算完整的预算参数集，确保新类型立即可用而不会 KeyError。

    Args:
        task_type: 新任务类型名称
        typical_tokens: 基础Token基数(默认3000)
        description: 类型描述
        json_path: JSON 持久化路径(可选)
        **kwargs: 覆盖特定预算参数(check_length, think_stride 等)

    Returns:
        bool: 是否注册成功
    """
    # 推算完整预算参数
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
    """检查任务类型是否已在系统中注册(内置或动态注册)。

    Args:
        task_type: 任务类型名称

    Returns:
        bool: 是否已知类型(不会触发 KeyError)
    """
    return task_type in TASK_TYPE_BUDGET_DEFAULTS or TaskType.has_value(task_type)


def compute_num_thinking_from_budget(think_budget: int, check_length: int = 500,
                                      min_rounds: int = 3, max_rounds: int = 15) -> int:
    """从总预算和初始思考长度计算最大轮次 — 统一计算公式，消除重复。

    替代散落在 history_budget.py 等处的 max(3, min(ceil(...), 15)) 重复逻辑。

    Args:
        think_budget: 总thinking token预算
        check_length: 初始思考长度
        min_rounds: 最小轮次(默认3)
        max_rounds: 最大轮次(默认15)

    Returns:
        int: 计算得出的最大轮次
    """
    import math
    effective_cl = max(check_length, 100)
    return max(min_rounds, min(math.ceil(think_budget / effective_cl), max_rounds))


def compute_stride_from_budget(think_budget: int, num_thinking: int,
                                min_stride: int = 50) -> int:
    """从总预算和轮次计算步长 — 统一计算公式，消除重复。

    Args:
        think_budget: 总thinking token预算
        num_thinking: 最大轮次
        min_stride: 最小步长(默认50)

    Returns:
        int: 计算得出的步长
    """
    return max(min_stride, think_budget // num_thinking)
