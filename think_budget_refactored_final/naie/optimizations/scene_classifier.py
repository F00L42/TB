"""商用场景分类器 — 从问题内容自动推断商用场景类型。

核心原则:
  - 不依赖 test_set 标签, 纯从问题文本推断场景
  - 同一商用场景的题目有相似的 token 消耗模式
  - 每个 scene_type 提供 typical_tokens 先验, 在 history 冷启动时作为 difficulty 的补充
  - 分类优先级由 budget_config.yaml TASK_TYPE_PRIORITY 控制
  - 场景定义和关键词由 budget_config.yaml SCENE_INDICATORS/SCENE_TYPES 统一管理
"""
import re
from typing import Dict, Optional, Tuple, List

from naie.config.unified_config import get_scene_full_config, get_task_type_priority


def _load_scene_types_from_config() -> Dict[str, Dict]:
    """从 budget_config.yaml 加载完整场景配置(合并 SCENE_TYPES + SCENE_INDICATORS)。"""
    try:
        full_config = get_scene_full_config()
        if full_config:
            return full_config
    except Exception:
        pass
    # 内置最小回退(仅 general)
    return {
        'general': {
            'typical_tokens': 3000,
            'token_range': (500, 15000),
            'indicators_strong': [],
            'indicators_weak': [],
        },
    }


# ─── 场景定义(从统一配置加载) ───
SCENE_TYPES: Dict[str, Dict] = _load_scene_types_from_config()

# 分类优先级(从统一配置加载)
_PRIORITY = get_task_type_priority()

# 仅保留场景优先级中存在的场景名
_SCENE_PRIORITY = [p for p in _PRIORITY if p in SCENE_TYPES]
# general 兜底
if 'general' not in _SCENE_PRIORITY:
    _SCENE_PRIORITY.append('general')

# 预编译正则
_CODE_BLOCK_RE = re.compile(r'```[\s\S]*?```', re.MULTILINE)
_LATEX_BLOCK_RE = re.compile(r'\$\$[\s\S]*?\$\$|\$[^$]+\$')
_MULTI_STEP_RE = re.compile(r'(第[一二三四五六七八九十\d]+步|step\s*\d+|phase\s*\d+)', re.IGNORECASE)


def classify_scene(question: str) -> Dict[str, any]:
    """根据问题内容推断商用场景类型。

    Returns:
        {
            'scene_type': str,           # 场景类型
            'scene_confidence': float,   # 置信度 [0, 1]
            'typical_tokens': int,       # 该场景的典型 token 消耗
            'token_range': tuple,        # (min, max) token 范围
            'scores': dict,              # 每个场景的得分详情
        }
    """
    q_lower = question.lower()
    scores: Dict[str, float] = {}

    for scene_name, scene_def in SCENE_TYPES.items():
        score = 0.0

        # Strong indicators: 每个 +3.0
        for kw in scene_def.get('indicators_strong', []):
            if kw.lower() in q_lower:
                score += 3.0
            # 正则匹配代码块额外加分
            if scene_name == 'code_generation' and kw == '```':
                code_blocks = _CODE_BLOCK_RE.findall(question)
                score += min(len(code_blocks) * 2.0, 6.0)

        # Weak indicators: 每个 +1.0
        for kw in scene_def.get('indicators_weak', []):
            if kw.lower() in q_lower:
                score += 1.0

        # 场景特定增强
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

    # 按优先级选取最高分场景
    best_scene = 'general'
    best_score = 0.0
    for scene_name in _SCENE_PRIORITY:
        s = scores.get(scene_name, 0.0)
        if s > best_score:
            best_score = s
            best_scene = scene_name

    # 置信度: 归一化到 [0, 1]
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
        'typical_tokens': scene_def.get('typical_tokens', 3000),
        'token_range': scene_def.get('token_range', (500, 15000)),
        'scores': {k: round(v, 2) for k, v in scores.items()},
    }


def get_scene_typical_tokens(scene_type: str) -> int:
    """快捷获取场景的典型 token 消耗。"""
    return SCENE_TYPES.get(scene_type, SCENE_TYPES.get('general', {})).get('typical_tokens', 3000)
