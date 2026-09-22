# PyTorch NPU ▒~F~E▒~X▒~H~F▒~E~M▒~X▒~L~V
export PYTORCH_NPU_ALLOC_CONF=expandable_segments:True
rm -rf /root/.cache/vllm/torch_compile_cache/torch_aot_compile/
# vLLM V1 Engine
export VLLM_USE_V1=1
#export TB_TOOL_STREAM_DEBUG=1
export MODEL="/opt/p00906181/NetGPT-Qwen3.6-27B-40K-R27.1.5-MTP/"
export PORT=9001

# DEER▒~N▒▒~O▒模▒~]~W
export PYTHONPATH=/opt/p00906181/TB:$PYTHONPATH
export ASCEND_RT_VISIBLE_DEVICES=4,5,6,7
nohup python -m vllm.entrypoints.openai.api_server \
  --model $MODEL \
  --served-model-name qwen3.6-27b \
  --seed 1024 \
  --max-num-batched-tokens 16384 \
  --max-num-seqs 32 \
  --data-parallel-size 1 \
  --tensor-parallel-size 4 \
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
  --speculative_config '{"method": "mtp", "num_speculative_tokens": 3}' \
  --async-scheduling \
  --language-model-only \
> server.log 2>&1 &

