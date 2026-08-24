# vLLM v0.23.0 DTB patches

This directory is the only deployable patch source for the DTB integration.
These files are versioned contracts against the fixed upstream tree, not
importable DTB implementations: the repository owns controller behavior under
`dtb/`, while each patch contains only the minimal upstream protocol/serving or
parser delta. Keeping both patches here avoids a second Python copy that could
silently diverge from the applied vLLM code.
Apply patches from the fixed upstream checkout in this order:

```bash
git apply --reject --whitespace=fix \
  /path/to/TB/patches/vllm/v0.23.0/dtb_serving.patch
git apply --reject --whitespace=fix \
  /path/to/TB/patches/vllm/v0.23.0/qwen3_coder_parser.patch
```

- `dtb_serving.patch` adds the external `enable_think_budget` protocol field
  and routes enabled requests to `DtbRequestController`. A false request flag
  or false server master switch remains on vLLM's native single-generate path.
  The enabled route resolves the whole assistant completion limit with vLLM's
  native `get_max_tokens()` before passing it through private route identity.
- `qwen3_coder_parser.patch` preserves parser partition equivalence for the
  fixed Qwen3-Coder tool parser.

Validate both patches against the repository's fixed `third_party/vllm`
checkout before deployment:

```bash
git -C third_party/vllm apply --check \
  ../../patches/vllm/v0.23.0/dtb_serving.patch
git -C third_party/vllm apply --check \
  ../../patches/vllm/v0.23.0/qwen3_coder_parser.patch
```
