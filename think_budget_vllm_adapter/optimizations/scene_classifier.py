"""商用场景分类器 — 从问题内容自动推断商用场景类型。

从 think_budget/optimizations/scene_classifier.py 迁移，无外部依赖。
"""
import re
from typing import Dict, Optional, Tuple, List


SCENE_TYPES: Dict[str, Dict] = {
    'tool_call': {
        'description': '工具调用/函数调用场景',
        'indicators_strong': [
            '调用', 'call function', 'API调用', '参数填充', '参数值',
            'SELECT ', 'INSERT ', 'UPDATE ', 'query', 'tool_call',
            'function call', 'invoke', 'execute query',
        ],
        'indicators_weak': [
            '参数', 'parameter', 'argument', '接口', 'interface',
            '请求', 'request', '返回', 'return',
        ],
        'typical_tokens': 1000,
        'token_range': (500, 2000),
    },
    'fault_diagnosis': {
        'description': '故障诊断场景',
        'indicators_strong': [
            '告警', '故障', '诊断', '根因', 'alarm', 'fault',
            '运维', '自愈', '闭环', '排障', 'troubleshoot',
            'ALM', '网元', 'KPI', '指标异常',
        ],
        'indicators_weak': [
            '网络', '设备', '基站', '核心网', '传输',
            '性能', '监测', '监控', '巡检',
        ],
        'typical_tokens': 1500,
        'token_range': (800, 3000),
    },
    'math_reasoning': {
        'description': '数学推理场景',
        'indicators_strong': [
            '证明', '计算', '方程', 'integral', 'prove',
            'equation', '$$', '\\frac', '\\sum', '\\int',
            '\\sqrt', '\\prod', 'polynomial', 'derivative',
            'AIME', 'AMC', 'mathematical',
        ],
        'indicators_weak': [
            '求解', '化简', 'evaluate', 'determine the value',
            'find the value', 'calculate', 'compute',
            '等于多少', '结果是多少',
        ],
        'typical_tokens': 8000,
        'token_range': (3000, 22000),
    },
    'code_generation': {
        'description': '代码生成场景',
        'indicators_strong': [
            '```', 'def ', 'class ', 'implement a function', 'write a program',
            'program', 'python', 'java', 'javascript', 'cpp',
            'algorithm', 'output', 'complete the code', 'import ',
            'LiveCodeBench', 'LeetCode', 'competitive programming',
            '编写程序', '编写代码', '实现函数', '代码补全',
        ],
        'indicators_weak': [
            '编写', '编程', '实现', '代码',
            '输入格式', '输出格式', 'input format', 'output format',
            'test case', '示例输入', '示例输出', 'return ',
        ],
        'typical_tokens': 12000,
        'token_range': (5000, 20000),
    },
    'knowledge_qa': {
        'description': '知识问答场景',
        'indicators_strong': [
            'which of the following', 'true or false', 'correct answer',
            '判断', '选择', '以下哪项', '下列说法',
            'multiple choice', 'single choice',
        ],
        'indicators_weak': [
            '解释', '说明', '描述', 'explain', 'describe',
            'define', 'what is', '是什么', '什么是',
        ],
        'typical_tokens': 3000,
        'token_range': (1000, 7000),
    },
    'retrieval': {
        'description': '检索查询场景',
        'indicators_strong': [
            '查询', '检索', '查找', '获取', '搜索', 'query',
            'retrieve', 'search', 'find', 'lookup', 'fetch',
            '查出', '列出', '列出所有', '帮我查', '查一下',
        ],
        'indicators_weak': [
            '什么', '哪个', '哪些', '有没有', '是否存在',
            'where', 'which', 'what', 'is there',
        ],
        'typical_tokens': 500,
        'token_range': (200, 2000),
    },
    'classification': {
        'description': '分类判断场景',
        'indicators_strong': [
            '分类', '归类', '属于哪类', '属于哪种', 'classify',
            'categorize', '判断对错', '正误判断', 'true or false',
            '选项', '以下哪项正确', 'which of the following',
        ],
        'indicators_weak': [
            '判断', '选择', '区分', '辨别', 'identify',
            'A.', 'B.', 'C.', 'D.',
        ],
        'typical_tokens': 800,
        'token_range': (300, 3000),
    },
    'planning': {
        'description': '任务规划场景',
        'indicators_strong': [
            '规划', '计划', '步骤', 'plan', 'workflow',
            '编排', 'orchestrate', '依次', '先后',
            '第一步', '第二步', 'step 1', 'step 2',
        ],
        'indicators_weak': [
            '流程', '方案', '策略', '方案设计',
            '执行顺序', '操作流程',
        ],
        'typical_tokens': 2500,
        'token_range': (1000, 5000),
    },
    'general': {
        'description': '兜底通用场景',
        'indicators_strong': [],
        'indicators_weak': [],
        'typical_tokens': 3000,
        'token_range': (500, 15000),
    },
}

_PRIORITY = ['code_generation', 'math_reasoning', 'planning', 'fault_diagnosis',
             'tool_call', 'classification', 'retrieval', 'knowledge_qa', 'general']

_CODE_BLOCK_RE = re.compile(r'```[\s\S]*?```', re.MULTILINE)
_LATEX_BLOCK_RE = re.compile(r'\$\$[\s\S]*?\$\$|\$[^$]+\$')
_MULTI_STEP_RE = re.compile(r'(第[一二三四五六七八九十\d]+步|step\s*\d+|phase\s*\d+)', re.IGNORECASE)


def classify_scene(question: str) -> Dict[str, any]:
    """根据问题内容推断商用场景类型。"""
    q_lower = question.lower()
    scores: Dict[str, float] = {}

    for scene_name, scene_def in SCENE_TYPES.items():
        score = 0.0

        for kw in scene_def['indicators_strong']:
            if kw.lower() in q_lower:
                score += 3.0
            if scene_name == 'code_generation' and kw == '```':
                code_blocks = _CODE_BLOCK_RE.findall(question)
                score += min(len(code_blocks) * 2.0, 6.0)

        for kw in scene_def['indicators_weak']:
            if kw.lower() in q_lower:
                score += 1.0

        if scene_name == 'code_generation':
            code_blocks = _CODE_BLOCK_RE.findall(question)
            if code_blocks:
                total_code_len = sum(len(b) for b in code_blocks)
                score += min(total_code_len / 200, 5.0)

        if scene_name == 'math_reasoning':
            latex_matches = _LATEX_BLOCK_RE.findall(question)
            score += min(len(latex_matches) * 1.5, 6.0)

        if scene_name == 'planning':
            steps = _MULTI_STEP_RE.findall(question)
            score += min(len(steps) * 2.0, 6.0)

        scores[scene_name] = score

    best_scene = 'general'
    best_score = 0.0
    for scene_name in _PRIORITY:
        s = scores.get(scene_name, 0.0)
        if s > best_score:
            best_score = s
            best_scene = scene_name

    total_score = sum(scores.values())
    if total_score > 0:
        confidence = best_score / total_score
    else:
        confidence = 0.0
        best_scene = 'general'

    scene_def = SCENE_TYPES[best_scene]

    return {
        'scene_type': best_scene,
        'scene_confidence': round(confidence, 3),
        'typical_tokens': scene_def['typical_tokens'],
        'token_range': scene_def['token_range'],
        'scores': {k: round(v, 2) for k, v in scores.items()},
    }


def get_scene_typical_tokens(scene_type: str) -> int:
    """快捷获取场景的典型 token 消耗。"""
    return SCENE_TYPES.get(scene_type, SCENE_TYPES['general'])['typical_tokens']
