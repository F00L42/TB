#!/bin/bash

# 清理编译缓存
rm -rf /root/.cache/vllm/torch_compile_cache/

# ================= 1. Ascend / vLLM 环境变量 =================

# 使用 4 张 NPU
export ASCEND_RT_VISIBLE_DEVICES=0,1,2,3

# PyTorch NPU 内存分配优化
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True

# vLLM V1 Engine
export VLLM_USE_V1=1

# 服务配置
export MODEL="/opt/foundation_model/Qwen3.6-27B"
export PORT=41016

# DEER接口模块
export PYTHONPATH=/opt/p00906181/260707/TB:$PYTHONPATH
# ================= 2. 启动 Qwen3.6-27B vLLM OpenAI API Server =================

python -m vllm.entrypoints.openai.api_server \
  --model $MODEL \
  --served-model-name "Qwen3.6-27B" \
  --max-model-len 32768 \
  --max-num-seqs 8 \
  --data-parallel-size 1 \
  --tensor-parallel-size 4 \
  --port $PORT \
  --host "0.0.0.0" \
  --block-size 128 \
  --gpu-memory-utilization 0.95 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative_config '{"method": "mtp", "num_speculative_tokens": 4}' \
  --async-scheduling \
  --language-model-only \
  2>&1 | tee log.txt

