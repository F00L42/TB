import naie._bootstrap  # sys.path

import json
import time

from naie.optimizations.difficulty_estimator import (
    estimate_difficulty, compute_budget_from_difficulty,
    count_entities, count_constraints, needs_multi_tool, detect_self_negation,
)
from naie.optimizations.trial_evaluator import TrialAnswerEvaluator
from naie.optimizations.complexity_analyzer import estimate_complexity


CASES = [
    {
        "name": "简单单工具查询",
        "question": "查询小区列表",
        "reference": '{"result": "atomic.get.cell.list"}',
        "task_type": "general",
        "tool_count": 1,
        "expected_difficulty": "low",
    },
    {
        "name": "中等复杂度告警分析",
        "question": "请诊断网元APP-HNGZgdpsbcc011BHW-00AHW012在2025-7-22 17:30:00发生的PSBC接通率低于二级门限性能故障。故障时间段内检测到ALM-26005告警，需要排查是否为注册攻击导致。性能指标1912981070在17:30周期为163244，较前周期151635显著上升。",
        "reference": '{"faultNumber": "F001", "faultIMSI": "460001", "isCaller": false}',
        "task_type": "naie",
        "tool_count": 3,
        "expected_difficulty": "medium",
    },
    {
        "name": "复杂多工具组合",
        "question": "请依次执行以下操作：首先查询场馆列表，然后对场馆A创建评估任务，最后导出评估结果报告。在这个过程中需要校验场馆A是否存在，确保数据完整。",
        "reference": "",
        "task_type": "general",
        "tool_count": 15,
        "expected_difficulty": "high",
    },
    {
        "name": "含表格和IP的根因分析",
        "question": "诊断网元APP-HNGZgdpsbcc011BHW的性能故障。| 网元 | 告警Id | 发生时间 | 定位信息 | | APP-HNGZgdpsbcc011BHW | ALM-26005 | 2025-07-22 00:42:30 | 模块号=11 | | APP-HNGZgdpsbcc011BHW | ALM-26005 | 2025-07-22 00:27:55 | 模块号=11 | IP: 141.73.2.25, 71.26.76.45。告警原因为固定用户名发起半注册攻击，IMSI: 460001234567890，接通率从15.1%降至10.2%。",
        "reference": '{"faultNumber": "F001", "faultIMSI": "460001234567890", "faultIp": "141.73.2.25"}',
        "task_type": "naie",
        "tool_count": 0,
        "expected_difficulty": "high",
    },
    {
        "name": "跨轮上下文校验",
        "question": "根据之前查询的场馆评估结果，验证场馆A的评估报告是否已经生成，如果已生成则导出，否则重新触发评估。需要确认之前的操作是否成功。",
        "reference": "",
        "task_type": "general",
        "tool_count": 8,
        "expected_difficulty": "medium",
    },
]


def test_difficulty_estimator():
    print("=" * 60)
    print("测试1: 难度估计器 (difficulty_estimator)")
    print("=" * 60)
    for case in CASES:
        result = estimate_difficulty(
            case["question"],
            reference=case.get("reference", ""),
            task_type=case["task_type"],
            tool_count=case.get("tool_count", 0),
        )
        diff = result["difficulty"]
        label = case["expected_difficulty"]
        ok = (label == "low" and diff < 0.3) or (label == "medium" and 0.3 <= diff < 0.6) or (label == "high" and diff >= 0.5)
        status = "PASS" if ok else "WARN"
        print(f"  [{status}] {case['name']}: difficulty={diff:.3f} (expect={label})")
        print(f"         entities={result['entity_count']} constraints={result['constraint_count']} "
              f"multi_tool={result['needs_multi_tool']} cross_turn={result['needs_cross_turn']} "
              f"validation={result['needs_validation']}")
    print()


def test_trial_evaluator():
    print("=" * 60)
    print("测试2: TrialAnswerEvaluator (DEER试答案评估)")
    print("=" * 60)
    config = {
        'enable_deer_trial': True,
        'deer_trial_stable_rounds': 2,
        'deer_trial_answer_len': 30,
        'deer_confidence_boost': 0.05,
    }
    evaluator = TrialAnswerEvaluator(config)
    evaluator.reset()

    content1 = "some thinkingৠ{\"faultNumber\": \"F001\"}"
    result1 = evaluator.evaluate_trial(content1, 0.7)
    print(f"  Trial1: stable={result1['is_stable']}, count={result1['stable_count']}, should_stop={result1['should_early_stop']}")

    content2 = "more thinkingৠ{\"faultNumber\": \"F001\"}"
    result2 = evaluator.evaluate_trial(content2, 0.75)
    print(f"  Trial2: stable={result2['is_stable']}, count={result2['stable_count']}, should_stop={result2['should_early_stop']}, boosted_conf={result2['boosted_confidence']}")

    content3 = "differentৠ{\"faultNumber\": \"F002\"}"
    result3 = evaluator.evaluate_trial(content3, 0.6)
    print(f"  Trial3 (changed answer): stable={result3['is_stable']}, count={result3['stable_count']}, should_stop={result3['should_early_stop']}")
    print()


def test_complexity_backward_compat():
    print("=" * 60)
    print("测试3: Complexity Analyzer 向后兼容")
    print("=" * 60)
    for case in CASES:
        result = estimate_complexity(case["question"], case.get("reference", ""))
        diff_result = estimate_difficulty(case["question"], case.get("reference", ""))
        print(f"  {case['name']}: complexity={result['complexity']:.3f} vs difficulty={diff_result['difficulty']:.3f}")
    print()


if __name__ == '__main__':
    test_difficulty_estimator()
    test_trial_evaluator()
    test_complexity_backward_compat()
    print("=" * 60)
    print("全部测试完成!")
    print("=" * 60)
