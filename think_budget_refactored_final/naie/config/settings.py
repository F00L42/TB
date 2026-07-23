import os
import json

_DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data", "naie"))

NAIE_CONFIG = {
    'model_name': 'Qwen3.6-27B',
    'max_token': 30000,
    'dataset_dir': os.path.join(_DATA_DIR, 'dataset_slow_thinking'),
    'tokenizer_path': os.path.join(_DATA_DIR, 'Qwen3.6-27B'),
    'tool_prompt_dir': os.path.join(_DATA_DIR, 'tool_prompt'),
}

# ═══════════════════════════════════════════════════════════════
# 统一配置中心 — 所有超参数集中在此配置，方便商业交接
# 格式: (值, 中文描述)
# ═══════════════════════════════════════════════════════════════
CONFIG = {
    # ─── P0 总开关 ───
    "enable_budget":              (True,    "[P0] BudgetForcing总开关 — False时模型自由生成不截断"),

    # ─── P1 核心budget参数 ───
    "check_length":               (200,     "[P1] 初始思考token数 — 第一次API调用请求的思考量"),
    "think_stride":               (100,     "[P1] 每轮思考增量token — 循环中每轮追加思考量"),
    "prob_th":                    (0.95,    "[P1] 置信度阈值 — decision_score>=此值且think充足时接受答案"),
    "num_thinking":               (10,      "[P1] 最大尝试轮次 — budget循环最多跑几轮"),
    "check_answer_len":           (4000,    "[P1] 试探答案长度 — 每轮induced后请求多少token生成答案(必须足够写完完整JSON)"),
    "max_tokens":                 (30000,   "[P1] 最大总token — 整体budget上限"),

    # ─── P2.2 连续相同答案早停 ───
    "enable_consecutive_same_stop": (True,  "[P2.2] 连续相同答案早停开关"),
    "consecutive_same_stop":      (2,       "[P2.2] 连续相同次数阈值 — 连续N轮答案一致则提前接受"),

    # ─── P3.1 Value最低概率(核心创新) ───
    "enable_value_min_confidence": (True,    "[P3.1] Value最低概率开关 — 取答案bottom-k token概率均值"),
    "value_min_bottom_k":         (5,       "[P3.1] 取最低K个token的logprob求均值"),

    # ─── P3.4 完整思维链+答案置信度 ───
    "enable_full_chain_confidence": (False,  "[P3.4] 完整链置信度 — 思维链Min-10%(宽容)+答案Bottom-K(零容忍), AND门"),
    "full_chain_min_percentile":    (0.1,    "[P3.4] 思维链最差百分比 — 0.1=最差10% Value Token均值"),

    # ─── P3.3 Clean Context ───
    "enable_clean_context":       (True,    "[P3.3] Clean Context — 继续思考时丢弃induced部分节省token"),

    # ─── 安全门 ───
    "enable_safety_gate":         (False,   "[安全门] 超时自动回退 — 超过1.5x baseline_time_limit强制返回"),
    "baseline_time_limit":        (90.0,    "[安全门] 基线单题平均延迟(s) — soft=1x触发/hard=1.5x触发"),

    # ─── 路由参数 ───
    "min_think_tokens":           (100,     "[路由] 最低思考token — actual_len不足则不提前接受(软门)"),
    "max_api_calls":              (20,      "[路由] 单题最大API调用次数 — 0=不限"),

    # ─── 任务类型判断 ───
    "enable_task_type_budget":    (True,    "[任务类型] 启用任务类型→token预算映射 — 根据检索/编码/数学/推理/分类等类型设置不同token预算"),
    "default_task_type":          ("general", "[任务类型] 默认任务类型 — 无法判断时使用"),
    "custom_task_types_path":     (None,    "[任务类型] 自定义任务类型配置路径 — None使用内置默认, 指定JSON文件则加载覆盖"),
    "task_type_override":         ("",      "[任务类型] 手动覆盖任务类型 — 空串=自动判断, 非空则强制使用指定类型"),

    # ─── 历史轨迹预算 ───
    "enable_history_budget":      (True,    "[历史轨迹] 启用历史轨迹驱动的预算推算 — 基于同类问题历史消耗动态调整预算"),
    "history_budget_multiplier":  (1.2,     "[历史轨迹] warm-start预算乘数 — 1.2=在历史均值基础上多20%余量避免低估"),
    "history_low_success_boost":  (1.3,     "[历史轨迹] 低成功率预算放大 — 成功率<0.7时预算乘此系数"),
    "history_stride_percentile":  (0.75,    "[历史轨迹] 步长计算历史百分位 — 0.75=取p75 token消耗的1/num_thinking"),
    "new_task_unlimited":         (True,    "[历史轨迹] 新任务类型不设budget限制 — 首次遇到新类型让模型自由发挥, 收集历史后再启用budget"),
    "auto_cluster_threshold":     (5,       "[历史轨迹] 自动聚类新任务类型的历史条数阈值 — 积累超过此数自动聚类"),
    "history_warmup_count":       (3,       "[历史轨迹] warm-start条数阈值 — 历史<=3条用warm-start(1.2x余量), >3条用hot-start(p75)"),
    "history_warmup_strategy":    ("moderate", "[历史轨迹] warm-start策略 — conservative(保守,1.5x余量,高方差时1.95x)/moderate(适中,1.2x余量,高方差时1.32x)/aggressive(激进,1.0x余量,高方差时1.1x)"),

    # ─── 人工可控超参 ───
    "fixed_budget_tokens":        (0,       "[人工] 固定budget token消耗 — 0=不固定(动态计算), >0则覆盖所有动态计算, 直接作为think预算上限"),
    "fixed_num_thinking":         (0,       "[人工] 固定最大轮次 — 0=不固定(动态计算), >0则覆盖num_thinking"),
    "fixed_think_stride":         (0,       "[人工] 固定步长 — 0=不固定(动态计算), >0则覆盖think_stride"),
    "fixed_check_length":         (0,       "[人工] 固定初始思考长度 — 0=不固定(动态计算), >0则覆盖check_length"),
    "fixed_prob_th":              (0.0,     "[人工] 固定置信度阈值 — 0.0=不固定(使用prob_th), >0则覆盖prob_th"),
    "fixed_min_think_tokens":     (0,       "[人工] 固定最低思考token — 0=不固定(动态计算), >0则覆盖min_think_tokens"),

    # ─── 统一预算配置 — 4个核心暴露超参数 (与 budget_config.yaml HUMAN_CONTROLLABLE 对应) ───
    "BUDGET_ENABLED":             (True,    "[统一配置] 是否启用预算控制 — 对应 budget_config.yaml BUDGET_ENABLED"),
    "FIXED_BUDGET_TOKEN":         (0,       "[统一配置] 人工固定Token消耗上限 — >0忽略历史/难度/任务类型(对应 FIXED_BUDGET_TOKEN)"),
    "CONFIDENCE_THRESHOLD":       (0.95,    "[统一配置] 置信度阈值 — decision_score>=此值接受答案, 也决定是否采纳历史推算(对应 CONFIDENCE_THRESHOLD)"),
    "PERCENTILE_STEP":            (0.75,    "[统一配置] 历史轨迹百分比步长 — 0.75=取P75, 0.05=按5%步进(对应 PERCENTILE_STEP)"),

    # ─── P5 难度估计器(多特征融合→动态budget) ───
    "enable_difficulty_estimator": (False,   "[P5] 难度估计器 — 14维连续特征+场景先验输出difficulty∈[0,1]映射budget"),
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
    "enable_deer_trial":          (False,   "[P6] DEER trial answer — 连续稳定则提前接受+置信度加成"),
    "deer_trial_stable_rounds":   (2,       "[P6] trial稳定轮次 — 连续N轮trial answer一致则触发"),
    "deer_trial_answer_len":      (30,      "[P6] trial answer比较长度"),
    "deer_confidence_boost":      (0.05,    "[P6] 稳定时置信度加成"),

    # ─── 软截断 ───
    "enable_soft_truncation":     (True,    "[软截断] 置信度达标后在自然断点(句号/换行/JSON闭合)截断"),

    # ─── 双通道评估 ───
    "enable_dual_channel":        (False,   "[双通道] 混合双通道评估开关 — 推理区(宽容Min-10%)+动作区(零容忍Bottom-K)"),
    "reasoning_min_percentile":   (0.1,     "[双通道] 推理区 Min-% 均值百分位 — 0.1=取最差10%的Value Token均值"),
}

# 兼容旧代码
THINK_BUDGET_CONFIG_47 = {k: v[0] for k, v in CONFIG.items()}


def get_value(key):
    """获取配置值"""
    if key in CONFIG:
        return CONFIG[key][0]
    return None


def set_value(key, value):
    """设置配置值"""
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
    enabled = [k for k, (d, v) in CONFIG.items() if k.startswith("enable_") and CONFIG[k][0]]
    print(f"  已启用优化: {', '.join(enabled)}")
    print("=" * 70)


def load_task_type_config(json_path=None):
    """从JSON文件加载任务类型token预算配置, 覆盖内置默认值。

    Args:
        json_path: JSON文件路径。None则使用内置默认。

    Returns:
        dict: 任务类型→token预算参数映射
    """
    if json_path and os.path.exists(json_path):
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    # 返回内置默认(由 task_type_budget.py 定义)
    return None
