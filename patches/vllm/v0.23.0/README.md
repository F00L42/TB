# vLLM v0.23.0 DTB patches

This directory is the only deployable patch source for the DTB integration.
These files are versioned contracts against the fixed upstream tree, not
importable DTB implementations: the repository owns controller behavior under
`dtb/`, while each patch contains only the minimal upstream protocol/serving or
parser delta. Keeping the patches here avoids a second Python copy that could
silently diverge from the applied vLLM code.
Apply patches from the fixed upstream checkout in this order:

```bash
git apply --check \
  /path/to/TB/patches/vllm/v0.23.0/dtb_serving.patch
git apply --check \
  /path/to/TB/patches/vllm/v0.23.0/qwen3_coder_parser.patch
git apply --check \
  /path/to/TB/patches/vllm/v0.23.0/qwen3_reasoning_parser.patch
git apply --whitespace=fix \
  /path/to/TB/patches/vllm/v0.23.0/dtb_serving.patch
git apply --whitespace=fix \
  /path/to/TB/patches/vllm/v0.23.0/qwen3_coder_parser.patch
git apply --whitespace=fix \
  /path/to/TB/patches/vllm/v0.23.0/qwen3_reasoning_parser.patch
```

If any check fails, stop and reconcile the checkout/patch version. Do not
continue with `--reject`, because a partially applied serving/parser contract
is not a valid DTB deployment.

- `dtb_serving.patch` adds the external `enable_think_budget` protocol field
  and routes enabled requests to `DtbRequestController`. A false request flag
  or false server master switch remains on vLLM's native single-generate path.
  Enabled requests first call `prepare_request_route()` once. A `native` action
  falls through to that same native serving code without creating DTB context,
  queues, response parser copies or budget decisions; `dtb` passes the prepared
  snapshot to `handle_request()`. This patch and controller must be upgraded
  together. See the [system routing deployment and rollback guide](../../../docs/design/dtb_system_routing_deployment.md).
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
- `qwen3_reasoning_parser.patch` preserves the ordinary reasoning prefix when
  the last delta ID is the reasoning end ID but its marker text was stripped
  by terminal-stop detokenization. Empty boundaries still return no text;
  nonterminal mismatches, marker-present splits, implicit tool boundaries,
  non-streaming parsing, sampling parameters, usage and stop semantics remain
  unchanged. It also adds direct and unified-parser regressions to upstream
  `tests/reasoning/test_qwen3_reasoning_parser.py`.

Validate all patches against the repository's fixed `third_party/vllm`
checkout before deployment:

```bash
git -C third_party/vllm apply --check \
  ../../patches/vllm/v0.23.0/dtb_serving.patch
git -C third_party/vllm apply --check \
  ../../patches/vllm/v0.23.0/qwen3_coder_parser.patch
git -C third_party/vllm apply --check \
  ../../patches/vllm/v0.23.0/qwen3_reasoning_parser.patch
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

## Terminal-stop reasoning fidelity

From the TB root, run the independent no-model gate. It executes the new
upstream regression functions, existing-path controls, actual detokenizer base
methods plus the unified parser, and all 60 recorded DTB parser chains:

```bash
python tools/dtb/reasoning_tail_fidelity.py --apply-repo-patch \
  --replay-dir tests/fixtures/parser_tail
python -m unittest discover -s tests -p test_qwen_reasoning_tail.py -v
```

Omit `--apply-repo-patch` for the unmodified-source negative control: it must
return 1, including 47 recorded reasoning-tail failures. The patched gate
returns 0 with 84 checks. The original source and tool patch remain read-only.
Dependencies and the character tokenizer are stubbed; archived records retain
their real text and IDs. This is not an HTTP/NPU or real-tokenizer pytest run.

For current deployment/rollback, upgrade the controller and serving patch together
using the [deployment guide](../../../docs/design/dtb_system_routing_deployment.md).
The old 915bec9 one-patch upgrade recipe is not valid for the current routing API.
Target-server checks use the self-contained [delivery replay](../../../validation/replay/README.md).
Record the actual parser import paths and SHA256, and compare public output with
the pre-parser prefix in a separate allowlisted stream-audit run when diagnosing boundaries.
