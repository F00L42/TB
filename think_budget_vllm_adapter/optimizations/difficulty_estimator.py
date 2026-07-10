"""难度估计器 — 14维连续特征融合输出difficulty∈[0,1], 分段映射动态budget。

从 think_budget/optimizations/difficulty_estimator.py 迁移，无外部依赖。
"""
import re
from typing import Dict, Optional, List, Any

# ─── 题目长度 bins (扩展到覆盖超长代码题) ───
_QUESTION_LEN_BINS = [
    (200, 0.02), (500, 0.05), (800, 0.08), (1500, 0.12),
    (3000, 0.16), (6000, 0.22), (10000, 0.28), (20000, 0.32),
]

_ENTITY_PATTERNS = [
    re.compile(r'网元[：:]?\s*[A-Za-z0-9\-_]+'),
    re.compile(r'告警[IDid]*[：:]?\s*ALM[\-]?\d+'),
    re.compile(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}'),
    re.compile(r'指标[IDid]*[：:]?\s*\d+'),
    re.compile(r'APP[\-][A-Za-z0-9]+'),
    re.compile(r'IMSI[：:]?\s*\d+'),
]
_CONSTRAINT_KEYWORDS = {
    '不包含', '排除', '必须', '不能', '仅限', '不低于', '不高于',
    '超过', '低于', '二级门限', '三级门限', '一级门限', '门限',
    '必须按照', '不允许', '严格', '且', '并', '同时满足',
}
_MULTI_TOOL_INDICATORS = {
    '依次', '然后', '之后', '先', '再', '第一步', '第二步',
    '以及', '和', '并', '同时', '还需要', '另外', '还有',
    '多个', '一系列', '按顺序',
}
_CROSS_TURN_INDICATORS = {
    '之前', '上次', '上次查询', '上一轮', '刚才', '之前的结果',
    '结合前面', '根据之前的', '在之前的', '历史',
}
_VALIDATION_INDICATORS = {
    '校验', '验证', '确认', '核实', '检查是否', '判断是否',
    '比对', '比较', '确认是否', '确保',
}
_AMBIGUITY_INDICATORS = {
    '可能', '大概', '好像', '不确定', '或者是', '还是',
    '之类的', '等', '等等', '什么的', '左右', '大约',
}
_SELF_NEGATION_WORDS = {
    '不对', '等等', '重新', '换个角度', '不，', '不对，',
    'Wait', 'wait', 'However', 'however', 'Alternatively',
    'alternatively', 'Actually', 'actually', 'No,', 'no,',
}

# ─── 通用能力检测 ───
_MATH_INDICATORS = {
    'prove', 'equation', 'integral', 'derivative', 'polynomial',
    'matrix', 'theorem', 'calculate', 'solve for', 'find the value',
    'simplify', 'evaluate', 'determine the', 'compute',
    '证明', '方程', '积分', '求导', '计算', '求解', '化简',
    'modulo', 'subsequence', 'permutation', 'bitwise', 'xor',
    'sum of', 'product of', 'maximum', 'minimum', 'optimize',
    '动态规划', '回溯', '排列', '组合', '异或','mathmatics',
}
_CODE_INDICATORS = {
    'function', 'implement', 'algorithm', 'def ', 'class ',
    'return ', 'import ', 'code', 'program', 'output',
    'python', 'java', 'javascript', 'cpp', 'sql',
    'implement a function', 'write a program', 'complete the code',
    '实现', '函数', '编写', '代码', '输出', '编程',
}
_LOGIC_INDICATORS = {
    'if and only if', 'therefore', 'implies', 'contradiction',
    'premise', 'conclusion', 'syllogism', 'valid argument',
    'must be true', 'can be inferred', 'which of the following',
    '推导', '前提', '结论', '矛盾', '逻辑', '推理',
}
_LATEX_MARKERS = {
    '$$', '\\frac', '\\sum', '\\int', '\\sqrt', '\\prod',
    '\\alpha', '\\beta', '\\gamma', '\\infty',
    '\\oplus', '\\ldots', '\\cdot', '\\leq', '\\geq',
    '\\rightarrow', '\\leftarrow', '\\Rightarrow',
}

# ─── 数学深度指标 ───
_MATH_DEPTH_VERBS = {
    'prove', 'derive', 'show that', 'demonstrate', 'establish',
    '证明', '推导', '证明存在', '归纳', '反证',
}
_MATH_DEPTH_COMPLEX = {
    '\\frac{', '\\sum_{', '\\int_{', '\\prod_{', '\\lim_',
    '\\forall', '\\exists', '\\iff', '\\implies',
    '\\oplus', '\\otimes', '\\mathbb', '\\mathcal',
    'induction', 'contradiction', 'bijection',
    'modulo 998244353', 'modulo 10^9+7',
    'combinatorial', 'subsequence', 'permutation',
}

# ─── 代码深度指标 ───
_CODE_DEPTH_ALGORITHMS = {
    'DP', 'dynamic programming', 'backtracking', 'recursion', 'recursive',
    'greedy', 'binary search', 'graph', 'BFS', 'DFS', 'topological',
    'dijkstra', 'bellman', 'floyd', 'minimum spanning tree',
    '动态规划', '回溯', '递归', '贪心', '二分', '图论', '拓扑排序',
}
_CODE_DEPTH_COMPLEXITY = {
    'O(n^2)', 'O(n log n)', 'O(2^n)', 'time complexity', 'space complexity',
    'optimize', 'efficient', 'TLE', 'MLE',
    '时间复杂度', '空间复杂度', '优化',
}

# ─── 推理链深度指标 ───
_REASONING_CHAIN_CONDITIONS = re.compile(
    r'(如果[^。？]{5,30}(?:那么|则|则需))', re.IGNORECASE
)
_REASONING_CHAIN_ENUMERATE = {
    '所有可能', '每种情况', 'for each', 'enumerate', 'every possible',
    '全部排列', 'all combinations', 'all permutations',
    '穷举', '遍历', 'brute force',
}
_REASONING_CHAIN_SUBPROBLEMS = re.compile(
    r'(step\s*\d+|phase\s*\d+|第[一二三四五六七八九十\d]+步|part\s*[A-D]|题\s*\d+)',
    re.IGNORECASE
)

_REPEAT_PHRASE_THRESHOLD = 15
_MAX_DIFFICULTY_SCORE = 1.0
_HISTORY_WARMUP_THRESHOLD = 10

# ─── 预编译正则 ───
_CODE_BLOCK_RE = re.compile(r'```[\s\S]*?```', re.MULTILINE)
_LATEX_INLINE_RE = re.compile(r'\$[^$]+\$|\$\$[\s\S]*?\$\$')


# ═══════════════════════════════════════
# 基础特征函数
# ═══════════════════════════════════════

def count_entities(question: str) -> int:
    total = 0
    for pat in _ENTITY_PATTERNS:
        total += len(pat.findall(question))
    return total


def count_constraints(question: str) -> int:
    q_lower = question.lower()
    return sum(1 for kw in _CONSTRAINT_KEYWORDS if kw in q_lower or kw in question)


def count_ambiguity(question: str) -> int:
    q_lower = question.lower()
    return sum(1 for kw in _AMBIGUITY_INDICATORS if kw in q_lower or kw in question)


def needs_multi_tool(question: str) -> bool:
    q_lower = question.lower()
    return any(kw in q_lower or kw in question for kw in _MULTI_TOOL_INDICATORS)


def needs_cross_turn(question: str) -> bool:
    q_lower = question.lower()
    return any(kw in q_lower or kw in question for kw in _CROSS_TURN_INDICATORS)


def needs_validation(question: str) -> bool:
    q_lower = question.lower()
    return any(kw in q_lower or kw in question for kw in _VALIDATION_INDICATORS)


def count_repeat_phrases(text: str, min_len: int = _REPEAT_PHRASE_THRESHOLD) -> int:
    if len(text) < min_len * 2:
        return 0
    seen = set()
    repeats = 0
    for i in range(0, len(text) - min_len, max(min_len // 3, 5)):
        chunk = text[i:i + min_len]
        if chunk in seen:
            repeats += 1
        else:
            seen.add(chunk)
    return repeats


def detect_self_negation(text: str) -> bool:
    return any(kw in text for kw in _SELF_NEGATION_WORDS)


# ═══════════════════════════════════════
# 连续深度特征函数
# ═══════════════════════════════════════

def math_depth_score(question: str) -> float:
    """数学深度分数 — 0~0.20 连续。"""
    score = 0.0
    q_lower = question.lower()

    has_basic_math = any(kw in q_lower for kw in _MATH_INDICATORS)
    has_latex = any(marker in question for marker in _LATEX_MARKERS)
    if not has_basic_math and not has_latex:
        return 0.0

    score += 0.05

    latex_matches = _LATEX_INLINE_RE.findall(question)
    latex_count = len(latex_matches)
    if latex_count >= 5:
        score += 0.06
    elif latex_count >= 2:
        score += 0.04
    elif latex_count >= 1:
        score += 0.02

    frac_depth = question.count('\\frac{')
    if frac_depth >= 4:
        score += 0.04
    elif frac_depth >= 2:
        score += 0.02

    depth_verb_count = sum(1 for kw in _MATH_DEPTH_VERBS if kw in q_lower)
    if depth_verb_count >= 2:
        score += 0.03
    elif depth_verb_count >= 1:
        score += 0.02

    complex_sym_count = sum(1 for kw in _MATH_DEPTH_COMPLEX if kw in question)
    if complex_sym_count >= 3:
        score += 0.03
    elif complex_sym_count >= 1:
        score += 0.01

    return min(score, 0.20)


def code_depth_score(question: str) -> float:
    """代码深度分数 — 0~0.18 连续。"""
    score = 0.0
    q_lower = question.lower()

    has_basic_code = any(kw in q_lower for kw in _CODE_INDICATORS)
    has_code_block = '```' in question
    if not has_basic_code and not has_code_block:
        return 0.0

    score += 0.04

    code_blocks = _CODE_BLOCK_RE.findall(question)
    if code_blocks:
        total_code_len = sum(len(b) for b in code_blocks)
        if total_code_len > 1000:
            score += 0.05
        elif total_code_len > 500:
            score += 0.03
        elif total_code_len > 100:
            score += 0.02

    algo_count = sum(1 for kw in _CODE_DEPTH_ALGORITHMS if kw.lower() in q_lower)
    if algo_count >= 3:
        score += 0.05
    elif algo_count >= 1:
        score += 0.03

    complexity_count = sum(1 for kw in _CODE_DEPTH_COMPLEXITY if kw.lower() in q_lower)
    if complexity_count >= 1:
        score += 0.02

    io_patterns = re.findall(r'(?:input|output|输入|输出)\s*(?:format|格式|specification)', q_lower)
    if len(io_patterns) >= 2:
        score += 0.02

    return min(score, 0.18)


def logic_depth_score(question: str) -> float:
    """逻辑推理深度分数 — 0~0.12 连续。"""
    score = 0.0
    q_lower = question.lower()

    has_basic_logic = any(kw in q_lower for kw in _LOGIC_INDICATORS)
    if not has_basic_logic:
        return 0.0

    score += 0.03

    logic_connectives = re.findall(
        r'(therefore|implies|iff|if and only if|contradiction|hence|thus|whence|since)',
        q_lower
    )
    if len(logic_connectives) >= 4:
        score += 0.05
    elif len(logic_connectives) >= 2:
        score += 0.03
    elif len(logic_connectives) >= 1:
        score += 0.02

    inference_patterns = re.findall(
        r'(must be true|can be inferred|logically follows|necessarily)',
        q_lower
    )
    if len(inference_patterns) >= 2:
        score += 0.04
    elif len(inference_patterns) >= 1:
        score += 0.02

    return min(score, 0.12)


def reasoning_chain_score(question: str) -> float:
    """推理链长度分数 — 0~0.15。"""
    score = 0.0
    q_lower = question.lower()

    condition_matches = _REASONING_CHAIN_CONDITIONS.findall(question)
    if len(condition_matches) >= 3:
        score += 0.05
    elif len(condition_matches) >= 2:
        score += 0.03
    elif len(condition_matches) >= 1:
        score += 0.02

    subproblems = _REASONING_CHAIN_SUBPROBLEMS.findall(question)
    if len(subproblems) >= 5:
        score += 0.05
    elif len(subproblems) >= 3:
        score += 0.03
    elif len(subproblems) >= 1:
        score += 0.02

    enumerate_count = sum(1 for kw in _REASONING_CHAIN_ENUMERATE if kw in q_lower)
    if enumerate_count >= 2:
        score += 0.05
    elif enumerate_count >= 1:
        score += 0.03

    return min(score, 0.15)


def _compute_history_weight(history_count: int) -> float:
    if history_count <= 0:
        return 0.0
    return min(history_count / _HISTORY_WARMUP_THRESHOLD, 1.0)


# ═══════════════════════════════════════
# 主函数: 难度估计
# ═══════════════════════════════════════

def estimate_difficulty(
    question: str,
    reference: str = '',
    task_type: str = 'naie',
    tool_count: int = 0,
    param_slots: int = 0,
    candidate_tool_list: Optional[List[str]] = None,
    history_avg_tokens: float = 0.0,
    history_failure_rate: float = 0.0,
    history_category_avg_tokens: float = 0.0,
    history_category_failure_rate: float = 0.0,
    history_count: int = 0,
    scene_type: str = '',
    scene_typical_tokens: int = 0,
    scene_confidence: float = 0.0,
) -> Dict[str, Any]:
    """估计题目难度, 输出 difficulty ∈ [0, 1] + 特征分解。"""
    q_len = len(question)

    text_len_score = 0.0
    for threshold, score in _QUESTION_LEN_BINS:
        if q_len > threshold:
            text_len_score = score

    entity_count = count_entities(question)
    entity_score = min(entity_count * 0.04, 0.12)

    constraint_count = count_constraints(question)
    constraint_score = min(constraint_count * 0.05, 0.12)

    ambiguity_count = count_ambiguity(question)
    ambiguity_score = min(ambiguity_count * 0.03, 0.08)

    effective_tool_count = tool_count
    if candidate_tool_list is not None:
        effective_tool_count = max(tool_count, len(candidate_tool_list))
    tool_score = 0.0
    if effective_tool_count > 0:
        if effective_tool_count > 10:
            tool_score = 0.10
        elif effective_tool_count > 5:
            tool_score = 0.07
        elif effective_tool_count > 2:
            tool_score = 0.04

    effective_param_slots = param_slots
    if candidate_tool_list is not None and param_slots == 0:
        for tool_name in candidate_tool_list:
            effective_param_slots += tool_name.count('_') + 1
        effective_param_slots = effective_param_slots // max(len(candidate_tool_list), 1)
    param_score = 0.0
    if effective_param_slots > 0:
        if effective_param_slots > 8:
            param_score = 0.08
        elif effective_param_slots > 4:
            param_score = 0.05
        elif effective_param_slots > 1:
            param_score = 0.02

    multi_tool_score = 0.08 if needs_multi_tool(question) else 0.0
    cross_turn_score = 0.06 if needs_cross_turn(question) else 0.0
    validation_score = 0.05 if needs_validation(question) else 0.0

    math_score = math_depth_score(question)
    code_score = code_depth_score(question)
    logic_score = logic_depth_score(question)
    chain_score = reasoning_chain_score(question)

    history_weight = _compute_history_weight(history_count)

    scene_prior_weight = 0.0
    if scene_typical_tokens > 0 and scene_confidence > 0:
        scene_prior_weight = (1.0 - history_weight) * scene_confidence

    history_token_score = 0.0
    effective_history_tokens = history_category_avg_tokens if history_category_avg_tokens > 0 else history_avg_tokens

    if effective_history_tokens > 0:
        if effective_history_tokens > 800:
            history_token_score = 0.08
        elif effective_history_tokens > 400:
            history_token_score = 0.05
        elif effective_history_tokens > 150:
            history_token_score = 0.02
        history_token_score *= history_weight
    elif scene_typical_tokens > 0 and scene_prior_weight > 0:
        if scene_typical_tokens > 8000:
            history_token_score = 0.08
        elif scene_typical_tokens > 4000:
            history_token_score = 0.06
        elif scene_typical_tokens > 1500:
            history_token_score = 0.04
        elif scene_typical_tokens > 500:
            history_token_score = 0.02
        history_token_score *= scene_prior_weight

    history_fail_score = 0.0
    effective_failure_rate = history_category_failure_rate if history_category_failure_rate > 0 else history_failure_rate
    if effective_failure_rate > 0:
        history_fail_score = min(effective_failure_rate * 0.12, 0.12) * history_weight
    elif scene_typical_tokens > 0 and scene_prior_weight > 0:
        scene_fail_prior = min(scene_typical_tokens / 20000, 0.5)
        history_fail_score = min(scene_fail_prior * 0.08, 0.08) * scene_prior_weight

    raw = (
        text_len_score + entity_score + constraint_score + ambiguity_score +
        tool_score + param_score + multi_tool_score + cross_turn_score +
        validation_score + math_score + code_score + logic_score +
        chain_score + history_token_score + history_fail_score
    )
    difficulty = min(raw, _MAX_DIFFICULTY_SCORE)

    _is_hard = math_score > 0 or code_score > 0 or logic_score > 0
    if _is_hard:
        difficulty = max(difficulty, 0.8)

    return {
        'difficulty': round(difficulty, 4),
        'scene_type': scene_type,
        'scene_confidence': scene_confidence,
        'text_len': q_len,
        'entity_count': entity_count,
        'constraint_count': constraint_count,
        'ambiguity_count': ambiguity_count,
        'tool_count': effective_tool_count,
        'param_slots': effective_param_slots,
        'needs_multi_tool': needs_multi_tool(question),
        'needs_cross_turn': needs_cross_turn(question),
        'needs_validation': needs_validation(question),
        'history_avg_tokens': effective_history_tokens,
        'history_failure_rate': effective_failure_rate,
        'history_weight': round(history_weight, 3),
        'history_count': history_count,
        'scene_prior_weight': round(scene_prior_weight, 3),
        'has_math': math_score > 0,
        'has_code': code_score > 0,
        'has_logic': logic_score > 0,
        'is_hard': _is_hard,
        'score_breakdown': {
            'text_len': round(text_len_score, 3),
            'entity': round(entity_score, 3),
            'constraint': round(constraint_score, 3),
            'ambiguity': round(ambiguity_score, 3),
            'tool': round(tool_score, 3),
            'param': round(param_score, 3),
            'multi_tool': round(multi_tool_score, 3),
            'cross_turn': round(cross_turn_score, 3),
            'validation': round(validation_score, 3),
            'math': round(math_score, 3),
            'code': round(code_score, 3),
            'logic': round(logic_score, 3),
            'chain': round(chain_score, 3),
            'history_token': round(history_token_score, 3),
            'history_fail': round(history_fail_score, 3),
        },
    }


# ═══════════════════════════════════════
# 分段映射函数
# ═══════════════════════════════════════

def _lerp(a, b, t):
    return a + t * (b - a)


def compute_budget_from_difficulty(
    difficulty: float,
    base_check_length: int = 150,
    max_check_length: int = 4000,
    base_min_think: int = 100,
    max_min_think: int = 4000,
    base_stride: int = 50,
    max_stride: int = 600,
    base_num_thinking: int = 3,
    max_num_thinking: int = 15,
    base_answer_len: int = 500,
    max_answer_len: int = 4000,
) -> Dict[str, int]:
    """难度→budget 分段映射。"""
    # check_length
    if difficulty < 0.3:
        mid_cl = int(_lerp(base_check_length, max_check_length, 0.3))
        cl = int(_lerp(base_check_length, mid_cl, difficulty / 0.3))
    elif difficulty < 0.6:
        t = (difficulty - 0.3) / 0.3
        cl_30 = int(_lerp(base_check_length, max_check_length, 0.3))
        cl_60 = int(_lerp(base_check_length, max_check_length, 0.6))
        cl = int(_lerp(cl_30, cl_60, t))
    else:
        t = (difficulty - 0.6) / 0.4
        cl_60 = int(_lerp(base_check_length, max_check_length, 0.6))
        cl = int(_lerp(cl_60, max_check_length, t ** 0.7))

    # min_think_tokens
    if difficulty < 0.3:
        mid_mt = int(_lerp(base_min_think, max_min_think, 0.3))
        mt = int(_lerp(base_min_think, mid_mt, difficulty / 0.3))
    elif difficulty < 0.6:
        t = (difficulty - 0.3) / 0.3
        mt_30 = int(_lerp(base_min_think, max_min_think, 0.3))
        mt_60 = int(_lerp(base_min_think, max_min_think, 0.6))
        mt = int(_lerp(mt_30, mt_60, t))
    else:
        t = (difficulty - 0.6) / 0.4
        mt_60 = int(_lerp(base_min_think, max_min_think, 0.6))
        mt = int(_lerp(mt_60, max_min_think, t ** 0.7))

    # stride
    if difficulty < 0.3:
        mid_st = int(_lerp(base_stride, max_stride, 0.3))
        st = int(_lerp(base_stride, mid_st, difficulty / 0.3))
    elif difficulty < 0.6:
        t = (difficulty - 0.3) / 0.3
        st_30 = int(_lerp(base_stride, max_stride, 0.3))
        st_60 = int(_lerp(base_stride, max_stride, 0.6))
        st = int(_lerp(st_30, st_60, t))
    else:
        t = (difficulty - 0.6) / 0.4
        st_60 = int(_lerp(base_stride, max_stride, 0.6))
        st = int(_lerp(st_60, max_stride, t ** 0.7))

    # num_thinking
    if difficulty < 0.3:
        mid_nt = int(_lerp(base_num_thinking, max_num_thinking, 0.3))
        nt = int(_lerp(base_num_thinking, mid_nt, difficulty / 0.3))
    elif difficulty < 0.6:
        t = (difficulty - 0.3) / 0.3
        nt_30 = int(_lerp(base_num_thinking, max_num_thinking, 0.3))
        nt_60 = int(_lerp(base_num_thinking, max_num_thinking, 0.6))
        nt = int(_lerp(nt_30, nt_60, t))
    else:
        t = (difficulty - 0.6) / 0.4
        nt_60 = int(_lerp(base_num_thinking, max_num_thinking, 0.6))
        nt = int(_lerp(nt_60, max_num_thinking, t ** 0.7))

    # answer_len
    if difficulty < 0.3:
        al = int(_lerp(base_answer_len, max_answer_len, difficulty / 0.3 * 0.2))
    elif difficulty < 0.6:
        t = (difficulty - 0.3) / 0.3
        al = int(_lerp(base_answer_len + 0.2 * (max_answer_len - base_answer_len),
                       base_answer_len + 0.5 * (max_answer_len - base_answer_len), t))
    else:
        t = (difficulty - 0.6) / 0.4
        al = int(_lerp(base_answer_len + 0.5 * (max_answer_len - base_answer_len),
                       max_answer_len, t ** 0.7))

    return {
        'check_length': max(base_check_length, min(cl, max_check_length)),
        'min_think_tokens': max(base_min_think, min(mt, max_min_think)),
        'think_stride': max(base_stride, min(st, max_stride)),
        'num_thinking': max(base_num_thinking, min(nt, max_num_thinking)),
        'check_answer_len': max(base_answer_len, min(al, max_answer_len)),
    }


# ═══════════════════════════════════════
# 兼容旧接口
# ═══════════════════════════════════════

def has_math_content(question: str) -> bool:
    return math_depth_score(question) > 0

def has_code_content(question: str) -> bool:
    return code_depth_score(question) > 0

def has_logic_content(question: str) -> bool:
    return logic_depth_score(question) > 0
