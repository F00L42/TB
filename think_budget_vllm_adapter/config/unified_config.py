"""Single-source server configuration for scene routing and Think Budget."""
from __future__ import annotations

import os
from enum import Enum
from typing import Any, Dict, List, Optional

try:
    import yaml
    _YAML_AVAILABLE = True
except ImportError:
    _YAML_AVAILABLE = False


_BUDGET_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "budget_config.yaml")
_config_cache: Optional[Dict[str, Any]] = None
_config_mtime: Optional[float] = None


_RUNTIME_DEFAULTS: Dict[str, Any] = {
    "BUDGET_ENABLED": True,
    "check_length": 200,
    "min_think_budget": 100,
    "think_stride": 100,
    "num_thinking": 10,
    "confidence_threshold": 0.95,
    "percentile_step": 0.75,
    "enable_value_min_confidence": False,
    "value_min_bottom_k": 5,
    "enable_safety_gate": False,
    "baseline_time_limit": 90.0,
    "enable_scene_budget": True,
    "enable_history_budget": False,
    "enable_difficulty_estimator": False,
    "scene_override": "",
    "scene_match_min_score": 3.0,
    "scene_match_min_margin": 1.0,
    "difficulty_check_length_base": 400,
    "difficulty_check_length_max": 6000,
    "difficulty_min_think_base": 100,
    "difficulty_min_think_max": 6000,
    "difficulty_stride_base": 400,
    "difficulty_stride_max": 6000,
    "difficulty_num_thinking_base": 10,
    "difficulty_num_thinking_max": 20,
    "fixed_budget_tokens": 0,
    "fixed_num_thinking": 0,
    "fixed_think_stride": 0,
    "fixed_check_length": 0,
    "fixed_prob_th": 0.0,
    "fixed_min_think_tokens": 0,
    "history_budget_multiplier": 1.2,
    "history_stride_percentile": 0.75,
    "history_warmup_count": 3,
    "history_warmup_strategy": "moderate",
    "new_task_unlimited": True,
    "max_internal_calls": 20,
    "max_thinking_tokens": 65536,
    "final_answer_max_tokens": 2048,
    "emit_phase_metrics": True,
    "verbose_stage_logging": False,
}


_SCENE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    # run_06 phase-one product routes. Keep the six frozen vectors exact until
    # cross-profile replay proves that a shared cluster center is non-inferior.
    "react_whitebox_overall_planning": {
        "description": "ReAct 白盒整体规划",
        "typical_tokens": 1500,
        "token_range": [850, 1500],
        "check_length": 850,
        "think_stride": 450,
        "num_thinking": 2,
        "min_think_tokens": 850,
        "max_think_tokens": 1500,
        "check_answer_len": 50,
        "prob_th": 0.93,
        "indicators": {
            "required_all": [
                "## Executed Records step1",
                "Actions in category (a) should be automated using their "
                "corresponding APIs.",
            ],
            "strong": [],
            "weak": [],
        },
    },
    "react_machine_data_analysis": {
        "description": "ReAct 白盒机器数据分析",
        "typical_tokens": 850,
        "token_range": [500, 850],
        "check_length": 500,
        "think_stride": 250,
        "num_thinking": 2,
        "min_think_tokens": 500,
        "max_think_tokens": 850,
        "check_answer_len": 50,
        "prob_th": 0.93,
        "indicators": {
            "required_all": [
                "<Packet Parameter Description>",
                "Can_a_conclusion_be_drawn",
            ],
            "strong": [],
            "weak": [],
        },
    },
    "kpi_configuration_assurance": {
        "description": "KPI 配置保障",
        "typical_tokens": 1600,
        "token_range": [500, 1600],
        "check_length": 500,
        "think_stride": 300,
        "num_thinking": 4,
        "min_think_tokens": 500,
        "max_think_tokens": 1600,
        "check_answer_len": 50,
        "prob_th": 0.93,
        "indicators": {
            "required_any": [
                "根因分析规则和分析线索",
                "root cause analysis rules and analysis clues",
            ],
            "strong": [],
            "weak": [],
        },
    },
    "netqad_e2e_single_step": {
        "description": "NetQAD 云核 E2E 单步规划",
        "typical_tokens": 1050,
        "token_range": [300, 1050],
        "check_length": 300,
        "think_stride": 300,
        "num_thinking": 3,
        "min_think_tokens": 300,
        "max_think_tokens": 1050,
        "check_answer_len": 50,
        "prob_th": 0.93,
        "indicators": {
            "required_all": [
                "绝对不允许超出“当前诊断思路”",
                "根据“当前诊断思路”、“已执行记录”",
            ],
            "strong": [],
            "weak": [],
        },
    },
    "netqad_e2e_planning": {
        "description": "NetQAD 云核 E2E 规划模块",
        "typical_tokens": 3050,
        "token_range": [950, 3050],
        "check_length": 950,
        "think_stride": 450,
        "num_thinking": 5,
        "min_think_tokens": 950,
        "max_think_tokens": 3050,
        "check_answer_len": 50,
        "prob_th": 0.95,
        "indicators": {
            "required_all": [
                "一个步骤编号可能包含多个可执行的操作",
                "工单中告警参数定义",
            ],
            "strong": [],
            "weak": [],
        },
    },
    "long_context_root_cause": {
        "description": "长文本根因推理",
        "typical_tokens": 2600,
        "token_range": [1200, 2600],
        "check_length": 1200,
        "think_stride": 400,
        "num_thinking": 4,
        "min_think_tokens": 1200,
        "max_think_tokens": 2600,
        "check_answer_len": 50,
        "prob_th": 0.93,
        "indicators": {
            "required_all": [
                "### 背景知识",
                "故障对应KPI说明",
            ],
            "strong": [],
            "weak": [],
        },
    },
    "retrieval": {
        "description": "检索任务",
        "typical_tokens": 500,
        "token_range": [200, 2000],
        "check_length": 200,
        "think_stride": 100,
        "num_thinking": 3,
        "min_think_tokens": 50,
        "check_answer_len": 2000,
        "prob_th": 0.90,
        "indicators": {
            "strong": [
                "查询", "检索", "查找", "搜索", "retrieve", "search",
                "lookup", "fetch", "帮我查", "查一下",
            ],
            "weak": ["获取", "列出", "where", "which", "is there"],
        },
    },
    "statistics_aggregation": {
        "description": "统计聚合任务",
        "typical_tokens": 4000,
        "token_range": [1000, 8000],
        "check_length": 800,
        "think_stride": 300,
        "num_thinking": 6,
        "min_think_tokens": 600,
        "check_answer_len": 4000,
        "prob_th": 0.90,
        "indicators": {
            "strong": [
                "统计", "汇总", "聚合", "求和", "计数", "aggregate",
                "aggregation", "sum", "count by", "group by",
            ],
            "weak": ["总数", "合计", "占比", "平均", "average"],
        },
    },
    "classification": {
        "description": "分类判断任务",
        "typical_tokens": 800,
        "token_range": [300, 3000],
        "check_length": 300,
        "think_stride": 100,
        "num_thinking": 4,
        "min_think_tokens": 100,
        "check_answer_len": 2000,
        "prob_th": 0.90,
        "indicators": {
            "strong": [
                "分类", "归类", "属于哪类", "classify", "categorize",
                "判断对错", "true or false", "选择题", "选项",
            ],
            "weak": ["区分", "辨别", "identify"],
        },
    },
    "code_generation": {
        "description": "代码生成任务",
        "typical_tokens": 12000,
        "token_range": [5000, 20000],
        "check_length": 2000,
        "think_stride": 500,
        "num_thinking": 15,
        "min_think_tokens": 2000,
        "check_answer_len": 4000,
        "prob_th": 0.95,
        "indicators": {
            "strong": [
                "编写程序", "编写代码", "实现函数", "代码补全",
                "implement a function", "write a program", "leetcode",
                "livecodebench", "competitive programming",
            ],
            "weak": [
                "python", "java", "javascript", "输入格式", "输出格式",
                "input format", "output format", "test case",
            ],
        },
    },
    "math_reasoning": {
        "description": "数学推理任务",
        "typical_tokens": 8000,
        "token_range": [3000, 22000],
        "check_length": 1500,
        "think_stride": 400,
        "num_thinking": 12,
        "min_think_tokens": 1500,
        "check_answer_len": 4000,
        "prob_th": 0.95,
        "indicators": {
            "strong": [
                "证明", "方程", "积分", "integral", "prove", "equation",
                "derivative", "polynomial", "aime", "mathematical",
            ],
            "weak": [
                "求解", "化简", "计算", "calculate", "compute",
                "determine the value", "find the value",
            ],
        },
    },
    "reasoning": {
        "description": "逻辑推理任务",
        "typical_tokens": 5000,
        "token_range": [1500, 12000],
        "check_length": 800,
        "think_stride": 300,
        "num_thinking": 8,
        "min_think_tokens": 500,
        "check_answer_len": 3000,
        "prob_th": 0.93,
        "indicators": {
            "strong": [
                "推理", "推导", "前提", "结论", "infer", "deduce",
                "logically", "逻辑判断",
            ],
            "weak": ["因果关系", "therefore", "hence"],
        },
    },
    "tool_call": {
        "description": "工具或函数调用任务",
        "typical_tokens": 1000,
        "token_range": [500, 2000],
        "check_length": 400,
        "think_stride": 200,
        "num_thinking": 8,
        "min_think_tokens": 100,
        "check_answer_len": 4000,
        "prob_th": 0.95,
        "indicators": {
            "strong": [
                "调用工具", "调用函数", "函数调用", "api调用", "参数填充",
                "tool_call", "function call", "invoke", "execute query",
            ],
            "weak": ["接口调用", "parameter", "argument"],
        },
    },
    "fault_diagnosis": {
        "description": "故障诊断任务",
        "typical_tokens": 1500,
        "token_range": [800, 3000],
        "check_length": 500,
        "think_stride": 200,
        "num_thinking": 8,
        "min_think_tokens": 200,
        "check_answer_len": 4000,
        "prob_th": 0.95,
        "indicators": {
            "strong": [
                "告警", "故障", "诊断", "根因", "排障", "自愈", "alarm",
                "fault", "troubleshoot", "指标异常", "网元", "kpi",
            ],
            "weak": ["运维", "巡检", "监控", "基站", "核心网"],
        },
    },
    "knowledge_qa": {
        "description": "知识问答任务",
        "typical_tokens": 3000,
        "token_range": [1000, 7000],
        "check_length": 600,
        "think_stride": 200,
        "num_thinking": 6,
        "min_think_tokens": 200,
        "check_answer_len": 3000,
        "prob_th": 0.92,
        "indicators": {
            "strong": ["解释一下", "请解释", "what is", "define"],
            "weak": ["解释", "说明", "描述", "explain", "describe", "是什么"],
        },
    },
    "planning": {
        "description": "任务规划任务",
        "typical_tokens": 2500,
        "token_range": [1000, 5000],
        "check_length": 600,
        "think_stride": 200,
        "num_thinking": 8,
        "min_think_tokens": 300,
        "check_answer_len": 3000,
        "prob_th": 0.93,
        "indicators": {
            "strong": [
                "规划", "计划", "编排", "执行顺序", "plan", "workflow",
                "orchestrate", "第一步", "第二步",
            ],
            "weak": ["步骤", "流程", "方案", "策略", "先后"],
        },
    },
    "general": {
        "description": "拒识或通用任务",
        "typical_tokens": 3000,
        "token_range": [500, 15000],
        "check_length": 500,
        "think_stride": 200,
        "num_thinking": 8,
        "min_think_tokens": 200,
        "check_answer_len": 3000,
        "prob_th": 0.93,
        "indicators": {"strong": [], "weak": []},
    },
}


_SCENE_PRIORITY = [
    "react_whitebox_overall_planning",
    "react_machine_data_analysis",
    "kpi_configuration_assurance",
    "netqad_e2e_single_step",
    "netqad_e2e_planning",
    "long_context_root_cause",
    "code_generation",
    "math_reasoning",
    "reasoning",
    "planning",
    "fault_diagnosis",
    "tool_call",
    "statistics_aggregation",
    "classification",
    "retrieval",
    "knowledge_qa",
    "general",
]


class DifficultyTier(str, Enum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"
    VERY_HARD = "very_hard"


def load_budget_config(
    config_path: Optional[str] = None,
    force_reload: bool = False,
) -> Dict[str, Any]:
    """Load the YAML configuration, with built-in values as a safe fallback."""
    global _config_cache, _config_mtime
    path = config_path or _BUDGET_CONFIG_PATH
    if not force_reload and _config_cache is not None and os.path.exists(path):
        mtime = os.path.getmtime(path)
        if _config_mtime == mtime:
            return _config_cache
    if not os.path.exists(path) or not _YAML_AVAILABLE:
        if os.path.exists(path) and not _YAML_AVAILABLE:
            print(
                "TB_WARNING event=config_fallback reason=pyyaml_not_installed "
                "action=use_builtin_defaults",
                flush=True,
            )
        _config_cache = _get_default_config()
        _config_mtime = (
            os.path.getmtime(path) if os.path.exists(path) else None)
        return _config_cache
    try:
        with open(path, "r", encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
        if not isinstance(config, dict):
            raise ValueError("top-level YAML value must be a mapping")
        _config_cache = config
        _config_mtime = os.path.getmtime(path)
        return config
    except Exception as exc:
        print(
            "TB_WARNING event=config_fallback "
            f"reason=load_failed path={path} error={exc} "
            "action=use_builtin_defaults",
            flush=True,
        )
        return _get_default_config()


def get_runtime_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = config or load_budget_config()
    runtime = cfg.get("RUNTIME", {})
    if not isinstance(runtime, dict):
        raise ValueError("RUNTIME in budget_config.yaml must be a mapping")
    unknown = set(runtime) - set(_RUNTIME_DEFAULTS)
    if unknown:
        raise ValueError(
            "unknown RUNTIME keys in budget_config.yaml: "
            + ", ".join(sorted(unknown)))
    return {**_RUNTIME_DEFAULTS, **runtime}


def get_performance_policy_config(
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from think_budget_vllm_adapter.context import PerformancePolicy

    cfg = config or load_budget_config()
    declared = cfg.get("PERFORMANCE_POLICY", {})
    if declared is None:
        declared = {}
    if not isinstance(declared, dict):
        raise ValueError("PERFORMANCE_POLICY must be a mapping")
    expected = PerformancePolicy().telemetry()
    merged = {**expected, **declared}
    if merged != expected:
        changed = sorted(
            key for key, value in merged.items()
            if expected.get(key) != value)
        raise ValueError(
            "PERFORMANCE_POLICY is fixed; invalid overrides: "
            + ", ".join(changed))
    return expected


def get_history_config(
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = config or load_budget_config()
    return {
        "new_task": cfg.get("HISTORY_NEW_TASK", {}),
        "warm_start": cfg.get("HISTORY_WARM_START", {}),
        "hot_start": cfg.get("HISTORY_HOT_START", {}),
    }


def get_scene_definitions(
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Dict[str, Any]]:
    cfg = config or load_budget_config()
    scenes = cfg.get("SCENES", {})
    if not isinstance(scenes, dict):
        raise ValueError("SCENES in budget_config.yaml must be a mapping")
    return scenes


def get_scene_budget(
    scene: str,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    scenes = get_scene_definitions(config)
    return dict(scenes.get(scene, scenes.get("general", {})))


def is_known_scene(
    scene: str,
    config: Optional[Dict[str, Any]] = None,
) -> bool:
    return scene != "general" and scene in get_scene_definitions(config)


def get_scene_priority(
    config: Optional[Dict[str, Any]] = None,
) -> List[str]:
    cfg = config or load_budget_config()
    priority = cfg.get("SCENE_PRIORITY", _SCENE_PRIORITY)
    return [str(scene) for scene in priority]


def get_difficulty_tier(
    difficulty: float,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    cfg = config or load_budget_config()
    tiers = cfg.get("DIFFICULTY_TIERS", {})
    for tier_name, tier_config in tiers.items():
        interval = tier_config.get("difficulty_range", [0.0, 1.0])
        if (
            interval[0] <= difficulty < interval[1]
            or (tier_name == "very_hard" and difficulty >= interval[0])
        ):
            return {"tier": tier_name, **tier_config}
    return {"tier": "medium", **tiers.get("medium", {})}


def get_difficulty_multiplier(
    tier_name: str,
    config: Optional[Dict[str, Any]] = None,
) -> float:
    cfg = config or load_budget_config()
    return cfg.get("DIFFICULTY_TIERS", {}).get(
        tier_name, {}).get("token_multiplier", 1.0)


def compute_difficulty_adjusted_budget(
    scene: str,
    difficulty: float,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    base_budget = get_scene_budget(scene, config)
    tier = get_difficulty_tier(difficulty, config)
    adjusted_tokens = int(
        base_budget.get("typical_tokens", 3000)
        * tier.get("token_multiplier", 1.0))
    check_length = min(
        int(
            base_budget.get("check_length", 500)
            * tier.get("check_length_factor", 0.5)
            * 2),
        adjusted_tokens,
    )
    stride = max(
        50,
        int(
            base_budget.get("think_stride", 200)
            * tier.get("stride_factor", 0.5)
            * 2),
    )
    num_thinking = max(
        tier.get("num_thinking_min", 3),
        min(
            tier.get("num_thinking_max", 10),
            max(3, adjusted_tokens // max(check_length, 100)),
        ),
    )
    return {
        "typical_tokens": adjusted_tokens,
        "check_length": check_length,
        "think_stride": stride,
        "num_thinking": num_thinking,
        "min_think_tokens": max(50, int(adjusted_tokens * 0.2)),
        "check_answer_len": base_budget.get("check_answer_len", 3000),
        "prob_th": base_budget.get("prob_th", 0.93),
        "difficulty_tier": tier.get("tier", "medium"),
        "difficulty_multiplier": tier.get("token_multiplier", 1.0),
    }


def _get_default_config() -> Dict[str, Any]:
    return {
        "RUNTIME": dict(_RUNTIME_DEFAULTS),
        "PERFORMANCE_POLICY": {
            "version": "think-budget-performance-v1",
            "trial_answer_length": 50,
            "clean_context_only": True,
            "fresh_final_answer": True,
            "retain_answer_list": False,
            "enable_soft_truncation": False,
            "confidence_missing_input": "fail_open",
            "confidence_scope": "current_trial",
            "enable_safety_amplification": False,
            "enable_safety_candidate_fallback": False,
        },
        "SCENES": {
            name: {
                **values,
                "indicators": {
                    key: list(items)
                    for key, items in values.get("indicators", {}).items()
                },
            }
            for name, values in _SCENE_DEFAULTS.items()
        },
        "SCENE_PRIORITY": list(_SCENE_PRIORITY),
        "DIFFICULTY_TIERS": {
            "easy": {
                "difficulty_range": [0.0, 0.3],
                "token_multiplier": 0.8,
                "check_length_factor": 0.3,
                "stride_factor": 0.3,
                "num_thinking_min": 3,
                "num_thinking_max": 6,
            },
            "medium": {
                "difficulty_range": [0.3, 0.6],
                "token_multiplier": 1.0,
                "check_length_factor": 0.6,
                "stride_factor": 0.6,
                "num_thinking_min": 5,
                "num_thinking_max": 10,
            },
            "hard": {
                "difficulty_range": [0.6, 0.8],
                "token_multiplier": 1.5,
                "check_length_factor": 0.8,
                "stride_factor": 0.8,
                "num_thinking_min": 8,
                "num_thinking_max": 15,
            },
            "very_hard": {
                "difficulty_range": [0.8, 1.0],
                "token_multiplier": 2.0,
                "check_length_factor": 1.0,
                "stride_factor": 1.0,
                "num_thinking_min": 10,
                "num_thinking_max": 20,
            },
        },
        "HISTORY_NEW_TASK": {
            "unlimited": True,
            "unlimited_budget_placeholder": 99999,
        },
        "HISTORY_WARM_START": {
            "warmup_count": 3,
            "multiplier": 1.2,
            "strategy": "moderate",
            "high_variance_boost": {
                "conservative": 1.3,
                "moderate": 1.1,
                "aggressive": 1.0,
            },
        },
        "HISTORY_HOT_START": {
            "stride_percentile": 0.75,
            "low_success_boost": 1.3,
            "high_failure_stride_factor": 0.8,
        },
    }
