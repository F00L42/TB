# vLLM v0.23.0 DTB patches

This directory is the only deployable patch source for the DTB integration.
These files are versioned contracts against the fixed upstream tree, not
importable DTB implementations: the repository owns controller behavior under
`dtb/`, while each patch contains only the minimal upstream protocol/serving or
parser delta. Keeping both patches here avoids a second Python copy that could
silently diverge from the applied vLLM code.
Apply patches from the fixed upstream checkout in this order:

```bash
git apply --check \
  /path/to/TB/patches/vllm/v0.23.0/dtb_serving.patch
git apply --check \
  /path/to/TB/patches/vllm/v0.23.0/qwen3_coder_parser.patch
git apply --whitespace=fix \
  /path/to/TB/patches/vllm/v0.23.0/dtb_serving.patch
git apply --whitespace=fix \
  /path/to/TB/patches/vllm/v0.23.0/qwen3_coder_parser.patch
```

If either check fails, stop and reconcile the checkout/patch version. Do not
continue with `--reject`, because a partially applied serving/parser contract
is not a valid DTB deployment.

- `dtb_serving.patch` adds the external `enable_think_budget` protocol field
  and routes enabled requests to `DtbRequestController`. A false request flag
  or false server master switch remains on vLLM's native single-generate path.
  The enabled route resolves the whole assistant completion limit with vLLM's
  native `get_max_tokens()` before passing it through private route identity.
- `qwen3_coder_parser.patch` replaces the partition-sensitive progressive XML
  parameter state machine with a v0.23.0 backport that parses each completed
  function from cumulative text and emits its canonical JSON arguments once.
  It covers parameter+close, whole-body, whole-call and adjacent multi-call
  bursts without inventing close tags for incomplete model output. It also
  consumes a Qwen tool-start delta whose `token_ids` contains the parser's own
  `tool_call_start_token_id` while `text` is empty: the parser maintains one
  request-local logical `<tool_call>` prefix without writing that text back to
  content, SSE, token accounting, or the continuation prompt. The tradeoff is
  intentional: arguments are emitted when `</function>` arrives rather than
  parameter by parameter; tool names may still be emitted as soon as the header
  is complete.

Validate both patches against the repository's fixed `third_party/vllm`
checkout before deployment:

```bash
git -C third_party/vllm apply --check \
  ../../patches/vllm/v0.23.0/dtb_serving.patch
git -C third_party/vllm apply --check \
  ../../patches/vllm/v0.23.0/qwen3_coder_parser.patch
```

Run the no-model partition gate against an applied copy of the parser:

```bash
python tools/dtb/parser_partition_equivalence.py \
  --qwen-parser-source /path/to/patched/qwen3coder_tool_parser.py \
  --summary-only --require-equivalence
```

The gate compares the historical parameter-close fixture, exact burst shapes
for upstream #39598/#43074/#45256/#45439/#45440, and marker-present versus
token-only `248058` starts against the non-streaming parser oracle. Both the
direct tool parser and unified delegating parser must be partition-equivalent.
Target-server streaming and non-streaming replay remain required; this local
gate does not validate vLLM serving finalization or NPU runtime code.
