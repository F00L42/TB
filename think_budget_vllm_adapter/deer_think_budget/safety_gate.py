"""安全门 — 超时回退逻辑。

从 naie/optimizations/safety_gate.py 移植，自包含。

超时后不再调API, 直接从已有answer_list取最佳答案返回。
无answer时关闭think标签返回已有prompt内容(不调API)。

Inlined from vllm/vllm/deer_think_budget/ into think_budget_vllm_adapter.
"""

def safety_gate_check(
    elapsed_fn,
    baseline_time_limit: float,
    enable_safety_gate: bool = True,
) -> bool:
    """检查安全门是否触发。

    HARD trigger (>1.5x): 立即返回 True, 强制结束推理。
    SOFT trigger (>1.0x): 允许当前轮完成, 但下一轮检查时可能 HARD。

    Args:
        elapsed_fn: 返回已耗时的 callable
        baseline_time_limit: 基线时间限制 (秒)
        enable_safety_gate: 是否启用安全门
    """
    if not enable_safety_gate:
        return False
    elapsed = elapsed_fn()
    if elapsed > baseline_time_limit * 1.5:
        print(
            "TB_SAFETY_GATE event=hard "
            f"elapsed_s={elapsed:.1f} limit_s={baseline_time_limit * 1.5:.1f}",
            flush=True,
        )
        return True
    if elapsed > baseline_time_limit:
        print(
            "TB_SAFETY_GATE event=soft "
            f"elapsed_s={elapsed:.1f} limit_s={baseline_time_limit:.1f} "
            "action=allow_current_round",
            flush=True,
        )
        return False
    return False
