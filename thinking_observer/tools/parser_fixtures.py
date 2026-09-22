"""CPU-only data/import fixtures extracted from the baseline parser harness.

Provenance: TB f342e127 tools/dtb/parser_partition_equivalence.py.
These stubs replace third-party data dependencies, NEVER parser algorithms.
Used only by verify_native.py in its isolated subprocess.
"""
from __future__ import annotations
import importlib.util
import json
import logging
import re
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
VLLM_ROOT = Path('.')


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
    id: str | None = None


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
    if "pydantic" not in sys.modules:
        class ValidationError(Exception):
            pass

        class TypeAdapter:
            def __init__(self, *_: Any, **__: Any) -> None:
                pass

            def validate_json(self, *_: Any, **__: Any) -> Any:
                raise ValidationError("unused parser-equivalence stub")

        _module(
            "pydantic",
            TypeAdapter=TypeAdapter,
            ValidationError=ValidationError,
        )

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
