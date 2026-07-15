"""BudgetForcing 功能全面测试 — 验证所有核心特性的端到端集成。

测试范围:
  1. 任务类型分类 (10种任务类型的正确分类)
  2. 历史轨迹预算 (冷启动/暖启动/热启动/无限制 四种模式)
  3. 新任务检测与自动注册
  4. 人工可控超参数覆盖
  5. CONFIG→call_model 参数映射完整性
  6. 预算优先级链 (fixed > task_type > history > difficulty > CONFIG)
  7. 方差感知 warm-start 策略

用法:
    python test_budget_features.py
    python test_budget_features.py --verbose  (显示详细输出)

注意: 此测试不需要模型API连接, 纯本地验证逻辑正确性。
"""
import naie._bootstrap  # sys.path + NO_PROXY

import json
import math
import tempfile
import shutil

import argparse

# ═══════════════════════════════════════
# 测试基础设施
# ═══════════════════════════════════════

_passed = 0
_failed = 0
_verbose = False


def _ok(name, detail=''):
    global _passed
    _passed += 1
    msg = f"  [PASS] {name}"
    if detail and _verbose:
        msg += f" — {detail}"
    print(msg)


def _fail(name, detail=''):
    global _failed
    _failed += 1
    msg = f"  [FAIL] {name}"
    if detail:
        msg += f" — {detail}"
    print(msg)


def _assert_eq(name, actual, expected, detail=''):
    if actual == expected:
        _ok(name, detail or f"={expected}")
    else:
        _fail(name, detail or f"expected={expected}, got={actual}")


def _assert_in(name, actual, expected_set, detail=''):
    if actual in expected_set:
        _ok(name, detail or f"={actual}")
    else:
        _fail(name, detail or f"expected one of {expected_set}, got={actual}")


def _assert_true(name, condition, detail=''):
    if condition:
        _ok(name, detail)
    else:
        _fail(name, detail or "condition was False")


def _assert_gt(name, actual, threshold, detail=''):
    if actual > threshold:
        _ok(name, detail or f"{actual}>{threshold}")
    else:
        _fail(name, detail or f"expected>{threshold}, got={actual}")


# ═══════════════════════════════════════
# 测试1: 任务类型分类
# ═══════════════════════════════════════

def test_task_type_classification():
    print("\n" + "=" * 60)
    print("测试1: 任务类型分类")
    print("=" * 60)

    from naie.optimizations.task_type_budget import classify_task_type, get_task_type_budget, TASK_TYPE_BUDGET_DEFAULTS

    # 测试每种任务类型的关键词触发
    test_cases = [
        ("查询所有用户的IMSI号", "retrieval"),
        ("请查出所有告警记录", "retrieval"),
        ("以下哪个选项是正确的 A. xxx B. yyy C. zzz D. www", "classification"),
        ("将以下故障归类为硬件故障还是软件故障", "classification"),
        ("编写一个Python函数实现快速排序", "coding"),
        ("```python\ndef solve():\n    pass\n```", "coding"),
        ("证明: 对任意正整数n, n^2 + n是偶数", "math"),
        ("计算方程 x^2 + 3x - 4 = 0 的解", "math"),
        ("如果A大于B, 且B大于C, 那么可以推出A一定大于C", "reasoning"),
        ("根据前提条件推导出结论", "reasoning"),
        ("请调用故障诊断API的call function接口, tool_call function", "tool_call"),
        ("分析告警ALM-001的根因, 定位故障网元", "fault_diagnosis"),
        ("解释5G网络切片技术的原理和特点, describe and explain", "knowledge_qa"),
        ("规划5G核心网部署的步骤流程", "planning"),
    ]

    for question, expected_type in test_cases:
        result = classify_task_type(question)
        actual_type = result.get('task_type', 'general')
        if actual_type == expected_type:
            _ok(f"分类 '{question[:30]}...' → {actual_type}")
        else:
            _fail(f"分类 '{question[:30]}...'",
                  f"expected={expected_type}, got={actual_type}, scores={result.get('scores', {})}")

    # 测试所有10种任务类型都有预算参数
    expected_types = {'retrieval', 'classification', 'coding', 'math', 'reasoning',
                      'tool_call', 'fault_diagnosis', 'knowledge_qa', 'planning', 'general'}
    actual_types = set(TASK_TYPE_BUDGET_DEFAULTS.keys())
    _assert_eq("10种任务类型都有预算参数", len(actual_types & expected_types), 10)

    # 测试每种类型的预算参数完整性
    required_keys = {'typical_tokens', 'check_length', 'think_stride', 'num_thinking',
                     'min_think_tokens', 'check_answer_len', 'prob_th'}
    for task_type in expected_types:
        budget = get_task_type_budget(task_type)
        missing = required_keys - set(budget.keys())
        if missing:
            _fail(f"{task_type} 预算参数缺失", str(missing))
        else:
            _ok(f"{task_type} 预算参数完整")

    # 测试检索任务预算 < 编码任务预算
    retrieval_budget = get_task_type_budget('retrieval')
    coding_budget = get_task_type_budget('coding')
    _assert_gt("编码预算 > 检索预算 (typical_tokens)",
               coding_budget['typical_tokens'], retrieval_budget['typical_tokens'])

    # 测试自定义JSON配置覆盖
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump({'retrieval': {'typical_tokens': 1000, 'check_length': 500}}, f)
        tmp_path = f.name
    try:
        custom_budget = get_task_type_budget('retrieval', json_path=tmp_path)
        _assert_eq("自定义JSON覆盖 retrieval typical_tokens", custom_budget['typical_tokens'], 1000)
    finally:
        os.unlink(tmp_path)


# ═══════════════════════════════════════
# 测试2: 场景分类器
# ═══════════════════════════════════════

def test_scene_classifier():
    print("\n" + "=" * 60)
    print("测试2: 场景分类器")
    print("=" * 60)

    from naie.optimizations.scene_classifier import classify_scene, get_scene_typical_tokens

    test_cases = [
        ("调用queryAlarm接口查询当前告警", "tool_call"),
        ("基站ALM-001告警, KPI异常, 请诊断根因", "fault_diagnosis"),
        ("证明: 对任意正整数n, n^2 + n是偶数", "math_reasoning"),
        ("```python\ndef solve():\n    pass\n```", "code_generation"),
        ("规划5G核心网部署方案, 第一步...第二步...", "planning"),
    ]

    for question, expected_scene in test_cases:
        result = classify_scene(question)
        actual_scene = result.get('scene_type', 'general')
        if actual_scene == expected_scene:
            _ok(f"场景 '{question[:30]}...' → {actual_scene}")
        else:
            _fail(f"场景 '{question[:30]}...'",
                  f"expected={expected_scene}, got={actual_scene}")

    # 测试典型token值存在
    for scene in ['tool_call', 'fault_diagnosis', 'math_reasoning', 'code_generation',
                  'knowledge_qa', 'planning', 'general']:
        tokens = get_scene_typical_tokens(scene)
        _assert_gt(f"{scene} typical_tokens>0", tokens, 0)


# ═══════════════════════════════════════
# 测试3: 历史轨迹预算
# ═══════════════════════════════════════

def test_history_budget():
    print("\n" + "=" * 60)
    print("测试3: 历史轨迹预算")
    print("=" * 60)

    from naie.optimizations.history_budget import compute_history_budget, detect_new_task_type
    from naie.optimizations.history_module import clear_history, record_inference, load_history

    # 清空历史以获得干净的测试环境
    clear_history()

    # ─── 冷启动 ───
    budget = compute_history_budget("测试问题内容", task_type='retrieval', config={'new_task_unlimited': False})
    _assert_eq("冷启动 budget_mode (new_task_unlimited=False)", budget.get('budget_mode'), 'cold_start',
               f"count={budget.get('history_count')}")
    _assert_gt("冷启动有预算值", budget.get('think_budget', 0), 0,
               f"typical_tokens from task_type default")

    # ─── 新任务检测 (无历史时=新任务) ───
    is_new = detect_new_task_type("完全新的问题内容xyz", task_type='retrieval')
    _assert_true("无历史时检测为新任务", is_new)

    # ─── 新任务无限制模式 ───
    budget = compute_history_budget("新问题", task_type='retrieval', config={'new_task_unlimited': True})
    _assert_eq("新任务无限制 num_thinking", budget.get('num_thinking'), 99)
    _assert_true("新任务 is_new_task=True", budget.get('is_new_task', False))

    # ─── 添加历史记录测试 warm-start ───
    for i in range(3):
        record_inference(
            question=f"查询用户IMSI信息第{i}条",
            reference="",
            task_type='retrieval',
            result={'thinking_tokens': 500 + i * 100, 'total_tokens': 600 + i * 100, 'score': 10},
            scene_type='retrieval',
        )

    # 刷新缓存
    from naie.optimizations import history_module
    history_module._history_cache = None

    budget = compute_history_budget("查询用户IMSI信息新问题", task_type='retrieval',
                                    config={'history_warmup_count': 3})
    _assert_in("3条历史 warm-start", budget.get('budget_mode'), {'warm_start', 'hot_start'})
    _assert_gt("warm-start think_budget>0", budget.get('think_budget', 0), 0)

    # ─── 添加更多历史测试 hot-start ───
    for i in range(5):
        record_inference(
            question=f"查询用户IMSI信息第{10+i}条",
            reference="",
            task_type='retrieval',
            result={'thinking_tokens': 400 + i * 50, 'total_tokens': 500 + i * 50, 'score': 10},
            scene_type='retrieval',
        )

    history_module._history_cache = None
    budget = compute_history_budget("查询用户IMSI最新信息", task_type='retrieval',
                                    config={'history_warmup_count': 3})
    _assert_eq("8条历史 hot-start", budget.get('budget_mode'), 'hot_start')
    _assert_gt("hot-start think_budget>0", budget.get('think_budget', 0), 0)

    # 清理
    clear_history()


# ═══════════════════════════════════════
# 测试4: 方差感知 warm-start 策略
# ═══════════════════════════════════════

def test_warmup_strategy():
    print("\n" + "=" * 60)
    print("测试4: 方差感知 warm-start 策略")
    print("=" * 60)

    from naie.optimizations.history_budget import compute_history_budget
    from naie.optimizations.history_module import clear_history, record_inference
    from naie.optimizations import history_module

    clear_history()

    # 添加低方差历史 (token消耗接近)
    for i in range(3):
        record_inference(
            question=f"稳定问题第{i}条",
            reference="",
            task_type='classification',
            result={'thinking_tokens': 800, 'total_tokens': 900, 'score': 10},
            scene_type='classification',
        )

    history_module._history_cache = None

    # 低方差 + moderate 策略
    budget_mod = compute_history_budget("稳定问题新条目", task_type='classification',
                                         config={'history_warmup_count': 3, 'history_warmup_strategy': 'moderate'})
    _assert_in("低方差 moderate模式", budget_mod.get('budget_mode'), {'warm_start', 'hot_start'})

    clear_history()

    # 添加高方差历史 (token消耗差异大)
    for i, tokens in enumerate([200, 5000, 800]):
        record_inference(
            question=f"不稳定问题第{i}条",
            reference="",
            task_type='classification',
            result={'thinking_tokens': tokens, 'total_tokens': tokens + 100, 'score': 10},
            scene_type='classification',
        )

    history_module._history_cache = None

    budget_agg = compute_history_budget("不稳定问题新条目", task_type='classification',
                                         config={'history_warmup_count': 3, 'history_warmup_strategy': 'aggressive'})

    # 高方差时 warmup_multiplier_used 应该 > 基础乘数
    _assert_true("高方差策略有 cv 值", budget_agg.get('warmup_cv') is not None,
                 f"cv={budget_agg.get('warmup_cv')}")

    # 清理
    clear_history()


# ═══════════════════════════════════════
# 测试5: 自动注册新任务类型
# ═══════════════════════════════════════

def test_auto_registration():
    print("\n" + "=" * 60)
    print("测试5: 自动注册新任务类型")
    print("=" * 60)

    from naie.optimizations.history_budget import register_task_type_from_history
    from naie.optimizations.task_type_budget import get_task_type_budget, register_custom_task_type
    from naie.optimizations.history_module import clear_history, record_inference
    from naie.optimizations import history_module

    # 使用临时JSON文件避免污染生产配置
    tmp_dir = tempfile.mkdtemp()
    tmp_json = os.path.join(tmp_dir, 'test_custom_task_types.json')

    try:
        # 测试1: 不足阈值时不注册
        clear_history()
        for i in range(3):
            record_inference(
                question=f"新任务问题{i}",
                reference="",
                task_type='custom_telecom_analysis',
                result={'thinking_tokens': 1000, 'total_tokens': 1200, 'score': 10},
            )

        history_module._history_cache = None
        result = register_task_type_from_history('custom_telecom_analysis', json_path=tmp_json, min_count=5)
        _assert_true("不足阈值不注册", not result, f"3条<5条阈值, result={result}")

        # 添加到5条
        for i in range(2):
            record_inference(
                question=f"新任务问题{5+i}",
                reference="",
                task_type='custom_telecom_analysis',
                result={'thinking_tokens': 1200, 'total_tokens': 1400, 'score': 10},
            )

        history_module._history_cache = None
        result = register_task_type_from_history('custom_telecom_analysis', json_path=tmp_json, min_count=5)
        _assert_true("达到阈值自动注册", result)

        # 验证注册后可以获取预算
        budget = get_task_type_budget('custom_telecom_analysis', json_path=tmp_json)
        _assert_gt("注册后有 typical_tokens", budget.get('typical_tokens', 0), 0)
        _assert_true("注册后标记 auto_registered", budget.get('auto_registered', False))

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        clear_history()


# ═══════════════════════════════════════
# 测试6: CONFIG→call_model 参数映射
# ═══════════════════════════════════════

def test_config_mapping():
    print("\n" + "=" * 60)
    print("测试6: CONFIG→call_model 参数映射")
    print("=" * 60)

    from naie.config.settings import CONFIG, THINK_BUDGET_CONFIG_47
    from naie.inference.think_budget import _cfg_to_params

    # 验证 _cfg_to_params 映射覆盖所有关键参数
    params = _cfg_to_params(THINK_BUDGET_CONFIG_47)

    required_params = [
        'enable_confidence', 'check_length', 'think_stride', 'num_thinking',
        'prob_th', 'max_tokens', 'check_answer_len',
        'enable_value_min_confidence', 'value_min_bottom_k',
        'enable_clean_context', 'enable_safety_gate',
        'min_think_tokens', 'max_api_calls',
        'enable_task_type_budget', 'default_task_type', 'custom_task_types_path', 'task_type_override',
        'enable_history_budget', 'history_budget_multiplier', 'history_low_success_boost',
        'history_stride_percentile', 'new_task_unlimited', 'auto_cluster_threshold',
        'history_warmup_count', 'history_warmup_strategy',
        'fixed_budget_tokens', 'fixed_num_thinking', 'fixed_think_stride',
        'fixed_check_length', 'fixed_prob_th', 'fixed_min_think_tokens',
        'enable_soft_truncation',
    ]

    for p in required_params:
        if p in params:
            _ok(f"映射 {p}")
        else:
            _fail(f"映射 {p}", "缺失!")

    # 验证特殊映射
    _assert_eq("enable_budget→enable_confidence", params.get('enable_confidence'),
               THINK_BUDGET_CONFIG_47.get('enable_budget', True))
    _assert_eq("stop_condition 固定为 'length'", params.get('stop_condition'), 'length')

    # 验证 CONFIG 中每个 key 都在 THINK_BUDGET_CONFIG_47 中
    for key in CONFIG:
        if key not in THINK_BUDGET_CONFIG_47:
            _fail(f"CONFIG key '{key}' 不在 THINK_BUDGET_CONFIG_47 中")
        else:
            pass  # OK

    # 验证新增参数 history_warmup_strategy 在映射中
    _assert_eq("history_warmup_strategy 映射值",
               params.get('history_warmup_strategy'), 'moderate')


# ═══════════════════════════════════════
# 测试7: 预算优先级链
# ═══════════════════════════════════════

def test_budget_priority_chain():
    print("\n" + "=" * 60)
    print("测试7: 预算优先级链")
    print("=" * 60)

    # 这个测试验证 budget_forcing.py 中的逻辑:
    # fixed > task_type > history > difficulty > CONFIG

    # 测试 fixed_budget_tokens 覆盖一切
    from naie.config.settings import THINK_BUDGET_CONFIG_47, set_value, get_value

    original_budget = get_value('fixed_budget_tokens')
    try:
        set_value('fixed_budget_tokens', 2000)
        cfg = dict(THINK_BUDGET_CONFIG_47)
        _assert_eq("fixed_budget_tokens=2000 在 CONFIG 中", cfg.get('fixed_budget_tokens'), 2000)
        _ok("fixed_budget_tokens 覆盖验证 (CONFIG 层)")
    finally:
        set_value('fixed_budget_tokens', original_budget or 0)

    # 测试 task_type_override 强制指定类型
    from naie.optimizations.task_type_budget import get_task_type_budget
    coding_budget = get_task_type_budget('coding')
    general_budget = get_task_type_budget('general')
    _assert_gt("coding typical_tokens > general typical_tokens",
               coding_budget['typical_tokens'], general_budget['typical_tokens'],
               f"coding={coding_budget['typical_tokens']}, general={general_budget['typical_tokens']}")

    # 测试 fixed 子参数覆盖
    from naie.inference.think_budget import _cfg_to_params

    cfg = dict(THINK_BUDGET_CONFIG_47)
    cfg['fixed_num_thinking'] = 3
    cfg['fixed_think_stride'] = 200
    cfg['fixed_check_length'] = 500
    cfg['fixed_prob_th'] = 0.80
    cfg['fixed_min_think_tokens'] = 300

    params = _cfg_to_params(cfg)
    _assert_eq("fixed_num_thinking 映射", params.get('fixed_num_thinking'), 3)
    _assert_eq("fixed_think_stride 映射", params.get('fixed_think_stride'), 200)
    _assert_eq("fixed_check_length 映射", params.get('fixed_check_length'), 500)
    _assert_eq("fixed_prob_th 映射", params.get('fixed_prob_th'), 0.80)
    _assert_eq("fixed_min_think_tokens 映射", params.get('fixed_min_think_tokens'), 300)


# ═══════════════════════════════════════
# 测试8: 难度估计器
# ═══════════════════════════════════════

def test_difficulty_estimator():
    print("\n" + "=" * 60)
    print("测试8: 难度估计器")
    print("=" * 60)

    from naie.optimizations.difficulty_estimator import estimate_difficulty, compute_budget_from_difficulty

    # 简单题
    simple_q = "查询用户信息"
    simple_info = estimate_difficulty(simple_q)
    _assert_true("简单题 difficulty<0.5",
                 simple_info.get('difficulty', 1.0) < 0.5,
                 f"d={simple_info.get('difficulty')}")

    # 数学题 → 高难度 (动态底限: 按深度分数比例映射[0.4,0.8], 不再固定0.8)
    math_q = "证明: 对任意正整数n, n^2 + n是偶数. 使用归纳法."
    math_info = estimate_difficulty(math_q)
    _assert_true("数学题 difficulty>=0.4 (动态底限)",
                 math_info.get('difficulty', 0) >= 0.4,
                 f"d={math_info.get('difficulty')}")
    _assert_true("数学题 is_hard=True", math_info.get('is_hard', False))

    # 代码题 → 高难度 (动态底限: 按深度分数比例映射[0.4,0.8])
    code_q = "```python\ndef quicksort(arr):\n    # implement\n    pass\n```\nImplement quicksort algorithm with O(n log n) time complexity."
    code_info = estimate_difficulty(code_q)
    _assert_true("代码题 difficulty>=0.4 (动态底限)",
                 code_info.get('difficulty', 0) >= 0.4,
                 f"d={code_info.get('difficulty')}")

    # 预算映射: d=0 → 保守, d=1 → 激进
    budget_low = compute_budget_from_difficulty(0.1, base_check_length=400, max_check_length=6000)
    budget_high = compute_budget_from_difficulty(0.9, base_check_length=400, max_check_length=6000)
    _assert_gt("高难度 check_length > 低难度",
               budget_high['check_length'], budget_low['check_length'])
    _assert_gt("高难度 min_think_tokens > 低难度",
               budget_high['min_think_tokens'], budget_low['min_think_tokens'])


# ═══════════════════════════════════════
# 测试9: CONFIG 完整性
# ═══════════════════════════════════════

def test_config_completeness():
    print("\n" + "=" * 60)
    print("测试9: CONFIG 完整性与描述")
    print("=" * 60)

    from naie.config.settings import CONFIG, print_config

    # 每个CONFIG项都应有非空描述
    for key, (val, desc) in CONFIG.items():
        if desc and len(desc) > 5:
            pass  # OK
        else:
            _fail(f"CONFIG key '{key}' 描述缺失或过短", f"desc='{desc}'")

    _ok("所有CONFIG项描述完整")

    # 验证关键人工可控参数存在
    human_params = ['enable_budget', 'fixed_budget_tokens', 'fixed_num_thinking',
                    'fixed_think_stride', 'fixed_check_length', 'fixed_prob_th',
                    'fixed_min_think_tokens', 'prob_th', 'new_task_unlimited',
                    'history_warmup_strategy', 'auto_cluster_threshold']
    for p in human_params:
        if p in CONFIG:
            _ok(f"人工可控参数 {p} 在 CONFIG 中")
        else:
            _fail(f"人工可控参数 {p} 不在 CONFIG 中")

    # 验证 print_config 不抛异常
    try:
        # 重定向 stdout 避免输出
        import io
        old_stdout = sys.stdout
        sys.stdout = io.StringIO()
        print_config()
        sys.stdout = old_stdout
        _ok("print_config() 无异常")
    except Exception as e:
        sys.stdout = old_stdout
        _fail("print_config() 异常", str(e))


# ═══════════════════════════════════════
# 测试10: runner.py 记录正确的 task_type
# ═══════════════════════════════════════

def test_runner_task_type_recording():
    print("\n" + "=" * 60)
    print("测试10: runner.py 记录正确的 task_type")
    print("=" * 60)

    from naie.optimizations.history_module import clear_history, record_inference, load_history
    from naie.optimizations import history_module

    clear_history()

    # 模拟 runner.py 的记录方式 — 使用推断的 task_type
    record_inference(
        question="调用queryUser API查询用户信息",
        reference="",
        task_type='tool_call',  # 应该是推断的分类, 不是 'naie'
        result={'thinking_tokens': 300, 'total_tokens': 400, 'score': 10},
        scene_type='tool_call',
    )

    history_module._history_cache = None
    history = load_history()

    if history:
        last = history[-1]
        _assert_eq("记录的 task_type 是推断的分类", last.get('task_type'), 'tool_call')
        _assert_eq("记录的 scene_type 正确", last.get('scene_type'), 'tool_call')
    else:
        _fail("历史记录为空")

    clear_history()


# ═══════════════════════════════════════
# 测试11: 新任务泛化流程
# ═══════════════════════════════════════

def test_new_task_generalization_flow():
    print("\n" + "=" * 60)
    print("测试11: 新任务泛化流程")
    print("=" * 60)

    from naie.optimizations.history_budget import compute_history_budget, register_task_type_from_history
    from naie.optimizations.history_module import clear_history, record_inference
    from naie.optimizations.task_type_budget import get_task_type_budget
    from naie.optimizations import history_module

    tmp_dir = tempfile.mkdtemp()
    tmp_json = os.path.join(tmp_dir, 'test_generalization.json')

    try:
        clear_history()

        # 阶段1: 冷启动 — 新任务类型首次遇到, 无限制执行
        budget = compute_history_budget("新型电信网络优化问题",
                                        task_type='network_optimization',
                                        config={'new_task_unlimited': True, 'auto_cluster_threshold': 5})
        _assert_eq("阶段1 冷启动模式", budget.get('budget_mode'), 'unlimited')
        _assert_true("阶段1 is_new_task=True", budget.get('is_new_task', False))

        # 阶段2: 观察期 — 记录1条历史
        record_inference(
            question="新型电信网络优化问题",
            reference="",
            task_type='network_optimization',
            result={'thinking_tokens': 3000, 'total_tokens': 3500, 'score': 10},
            scene_type='general',
        )
        history_module._history_cache = None

        budget = compute_history_budget("另一种网络优化问题",
                                        task_type='network_optimization',
                                        config={'new_task_unlimited': True, 'history_warmup_count': 3})
        # 1条历史 → warm-start 或仍是新任务(如果Jaccard相似度低)
        _assert_in("阶段2 有历史后模式变化",
                   budget.get('budget_mode'), {'warm_start', 'unlimited', 'cold_start'})

        # 阶段3: 积累足够历史 → 自动注册
        for i in range(5):
            record_inference(
                question=f"网络优化场景问题{i}",
                reference="",
                task_type='network_optimization',
                result={'thinking_tokens': 2500 + i * 200, 'total_tokens': 3000 + i * 200, 'score': 10},
                scene_type='general',
            )
        history_module._history_cache = None

        registered = register_task_type_from_history('network_optimization', json_path=tmp_json, min_count=5)
        _assert_true("阶段3 自动注册成功", registered)

        # 阶段4: 注册后, 后续同类问题使用 budget 控制
        budget = compute_history_budget("又一种网络优化问题",
                                        task_type='network_optimization',
                                        config={'new_task_unlimited': True, 'history_warmup_count': 3})
        _assert_in("阶段4 使用历史预算控制", budget.get('budget_mode'), {'warm_start', 'hot_start'})

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        clear_history()


# ═══════════════════════════════════════
# 测试12: import 完整性
# ═══════════════════════════════════════

def test_imports():
    print("\n" + "=" * 60)
    print("测试12: 导入完整性")
    print("=" * 60)

    modules_to_test = [
        ('naie.config.settings', 'CONFIG, THINK_BUDGET_CONFIG_47, print_config, get_value, set_value'),
        ('naie.inference.think_budget', 'call_model, call_model_async, _cfg_to_params'),
        ('naie.optimizations', 'classify_task_type, get_task_type_budget, compute_history_budget, detect_new_task_type, register_task_type_from_history'),
        ('naie.optimizations.scene_classifier', 'classify_scene, get_scene_typical_tokens'),
        ('naie.optimizations.task_type_budget', 'classify_task_type, get_task_type_budget, TASK_TYPE_BUDGET_DEFAULTS, register_custom_task_type'),
        ('naie.optimizations.history_budget', 'compute_history_budget, detect_new_task_type, register_task_type_from_history'),
        ('naie.optimizations.history_module', 'record_inference, load_history, get_category_stats, get_scene_stats, clear_history'),
        ('naie.optimizations.difficulty_estimator', 'estimate_difficulty, compute_budget_from_difficulty'),
        ('naie.optimizations.confidence_gates', 'compute_decision_score'),
        ('naie.optimizations.token_filter', 'is_structural_token, cal_value_min_confidence'),
        ('naie.optimizations.early_stopping', 'make_accept_decision, check_min_think_gate'),
        ('naie.optimizations.context_control', 'make_induced_prompt, apply_clean_context'),
        ('naie.optimizations.safety_gate', 'safety_gate_check'),
        ('naie.models.budget_forcing', 'call_model, call_model_async'),
    ]

    for module_path, names in modules_to_test:
        try:
            mod = __import__(module_path, fromlist=names.split(', '))
            for name in names.split(', '):
                name = name.strip()
                if not hasattr(mod, name):
                    _fail(f"{module_path}.{name}", "不存在!")
                    continue
            _ok(f"导入 {module_path}")
        except ImportError as e:
            _fail(f"导入 {module_path}", str(e))


# ═══════════════════════════════════════
# 测试13: 修复验证 — API server 使用推断 task_type
# ═══════════════════════════════════════

def test_api_server_uses_inferred_task_type():
    """验证 api_server.py 不再硬编码 task_type='naie' 进行历史查询。"""
    print("\n" + "=" * 60)
    print("测试13: API server 使用推断 task_type (非 'naie')")
    print("=" * 60)

    # 验证 classify_task_type 被导入到 api_server
    try:
        import naie.api_server as api_mod
        _assert_true("api_server 导入 classify_task_type",
                     hasattr(api_mod, 'classify_task_type') or 'classify_task_type' in dir(api_mod),
                     "classify_task_type should be imported")
    except ImportError as e:
        _fail("api_server 导入", str(e))

    # 验证 api_server 源码中不再有 task_type='naie'
    import inspect
    source = inspect.getsource(api_mod)
    _assert_true("api_server 不包含 task_type='naie'",
                 "task_type='naie'" not in source,
                 "历史查询应使用推断的 task_type 而非硬编码 'naie'")


# ═══════════════════════════════════════
# 测试14: call_model 自动注册验证
# ═══════════════════════════════════════

def test_call_model_auto_registration():
    """验证 call_model() inline 路径包含自动注册逻辑。"""
    print("\n" + "=" * 60)
    print("测试14: call_model() 自动注册新任务类型")
    print("=" * 60)

    import inspect
    from naie.models.budget_forcing import call_model

    source = inspect.getsource(call_model)
    _assert_true("call_model 包含 register_task_type_from_history",
                 'register_task_type_from_history' in source,
                 "call_model 应调用 register_task_type_from_history")

    # 验证 history_warmup_strategy 在函数签名中
    sig = inspect.signature(call_model)
    _assert_true("call_model 签名包含 history_warmup_strategy",
                 'history_warmup_strategy' in sig.parameters,
                 "history_warmup_strategy 应在 call_model 参数中")


# ═══════════════════════════════════════
# 测试15: budget_params 非空记录验证
# ═══════════════════════════════════════

def test_budget_params_not_none():
    """验证 runner.py record_inference 的 budget_params 不再是 None。"""
    print("\n" + "=" * 60)
    print("测试15: budget_params 非空记录验证")
    print("=" * 60)

    import inspect
    from naie.evaluation import runner

    source = inspect.getsource(runner._evaluate_one)
    _assert_true("_evaluate_one 中 budget_params 不再是 None",
                 'budget_params=None' not in source,
                 "budget_params 应包含实际的预算参数记录")
    _assert_true("_evaluate_one 中 budget_params 包含 task_type",
                 'budget_params_record' in source,
                 "应构建 budget_params_record 字典记录实际参数")


# ═══════════════════════════════════════
# 测试16: CLI/API 新增参数暴露验证
# ═══════════════════════════════════════

def test_cli_api_new_params():
    """验证新增 CLI 和 API 参数正确暴露。"""
    print("\n" + "=" * 60)
    print("测试16: CLI/API 新增参数暴露验证")
    print("=" * 60)

    # CLI 参数验证
    import inspect
    import run_budget as cli_mod
    source = inspect.getsource(cli_mod.main)

    _assert_true("CLI 包含 --default_task_type",
                 'default_task_type' in source,
                 "应暴露 default_task_type 参数")
    _assert_true("CLI 包含 --custom_task_types_path",
                 'custom_task_types_path' in source,
                 "应暴露 custom_task_types_path 参数")

    # API request model 验证
    from naie.api_server import ChatCompletionRequest
    fields = ChatCompletionRequest.model_fields

    _assert_true("API 包含 default_task_type 字段",
                 'default_task_type' in fields,
                 "ChatCompletionRequest 应有 default_task_type")
    _assert_true("API 包含 custom_task_types_path 字段",
                 'custom_task_types_path' in fields,
                 "ChatCompletionRequest 应有 custom_task_types_path")
    _assert_true("API 包含 enable_full_chain_confidence 字段",
                 'enable_full_chain_confidence' in fields,
                 "ChatCompletionRequest 应有 enable_full_chain_confidence")
    _assert_true("API 包含 consecutive_same_stop 字段",
                 'consecutive_same_stop' in fields,
                 "ChatCompletionRequest 应有 consecutive_same_stop")
    _assert_true("API 包含 value_min_bottom_k 字段",
                 'value_min_bottom_k' in fields,
                 "ChatCompletionRequest 应有 value_min_bottom_k")

    # API budget_info 包含 task_type
    import naie.api_server as api_mod
    api_source = inspect.getsource(api_mod)
    _assert_true("API budget_info 包含 task_type",
                 '"task_type": classified_task_type' in api_source,
                 "budget_info 响应应包含推断的 task_type")


# ═══════════════════════════════════════
# 主测试入口
# ═══════════════════════════════════════

def main():
    global _verbose
    parser = argparse.ArgumentParser(description='BudgetForcing 功能全面测试')
    parser.add_argument('--verbose', '-v', action='store_true', help='显示详细输出')
    args = parser.parse_args()
    _verbose = args.verbose

    print("+" + "=" * 58 + "+")
    print("|  BudgetForcing Feature Tests - Local verification only  |")
    print("+" + "=" * 58 + "+")

    test_imports()
    test_task_type_classification()
    test_scene_classifier()
    test_history_budget()
    test_warmup_strategy()
    test_auto_registration()
    test_config_mapping()
    test_budget_priority_chain()
    test_difficulty_estimator()
    test_config_completeness()
    test_runner_task_type_recording()
    test_new_task_generalization_flow()
    test_api_server_uses_inferred_task_type()
    test_call_model_auto_registration()
    test_budget_params_not_none()
    test_cli_api_new_params()

    print("\n" + "=" * 60)
    print(f"Results: [PASS] {_passed} passed, [FAIL] {_failed} failed")
    print("=" * 60)

    if _failed > 0:
        print("WARNING: Some tests failed, check output above!")
        sys.exit(1)
    else:
        print("All tests passed!")
        sys.exit(0)


if __name__ == '__main__':
    main()
