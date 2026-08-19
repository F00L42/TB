export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
export MODEL="/opt/w00618058/Qwen3.6-27B"
export PORT=9000
export PYTHONPATH=/opt/p00906181/TB-main:$PYTHONPATH
# 仅在工具流排障窗口显式设为 1；日志会包含模型输出和工具参数正文。
export TB_TOOL_STREAM_DEBUG=${TB_TOOL_STREAM_DEBUG:-0}

nohup python -m vllm.entrypoints.openai.api_server \
  --model $MODEL \
  --served-model-name qwen3.6-27b \
  --seed 1024 \
  --max-num-batched-tokens 16384 \
  --max-num-seqs 16 \
  --data-parallel-size 1 \
  --tensor-parallel-size 8 \
  --port $PORT \
  --host "0.0.0.0" \
  --block-size 128 \
  --gpu-memory-utilization 0.92 \
  --enable-auto-tool-choice \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --enable-prefix-caching \
  --enable-chunked-prefill \
  --compilation-config '{"cudagraph_mode":"FULL_DECODE_ONLY"}' \
  --speculative_config '{"method": "mtp", "num_speculative_tokens": 4}' \
  --async-scheduling \
  --language-model-only \
> server.log 2>&1 &
