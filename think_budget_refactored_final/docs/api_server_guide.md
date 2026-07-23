# API Server Guide

`naie/api_server.py` provides OpenAI-compatible endpoints with optional Think
Budget control.

Default backend:

- model: `Qwen3.6-27B`
- base URL: `http://10.44.84.60:8000/v1`

## Endpoints

- `POST /v1/chat/completions`
- `POST /v1/completions`
- `GET /health`
- `GET /v1/overrides`
- `GET /v1/config`
- `POST /v1/config` (administrator key required)

## Public request fields

External callers may only set OpenAI-compatible generation fields,
`enable_budget`, and fixed manual budget controls. Internal Think Budget
strategy parameters are server-side only.

### Chat completion fields

- `model`
- `messages`
- `temperature`
- `max_tokens`
- `top_p`
- `n`
- `stream` (`true` is rejected because streaming is not implemented)
- `stop`
- `presence_penalty`
- `frequency_penalty`
- `logit_bias`
- `user`
- `seed`
- `response_format`
- `logprobs`
- `top_logprobs`
- `enable_budget`
- `fixed_budget_tokens`
- `fixed_num_thinking`
- `fixed_think_stride`
- `fixed_check_length`
- `fixed_prob_th`
- `fixed_min_think_tokens`

### Text completion fields

- `model`
- `prompt`
- `suffix`
- `temperature`
- `max_tokens`
- `top_p`
- `n`
- `stream` (`true` is rejected because streaming is not implemented)
- `stop`
- `presence_penalty`
- `frequency_penalty`
- `logit_bias`
- `user`
- `seed`
- `echo`
- `logprobs`
- `enable_budget`
- `fixed_budget_tokens`
- `fixed_num_thinking`
- `fixed_think_stride`
- `fixed_check_length`
- `fixed_prob_th`
- `fixed_min_think_tokens`

Unknown fields are rejected by request validation.

## Fields callers must not set

These are intentionally not public API request fields:

- `custom_task_types_path`
- `scene_type`
- `task_type`
- `default_task_type`
- `check_length`
- `think_stride`
- `num_thinking`
- `check_answer_len`
- `prob_th`
- `min_think_tokens`
- `max_api_calls`
- `enable_value_min_confidence`
- `enable_clean_context`
- `enable_safety_gate`
- `baseline_time_limit`
- `enable_difficulty_estimator`
- `enable_deer_trial`
- `enable_soft_truncation`
- `enable_task_type_budget`
- `enable_history_budget`
- `history_budget_multiplier`
- `history_low_success_boost`
- `history_stride_percentile`
- `new_task_unlimited`
- `history_warmup_count`
- `history_warmup_strategy`
- `auto_cluster_threshold`
- `enable_full_chain_confidence`
- `full_chain_min_percentile`
- `enable_dual_channel`
- `reasoning_min_percentile`
- `consecutive_same_stop`
- `value_min_bottom_k`

The server still uses scene classification, task-type classification, history
statistics, confidence scoring, and other budget logic internally. The point is
that callers cannot override those strategy knobs per request.

Use `GET /v1/overrides` to inspect the live allowlist.

## Examples

Budget mode:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-27B",
    "messages": [{"role": "user", "content": "请用一句话说明 API server 是否可用"}],
    "enable_budget": true,
    "logprobs": true
  }'
```

Non-budget mode:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-27B",
    "messages": [{"role": "user", "content": "请直接回答：服务可用吗？"}],
    "enable_budget": false,
    "logprobs": true
  }'
```

Fixed manual budget:

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-27B",
    "messages": [{"role": "user", "content": "诊断这个故障"}],
    "enable_budget": true,
    "fixed_budget_tokens": 2000,
    "fixed_num_thinking": 5
  }'
```

Text completion:

```bash
curl -X POST http://localhost:8000/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3.6-27B",
    "prompt": "用一句话回答：API server 是否可用？",
    "enable_budget": false,
    "logprobs": true
  }'
```

Disallowed override example:

```json
{
  "messages": [{"role": "user", "content": "hello"}],
  "prob_th": 0.8
}
```

The request above is rejected because `prob_th` is an internal budget strategy
parameter.

## Runtime configuration endpoint

`POST /v1/config` updates process-wide server configuration and requires
`X-API-Key` matching `THINK_BUDGET_ADMIN_KEY`.

This endpoint is intentionally separate from public inference requests.
