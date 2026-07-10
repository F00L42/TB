"""Think Budget 超参配置 — 所有参数集中定义。

从 think_budget/config/settings.py 迁移，调整路径。
"""
import os
import json

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))

# ═══════════════════════════════════════════════════════════════
# 统一配置中心 — 所有超参数集中在此配置
# 格式: (值, 中文描述)
# ═══════════════════════════════════════════════════════════════
CONFIG = {
    # ─── P0 总开关 ───
    "enable_budget":              (True,    "[P0] BudgetForcing总开关"),

    # ─── P1 核心budget参数 ───
    "check_length":               (200,     "[P1] 初始思考token数"),
    "think_stride":               (100,     "[P1] 每轮思考增量token"),
    "prob_th":                    (0.95,    "[P1] 置信度阈值"),
    "num_thinking":               (10,      "[P1] 最大尝试轮次"),
    "check_answer_len":           (4000,    "[P1] 试探答案长度"),
    "max_tokens":                 (65536,   "[P1] 最大总token"),

    # ─── P2.2 连续相同答案早停 ───
    "enable_consecutive_same_stop": (True,  "[P2.2] 连续相同答案早停开关"),
    "consecutive_same_stop":      (2,       "[P2.2] 连续相同次数阈值"),

    # ─── P3.1 Value最低概率 ───
    "enable_value_min_confidence": (True,    "[P3.1] Value最低概率开关"),
    "value_min_bottom_k":         (5,       "[P3.1] 取最低K个token的logprob"),

    # ─── P3.4 完整思维链+答案置信度 ───
    "enable_full_chain_confidence": (False,  "[P3.4] 完整链置信度"),
    "full_chain_min_percentile":    (0.1,    "[P3.4] 思维链最差百分比"),

    # ─── P3.3 Clean Context ───
    "enable_clean_context":       (True,    "[P3.3] Clean Context"),

    # ─── 安全门 ───
    "enable_safety_gate":         (False,   "[安全门] 超时自动回退"),
    "baseline_time_limit":        (90.0,    "[安全门] 基线单题平均延迟(s)"),

    # ─── 路由参数 ───
    "min_think_tokens":           (100,     "[路由] 最低思考token"),
    "max_api_calls":              (20,      "[路由] 单题最大API调用次数"),

    # ─── 任务类型判断 ───
    "enable_task_type_budget":    (True,    "[任务类型] 启用任务类型预算映射"),
    "default_task_type":          ("general", "[任务类型] 默认任务类型"),
    "custom_task_types_path":     (None,    "[任务类型] 自定义任务类型配置路径"),
    "task_type_override":         ("",      "[任务类型] 手动覆盖任务类型"),

    # ─── 历史轨迹预算 ───
    "enable_history_budget":      (True,    "[历史轨迹] 启用历史轨迹预算推算"),
    "history_budget_multiplier":  (1.2,     "[历史轨迹] warm-start预算乘数"),
    "history_low_success_boost":  (1.3,     "[历史轨迹] 低成功率预算放大"),
    "history_stride_percentile":  (0.75,    "[历史轨迹] 步长计算历史百分位"),
    "new_task_unlimited":         (True,    "[历史轨迹] 新任务不设budget限制"),
    "auto_cluster_threshold":     (5,       "[历史轨迹] 自动聚类阈值"),
    "history_warmup_count":       (3,       "[历史轨迹] warm-start条数阈值"),
    "history_warmup_strategy":    ("moderate", "[历史轨迹] warm-start策略"),

    # ─── 人工可控超参 ───
    "fixed_budget_tokens":        (0,       "[人工] 固定budget token消耗"),
    "fixed_num_thinking":         (0,       "[人工] 固定最大轮次"),
    "fixed_think_stride":         (0,       "[人工] 固定步长"),
    "fixed_check_length":         (0,       "[人工] 固定初始思考长度"),
    "fixed_prob_th":              (0.0,     "[人工] 固定置信度阈值"),
    "fixed_min_think_tokens":     (0,       "[人工] 固定最低思考token"),

    # ─── 统一预算配置 — 4个核心暴露超参数 ───
    "BUDGET_ENABLED":             (True,    "[统一配置] 是否启用预算控制"),
    "FIXED_BUDGET_TOKEN":         (0,       "[统一配置] 人工固定Token消耗上限"),
    "CONFIDENCE_THRESHOLD":       (0.95,    "[统一配置] 置信度阈值"),
    "PERCENTILE_STEP":            (0.75,    "[统一配置] 历史轨迹百分比步长"),

    # ─── P5 难度估计器 ───
    "enable_difficulty_estimator": (False,   "[P5] 难度估计器"),
    "difficulty_check_length_base": (400,   "[P5] 简单题check_length"),
    "difficulty_check_length_max":  (6000,  "[P5] 困难题check_length"),
    "difficulty_min_think_base":    (100,   "[P5] 简单题min_think_tokens"),
    "difficulty_min_think_max":     (6000,  "[P5] 困难题min_think_tokens"),
    "difficulty_stride_base":       (400,   "[P5] 简单题think_stride"),
    "difficulty_stride_max":        (6000,  "[P5] 困难题think_stride"),
    "difficulty_num_thinking_base": (10,    "[P5] 简单题num_thinking"),
    "difficulty_num_thinking_max":  (20,    "[P5] 困难题num_thinking"),
    "difficulty_answer_len_base":   (2000,  "[P5] 简单题check_answer_len"),
    "difficulty_answer_len_max":    (6000,  "[P5] 困难题check_answer_len"),

    # ─── P6 DEER trial answer评估 ───
    "enable_deer_trial":          (False,   "[P6] DEER trial answer"),
    "deer_trial_stable_rounds":   (2,       "[P6] trial稳定轮次"),
    "deer_trial_answer_len":      (30,      "[P6] trial answer比较长度"),
    "deer_confidence_boost":      (0.05,    "[P6] 稳定时置信度加成"),

    # ─── 软截断 ───
    "enable_soft_truncation":     (True,    "[软截断] 自然断点截断"),

    # ─── 双通道评估 ───
    "enable_dual_channel":        (False,   "[双通道] 混合双通道评估"),
    "reasoning_min_percentile":   (0.1,     "[双通道] 推理区 Min-%"),
}

THINK_BUDGET_CONFIG_47 = {k: v[0] for k, v in CONFIG.items()}


def get_value(key):
    if key in CONFIG:
        return CONFIG[key][0]
    return None


def set_value(key, value):
    if key in CONFIG:
        CONFIG[key] = (value, CONFIG[key][1])
        THINK_BUDGET_CONFIG_47[key] = value


def print_config():
    print("=" * 70)
    print("THINK_BUDGET_CONFIG — 当前配置")
    print("=" * 70)
    for key, (val, desc) in CONFIG.items():
        if isinstance(val, bool):
            val_str = f"{'ON' if val else 'OFF':>5}"
        elif val is None:
            val_str = f"{'None':>8}"
        elif isinstance(val, float):
            val_str = f"{val:>8}"
        else:
            val_str = f"{val:>8}"
        print(f"  {desc:<55s} {val_str}")
    print("=" * 70)


def load_task_type_config(json_path=None):
    if json_path and os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return None
