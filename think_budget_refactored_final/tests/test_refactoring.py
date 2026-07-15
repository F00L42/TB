"""Test trajectory router cold-start + end-to-end budget forcing after refactoring.

Usage:
    python test_refactoring.py [--skip_e2e] [--max_cases N]

Options:
    --skip_e2e      Skip the expensive API-call e2e test
    --max_cases N   Only run first N cases (default 20)
"""
import naie._bootstrap  # sys.path + NO_PROXY

import time
import math
import json

from naie.optimizations.token_filter import (
    is_structural_token, set_task_key_names, cal_value_min_confidence,
)
from naie.optimizations.confidence_gates import compute_decision_score
from naie.optimizations.early_stopping import (
    extract_answer_for_sc, check_consecutive_same, make_accept_decision,
)
from naie.optimizations.context_control import make_induced_prompt, apply_clean_context
from naie.optimizations.safety_gate import safety_gate_check, build_safety_fallback


def test_token_filter_generic():
    print("\n" + "=" * 60)
    print("TEST 1: Generic token_filter (no NAIE-specific names)")
    print("=" * 60)

    assert is_structural_token('{') is True, "JSON brace should be structural"
    assert is_structural_token('hello') is False, "Normal word should not be structural"
    assert is_structural_token('faultNumber') is False, "NAIE name should NOT be structural by default"
    assert is_structural_token('') is True, "Empty token should be structural"
    assert is_structural_token('  ') is True, "Whitespace should be structural"
    print("  PASS: default behavior (no NAIE key names)")

    set_task_key_names(['faultNumber', 'faultIMSI', 'faultAPN'])
    assert is_structural_token('faultNumber') is True, "After set_task_key_names, NAIE names should be structural"
    assert is_structural_token('unknown_field') is False, "Non-registered names should not be structural"
    print("  PASS: set_task_key_names() makes task-specific names structural")

    lps = [-0.05, -0.1, -0.2, -0.3]
    toks = ['{', 'faultNumber', 'hello', ':']
    r = cal_value_min_confidence(lps, toks, bottom_k=3)
    assert abs(r - (-0.2)) < 1e-6, f"Expected -0.2, got {r}"
    print(f"  PASS: cal_value_min_confidence with structural filtering = {r:.4f}")

    print("  token_filter: ALL TESTS PASSED")


def test_optimization_modules():
    print("\n" + "=" * 60)
    print("TEST 2: Individual optimization modules")
    print("=" * 60)

    ds, vmr = compute_decision_score(
        ans_logprobs=[-0.05, -0.1, -0.15],
        ans_tokens=['a', 'b', 'c'],
        confidence=0.85,
        perplexity=5.0,
        enable_value_min_confidence=True,
        value_min_bottom_k=3,
    )
    assert 0 < ds <= 1.0, f"decision_score should be (0,1], got {ds}"
    print(f"  compute_decision_score (value_min): score={ds:.4f}, raw={vmr:.4f}")

    ds2, _ = compute_decision_score(
        ans_logprobs=None, ans_tokens=None,
        confidence=0.9, perplexity=5.0,
        enable_value_min_confidence=False,
    )
    assert ds2 == 0.9, f"Should fall back to confidence, got {ds2}"
    print(f"  compute_decision_score (fallback):   score={ds2:.4f}")

    should_acc, reason = make_accept_decision(
        decision_score=0.9, prob_th=0.85, actual_len=700, min_think_tokens=600,
        is_paired=False, consecutive_same_count=0, consecutive_same_stop=2,
        enable_value_min_confidence=True, decision_score_raw=-0.15,
    )
    assert should_acc is True, "Should accept when score >= threshold and think sufficient"
    assert 'value_min_conf' in reason, f"Reason should mention value_min_conf, got {reason}"
    print(f"  make_accept_decision (accept): reason={reason}")

    should_acc2, reason2 = make_accept_decision(
        decision_score=0.5, prob_th=0.85, actual_len=200, min_think_tokens=600,
        is_paired=False, consecutive_same_count=0, consecutive_same_stop=2,
        enable_value_min_confidence=True, decision_score_raw=-0.7,
    )
    assert should_acc2 is False, "Should NOT accept when score < threshold and think insufficient"
    print(f"  make_accept_decision (reject): should_accept={should_acc2}")

    prompt, out_len = apply_clean_context(True, "base_prompt", "induced_prompt", "answer_text", 100, 30)
    assert prompt == "base_prompt", "Clean context should keep original prompt"
    assert out_len == 100, "Clean context should not add token_num"
    prompt2, out_len2 = apply_clean_context(False, "base_prompt", "induced_prompt", "answer_text", 100, 30)
    assert prompt2 == "induced_promptanswer_text", "No clean context should use induced+answer"
    assert out_len2 == 130, "No clean context should add token_num"
    print("  apply_clean_context: OK")

    class FakeElapsed:
        def __init__(self, val): self.val = val
        def __call__(self): return self.val
    assert safety_gate_check(FakeElapsed(30.0), 40.0, True) is False
    assert safety_gate_check(FakeElapsed(45.0), 40.0, True) is True
    assert safety_gate_check(FakeElapsed(50.0), 40.0, False) is False
    print("  safety_gate_check: OK")

    print("  Optimization modules: ALL TESTS PASSED")


def test_e2e_budget(max_cases=20):
    print("\n" + "=" * 60)
    print(f"TEST 3: End-to-end budget forcing ({max_cases} cases)")
    print("=" * 60)

    from naie.config.settings import NAIE_CONFIG, THINK_BUDGET_CONFIG_47, print_config
    from naie.evaluation.runner import load_datasets, evaluate_budget, calculate_metrics

    print_config()

    datasets = load_datasets(NAIE_CONFIG['dataset_dir'])
    print(f"  Loaded {len(datasets)} datasets")

    if max_cases > 0:
        trimmed = []
        for ds in datasets:
            trimmed.append({'name': ds['name'], 'data': ds['data'].head(max_cases)})
        datasets = trimmed

    result_dir = os.path.join(os.path.dirname(__file__), 'data', 'naie', 'results_refactor_test')
    os.makedirs(result_dir, exist_ok=True)

    cfg = dict(THINK_BUDGET_CONFIG_47)
    wall_start = time.time()
    results = evaluate_budget(datasets, result_dir, cfg)
    wall_total = time.time() - wall_start

    metrics = calculate_metrics(results)

    print("\n" + "=" * 60)
    print("E2E Budget Result Summary (post-refactoring)")
    print("=" * 60)
    print(f"  Total cases:     {metrics['total']}")
    print(f"  Correct:         {metrics['correct']}")
    print(f"  Accuracy:        {metrics['accuracy']:.2%}")
    print(f"  Avg latency:     {metrics['avg_elapsed_time']:.2f}s")
    print(f"  Wall time:       {wall_total:.1f}s")
    print(f"  Avg think tok:   {metrics['avg_thinking_tokens']:.0f}")
    print(f"  Avg API calls:   {metrics['avg_api_calls']:.1f}")

    accept_reasons = {}
    for r in results:
        ar = r.get('accept_reason_cn', r.get('accept_reason', 'unknown'))
        accept_reasons[ar] = accept_reasons.get(ar, 0) + 1
    print(f"  Accept reasons:  {accept_reasons}")

    failed_cases = [r for r in results if not r['correct']]
    if failed_cases:
        print(f"\n  Failed cases ({len(failed_cases)}):")
        for r in failed_cases:
            vmc = f"{r['value_min_probability']:.2f}" if r.get('value_min_probability') is not None else '-'
            print(f"    Case {r['id']:>3d}: score={r['score']}, think={r['thinking_tokens'] or 0}tok, "
                  f"latency={r['elapsed_time']:.1f}s, accept={r.get('accept_reason_cn','?')}, vmc={vmc}")

    print("=" * 60)

    return metrics


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--skip_e2e', action='store_true', help='Skip expensive e2e test')
    parser.add_argument('--max_cases', type=int, default=20, help='Max cases for e2e test')
    args = parser.parse_args()

    test_token_filter_generic()
    test_optimization_modules()

    if not args.skip_e2e:
        metrics = test_e2e_budget(args.max_cases)
    else:
        print("\n  [SKIPPED] E2E budget test (--skip_e2e)")

    print("\n" + "=" * 60)
    print("ALL TESTS COMPLETE")
    print("=" * 60)
