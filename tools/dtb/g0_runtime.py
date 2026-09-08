"""Target-environment vLLM adapter; imported only for actual decoder tests."""
from __future__ import annotations

import copy
from importlib import metadata
import inspect
from pathlib import Path
import hashlib
import json

_TOKENIZER_IDENTITIES = {}


def identity(tokenizer=None, decoder=None):
    packages = {}
    for name in ("vllm", "vllm-ascend", "transformers", "tokenizers", "fastokens", "torch", "torch-npu"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    result = {"packages": packages}
    if tokenizer is not None:
        result["tokenizer_class"] = f"{type(tokenizer).__module__}.{type(tokenizer).__name__}"
        key = id(tokenizer)
        if key not in _TOKENIZER_IDENTITIES:
            backend = getattr(tokenizer, "_tokenizer", None)
            if backend is not None and callable(getattr(backend, "to_str", None)):
                serialized = backend.to_str()
                mode = "backend_json"
            else:
                serialized = json.dumps(tokenizer.get_vocab(), sort_keys=True, ensure_ascii=False)
                mode = "vocabulary_only"
            _TOKENIZER_IDENTITIES[key] = (tokenizer, mode, hashlib.sha256(serialized.encode()).hexdigest())
        _, mode, fingerprint = _TOKENIZER_IDENTITIES[key]
        result["tokenizer_fingerprint_kind"] = mode
        result["tokenizer_fingerprint"] = fingerprint
    if decoder is not None:
        cls = type(decoder)
        result["decoder_class"] = f"{cls.__module__}.{cls.__name__}"
        path = inspect.getsourcefile(cls)
        result["decoder_source"] = path
        result["decoder_source_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest() if path else None
    import tokenizers.decoders
    import vllm.v1.engine.detokenizer as module
    result["decode_stream_module"] = tokenizers.decoders.DecodeStream.__module__
    result["use_fast_detokenizer"] = module.USE_FAST_DETOKENIZER
    return result


class Runtime:
    def __init__(self, tokenizer_path):
        from vllm.sampling_params import SamplingParams, RequestOutputKind
        from vllm.tokenizers import get_tokenizer
        from vllm.v1.engine import EngineCoreRequest
        from vllm.v1.engine.detokenizer import IncrementalDetokenizer
        from vllm.v1.engine.output_processor import OutputProcessor, RequestOutputCollector
        self.Params, self.Kind = SamplingParams, RequestOutputKind
        self.Request, self.Decoder = EngineCoreRequest, IncrementalDetokenizer
        self.Processor, self.Collector = OutputProcessor, RequestOutputCollector
        self.tokenizer = get_tokenizer(tokenizer_path)

    def encode(self, text):
        return self.tokenizer.encode(text, add_special_tokens=False)

    def params(self, profile):
        values = copy.deepcopy(profile["sampling_params"])
        unknown = set(values)-set(self.Params.__struct_fields__)
        if unknown:
            raise ValueError(f"Unknown SamplingParams fields: {sorted(unknown)}")
        values["output_kind"] = self.Kind[values["output_kind"]]
        params = self.Params(**values)
        state = profile.get("runtime_state", {})
        if set(state)-{"_eos_token_id", "_all_stop_token_ids"}:
            raise ValueError("unknown captured runtime state")
        if "_eos_token_id" in state:
            params._eos_token_id = state["_eos_token_id"]
        if "_all_stop_token_ids" in state:
            params._all_stop_token_ids = set(state["_all_stop_token_ids"])
        if not set(params.stop_token_ids or []).issubset(params._all_stop_token_ids):
            raise ValueError("incoherent stop token state")
        return params

    def request(self, prompt, params):
        return self.Request(request_id="g0-internal", external_req_id="g0-external",
            prompt_token_ids=list(prompt), mm_features=None, pooling_params=None,
            sampling_params=copy.deepcopy(params), arrival_time=1.0,
            lora_request=None, cache_salt=None, data_parallel_rank=None)

    def decoder(self, prompt, params):
        return self.Decoder.from_new_request(self.tokenizer, self.request(prompt, params))

    @staticmethod
    def update(decoder, token, count, params):
        # Mirrors the already-decided token/EOS stop passed to frontend decoder.
        # This CPU gate does not emulate scheduler timing or sampling.
        eos = not params.ignore_eos and token == params._eos_token_id
        token_stop = token in (params.stop_token_ids or []) and not eos
        matched = decoder.update([token], eos or token_stop)
        if eos or token_stop or matched is not None:
            return "stop"
        if params.max_tokens is not None and count >= params.max_tokens:
            return "length"
        return None

    def decode(self, prompt, sequence, params):
        decoder = self.decoder(prompt, params)
        pieces, trace, consumed = [], [], []
        reason = None
        for token in sequence:
            consumed.append(token)
            reason = self.update(decoder, token, len(consumed), params)
            text = decoder.get_next_output_text(reason is not None, True)
            pieces.append(text)
            trace.append({"token_id": token, "text": text, "finish_reason": reason})
            if reason:
                break
        flush = decoder.get_next_output_text(True, True)
        pieces.append(flush)
        return {"text": "".join(pieces), "token_ids": consumed, "trace": trace,
                "flush": flush, "finish_reason": reason or "fixture_end"}

    def abort_prefix(self, prompt, prefix, trigger, params):
        processor = self.Processor(self.tokenizer, log_stats=False, stream_interval=1)
        queue = self.Collector(self.Kind.DELTA, "g0-internal")
        req = self.request(prompt, params)
        processor.add_request(req, prompt=None, queue=queue)
        state = processor.request_states[req.request_id]
        pieces, consumed, trace = [], [], []
        # Reached natural stop before proposed abort: report it, never feed past it.
        for i, token in enumerate(prefix):
            reason = self.update(state.detokenizer, token, i+1, params)
            if reason:
                processor.abort_requests([req.external_req_id], internal=False)
                if processor.request_states or processor.external_req_ids:
                    raise AssertionError("processor leaked after cleanup")
                return {"natural_before_abort": reason, "at_token": i+1}
            out = state.make_request_output([token], None, None, None)
            queue.put(out)
            if i < trigger:
                item = queue.get_nowait()
                pieces.append(item.outputs[0].text)
                consumed.extend(item.outputs[0].token_ids)
                trace.append({"text": item.outputs[0].text, "token_ids": list(item.outputs[0].token_ids)})
        processor.abort_requests([req.external_req_id], internal=False)
        terminal = queue.get_nowait()
        if not terminal.finished or terminal.outputs[0].finish_reason != "abort":
            raise AssertionError("missing owned abort terminal")
        pieces.append(terminal.outputs[0].text)
        consumed.extend(terminal.outputs[0].token_ids)
        trace.append({"text": terminal.outputs[0].text,
                      "token_ids": list(terminal.outputs[0].token_ids), "finish_reason": "abort"})
        if consumed != list(prefix) or processor.request_states or processor.external_req_ids:
            raise AssertionError("prefix conservation or processor cleanup failed")
        return {"text": "".join(pieces), "token_ids": consumed, "trace": trace,
                "natural_before_abort": None}

    def direct_prefix(self, prompt, prefix, params):
        decoder = self.decoder(prompt, params)
        pieces = []
        for token in prefix:
            # Caller already established that the prefix precedes natural stop.
            decoder.update([token], False)
            pieces.append(decoder.get_next_output_text(False, True))
        pieces.append(decoder.get_next_output_text(True, True))
        return "".join(pieces)
