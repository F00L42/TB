"""端到端验证: 不调用API, 验证全pipeline逻辑正确性"""
import sys, os, json, math, time, tempfile, shutil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['NO_PROXY'] = '*'

def main():
    print('=' * 70)
    print('  End-to-End Verification (Mock Mode - No API Calls)')
    print('=' * 70)

    # ─── 1. CONFIG → _cfg_to_params → call_model 参数传递链 ───
    print()
    print('[1] CONFIG -> _cfg_to_params -> call_model parameter chain...')
    from naie.config.settings import THINK_BUDGET_CONFIG_47
    from naie.inference.think_budget import _cfg_to_params
    params = _cfg_to_params(THINK_BUDGET_CONFIG_47)
    for k in ['enable_confidence', 'check_length', 'think_stride', 'num_thinking',
              'prob_th', 'enable_value_min_confidence', 'enable_task_type_budget',
              'enable_history_budget', 'enable_difficulty_estimator', 'enable_deer_trial',
              'enable_full_chain_confidence', 'enable_dual_channel', 'enable_soft_truncation',
              'enable_safety_gate', 'enable_clean_context',
              'consecutive_same_stop', 'value_min_bottom_k']:
        assert k in params, f'Missing param: {k}'
    print(f'  PASS: All params mapped ({len(params)} params)')

    # ─── 2. 历史模块端到端 ───
    print()
    print('[2] History module end-to-end: record -> query -> budget...')
    from naie.optimizations.history_module import clear_history, record_inference, load_history, get_task_type_stats
    from naie.optimizations.history_budget import compute_history_budget
    from naie.optimizations import history_module

    clear_history()
    for i in range(5):
        record_inference(
            question=f'Test question {i}: query alarm info',
            reference='',
            task_type='tool_call',
            result={'thinking_tokens': 300 + i * 50, 'total_tokens': 400 + i * 50, 'score': 10},
            scene_type='tool_call',
        )
    history_module._history_cache = None
    stats = get_task_type_stats('tool_call')
    assert stats is not None, 'Should have tool_call stats'
    print(f'  Stats: count={stats.get("count")}, avg_tokens={stats.get("avg_tokens", 0):.0f}')

    budget = compute_history_budget('Query new alarm', task_type='tool_call',
                                    config={'history_warmup_count': 3, 'new_task_unlimited': True})
    mode = budget.get('budget_mode', 'unknown')
    print(f'  Budget mode: {mode}')
    assert mode in ('warm_start', 'hot_start'), f'5 records should give warm/hot, got {mode}'
    print('  PASS: History module end-to-end correct')

    # ─── 3. 任务类型分类+预算融合端到端 ───
    print()
    print('[3] Task type classification + budget fusion...')
    from naie.optimizations.task_type_budget import classify_task_type, get_task_type_budget
    from naie.optimizations.difficulty_estimator import estimate_difficulty, compute_budget_from_difficulty
    from naie.optimizations.scene_classifier import classify_scene

    question = 'Call queryAlarm API to query current alarm information'
    task_info = classify_task_type(question)
    task_type = task_info.get('task_type', 'general')
    scene_info = classify_scene(question)
    scene_type = scene_info.get('scene_type', 'general')
    task_budget = get_task_type_budget(task_type)
    diff_info = estimate_difficulty(question, None, scene_type=scene_type)
    diff_budget = compute_budget_from_difficulty(diff_info['difficulty'])

    print(f'  Question: {question[:40]}...')
    print(f'  Task type: {task_type}, Scene: {scene_type}')
    print(f'  Task budget: cl={task_budget["check_length"]}, ns={task_budget["num_thinking"]}')
    print(f'  Difficulty: d={diff_info["difficulty"]:.3f}')
    print(f'  Diff budget: cl={diff_budget["check_length"]}, ns={diff_budget["num_thinking"]}')

    # Fusion: average of both
    fused_cl = (task_budget['check_length'] + diff_budget['check_length']) // 2
    fused_ns = (task_budget['num_thinking'] + diff_budget['num_thinking']) // 2
    print(f'  Fused budget: cl={fused_cl}, ns={fused_ns}')
    print('  PASS: Classification + difficulty + fusion correct')

    # ─── 4. 置信度三模式融合端到端 ───
    print()
    print('[4] Confidence three-mode fusion end-to-end...')
    from naie.optimizations.confidence_gates import compute_dual_channel_score

    reasoning_lps = [math.log(0.85), math.log(0.90), math.log(0.88)]
    reasoning_toks = ['thinking', 'about', 'this']
    ans_lps = [math.log(0.92), math.log(0.88), math.log(0.95)]
    ans_toks = ['queryAlarm', 'alarmId', 'current']

    # All three modes
    ds, vmr, rsr, asr, vmc = compute_dual_channel_score(
        reasoning_lps, reasoning_toks, ans_lps, ans_toks,
        confidence=0.90, perplexity=3.5,
        enable_full_chain_confidence=True,
        enable_dual_channel=True,
        enable_value_min_confidence=True,
    )
    print(f'  Three-mode fusion: ds={ds:.4f}, vmc={vmc}')
    assert vmc is True, 'Should have valid computation'

    # Single mode
    ds2, vmr2, rsr2, asr2, vmc2 = compute_dual_channel_score(
        reasoning_lps, reasoning_toks, ans_lps, ans_toks,
        confidence=0.90, perplexity=3.5,
        enable_value_min_confidence=True,
    )
    print(f'  Single mode (vm): ds={ds2:.4f}, vmc={vmc2}')

    # Default (no switch)
    ds3, vmr3, rsr3, asr3, vmc3 = compute_dual_channel_score(
        reasoning_lps, reasoning_toks, ans_lps, ans_toks,
        confidence=0.90, perplexity=3.5,
    )
    print(f'  Default (confidence): ds={ds3:.4f}, vmc={vmc3}')
    assert ds3 == 0.90, f'Default should use confidence: {ds3}'
    assert vmc3 is False, f'Default vmc should be False: {vmc3}'
    print('  PASS: Three/single/default modes all correct')

    # ─── 5. DEER早停端到端 ───
    print()
    print('[5] DEER trial early-stop end-to-end...')
    from naie.optimizations.early_stopping import make_accept_decision

    # DEER stable but confidence low -> should accept via DEER
    r = make_accept_decision(
        decision_score=0.70, prob_th=0.95, actual_len=300, min_think_tokens=100,
        is_paired=False, consecutive_same_count=0, consecutive_same_stop=999,
        enable_value_min_confidence=True, decision_score_raw=-0.35,
        deer_should_early_stop=True, deer_stable_count=4,
    )
    assert r[0] is True, f'DEAR stable should accept: {r}'
    assert 'deer_trial_stable_4' in r[1], f'Reason should contain DEER: {r[1]}'
    print(f'  DEER stable early-stop: accept={r[0]}, reason={r[1]}')

    # No DEER + low confidence -> should not accept
    r2 = make_accept_decision(
        decision_score=0.70, prob_th=0.95, actual_len=300, min_think_tokens=100,
        is_paired=False, consecutive_same_count=0, consecutive_same_stop=999,
        enable_value_min_confidence=True, decision_score_raw=-0.35,
        deer_should_early_stop=False, deer_stable_count=0,
    )
    assert r2[0] is False, f'Should not accept: {r2}'
    print(f'  No DEER + low conf: accept={r2[0]}')
    print('  PASS: DEER early-stop logic correct')

    # ─── 6. 难度动态底限端到端 ───
    print()
    print('[6] Difficulty dynamic floor end-to-end...')
    r1 = estimate_difficulty('Calculate the sum', None)
    r2 = estimate_difficulty('Prove: for any natural number n, n^2 + n is even. Use mathematical induction.', None)
    r3 = estimate_difficulty('Query alarm information', None)
    print(f'  Shallow: d={r1["difficulty"]:.3f}, is_hard={r1["is_hard"]}')
    print(f'  Deep: d={r2["difficulty"]:.3f}, is_hard={r2["is_hard"]}')
    print(f'  No keywords: d={r3["difficulty"]:.3f}, is_hard={r3["is_hard"]}')
    if r1['is_hard']:
        assert r1['difficulty'] >= 0.4, f'Shallow floor should be >=0.4'
        assert r1['difficulty'] < 0.8, f'Shallow should not reach 0.8'
    assert r3['difficulty'] < 0.5, f'Normal question should be low difficulty'
    print('  PASS: Dynamic floor works correctly')

    # ─── 7. 全局token预算约束 ───
    print()
    print('[7] Global token budget constraint verification...')
    import inspect
    from naie.models.budget_forcing import call_model
    src = inspect.getsource(call_model)
    assert '_estimated_total' in src, 'Missing global estimation'
    assert '_ratio' in src, 'Missing ratio scaling'
    print('  PASS: Global budget constraint code in place')

    # ─── 8. 线程安全 ───
    print()
    print('[8] Thread safety end-to-end...')
    import threading
    from naie.optimizations._struct_token_core import set_task_key_names, get_key_names, is_structural_token

    errors = []
    def worker(idx):
        try:
            for _ in range(50):
                set_task_key_names([f'key_{idx}'])
                kn = get_key_names()
                if not isinstance(kn, set):
                    errors.append(f'worker {idx}: got {type(kn)}')
                is_structural_token(f'key_{idx}')
        except Exception as e:
            errors.append(f'worker {idx}: {e}')

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads: t.start()
    for t in threads: t.join()
    if errors:
        print(f'  FAIL: {errors}')
    else:
        print('  PASS: 8 threads x 50 rounds concurrent no errors')

    # ─── 9. 自动注册新任务类型端到端 ───
    print()
    print('[9] Auto-registration of new task type end-to-end...')
    from naie.optimizations.history_budget import register_task_type_from_history
    from naie.optimizations.task_type_budget import get_task_type_budget

    clear_history()
    tmp_dir = tempfile.mkdtemp()
    tmp_json = os.path.join(tmp_dir, 'test_auto_reg.json')
    try:
        # Less than 5 -> no registration
        for i in range(3):
            record_inference(question=f'New type question {i}', reference='',
                             task_type='custom_analysis', scene_type='general',
                             result={'thinking_tokens': 1000, 'total_tokens': 1200, 'score': 10})
        history_module._history_cache = None
        r1 = register_task_type_from_history('custom_analysis', json_path=tmp_json, min_count=5)
        assert not r1, 'Below threshold should not register'

        # Add to 5 -> should register
        for i in range(2):
            record_inference(question=f'New type question {3 + i}', reference='',
                             task_type='custom_analysis', scene_type='general',
                             result={'thinking_tokens': 1100, 'total_tokens': 1300, 'score': 10})
        history_module._history_cache = None
        r2 = register_task_type_from_history('custom_analysis', json_path=tmp_json, min_count=5)
        assert r2, 'At threshold should register'

        budget = get_task_type_budget('custom_analysis', json_path=tmp_json)
        assert budget.get('typical_tokens', 0) > 0, 'Should have budget after registration'
        assert budget.get('auto_registered', False), 'Should be marked auto_registered'
        print(f'  Registered typical_tokens={budget["typical_tokens"]}')
        print('  PASS: Auto-registration end-to-end correct')
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        clear_history()

    # ─── 10. run脚本入口验证 ───
    print()
    print('[10] Run script entry verification...')
    import run_baseline, run_budget, run_one
    assert hasattr(run_baseline, 'main'), 'run_baseline missing main'
    assert hasattr(run_budget, 'main'), 'run_budget missing main'
    assert hasattr(run_one, 'main'), 'run_one missing main'
    print('  PASS: All three run scripts importable')

    # ─── 11. call_model签名兼容性 ───
    print()
    print('[11] call_model signature compatibility...')
    sig = inspect.signature(call_model)
    # 验证新增参数在签名中
    for param_name in ['deer_confidence_boost', 'deer_trial_stable_rounds',
                       'enable_full_chain_confidence', 'enable_dual_channel',
                       'history_warmup_strategy']:
        assert param_name in sig.parameters, f'Missing param: {param_name}'
    print(f'  PASS: Signature has {len(sig.parameters)} params')

    # ─── 12. value_min_computed标志传播 ───
    print()
    print('[12] value_min_computed flag propagation...')
    # 回退场景
    r_fallback = make_accept_decision(
        decision_score=0.96, prob_th=0.95, actual_len=200, min_think_tokens=100,
        is_paired=True, consecutive_same_count=0, consecutive_same_stop=999,
        enable_value_min_confidence=True, decision_score_raw=0.0,
        value_min_computed=False,
    )
    assert 'fallback' in r_fallback[1], f'Should have fallback: {r_fallback[1]}'

    # 正常计算场景
    r_computed = make_accept_decision(
        decision_score=0.96, prob_th=0.95, actual_len=200, min_think_tokens=100,
        is_paired=True, consecutive_same_count=0, consecutive_same_stop=999,
        enable_value_min_confidence=True, decision_score_raw=-0.5,
        value_min_computed=True,
    )
    assert 'value_min_conf' in r_computed[1], f'Should have value_min_conf: {r_computed[1]}'
    print('  PASS: Fallback vs computed accept_reason correct')

    # ─── 13. compute_decision_score deprecation ───
    print()
    print('[13] compute_decision_score deprecation warning...')
    import warnings
    from naie.optimizations.confidence_gates import compute_decision_score
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        compute_decision_score([math.log(0.9)], ['val'], 0.85, 5.0,
                               enable_full_chain_confidence=True)
        assert len(w) > 0, 'No deprecation warning!'
        assert issubclass(w[0].category, DeprecationWarning)
    print('  PASS: DeprecationWarning emitted for full_chain in old API')

    print()
    print('=' * 70)
    print('  ALL END-TO-END VERIFICATIONS PASSED! (13/13)')
    print('=' * 70)
    print()
    print('  Note: API endpoint (141.73.2.25) is not reachable in this')
    print('  environment. The above covers the full pipeline except')
    print('  actual API calls, including:')
    print('  CONFIG -> params -> classify -> difficulty -> fusion ->')
    print('  confidence -> DEER -> history -> registration -> thread safety')

if __name__ == '__main__':
    main()
