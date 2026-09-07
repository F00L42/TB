"""CPU-only gate using the deployed vLLM/tokenizer, not an inference model.

Run in the target service environment. JSONL is emitted to stdout; nonzero exit
means a restart mismatch. This is NOT a sampling or parser correctness test.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from importlib.metadata import version


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tokenizer", required=True, help="Local deployed tokenizer directory")
    args = parser.parse_args()
    # Import only when executing on the server; no vLLM/model dependency locally.
    from vllm.sampling_params import SamplingParams, RequestOutputKind
    from vllm.tokenizers import get_tokenizer
    from vllm.v1.engine import EngineCoreRequest
    from vllm.v1.engine.detokenizer import IncrementalDetokenizer
    from vllm.v1.engine.output_processor import OutputProcessor, RequestOutputCollector

    tokenizer = get_tokenizer(args.tokenizer)
    prompt = tokenizer.encode("<|im_start|>assistant\n<think>\n", add_special_tokens=False)
    samples = [
        "Plan carefully, then answer. 12345 abcdefgh.",
        "分析中文与 emoji 👩🏽‍💻🙂；组合字符 e\u0301 与换行\n结束。",
        'inspect literal </think> and continue reasoning',
        '</think>\n<tool_call>{"name":"read","arguments":{"path":"中文.py"}}</tool_call>',
    ]
    print(json.dumps({"event": "identity", "tokenizer": args.tokenizer,
                      "vllm": version("vllm"), "tokenizers": version("tokenizers"),
                      "tokenizer_class": type(tokenizer).__name__}, ensure_ascii=False))

    def request(ids, stop):
        return EngineCoreRequest(
            request_id="internal-test", external_req_id="external-test",
            prompt_token_ids=ids, mm_features=None, pooling_params=None,
            sampling_params=SamplingParams(
                max_tokens=4096, output_kind=RequestOutputKind.DELTA,
                stop=stop, skip_special_tokens=False),
            arrival_time=1.0, lora_request=None, cache_salt=None, data_parallel_rank=None)

    def suffix_text(ids, suffix, stop):
        decoder = IncrementalDetokenizer.from_new_request(tokenizer, request(ids, stop))
        pieces = []
        for token in suffix:
            decoder.update([token], False)
            pieces.append(decoder.get_next_output_text(False, True))
        pieces.append(decoder.get_next_output_text(True, True))
        return "".join(pieces)

    failed = tested = 0
    for sample_index, sample in enumerate(samples):
        generated = tokenizer.encode(sample, add_special_tokens=False)
        for stop in ([], ["NEVER_MATCH_THIS_STOP"]):
            for k in range(1, len(generated)+1):
                for pending in (0, 1, 4):
                    end = min(k+pending, len(generated))
                    processor = OutputProcessor(tokenizer, log_stats=False, stream_interval=1)
                    queue = RequestOutputCollector(RequestOutputKind.DELTA, "internal-test")
                    req = request(prompt, stop)
                    processor.add_request(req, prompt=None, queue=queue)
                    state = processor.request_states[req.request_id]
                    pieces, ids = [], []
                    for i, token in enumerate(generated[:end]):
                        state.detokenizer.update([token], False)
                        out = state.make_request_output([token], None, None, None)
                        queue.put(out)
                        if i < k:
                            out = queue.get_nowait()
                            pieces.append(out.outputs[0].text)
                            ids.extend(out.outputs[0].token_ids)
                    processor.abort_requests(["external-test"], internal=False)
                    terminal = queue.get_nowait()
                    assert terminal.finished and terminal.outputs[0].finish_reason == "abort"
                    pieces.append(terminal.outputs[0].text)
                    ids.extend(terminal.outputs[0].token_ids)
                    assert ids == generated[:end]
                    assert not processor.request_states and not processor.external_req_ids
                    # Same fixed suffix on both sides: isolates detokenizer restart
                    # from changes in RNG, logits processors, and probe decisions.
                    for forced in (False, True):
                        suffix = (tokenizer.encode("</think>\nanswer", add_special_tokens=False)
                                  if forced else generated[end:])
                        expected = suffix_text(prompt, generated[:end]+suffix, stop)
                        actual = "".join(pieces)+suffix_text(prompt+ids, suffix, stop)
                        ok = actual == expected
                        tested += 1
                        failed += not ok
                        record = {"event":"cut", "sample":sample_index, "trigger":k,
                                  "restart":end, "pending":pending, "string_stop":bool(stop),
                                  "forced_close":forced, "ok":ok,
                                  "prefix_sha256":hashlib.sha256(json.dumps(ids).encode()).hexdigest()}
                        if not ok:
                            record.update(actual=actual, expected=expected, prefix_ids=ids)
                        print(json.dumps(record, ensure_ascii=False))
    print(json.dumps({"event":"summary", "tested":tested, "failed":failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
