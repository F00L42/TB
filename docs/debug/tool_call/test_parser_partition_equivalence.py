#!/usr/bin/env python3
"""Replay equivalent stream partitions through the native vLLM parsers.

The target vLLM checkout cannot be imported as a complete package on the local
Windows host because its package initializer requires torch.  This harness
stubs only protocol/data dependencies, then loads and executes parser source
files.  By default all sources come from ``third_party/vllm``; an experimental
Qwen parser source can be supplied without modifying that fixed checkout.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import logging
import re
import sys
import types
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Generator
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
VLLM_ROOT = ROOT / "third_party" / "vllm" / "vllm"
VLLM_TESTS_ROOT = ROOT / "third_party" / "vllm" / "tests"


def _package(name: str, path: Path | None = None) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__path__ = [] if path is None else [str(path)]
    sys.modules[name] = module
    return module


def _module(name: str, **members: Any) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__dict__.update(members)
    sys.modules[name] = module
    return module


def _load_source(name: str, path: Path) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {name} from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load_function_from_source(
    path: Path,
    function_name: str,
    namespace: dict[str, Any],
) -> Any:
    """Execute one unmodified helper definition without importing its test module."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    function = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == function_name
    )
    isolated = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(isolated)
    exec(compile(isolated, str(path), "exec"), namespace)
    return namespace[function_name]


class _Record:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class ChatCompletionNamedToolChoiceParam(_Record):
    pass


class ChatCompletionRequest(_Record):
    def __init__(
        self,
        *,
        tools: list[Any],
        tool_choice: str | None = "auto",
    ) -> None:
        super().__init__(tools=tools, tool_choice=tool_choice)


class ChatCompletionToolsParam(_Record):
    pass


class FunctionTool(_Record):
    pass


@dataclass
class DeltaFunctionCall:
    name: str | None = None
    arguments: str | None = None


@dataclass
class DeltaToolCall:
    index: int
    id: str | None = None
    function: DeltaFunctionCall | None = None
    type: str | None = None


@dataclass
class DeltaMessage:
    content: str | None = None
    reasoning: str | None = None
    tool_calls: list[DeltaToolCall] = field(default_factory=list)


@dataclass
class FunctionCall:
    name: str
    arguments: str


@dataclass
class ToolCall:
    function: FunctionCall
    type: str = "function"
    id: str | None = None


@dataclass
class ExtractedToolCallInformation:
    tools_called: bool
    tool_calls: list[ToolCall]
    content: str | None


class FunctionDefinition(_Record):
    pass


class StructuredOutputsParams(_Record):
    pass


class ResponsesRequest(_Record):
    pass


class ReasoningParser:
    pass


class TokenizerLike:
    pass


class FakeTokenizer(TokenizerLike):
    def get_vocab(self) -> dict[str, int]:
        # Qwen3.6-27B IDs captured in run_after_handoff_fix_14 attempt 1.
        return {
            **{chr(codepoint): codepoint for codepoint in range(128)},
            "<tool_call>": 248_058,
            "</tool_call>": 248_046,
        }

    def encode(self, text: str, add_special_tokens: bool = False) -> list[int]:
        del add_special_tokens
        vocab = self.get_vocab()
        return [vocab[token] for token in self.tokenize(text)]

    def decode(self, token_ids: list[int]) -> str:
        inverse_vocab = {value: key for key, value in self.get_vocab().items()}
        return "".join(inverse_vocab[token_id] for token_id in token_ids)

    def tokenize(self, text: str) -> list[str]:
        tokens: list[str] = []
        index = 0
        structural_tokens = ("<tool_call>", "</tool_call>")
        while index < len(text):
            structural = next(
                (token for token in structural_tokens if text.startswith(token, index)),
                None,
            )
            if structural is not None:
                tokens.append(structural)
                index += len(structural)
            else:
                tokens.append(text[index])
                index += 1
        return tokens


def _find_tool_properties(tools: list[Any], function_name: str) -> dict[str, Any]:
    for tool in tools:
        function = getattr(tool, "function", None)
        if getattr(function, "name", None) == function_name:
            parameters = getattr(function, "parameters", {}) or {}
            return dict(parameters.get("properties") or {})
    return {}


def _extract_types_from_schema(schema: dict[str, Any]) -> list[str]:
    value = schema.get("type")
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str)]
    return []


def _coerce_to_schema_type(value: str, param_types: list[str]) -> Any:
    if "integer" in param_types:
        return int(value)
    if "number" in param_types:
        return float(value)
    if "boolean" in param_types:
        return value.strip().lower() == "true"
    if "array" in param_types or "object" in param_types:
        return json.loads(value)
    if "null" in param_types and value.strip().lower() == "null":
        return None
    return value


def _install_import_boundary() -> None:
    """Install only dependencies required to execute the native parser files."""
    sys.modules["regex"] = re

    _package("openai")
    _package("openai.types")
    responses = _package("openai.types.responses")
    for name in (
        "ResponseFormatTextJSONSchemaConfig",
        "ResponseTextConfig",
        "ResponseFunctionToolCall",
        "ResponseOutputItem",
        "ResponseOutputMessage",
        "ResponseOutputText",
        "ResponseReasoningItem",
        "ToolChoiceFunction",
    ):
        setattr(responses, name, type(name, (_Record,), {}))
    _module("openai.types.responses.function_tool", FunctionTool=FunctionTool)
    _module(
        "openai.types.responses.response_output_text",
        Logprob=type("Logprob", (_Record,), {}),
    )
    _module(
        "openai.types.responses.response_reasoning_item",
        Content=type("Content", (_Record,), {}),
    )

    _package("vllm", VLLM_ROOT)
    _package("vllm.entrypoints")
    _module("vllm.entrypoints.chat_utils", make_tool_call_id=lambda **_: "call_stub")
    _package("vllm.entrypoints.openai")
    _package("vllm.entrypoints.openai.chat_completion")
    _module(
        "vllm.entrypoints.openai.chat_completion.protocol",
        ChatCompletionNamedToolChoiceParam=ChatCompletionNamedToolChoiceParam,
        ChatCompletionRequest=ChatCompletionRequest,
        ChatCompletionToolsParam=ChatCompletionToolsParam,
    )
    _package("vllm.entrypoints.openai.engine")
    _module(
        "vllm.entrypoints.openai.engine.protocol",
        DeltaFunctionCall=DeltaFunctionCall,
        DeltaMessage=DeltaMessage,
        DeltaToolCall=DeltaToolCall,
        ExtractedToolCallInformation=ExtractedToolCallInformation,
        FunctionCall=FunctionCall,
        FunctionDefinition=FunctionDefinition,
        ToolCall=ToolCall,
    )
    _package("vllm.entrypoints.openai.responses")
    _module(
        "vllm.entrypoints.openai.responses.protocol",
        ResponsesRequest=ResponsesRequest,
    )
    _module("vllm.envs", VLLM_ENFORCE_STRICT_TOOL_CALLING=False)
    _module("vllm.logger", init_logger=lambda name: logging.getLogger(name))
    _module("vllm.sampling_params", StructuredOutputsParams=StructuredOutputsParams)
    _module("vllm.tokenizers", TokenizerLike=TokenizerLike)

    _package("vllm.tool_parsers", VLLM_ROOT / "tool_parsers")
    _module(
        "vllm.tool_parsers.utils",
        Tool=Any,
        coerce_to_schema_type=_coerce_to_schema_type,
        extract_types_from_schema=_extract_types_from_schema,
        find_tool_properties=_find_tool_properties,
        get_json_schema_from_tools=lambda **_: {},
    )
    _module(
        "vllm.tool_parsers.structural_tag_registry",
        get_enable_structured_outputs_in_reasoning=lambda: False,
        get_model_structural_tag=lambda **_: None,
    )
    _module(
        "vllm.tool_parsers.streaming",
        extract_named_tool_call_streaming=lambda **_: (None, False),
        extract_required_tool_call_streaming=lambda **_: (None, False),
    )

    _package("vllm.reasoning")
    _module(
        "vllm.reasoning.abs_reasoning_parsers",
        ReasoningParser=ReasoningParser,
    )
    utils = _package("vllm.utils")
    utils.random_uuid = lambda: "uuid_stub"
    _module("vllm.utils.collection_utils", is_list_of=lambda *_: False)
    _module("vllm.utils.import_utils", import_from_path=lambda *_: None)
    _module("vllm.utils.mistral", is_mistral_tool_parser=lambda *_: False)
    _package("vllm.parser", VLLM_ROOT / "parser")


@dataclass(frozen=True)
class InputDelta:
    text: str
    token_ids: tuple[int, ...]
    finished: bool = False


def _build_minimized_trajectories(
) -> tuple[dict[str, Any], dict[str, list[InputDelta]]]:
    """Build a small fixture reduced from the archived line-14 failure.

    The full server analysis remains on ``debug_tool_call``. The minimized
    fixture keeps the causally relevant shape: after header and JSON-open
    deltas, the final parameter and both XML close tags arrive in one finished
    source delta. Values are synthetic so raw commands and paths do not enter
    the production branch.
    """
    tokenizer = FakeTokenizer()
    texts = [
        "<tool_call>\n<function=bash>\n",
        "<parameter=command>\necho ok\n</parameter>\n",
        "\n",
        (
            "<parameter=workdir>\n/work\n</parameter>\n"
            "</function>\n</tool_call>"
        ),
    ]
    bundled = [
        InputDelta(
            text=text,
            token_ids=tuple(tokenizer.encode(text)),
            finished=index == len(texts) - 1,
        )
        for index, text in enumerate(texts)
    ]

    terminal = bundled[-1]
    close_text = "</function>\n</tool_call>"
    close_index = terminal.text.find(close_text)
    if close_index <= 0:
        raise AssertionError("fixture terminal does not bundle parameter and close tags")
    prefix_text = terminal.text[:close_index]
    prefix_ids = tuple(tokenizer.encode(prefix_text))
    close_ids = tuple(tokenizer.encode(close_text))

    separate = [
        *bundled[:-1],
        InputDelta(prefix_text, prefix_ids),
        InputDelta(close_text, close_ids, finished=True),
    ]
    synthetic = [
        *bundled[:-1],
        InputDelta(terminal.text, terminal.token_ids),
        InputDelta("", (), finished=True),
    ]
    fixture = {
        "case_id": "line14_attempt1_dtb_final_answer",
        "source_archive_branch": "debug_tool_call",
        "source_archive_commit": "821ded1",
        "source_run": "docs/debug/tool_call/run_after_handoff_fix_14",
        "source_request_id": "chatcmpl-tbtool-l14-a1-dtb-20260814T085612Z",
        "minimized": True,
        "synthetic_parameter_values": True,
        "preserved_failure_shape": "final parameter and XML close in one delta",
        "terminal_text": terminal.text,
        "terminal_token_ids": list(terminal.token_ids),
        "partition_assumption": "all trajectories have identical text and token IDs",
    }
    return fixture, {
        "separate_close": separate,
        "bundled_source_terminal": bundled,
        "synthetic_empty_terminal": synthetic,
    }


def _message_dict(message: DeltaMessage | None) -> dict[str, Any] | None:
    if message is None:
        return None
    return {
        "content": message.content,
        "reasoning": message.reasoning,
        "tool_calls": [
            {
                "index": call.index,
                "has_id": call.id is not None,
                "name": call.function.name if call.function else None,
                "arguments": call.function.arguments if call.function else None,
            }
            for call in (message.tool_calls or [])
        ],
    }


def _state_dict(tool_parser: Any) -> dict[str, Any]:
    return {
        "param_count": tool_parser.param_count,
        "in_function": tool_parser.in_function,
        "json_started": tool_parser.json_started,
        "json_closed": tool_parser.json_closed,
        "streamed_args_for_tool": list(tool_parser.streamed_args_for_tool),
        "prev_arguments": [
            item.get("arguments") for item in tool_parser.prev_tool_call_arr
        ],
    }


def _assemble(messages: list[DeltaMessage | None]) -> dict[str, Any]:
    calls: dict[int, dict[str, Any]] = {}
    content = ""
    for message in messages:
        if message is None:
            continue
        content += message.content or ""
        for call in message.tool_calls or []:
            item = calls.setdefault(call.index, {"name": None, "arguments": ""})
            if call.function is not None:
                if call.function.name is not None:
                    item["name"] = call.function.name
                if call.function.arguments is not None:
                    item["arguments"] += call.function.arguments
    ordered = [calls[index] for index in sorted(calls)]
    invalid_indexes: list[int] = []
    parsed_arguments: list[Any] = []
    for index, call in enumerate(ordered):
        try:
            parsed_arguments.append(json.loads(call["arguments"]))
        except json.JSONDecodeError:
            invalid_indexes.append(index)
            parsed_arguments.append(None)
    return {
        "content": content,
        "tool_calls": ordered,
        "parsed_arguments": parsed_arguments,
        "invalid_argument_indexes": invalid_indexes,
    }


def _assemble_reconstructor(reconstructor: Any) -> dict[str, Any]:
    calls = [
        {
            "name": call.function.name,
            "arguments": call.function.arguments,
        }
        for call in reconstructor.tool_calls
    ]
    invalid_indexes: list[int] = []
    parsed_arguments: list[Any] = []
    for index, call in enumerate(calls):
        try:
            parsed_arguments.append(json.loads(call["arguments"]))
        except json.JSONDecodeError:
            invalid_indexes.append(index)
            parsed_arguments.append(None)
    return {
        "content": reconstructor.other_content,
        "tool_calls": calls,
        "parsed_arguments": parsed_arguments,
        "invalid_argument_indexes": invalid_indexes,
    }


def _run_direct(
    parser_cls: type,
    tokenizer: FakeTokenizer,
    tools: list[Any],
    request: ChatCompletionRequest,
    deltas: list[InputDelta],
) -> dict[str, Any]:
    parser = parser_cls(tokenizer, tools=tools)
    previous_text = ""
    previous_ids: list[int] = []
    messages: list[DeltaMessage | None] = []
    trace: list[dict[str, Any]] = []
    for sequence, delta in enumerate(deltas, start=1):
        current_text = previous_text + delta.text
        delta_ids = list(delta.token_ids)
        current_ids = previous_ids + delta_ids
        message = parser.extract_tool_calls_streaming(
            previous_text=previous_text,
            current_text=current_text,
            delta_text=delta.text,
            previous_token_ids=previous_ids,
            current_token_ids=current_ids,
            delta_token_ids=delta_ids,
            request=request,
        )
        messages.append(message)
        trace.append({
            "sequence": sequence,
            "input": asdict(delta),
            "output": _message_dict(message),
            "state": _state_dict(parser),
        })
        previous_text = current_text
        previous_ids = current_ids
    return {"assembled": _assemble(messages), "trace": trace}


def _run_delegating(
    delegating_cls: type,
    parser_cls: type,
    tokenizer: FakeTokenizer,
    tools: list[Any],
    request: ChatCompletionRequest,
    deltas: list[InputDelta],
) -> dict[str, Any]:
    parser = delegating_cls(tokenizer, tools=tools)
    parser.tool_parser = parser_cls(tokenizer, tools=tools)
    messages: list[DeltaMessage | None] = []
    trace: list[dict[str, Any]] = []
    for sequence, delta in enumerate(deltas, start=1):
        message = parser.parse_delta(
            delta_text=delta.text,
            delta_token_ids=list(delta.token_ids),
            request=request,
            prompt_token_ids=[] if sequence == 1 else None,
            finished=delta.finished,
        )
        messages.append(message)
        trace.append({
            "sequence": sequence,
            "input": asdict(delta),
            "output": _message_dict(message),
            "stream_state": {
                "reasoning_ended": parser._stream_state.reasoning_ended,
                "previous_text_length": len(parser._stream_state.previous_text),
                "previous_token_count": len(parser._stream_state.previous_token_ids),
            },
            "tool_state": _state_dict(parser.tool_parser),
        })
    return {"assembled": _assemble(messages), "trace": trace}


def _run_upstream_string_helper(
    test_utils: types.ModuleType,
    parser_cls: type,
    tokenizer: FakeTokenizer,
    tools: list[Any],
    request: ChatCompletionRequest,
    deltas: list[InputDelta],
) -> dict[str, Any]:
    """Run vLLM's own test helper as a secondary, string-delta control."""
    parser = parser_cls(tokenizer, tools=tools)
    reconstructor = test_utils.run_tool_extraction_streaming(
        parser,
        [delta.text for delta in deltas],
        request,
        assert_one_tool_per_delta=False,
    )
    return {
        "assembled": _assemble_reconstructor(reconstructor),
        "trace": [],
        "exact_archived_token_ids_used": False,
        "finished_semantics_covered": False,
    }


def _run_upstream_token_control(
    stream_generator: Any,
    parser_cls: type,
    tokenizer: FakeTokenizer,
    tools: list[Any],
    request: ChatCompletionRequest,
    logical_text: str,
) -> dict[str, Any]:
    """Exercise vLLM's exact stream_delta_message_generator definition."""
    parser = parser_cls(tokenizer, tools=tools)
    messages = list(stream_generator(parser, tokenizer, logical_text, request))
    return {
        "assembled": _assemble(messages),
        "purpose": (
            "one-token-per-callback success control using the upstream "
            "stream_delta_message_generator definition"
        ),
        "exact_archived_token_ids_used": False,
        "finished_semantics_covered": False,
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path.resolve())


def _detokenize_incrementally_stub(
    *,
    tokenizer: FakeTokenizer,
    all_input_ids: list[int],
    prev_tokens: list[str] | None,
    prefix_offset: int,
    read_offset: int,
    **_: Any,
) -> tuple[list[str], str, int, int]:
    del prev_tokens, prefix_offset
    decoded = tokenizer.decode(all_input_ids)
    delta_text = decoded[read_offset:]
    return [delta_text], delta_text, 0, len(decoded)


def run(qwen_parser_source: Path | None = None) -> dict[str, Any]:
    _install_import_boundary()
    base_path = VLLM_ROOT / "tool_parsers" / "abstract_tool_parser.py"
    qwen_path = (
        qwen_parser_source.resolve()
        if qwen_parser_source is not None
        else VLLM_ROOT / "tool_parsers" / "qwen3coder_tool_parser.py"
    )
    if not qwen_path.is_file():
        raise FileNotFoundError(f"Qwen parser source does not exist: {qwen_path}")
    delegating_path = VLLM_ROOT / "parser" / "abstract_parser.py"
    test_utils_path = VLLM_TESTS_ROOT / "tool_parsers" / "utils.py"
    qwen_test_path = VLLM_TESTS_ROOT / "tool_parsers" / "test_qwen3coder_tool_parser.py"
    base_module = _load_source("vllm.tool_parsers.abstract_tool_parser", base_path)
    sys.modules["vllm.tool_parsers"].ToolParser = base_module.ToolParser
    qwen_module = _load_source(
        "vllm.tool_parsers.qwen3coder_tool_parser", qwen_path)
    delegating_module = _load_source("vllm.parser.abstract_parser", delegating_path)
    _package("tests", VLLM_TESTS_ROOT)
    _package("tests.tool_parsers", VLLM_TESTS_ROOT / "tool_parsers")
    test_utils = _load_source("tests.tool_parsers.utils", test_utils_path)
    stream_generator = _load_function_from_source(
        qwen_test_path,
        "stream_delta_message_generator",
        {
            "Generator": Generator,
            "TokenizerLike": TokenizerLike,
            "ChatCompletionRequest": ChatCompletionRequest,
            "DeltaMessage": DeltaMessage,
            "detokenize_incrementally": _detokenize_incrementally_stub,
        },
    )

    tokenizer = FakeTokenizer()
    tools = [ChatCompletionToolsParam(
        type="function",
        function=SimpleNamespace(
            name="bash",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "description": {"type": "string"},
                    "timeout": {"type": "integer"},
                    "workdir": {"type": "string"},
                },
            },
        ),
    )]
    request = ChatCompletionRequest(tools=tools, tool_choice="auto")
    fixture, trajectories = _build_minimized_trajectories()

    logical_texts = {
        name: "".join(delta.text for delta in deltas)
        for name, deltas in trajectories.items()
    }
    logical_ids = {
        name: [token for delta in deltas for token in delta.token_ids]
        for name, deltas in trajectories.items()
    }
    token_by_token_control = _run_upstream_token_control(
        stream_generator,
        qwen_module.Qwen3CoderToolParser,
        tokenizer,
        tools,
        request,
        logical_texts["separate_close"],
    )
    layers: dict[str, dict[str, Any]] = {
        "direct_tool_parser": {},
        "delegating": {},
        "upstream_test_utility": {},
    }
    for name, deltas in trajectories.items():
        layers["direct_tool_parser"][name] = _run_direct(
            qwen_module.Qwen3CoderToolParser,
            tokenizer,
            tools,
            request,
            deltas,
        )
        layers["delegating"][name] = _run_delegating(
            delegating_module.DelegatingParser,
            qwen_module.Qwen3CoderToolParser,
            tokenizer,
            tools,
            request,
            deltas,
        )
        layers["upstream_test_utility"][name] = _run_upstream_string_helper(
            test_utils,
            qwen_module.Qwen3CoderToolParser,
            tokenizer,
            tools,
            request,
            deltas,
        )

    equivalence: dict[str, Any] = {}
    for layer_name, results in layers.items():
        assembled = {name: result["assembled"] for name, result in results.items()}
        fingerprints = {
            name: json.dumps(value, ensure_ascii=False, sort_keys=True)
            for name, value in assembled.items()
        }
        baseline = fingerprints["separate_close"]
        baseline_arguments = assembled["separate_close"]["tool_calls"][0][
            "arguments"
        ]
        missing_suffix: dict[str, str | None] = {}
        terminal_observations: dict[str, Any] = {}
        for name, result in results.items():
            arguments = assembled[name]["tool_calls"][0]["arguments"]
            missing_suffix[name] = (
                baseline_arguments[len(arguments):]
                if baseline_arguments.startswith(arguments)
                else None
            )
            terminal_trace = next(
                (
                    row
                    for row in result["trace"]
                    if row["input"]["finished"]
                ),
                None,
            )
            terminal_observations[name] = (
                {
                    "input_text": terminal_trace["input"]["text"],
                    "input_token_ids": terminal_trace["input"]["token_ids"],
                    "output": terminal_trace["output"],
                    "tool_state": terminal_trace.get(
                        "state", terminal_trace.get("tool_state")
                    ),
                }
                if terminal_trace is not None
                else None
            )
        equivalence[layer_name] = {
            "all_partitions_equal": len(set(fingerprints.values())) == 1,
            "separate_close_valid_json": not assembled["separate_close"][
                "invalid_argument_indexes"],
            "bundled_source_terminal_valid_json": not assembled[
                "bundled_source_terminal"]["invalid_argument_indexes"],
            "synthetic_empty_terminal_valid_json": not assembled[
                "synthetic_empty_terminal"]["invalid_argument_indexes"],
            "differs_from_separate_close": [
                name for name, fingerprint in fingerprints.items()
                if fingerprint != baseline
            ],
            "argument_suffix_missing_from_separate_close": missing_suffix,
            "terminal_observations": terminal_observations,
        }

    return {
        "source_files": {
            "abstract_tool_parser": {
                "path": str(base_path.relative_to(ROOT)),
                "sha256": _sha256(base_path),
            },
            "qwen3coder_tool_parser": {
                "path": _display_path(qwen_path),
                "sha256": _sha256(qwen_path),
            },
            "delegating_parser": {
                "path": str(delegating_path.relative_to(ROOT)),
                "sha256": _sha256(delegating_path),
            },
            "upstream_test_utility": {
                "path": str(test_utils_path.relative_to(ROOT)),
                "sha256": _sha256(test_utils_path),
            },
            "upstream_stream_generator": {
                "path": str(qwen_test_path.relative_to(ROOT)),
                "sha256": _sha256(qwen_test_path),
                "definition": "stream_delta_message_generator",
            },
        },
        "import_boundary": {
            "native_parser_source_executed": True,
            "model_loaded": False,
            "torch_required": False,
            "protocol_types_stubbed": True,
            "parser_logic_stubbed": False,
            "upstream_streaming_test_utility_executed": True,
            "upstream_stream_delta_generator_executed": True,
        },
        "fixture": fixture,
        "input_invariants": {
            "logical_text_equal": len(set(logical_texts.values())) == 1,
            "logical_token_ids_equal": len({tuple(ids) for ids in logical_ids.values()}) == 1,
            "finished_count_by_trajectory": {
                name: sum(delta.finished for delta in deltas)
                for name, deltas in trajectories.items()
            },
            "logical_text": logical_texts["separate_close"],
            "logical_token_ids": logical_ids["separate_close"],
        },
        "controls": {
            "upstream_token_by_token_pattern": token_by_token_control,
        },
        "equivalence": equivalence,
        "layers": layers,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--summary-only",
        action="store_true",
        help="omit per-delta traces from stdout",
    )
    parser.add_argument(
        "--require-equivalence",
        action="store_true",
        help="exit non-zero when either native parser layer is partition-sensitive",
    )
    parser.add_argument(
        "--qwen-parser-source",
        type=Path,
        help=(
            "load Qwen3CoderToolParser from this source file while keeping "
            "the remaining vLLM sources fixed"
        ),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run(args.qwen_parser_source)
    if args.summary_only:
        layers = result["layers"]
        result = {
            key: value for key, value in result.items() if key != "layers"
        }
        result["assembled"] = {
            layer: {
                trajectory: data["assembled"]
                for trajectory, data in trajectories.items()
            }
            for layer, trajectories in layers.items()
        }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.require_equivalence and not all(
        row["all_partitions_equal"] for row in result["equivalence"].values()
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
