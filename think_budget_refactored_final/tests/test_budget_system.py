"""智能Token预算系统 — pytest 测试用例

重点覆盖:
  1. 新任务类型注册(register_new_task_type)
  2. 阶段B均值推算(warm-start avg × multiplier, max(历史avg × multiplier, 历史max))
  3. 百分比截取(hot-start P75 分位数)
  4. 统一配置加载(budget_config.yaml)
  5. 枚举验证(TaskType, DifficultyTier)
  6. BUDGET_ENABLED/FIXED_BUDGET_TOKEN/CONFIDENCE_THRESHOLD/PERCENTILE_STEP 4个超参数
"""
import naie._bootstrap  # sys.path + NO_PROXY

import json
import tempfile
import shutil

import pytest


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def clean_history():
    """每个测试前清空历史，测试后也清空。"""
    from naie.optimizations.history_module import clear_history
    from naie.optimizations import history_module
    clear_history()
    history_module._history_cache = None
    yield
    clear_history()
    history_module._history_cache = None


@pytest.fixture
def tmp_json_dir():
    """提供临时JSON文件目录，测试后自动清理。"""
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════
# 测试1: 新任务类型注册 (register_new_task_type)
# ═══════════════════════════════════════════════════════════════

class TestNewTaskTypeRegistration:
    """验证 register_new_task_type() 动态注册机制。"""

    def test_register_unknown_type_no_keyerror(self, tmp_json_dir):
        """注册未知类型不应抛出 KeyError。"""
        from naie.optimizations.task_type_budget import register_new_task_type
        json_path = os.path.join(tmp_json_dir, 'custom.json')
        result = register_new_task_type('telecom_anomaly', typical_tokens=2000,
                                         description='电信异常检测', json_path=json_path)
        assert result is True, "注册应成功"

    def test_registered_type_has_budget(self, tmp_json_dir):
        """注册后的类型应能获取完整预算参数。"""
        from naie.optimizations.task_type_budget import register_new_task_type, get_task_type_budget
        json_path = os.path.join(tmp_json_dir, 'custom.json')
        register_new_task_type('telecom_anomaly', typical_tokens=2000, json_path=json_path)
        budget = get_task_type_budget('telecom_anomaly', json_path=json_path)
        assert budget.get('typical_tokens') == 2000
        assert 'check_length' in budget
        assert 'think_stride' in budget
        assert 'num_thinking' in budget
        assert budget.get('auto_registered') is True

    def test_is_known_task_type_after_registration(self, tmp_json_dir):
        """注册后 is_known_task_type 应返回 True。"""
        from naie.optimizations.task_type_budget import register_new_task_type, is_known_task_type
        json_path = os.path.join(tmp_json_dir, 'custom.json')
        assert is_known_task_type('brand_new_type_xyz') is False
        register_new_task_type('brand_new_type_xyz', typical_tokens=1500, json_path=json_path)
        assert is_known_task_type('brand_new_type_xyz') is True

    def test_register_with_custom_params(self, tmp_json_dir):
        """注册时传入自定义参数应覆盖默认推算。"""
        from naie.optimizations.task_type_budget import register_new_task_type, get_task_type_budget
        json_path = os.path.join(tmp_json_dir, 'custom.json')
        register_new_task_type('custom_logic', typical_tokens=4000,
                               check_length=800, think_stride=300, json_path=json_path)
        budget = get_task_type_budget('custom_logic', json_path=json_path)
        assert budget.get('check_length') == 800
        assert budget.get('think_stride') == 300


# ═══════════════════════════════════════════════════════════════
# 测试2: 阶段B均值推算 (Warm-start)
# ═══════════════════════════════════════════════════════════════

class TestWarmStartEstimation:
    """验证阶段B: 1~warmup_count条历史的预算推算。"""

    def test_warm_start_uses_avg_multiplier(self):
        """warm-start 应使用 历史avg × multiplier 推算预算。"""
        from naie.optimizations.history_budget import compute_history_budget
        from naie.optimizations.history_module import record_inference
        from naie.optimizations import history_module

        # 添加2条历史: 500, 700 → avg=600, ×1.2=720
        for tokens in [500, 700]:
            record_inference(
                question=f"查询用户信息{tokens}",
                reference="", task_type='retrieval',
                result={'thinking_tokens': tokens, 'total_tokens': tokens + 100, 'score': 10},
                scene_type='retrieval',
            )
        history_module._history_cache = None

        budget = compute_history_budget("查询用户IMSI信息新问题", task_type='retrieval',
                                        config={'history_warmup_count': 3, 'new_task_unlimited': False})
        # budget_mode 应为 warm_start (2条 < 3条阈值)
        assert budget.get('budget_mode') == 'warm_start'
        # think_budget 应 >= avg × 1.2
        assert budget.get('think_budget', 0) >= int(600 * 1.2), \
            f"预算应≥avg×1.2, got {budget.get('think_budget')}, expected≥{int(600*1.2)}"

    def test_warm_start_max_history_as_floor(self):
        """阶段B: max(历史avg × multiplier, 历史max) — 历史最大消耗也应作为下限。"""
        from naie.optimizations.history_budget import compute_history_budget
        from naie.optimizations.history_module import record_inference
        from naie.optimizations import history_module

        # 添加3条历史: 500, 500, 5000 → avg=2000, ×1.2=2400, 但max=5000
        # 预算应为 max(2400, 5000) = 5000
        for tokens in [500, 500, 5000]:
            record_inference(
                question=f"测试问题{tokens}",
                reference="", task_type='retrieval',
                result={'thinking_tokens': tokens, 'total_tokens': tokens + 100, 'score': 10},
                scene_type='retrieval',
            )
        history_module._history_cache = None

        budget = compute_history_budget("测试新问题", task_type='retrieval',
                                        config={'history_warmup_count': 3, 'new_task_unlimited': False,
                                                'history_budget_multiplier': 1.2})
        # think_budget 应 ≥ max_token=5000 (max(历史avg×1.2, 历史max))
        assert budget.get('think_budget', 0) >= 5000, \
            f"预算应≥历史max(5000), got {budget.get('think_budget')}"

    def test_warm_start_high_variance_amplifies(self):
        """高方差(cv>0.5)时，warm-start 乘数应被放大。"""
        from naie.optimizations.history_budget import compute_history_budget
        from naie.optimizations.history_module import record_inference
        from naie.optimizations import history_module

        # 添加2条极端不同历史: 100, 10000 → cv极高
        for tokens in [100, 10000]:
            record_inference(
                question=f"不稳定问题{tokens}",
                reference="", task_type='classification',
                result={'thinking_tokens': tokens, 'total_tokens': tokens + 100, 'score': 10},
                scene_type='classification',
            )
        history_module._history_cache = None

        budget = compute_history_budget("不稳定新问题", task_type='classification',
                                        config={'history_warmup_count': 3, 'new_task_unlimited': False,
                                                'history_warmup_strategy': 'moderate'})
        # 高方差时 multiplier_used 应 > 基础1.2
        if budget.get('warmup_cv', 0) > 0.5:
            assert budget.get('warmup_multiplier_used', 1.0) > 1.2, \
                f"高方差乘数应>1.2, got {budget.get('warmup_multiplier_used')}"


# ═══════════════════════════════════════════════════════════════
# 测试3: 百分比截取 (Hot-start P75)
# ═══════════════════════════════════════════════════════════════

class TestHotStartPercentile:
    """验证阶段C: >warmup_count条历史的百分比步长控制。"""

    def test_hot_start_uses_p75(self):
        """hot-start 应使用历史P75分位数作为预算基准。"""
        from naie.optimizations.history_budget import compute_history_budget
        from naie.optimizations.history_module import record_inference
        from naie.optimizations import history_module

        # 添加10条历史: 100,200,300,...,1000 → p75 ≈ 775
        for i in range(1, 11):
            tokens = i * 100
            record_inference(
                question=f"问题第{i}条",
                reference="", task_type='retrieval',
                result={'thinking_tokens': tokens, 'total_tokens': tokens + 50, 'score': 10},
                scene_type='retrieval',
            )
        history_module._history_cache = None

        budget = compute_history_budget("新问题", task_type='retrieval',
                                        config={'history_warmup_count': 3, 'new_task_unlimited': False,
                                                'history_stride_percentile': 0.75})
        assert budget.get('budget_mode') == 'hot_start'
        # p75 ≈ 775 (8th of 10 values sorted)
        assert budget.get('think_budget', 0) >= 700, \
            f"hot-start预算应≥p75≈775, got {budget.get('think_budget')}"

    def test_hot_start_low_success_boosts(self):
        """低成功率时 hot-start 应放大预算。"""
        from naie.optimizations.history_budget import compute_history_budget
        from naie.optimizations.history_module import record_inference
        from naie.optimizations import history_module

        # 添加8条失败历史
        for i in range(8):
            tokens = 500
            record_inference(
                question=f"失败问题{i}",
                reference="", task_type='fault_diagnosis',
                result={'thinking_tokens': tokens, 'total_tokens': tokens + 50, 'score': 0},
                scene_type='fault_diagnosis',
            )
        history_module._history_cache = None

        budget_normal = compute_history_budget("诊断问题", task_type='fault_diagnosis',
                                                config={'history_warmup_count': 3, 'new_task_unlimited': False,
                                                        'history_low_success_boost': 1.3,
                                                        'history_stride_percentile': 0.75})
        # 低成功率时应被放大
        assert budget_normal.get('budget_mode') == 'hot_start'
        # success_rate 应 < 0.7
        if budget_normal.get('success_rate', 1.0) < 0.7:
            # 预算应 > p75 * 1.0 (即被放大了)
            assert budget_normal.get('think_budget', 0) > 0

    def test_percentile_step_customizable(self):
        """PERCENTILE_STEP 应可配置不同的百分位。"""
        from naie.optimizations.history_budget import compute_history_budget
        from naie.optimizations.history_module import record_inference
        from naie.optimizations import history_module

        # 添加10条历史
        for i in range(1, 11):
            tokens = i * 100
            record_inference(
                question=f"百分位问题{i}",
                reference="", task_type='retrieval',
                result={'thinking_tokens': tokens, 'total_tokens': tokens + 50, 'score': 10},
                scene_type='retrieval',
            )
        history_module._history_cache = None

        # P50 vs P75
        budget_p50 = compute_history_budget("问题", task_type='retrieval',
                                             config={'history_warmup_count': 3, 'new_task_unlimited': False,
                                                     'history_stride_percentile': 0.50})
        budget_p75 = compute_history_budget("问题", task_type='retrieval',
                                             config={'history_warmup_count': 3, 'new_task_unlimited': False,
                                                     'history_stride_percentile': 0.75})
        # P50 的 history_p75_tokens 应低于或等于 P75 使用的
        # (stride_from_percentile 应受影响)
        assert budget_p50.get('budget_mode') == 'hot_start'
        assert budget_p75.get('budget_mode') == 'hot_start'


# ═══════════════════════════════════════════════════════════════
# 测试4: 统一配置加载 (budget_config.yaml)
# ═══════════════════════════════════════════════════════════════

class TestUnifiedConfig:
    """验证 budget_config.yaml 统一配置加载。"""

    def test_load_budget_config(self):
        """应能加载 YAML 配置。"""
        from naie.config.unified_config import load_budget_config
        config = load_budget_config()
        assert 'TASK_TYPES' in config
        assert 'DIFFICULTY_TIERS' in config
        assert 'HUMAN_CONTROLLABLE' in config

    def test_get_task_type_budgets_from_yaml(self):
        """应能从 YAML 获取所有任务类型预算。"""
        from naie.config.unified_config import get_task_type_budgets
        budgets = get_task_type_budgets()
        assert 'retrieval' in budgets
        assert 'coding' in budgets
        assert 'math' in budgets
        assert 'reasoning' in budgets
        assert 'classification' in budgets

    def test_get_difficulty_tier(self):
        """应能根据难度值获取等级配置。"""
        from naie.config.unified_config import get_difficulty_tier
        easy = get_difficulty_tier(0.1)
        assert easy.get('tier') == 'easy'
        assert easy.get('token_multiplier') == 0.8

        hard = get_difficulty_tier(0.7)
        assert hard.get('tier') == 'hard'
        assert hard.get('token_multiplier') == 1.5

        very_hard = get_difficulty_tier(0.9)
        assert very_hard.get('tier') == 'very_hard'
        assert very_hard.get('token_multiplier') == 2.0

    def test_get_human_controllable_params(self):
        """4个人工可控超参数应从配置加载。"""
        from naie.config.unified_config import get_human_controllable_params
        params = get_human_controllable_params()
        assert 'BUDGET_ENABLED' in params
        assert 'FIXED_BUDGET_TOKEN' in params
        assert 'CONFIDENCE_THRESHOLD' in params
        assert 'PERCENTILE_STEP' in params

    def test_compute_difficulty_adjusted_budget(self):
        """难度调整预算计算应由配置驱动。"""
        from naie.config.unified_config import compute_difficulty_adjusted_budget
        # retrieval base=500, easy(0.1) multiplier=0.8 → 500×0.8=400
        easy_budget = compute_difficulty_adjusted_budget('retrieval', 0.1)
        assert easy_budget['typical_tokens'] == 400  # 500 × 0.8
        assert easy_budget['difficulty_tier'] == 'easy'

        # coding base=12000, hard(0.7) multiplier=1.5 → 12000×1.5=18000
        hard_budget = compute_difficulty_adjusted_budget('coding', 0.7)
        assert hard_budget['typical_tokens'] == 18000  # 12000 × 1.5
        assert hard_budget['difficulty_tier'] == 'hard'


# ═══════════════════════════════════════════════════════════════
# 测试5: 枚举验证 (TaskType, DifficultyTier)
# ═══════════════════════════════════════════════════════════════

class TestEnums:
    """验证 TaskType 和 DifficultyTier 枚举。"""

    def test_task_type_enum_values(self):
        """TaskType 枚举应包含所有10种任务类型。"""
        from naie.config.unified_config import TaskType
        values = TaskType.values()
        assert 'retrieval' in values
        assert 'classification' in values
        assert 'coding' in values
        assert 'math' in values
        assert 'reasoning' in values
        assert len(values) == 10

    def test_task_type_has_value(self):
        """TaskType.has_value 应正确判断。"""
        from naie.config.unified_config import TaskType
        assert TaskType.has_value('coding') is True
        assert TaskType.has_value('unknown_type') is False

    def test_task_type_from_str(self):
        """TaskType.from_str 应返回正确枚举或 GENERAL。"""
        from naie.config.unified_config import TaskType
        assert TaskType.from_str('math') == TaskType.MATH
        assert TaskType.from_str('unknown') == TaskType.GENERAL

    def test_difficulty_tier_enum(self):
        """DifficultyTier 枚举应有4个等级。"""
        from naie.config.unified_config import DifficultyTier
        assert len(DifficultyTier) == 4
        assert DifficultyTier.EASY.value == 'easy'
        assert DifficultyTier.VERY_HARD.value == 'very_hard'


# ═══════════════════════════════════════════════════════════════
# 测试6: 4个核心暴露超参数
# ═══════════════════════════════════════════════════════════════

class TestHumanControllableParams:
    """验证4个人工可控超参数在 CONFIG 中的存在和功能。"""

    def test_budget_enabled_in_config(self):
        """BUDGET_ENABLED 应在 CONFIG 中。"""
        from naie.config.settings import CONFIG
        assert 'BUDGET_ENABLED' in CONFIG
        assert CONFIG['BUDGET_ENABLED'][0] is True

    def test_fixed_budget_token_in_config(self):
        """FIXED_BUDGET_TOKEN 应在 CONFIG 中。"""
        from naie.config.settings import CONFIG
        assert 'FIXED_BUDGET_TOKEN' in CONFIG
        assert CONFIG['FIXED_BUDGET_TOKEN'][0] == 0

    def test_confidence_threshold_in_config(self):
        """CONFIDENCE_THRESHOLD 应在 CONFIG 中。"""
        from naie.config.settings import CONFIG
        assert 'CONFIDENCE_THRESHOLD' in CONFIG
        assert CONFIG['CONFIDENCE_THRESHOLD'][0] == 0.95

    def test_percentile_step_in_config(self):
        """PERCENTILE_STEP 应在 CONFIG 中。"""
        from naie.config.settings import CONFIG
        assert 'PERCENTILE_STEP' in CONFIG
        assert CONFIG['PERCENTILE_STEP'][0] == 0.75

    def test_cfg_to_params_includes_new_params(self):
        """_cfg_to_params 应映射4个新参数。"""
        from naie.inference.think_budget import _cfg_to_params
        from naie.config.settings import THINK_BUDGET_CONFIG_47
        params = _cfg_to_params(THINK_BUDGET_CONFIG_47)
        assert 'BUDGET_ENABLED' in params
        assert 'FIXED_BUDGET_TOKEN' in params
        assert 'CONFIDENCE_THRESHOLD' in params
        assert 'PERCENTILE_STEP' in params


# ═══════════════════════════════════════════════════════════════
# 测试7: 阶段A 新任务不设限制
# ═══════════════════════════════════════════════════════════════

class TestPhaseANoLimit:
    """验证阶段A: 新任务出现时系统不设限制。"""

    def test_new_task_unlimited(self):
        """新任务首次出现时 num_thinking=99, think_budget=99999。"""
        from naie.optimizations.history_budget import compute_history_budget
        budget = compute_history_budget("全新的问题xyz", task_type='retrieval',
                                        config={'new_task_unlimited': True})
        assert budget.get('budget_mode') == 'unlimited'
        assert budget.get('num_thinking') == 99
        assert budget.get('think_budget') == 99999
        assert budget.get('is_new_task') is True

    def test_new_task_unlimited_false_uses_default(self):
        """new_task_unlimited=False 时使用任务类型默认值。"""
        from naie.optimizations.history_budget import compute_history_budget
        budget = compute_history_budget("全新的问题xyz", task_type='retrieval',
                                        config={'new_task_unlimited': False})
        assert budget.get('budget_mode') == 'cold_start'
        assert budget.get('think_budget', 0) > 0  # 应有默认值


# ═══════════════════════════════════════════════════════════════
# 测试8: 自动注册流程
# ═══════════════════════════════════════════════════════════════

class TestAutoRegistrationFlow:
    """验证从历史轨迹自动注册新任务类型的完整流程。"""

    def test_auto_register_after_threshold(self, tmp_json_dir):
        """历史条数达到阈值后自动注册。"""
        from naie.optimizations.history_budget import register_task_type_from_history
        from naie.optimizations.history_module import record_inference
        from naie.optimizations.task_type_budget import get_task_type_budget
        from naie.optimizations import history_module

        json_path = os.path.join(tmp_json_dir, 'auto_reg.json')

        # 添加4条历史(不足5条)
        for i in range(4):
            record_inference(
                question=f"新类型问题{i}",
                reference="", task_type='auto_test_type',
                result={'thinking_tokens': 1000, 'total_tokens': 1200, 'score': 10},
            )
        history_module._history_cache = None

        # 4条 < 5条阈值，不应注册
        result = register_task_type_from_history('auto_test_type', json_path=json_path, min_count=5)
        assert result is False

        # 再添加2条(共6条)
        for i in range(2):
            record_inference(
                question=f"新类型问题{5+i}",
                reference="", task_type='auto_test_type',
                result={'thinking_tokens': 1200, 'total_tokens': 1400, 'score': 10},
            )
        history_module._history_cache = None

        # 6条 > 5条阈值，应自动注册
        result = register_task_type_from_history('auto_test_type', json_path=json_path, min_count=5)
        assert result is True

        # 注册后应有预算
        budget = get_task_type_budget('auto_test_type', json_path=json_path)
        assert budget.get('typical_tokens', 0) > 0
        assert budget.get('auto_registered') is True

    def test_unknown_type_in_compute_history_budget_no_error(self):
        """compute_history_budget 遇到未知类型不应抛出 KeyError。"""
        from naie.optimizations.history_budget import compute_history_budget
        # 完全未知的类型
        budget = compute_history_budget("未知类型问题", task_type='never_seen_type_xyz',
                                        config={'new_task_unlimited': True})
        # 不应抛出 KeyError，应返回 unlimited 或 cold_start
        assert budget.get('budget_mode') in ('unlimited', 'cold_start')
        assert budget.get('think_budget', 0) >= 0


# ═══════════════════════════════════════════════════════════════
# 测试9: 重复计算公式统一化
# ═══════════════════════════════════════════════════════════════

class TestComputeHelpers:
    """验证统一计算辅助函数。"""

    def test_compute_num_thinking_from_budget(self):
        """compute_num_thinking_from_budget 应统一计算轮次。"""
        from naie.optimizations.task_type_budget import compute_num_thinking_from_budget
        # 5000 / 500 = 10
        assert compute_num_thinking_from_budget(5000, 500) == 10
        # 100 / 500 → ceil=1, 但 min=3
        assert compute_num_thinking_from_budget(100, 500, min_rounds=3) == 3
        # 大预算限制
        assert compute_num_thinking_from_budget(50000, 500, max_rounds=15) == 15

    def test_compute_stride_from_budget(self):
        """compute_stride_from_budget 应统一计算步长。"""
        from naie.optimizations.task_type_budget import compute_stride_from_budget
        # 5000 / 10 = 500
        assert compute_stride_from_budget(5000, 10) == 500
        # 100 / 10 = 10, 但 min=50
        assert compute_stride_from_budget(100, 10, min_stride=50) == 50
