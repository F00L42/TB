"""Simple elapsed-time gate.

The gate only asks the three-state machine to enter a fresh FinalAnswerState.
It never amplifies the limit and never selects a hidden trial candidate.
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
