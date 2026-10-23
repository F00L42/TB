"""Optionally run SDK outputs through supplied vLLM 0.23 parser/serving sources.

No Git metadata or third_party checkout is required.
No GPU/HTTP/vLLM installation. Data types and engine are CPU test doubles;
native Qwen reasoning/tool and DelegatingParser algorithms execute unchanged.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import __future__
import json
import re
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS

sys.dont_write_bytecode = True  # The upstream checkout is a read-only dependency.

from verify_patch import PACKAGE, patched_tree
import parser_fixtures as fixtures

sys.path.insert(0, str(PACKAGE / "tests"))
from test_observer import Engine, Kind, RecordingDetector, collect, result
from thinking_observer import ObserverConfig, ObserverContext, ThinkingObserver, TokenLimitDetector
from thinking_observer.integration import VllmObserver, bind_qwen3, close_with_response


class Tokenizer(fixtures.FakeTokenizer):
    def get_vocab(self):
        return {"<think>": 90001, "</think>": 90002,
                "<tool_call>": 90003, "</tool_call>": 90004}

    @property
    def all_special_ids(self):
        return list(self.get_vocab().values())

    def encode(self, text, add_special_tokens=False):
        vocab = self.get_vocab()
        parts = re.split(r"(</?think>|</?tool_call>)", text)
        return [token for part in parts for token in (
            [vocab[part]] if part in vocab else [ord(c) for c in part])]

    def decode(self, ids):
        vocab = {v: k for k, v in self.get_vocab().items()}
        return "".join(vocab.get(i, chr(i)) for i in ids)


def load_native(upstream, patched):
    fixtures.VLLM_ROOT = upstream / "vllm"
    fixtures._install_import_boundary()
    fixtures._package("vllm.entrypoints.mcp")
    fixtures._module("vllm.entrypoints.mcp.tool_server", ToolServer=NS)
    sys.modules["vllm.sampling_params"].RequestOutputKind = Kind
    for name in ("abs_reasoning_parsers", "basic_parsers"):
        fixtures._load_source(f"vllm.reasoning.{name}", upstream / f"vllm/reasoning/{name}.py")
    reasoner = fixtures._load_source("vllm.reasoning.qwen3_reasoning_parser",
                                   patched / "vllm/reasoning/qwen3_reasoning_parser.py")
    base = fixtures._load_source("vllm.tool_parsers.abstract_tool_parser",
                                upstream / "vllm/tool_parsers/abstract_tool_parser.py")
    sys.modules["vllm.tool_parsers"].ToolParser = base.ToolParser
    tool = fixtures._load_source("vllm.tool_parsers.qwen3coder_tool_parser",
                                 patched / "vllm/tool_parsers/qwen3coder_tool_parser.py")
    unified = fixtures._load_source("vllm.parser.abstract_parser",
                                    upstream / "vllm/parser/abstract_parser.py")
    return reasoner.Qwen3ReasoningParser, tool.Qwen3CoderToolParser, unified.DelegatingParser


async def run(upstream, patched):
    Reasoner, Tool, Unified = load_native(upstream, patched)
    tokenizer = Tokenizer()
    function = fixtures.FunctionDefinition(name="lookup", parameters={
        "type": "object", "properties": {"q": {"type": "string"}}})
    tools = [fixtures.ChatCompletionToolsParam(type="function", function=function)]
    request = fixtures.ChatCompletionRequest(tools=tools, tool_choice="auto")

    def parser():
        instance = Unified(tokenizer)
        instance.reasoning_parser = Reasoner(tokenizer)
        instance.tool_parser = Tool(tokenizer, tools)
        return instance

    def delta(text="", finish=None, ids=None):
        return result(text, tokenizer.encode(text) if ids is None else ids, finish)

    def parsed(outputs):
        native = parser()
        reasoning, content, calls = [], [], {}
        for item in outputs:
            out = item.outputs[0]
            message = native.parse_delta(out.text, out.token_ids, request,
                                         prompt_token_ids=item.prompt_token_ids,
                                         finished=item.finished)
            if message is None:
                continue
            reasoning.append(message.reasoning or "")
            content.append(message.content or "")
            for call in message.tool_calls:
                target = calls.setdefault(call.index, {"name": "", "arguments": ""})
                if call.function:
                    target["name"] += call.function.name or ""
                    target["arguments"] += call.function.arguments or ""
        normalized = [(v["name"], json.loads(v["arguments"])) for v in calls.values()]
        full = parser().parse("".join(o.outputs[0].text for o in outputs), request,
                              enable_auto_tools=True)
        full_calls = [(v.name, json.loads(v.arguments)) for v in full[2] or []]
        assert ("".join(reasoning), "".join(content), normalized) == (
            full[0] or "", full[1] or "", full_calls)
        return "".join(reasoning), "".join(content), normalized

    body = '<tool_call>\n<function=lookup>\n<parameter=q>\n中文 "abc"\n</parameter>\n</function>\n</tool_call>'
    scenarios = [
        ("native_tool", Engine([delta("reason"), delta("</think>" + body, "stop")]), False, "reason"),
        ("forced_plain", Engine([delta("reason")], [delta(" tail", "abort")],
                                [delta("answer", "stop")]), True, "reason tail"),
        ("forced_tool", Engine([delta("reason")], [delta(" tail", "abort")],
                               [delta(body[:30]), delta(body[30:], "stop")]), True, "reason tail"),
        ("drained_tool", Engine([delta("reason")], [delta(body[:30], "abort")],
                                [delta(body[30:], "stop")]), True, "reason"),
        ("same_batch_tool", Engine([delta("reason" + body), delta(finish="stop")]), True, "reason"),
        ("race_terminal", Engine([delta("reason")], [delta("</think>answer", "stop")]), True, "reason"),
        ("text_only_tail", Engine([delta("reason")], [delta("尾", "abort", [])],
                                  [delta("answer", "stop")]), True, "reason尾"),
    ]
    for name, engine, trigger, expected in scenarios:
        protocol = bind_qwen3(tokenizer, Reasoner(tokenizer), parser())
        context = ObserverContext(name, "fixture", protocol, 1024, 4096, 1, tokenizer)
        params = NS(max_tokens=1024, output_kind=Kind.DELTA)
        observer = ThinkingObserver(TokenLimitDetector(2) if trigger else RecordingDetector(),
                                    ObserverConfig(enabled=True, answer_cue="", callback_limit_ms=1000))
        outputs = await collect(observer.stream(engine,
            {"prompt_token_ids": [90001]}, params, context, {"reasoning_ended": False}))
        thought, content, calls = parsed(outputs)
        assert thought == expected, (name, thought)
        if "tool" in name:
            assert calls == [("lookup", {"q": '中文 "abc"'})], (name, calls)
            assert content.strip() == "", (name, content)
        else:
            assert content == "answer", (name, content)
        assert sum(o.finished for o in outputs) == 1

    # Execute the patched native scheduling method, not a reimplementation.
    serving_path = patched / "vllm/entrypoints/openai/chat_completion/serving.py"
    tree = ast.parse(serving_path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OpenAIServingChat")
    method = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef)
                  and n.name == "_create_chat_completion")
    class ErrorResponse: pass
    class BeamSearchParams: pass
    namespace = dict(ErrorResponse=ErrorResponse, BeamSearchParams=BeamSearchParams,
                     RequestResponseMetadata=NS, get_max_tokens=lambda *a, **k: 128,
                     close_with_response=close_with_response)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(serving_path), "exec",
                 flags=__future__.annotations.compiler_flag), namespace)

    class Serving:
        _create_chat_completion = namespace["_create_chat_completion"]
        renderer = NS(tokenizer=tokenizer)
        reasoning_parser_cls = Reasoner
        model_config = NS(max_model_len=4096)
        models = NS(model_name=lambda lora: "fixture")
        default_sampling_params, override_max_tokens = {}, None

        def _effective_chat_template_kwargs(self, request): return {}
        def _base_request_id(self, raw, request_id): return "r"
        def _maybe_get_adapters(self, *a, **k): return "adapter"
        def _get_data_parallel_rank(self, raw): return 2
        def _extract_prompt_components(self, prompt): return NS(token_ids=[90001])
        def _extract_prompt_len(self, prompt): return 1
        def _log_inputs(self, *a, **k): pass
        def parser_cls(self, *a, **k): return parser()

        async def render_chat_request(self, request):
            return [], [{"prompt_token_ids": [90001]}]

        def chat_completion_stream_generator(self, request, source, *a, **k):
            return source

        async def chat_completion_full_generator(self, request, source, *a, **k):
            return await collect(source)

    checks = 0
    for stream in (True, False):
        for enabled, flag, unsupported in ((True, True, False), (False, True, False),
                                            (True, False, False), (True, True, True)):
            serving = Serving()
            detector = RecordingDetector(lambda d: __import__("thinking_observer").StopAndAnswer("test", 2))
            serving.thinking_observer = VllmObserver(ThinkingObserver(detector,
                ObserverConfig(enabled=enabled, answer_cue="", callback_limit_ms=1000)))
            observing = enabled and flag and not unsupported
            serving.engine_client = Engine([delta("reason")],
                [delta(" tail", "abort")] if observing else [delta("</think>answer", "stop")],
                [delta("answer", "stop")])
            params = NS(max_tokens=128, output_kind=Kind.DELTA if stream else Kind.FINAL_ONLY,
                        n=1, presence_penalty=1 if unsupported else 0)
            req = NS(stream=stream, enable_thinking_observer=flag, request_id=None,
                     max_completion_tokens=128, max_tokens=None, truncate_prompt_tokens=None,
                     use_beam_search=False, include_reasoning=True, _grammar_from_tool_parser=False,
                     tools=tools, priority=5, to_sampling_params=lambda *a: params)
            response = await serving._create_chat_completion(req)
            output = await collect(response) if stream else response
            assert bool(detector.states) == observing
            assert len(serving.engine_client.calls) == (2 if observing else 1)
            assert serving.engine_client.calls[0][3]["data_parallel_rank"] == 2
            if observing:
                assert parsed(output)[0] == "reason tail"
                if not stream:
                    assert len(output) == 1
                assert params.output_kind == (Kind.DELTA if stream else Kind.FINAL_ONLY)
            checks += 1
    print(json.dumps({"native_parser_scenarios": len(scenarios), "patched_serving_routes": checks,
                      "status": "PASS", "scope": "CPU source execution with data/engine stubs"}))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("upstream", type=Path,
                     help="vLLM 0.23 source directory; Git metadata is not required")
    args = cli.parse_args()
    with tempfile.TemporaryDirectory(prefix="observer-native-") as directory:
        patched = Path(directory)
        patched_tree(args.upstream.resolve(), patched)
        asyncio.run(run(args.upstream.resolve(), patched))
