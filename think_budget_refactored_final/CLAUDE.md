# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Think Budget Refactored is a Python research framework for **BudgetForcing** — an adaptive thinking-budget mechanism that controls how many reasoning tokens an LLM consumes before producing an answer. The framework evaluates tool-calling accuracy (select tool + fill parameters) on telecom operations datasets, comparing baseline (free-form generation) against budget (BudgetForcing with confidence-gated early stopping).

The core innovation is **Value Min Confidence (P3.1)**: filtering structural tokens (JSON braces, key names) from logprob averaging so only value tokens determine whether the model is confident enough to stop thinking.

## Running Commands

All commands run from `think_budget_refactored/`. Activate venv first: `C:\Users\r50058444\Desktop\work\workspace\.venv\Scripts\activate` (Python 3.12).

```
# Quick verification
python tests/test_single_case.py

# Comprehensive feature test (no API needed)
python tests/test_budget_features.py

# Evaluations
python run_baseline.py --max_cases 5
python run_budget.py --max_cases 5 --prob_th 0.95
python run_per_dataset.py --max_cases 5
python run_compare.py

# Single-case debug with detailed confidence output
python run_one.py

# Ablation experiments
python run_ablation.py

# API server (use naie/api_server.py — the production version with full CONFIG mapping)
python naie/api_server.py --port 8000          # or: uvicorn naie.api_server:app --host 0.0.0.0 --port 8000

# Config verification
python -c "import sys;sys.path.insert(0,'.');from naie.config.settings import print_config;print_config()"
python -c "import sys;sys.path.insert(0,'.');from naie.optimizations import *;from naie.optimizations.history_module import get_task_type_stats;print('ALL OK')"
```

Key CLI parameters for `run_budget.py` (all optional, override CONFIG defaults):
- `--max_cases N`, `--check_length N`, `--think_stride N`, `--num_thinking N`
- `--prob_th FLOAT`, `--min_think_tokens N`, `--max_api_calls N`, `--baseline_time_limit FLOAT`
- Switches: `--enable_budget on|off`, `--enable_value_min_confidence on|off`, `--enable_clean_context on|off`, `--enable_safety_gate on|off`, `--enable_soft_truncation on|off`

Task Type & History Budget:
- `--enable_task_type_budget on|off` — 启用任务类型判断(默认ON)
- `--task_type NAME` — 手动覆盖任务类型(retrieval/coding/math/reasoning/classification/tool_call/fault_diagnosis/knowledge_qa/planning/general)
- `--default_task_type NAME` — 默认任务类型(默认general)
- `--custom_task_types_path PATH` — 自定义任务类型配置路径(None=内置默认)
- `--enable_history_budget on|off` — 启用历史轨迹预算(默认ON)
- `--new_task_unlimited on|off` — 新任务类型不设限制(默认ON)
- `--history_warmup_strategy STR` — warm-start策略: conservative(1.5x)/moderate(1.2x)/aggressive(1.0x)
- `--auto_cluster_threshold N` — 自动聚类新任务类型的历史条数阈值(默认5)
- `--history_budget_multiplier F` — warm-start预算乘数(默认1.2)
- `--history_low_success_boost F` — 低成功率预算放大(默认1.3)
- `--history_stride_percentile F` — 步长历史百分位(默认0.75)
- `--history_warmup_count N` — warm-start条数阈值(默认3)

Human Control (fixed_* params override all dynamic computation):
- `--fixed_budget_tokens N` — 固定budget token消耗(0=动态计算)
- `--fixed_num_thinking N` — 固定最大轮次(0=动态计算)
- `--fixed_think_stride N` — 固定步长(0=动态计算)
- `--fixed_check_length N` — 固定初始思考长度(0=动态计算)
- `--fixed_prob_th FLOAT` — 固定置信度阈值(0.0=使用prob_th)
- `--fixed_min_think_tokens N` — 固定最低思考token(0=动态计算)

Parameter priority: **CLI > request body > CONFIG defaults**.

## Architecture

### Three-Layer Call Chain

```
Entry scripts (run_baseline.py, run_budget.py, api_server.py)
  → naie/evaluation/runner.py         # evaluate_baseline(), evaluate_budget(), _evaluate_one()
    → naie/inference/think_budget.py   # call_model() — CONFIG→params adapter (_cfg_to_params)
      → naie/models/budget_forcing.py  # call_model() — core BudgetForcing loop
        → naie/models/model_client.py  # model_client — Completions API client with connection pool
        → naie/optimizations/*          # all optimization modules
```

### CONFIG → call_model Parameter Mapping

`naie/config/settings.py` defines CONFIG as `(value, description)` tuples — **all hyperparameters are centralized here for easy commercial handoff**. `THINK_BUDGET_CONFIG_47` extracts just the values. `naie/inference/think_budget.py::_cfg_to_params()` maps CONFIG keys to `budget_forcing.call_model()` parameter names — **the mapping is not always 1:1**:
- `enable_budget` → `enable_confidence` (different name)
- `enable_consecutive_same_stop=False` → `consecutive_same_stop=999` (never triggers)
- `model_name` is always forced to `mg2hg` regardless of input

### Current CONFIG Defaults (live values)

check_length=200, think_stride=100, prob_th=0.95, num_thinking=10, check_answer_len=4000, max_tokens=65536, min_think_tokens=100, max_api_calls=20, baseline_time_limit=90.0. P4-P6 experimental features default to OFF. Task type budget and history budget are ON by default.

### Unified Budget Priority Chain

When `call_model()` is invoked, the budget is computed with this priority (highest first):

0. **`BUDGET_ENABLED = False`** → Master switch OFF: disables ALL budget control (enable_confidence, task_type_budget, history_budget, difficulty_estimator), model generates freely
0.5. **`FIXED_BUDGET_TOKEN > 0`** (when `fixed_budget_tokens=0`) → Unified budget config override, sets `fixed_budget_tokens = FIXED_BUDGET_TOKEN`
1. **`fixed_budget_tokens > 0`** → Fixed override, skips ALL dynamic computation (check_length=min(budget,check_length), min_think_tokens=budget, num_thinking calculated)
2. **Task type override** (`task_type_override` non-empty) → Force that task type's budget (unknown types auto-registered)
3. **Task type budget** (`enable_task_type_budget`) → Per-type token budget from classification (unknown types auto-registered)
4. **Difficulty estimator** (`enable_difficulty_estimator`) → 14-feature fusion → segmented budget mapping (when enabled, history_budget does NOT override check_length/num_thinking, only think_stride)
5. **History trajectory budget** (`enable_history_budget`) → Cold/warm/hot/unlimited modes (availability depends on historical data count per task_type)
6. **CONFIG defaults** → Raw CONFIG values
7. **Individual fixed sub-parameters** → Override specific values regardless of the chain above
8. **`CONFIDENCE_THRESHOLD`** (if ≠0.95 and fixed_prob_th=0) → Overrides prob_th; **`PERCENTILE_STEP`** (if ≠0.75 and enable_history_budget) → Overrides history_stride_percentile

**Key interactions**:
- When `enable_difficulty_estimator=True`, history_budget warm/hot modes only override `think_stride`, NOT `check_length` or `num_thinking` (those are controlled by difficulty_estimator)
- When `enable_difficulty_estimator=False`, history_budget warm/hot modes can override `check_length`, `min_think_tokens`, `num_thinking`, and `think_stride`
- **No online dynamic budget adjustment**: All budget parameters are computed once at `call_model()` entry; the loop never modifies budget parameters mid-execution

### Core BudgetForcing Loop (naie/models/budget_forcing.py)

`call_model()` implements the main reasoning loop as a single inline path.

Loop flow: initial think generation → for each round: check think tag pairing + min_think_tokens gate → generate induced prompt → trial answer → compute decision_score → accept/continue → if continue: apply clean_context, compute stride, continue thinking → final fallback.

### Optimization Modules (naie/optimizations/)

| Module | Label | Status | Role |
|--------|-------|--------|------|
| token_filter.py | P3.1 | ACTIVE | Structural token filtering + Value Min Confidence calculation |
| confidence_gates.py | P3.1 | ACTIVE | compute_decision_score() / compute_dual_channel_score() |
| early_stopping.py | P2.2 | ACTIVE | make_accept_decision(), consecutive same answer detection |
| context_control.py | P3.3 | ACTIVE | make_induced_prompt() (copy mechanism), apply_clean_context() |
| safety_gate.py | — | Configurable | Timeout fallback + soft truncation (natural breakpoint closure on truncation) |
| complexity_analyzer.py | P4 | Configurable | Question complexity estimation — imported and used in budget_forcing.py |
| difficulty_estimator.py | P5 | Configurable | 14-dimension continuous feature fusion → difficulty∈[0,1] → segmented budget mapping |
| trial_evaluator.py | P6 | Configurable (default OFF) | DEER-style trial answer stability — imported and used in budget_forcing.py when enabled |
| scene_classifier.py | — | ACTIVE | Scene type classification (8 types) with typical_tokens prior |
| task_type_budget.py | — | ACTIVE | 10 task type classification with per-type token budgets |
| history_budget.py | — | ACTIVE | History-driven budget: cold/warm/hot-start + new task detection + auto-registration |
| history_module.py | — | ACTIVE | Records inference history to JSONL, provides per-task-type/scene stats queries + sliding window eviction + aggregated stats cache + task_type index |

## Critical Constraints

- **Circular import (resolved via PEP 562)**: `naie/optimizations/__init__.py` uses `__getattr__` lazy loading (PEP 562) to break the circular dependency chain: `confidence.py` → `_struct_token_core` → `__init__.py` → `confidence_gates.py` → `confidence.py`. All subpackage `__init__.py` files now use lazy loading.
- **Single api_server.py**: `naie/api_server.py` is the production version with full CONFIG mapping, task type support, history budget integration, and concurrency control.
- **Model forced to mg2hg**: Any model_name parameter is silently overridden. Only available endpoint is `http://141.73.2.25:87/v1/completions` (Completions API). Chat completions returns 404.
- **NO_PROXY required**: All scripts set `os.environ["NO_PROXY"] = "*"` to bypass corporate proxy for internal model endpoints.
- **sys.path.insert(0, ...) required**: All scripts manually add project root to sys.path. There is no setup.py or pyproject.toml.
- **Think tags**: `confidence.py` and `budget_forcing.py` use standard XML tags `iesa`/`eiraot` (`_THINK_OPEN_TAG`/`_THINK_CLOSE_TAG`).
- **Answer integrity**: Thinking can be truncated, but final answer generation always gets `max_tokens_answer=65536` to ensure JSON completeness.
- **mg2hg model behavior**: On Completions API, the model typically generates 3-5 tokens then closes think tag and outputs answer — it does not produce deep reasoning. The BudgetForcing loop primarily provides multiple trial-answer rounds.
- **Datasets**: NAIE uses Excel .xlsx files (columns: 问题, 参考答案, 二级能力, etc.).
- **Tokenizer**: Qwen3.6-27B tokenizer at `data/naie/Qwen3.6-27B` for token counting in evaluation.
- **CONFIG values may differ from call_model() signature defaults**: `_cfg_to_params()` reads CONFIG and passes those values, which override the function signature defaults.
- **History task_type recording**: `runner.py::_evaluate_one()` records the classified task_type in `record_inference()`.
- **budget_forcing.py defaults vs CONFIG defaults**: `budget_forcing.py::_init_params_from_config()` has conservative defaults. Production (NAIE) overrides these via CONFIG + `_cfg_to_params()`.
- **soft_truncation applies at all truncation points**: When `enable_soft_truncation=True`, `_truncate_thinking_at_natural_breakpoint()` is called not just at the final answer stage, but also during initial thinking, min_think_tokens gate continuation, and stride-based continuation — any time thinking output is truncated by max_tokens/stride (stop_reason='length').
- **History module has sliding window**: `history_module.py` automatically evicts records older than 7 days or exceeding 5000 entries (triggered every 50 writes). Stats are cached with 60s TTL and indexed by task_type for fast queries.
- **make_induced_prompt behavior depends on think tag pairing**: When `is_think_tag_paired(prompt)=True`, `induced_prompt = prompt` (direct use, no copy needed — think close tag already exists). When unpaired, `make_induced_prompt(prompt)` creates a copy with close tag + induced prompt text.

## Project Structure

```
think_budget_refactored/
├── run_baseline.py               # Baseline evaluation entry
├── run_budget.py                 # BudgetForcing evaluation entry
├── run_per_dataset.py            # Per-dataset evaluation
├── run_compare.py                # Result comparison tool
├── run_ablation.py               # Ablation experiments
├── run_one.py                    # Single-case debug tool
├── requirements.txt              # Python dependencies
│
├── naie/                          # All project code (unified)
│   ├── api_server.py              # FastAPI server (production)
│   ├── config/
│   │   ├── settings.py            # CONFIG hyperparameters
│   │   ├── budget_config.yaml     # Unified budget config (YAML, human-readable)
│   │   └── unified_config.py      # YAML loader + enums + compute functions
│   ├── evaluation/                # Evaluation orchestrator + parallel runner + analyzer
│   │   ├── runner.py              # evaluate_baseline/budget, _evaluate_one
│   │   ├── parallel_runner.py     # Async parallel evaluation
│   │   ├── analyzer.py            # Result analysis
│   │   └── trajectory_logger.py   # Trajectory logging
│   ├── inference/think_budget.py  # CONFIG→call_model adapter
│   ├── models/                    # Core model interaction
│   │   ├── budget_forcing.py      # Core BudgetForcing loop (call_model + call_model_async)
│   │   ├── confidence.py          # Confidence/perplexity calculation
│   │   ├── model_client.py        # Completions API client with connection pool
│   │   └── config.py              # MODEL_CONFIGS
│   ├── optimizations/             # All optimization modules (PEP 562 lazy __init__)
│   │   ├── _struct_token_core.py  # Structural token core (no imports)
│   │   ├── token_filter.py        # P3.1 Value Min Confidence
│   │   ├── confidence_gates.py    # Decision score computation
│   │   ├── early_stopping.py      # Accept/continue decision
│   │   ├── context_control.py     # Clean context management
│   │   ├── safety_gate.py         # Timeout/soft truncation fallback
│   │   ├── complexity_analyzer.py # P4 Question complexity
│   │   ├── difficulty_estimator.py# P5 14-dim difficulty fusion
│   │   ├── trial_evaluator.py     # P6 DEER trial stability
│   │   ├── scene_classifier.py    # Scene type classification
│   │   ├── task_type_budget.py    # 10 task type classification
│   │   ├── history_budget.py      # History-driven budget
│   │   └── history_module.py      # Inference history recording + sliding window + stats cache + index
│   ├── scoring/
│   │   ├── json_utils.py          # JSON parsing
│   │   ├── answer_extractor.py    # Answer extraction
│   │   ├── score.py               # Score calculation
│   │   └── llm_judge.py           # LLM-assisted judging
│   └── prompts/
│       ├── templates.py           # Prompt templates
│       └── builder.py             # Prompt builder
│
├── tests/                         # All test files
│   ├── test_budget_features.py    # Comprehensive feature test (no API needed)
│   ├── test_single_case.py        # Single-case API test
│   ├── test_mock_budget.py        # Mock-based unit test
│   ├── test_refactoring.py        # Refactoring verification
│   ├── test_new_optimizations.py  # New optimization tests (difficulty, trial, complexity)
│   ├── test_budget_system.py      # Budget system pytest tests (YAML config, warm/hot-start, registration)
│
├── docs/                          # Documentation
│   ├── README.md                  # Project overview
│   ├── architecture.md            # Architecture details
│   ├── confidence_estimation.md   # ★ Confidence flow (key document)
│   ├── configuration_guide.md     # CONFIG parameter reference
│   ├── api_server_guide.md        # API server usage
│   └── evaluation_guide.md        # Evaluation how-to
│
└── data/naie/                     # Datasets, tokenizer, results
```

## Documentation

- **[docs/confidence_estimation.md](docs/confidence_estimation.md)** — ★ Key document: detailed confidence estimation flow, structural token filtering, decision scoring, early stopping logic
- **[docs/architecture.md](docs/architecture.md)** — Three-layer call chain, CONFIG mapping, budget priority chain, task type budget
- **[docs/configuration_guide.md](docs/configuration_guide.md)** — All CONFIG parameters explained with defaults and effects
- **[docs/api_server_guide.md](docs/api_server_guide.md)** — API server endpoints, request/response format, concurrency control
- **[docs/evaluation_guide.md](docs/evaluation_guide.md)** — How to run all evaluation modes, CLI parameters, output interpretation
- **[docs/README.md](docs/README.md)** — Project overview and quick start

## Dependencies

openai, httpx, pandas, tqdm, transformers, python-dotenv, openpyxl, fastapi, uvicorn, scikit-learn (see requirements.txt)
