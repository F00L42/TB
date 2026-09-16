"""Read-only parser diagnostics with real local tokenizers and dependency stubs.

Executes fixed vLLM source, applying repository Qwen patches only in memory.
This is neither a vLLM installation nor an HTTP/EngineCore/NPU validation.
Reported mismatches are diagnostic results, not a failing migration test suite.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import sys
from typing import Any
import warnings

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.dtb import parser_partition_equivalence as h
from tools.dtb import reasoning_tail_fidelity as f


def load_parsers():
    import regex
    from transformers import AutoTokenizer

    tokenizers = {
        key: AutoTokenizer.from_pretrained(
            ROOT / "docs/tokenizer" / name, local_files_only=True, trust_remote_code=False
        ) for key, name in (("qwen", "Qwen3.6-27B"), ("glm", "GLM-5.3"))
    }
    qreason, qtool, unified = f.load_native(f.patched_source(f.REASONER, f.PATCH))
    sys.modules["regex"] = regex
    # Protocol records only: emulate Pydantic's dict-to-nested-record conversion.
    h.DeltaFunctionCall.model_dump = lambda self, **kw: {
        k: v for k, v in asdict(self).items()
        if not kw.get("exclude_none") or v is not None
    }

    @dataclass
    class DeltaToolCall(h.DeltaToolCall):
        def __post_init__(self):
            if isinstance(self.function, dict):
                self.function = h.DeltaFunctionCall(**self.function)

    sys.modules["vllm.entrypoints.openai.engine.protocol"].DeltaToolCall = DeltaToolCall
    # Execute actual coercion/schema helpers rather than simplified harness helpers.
    utils_path = h.VLLM_ROOT / "tool_parsers/utils.py"
    utils = sys.modules["vllm.tool_parsers.utils"]
    utils.__dict__.update(ast=ast, json=json, warnings=warnings,
                          FunctionTool=h.FunctionTool,
                          ChatCompletionToolsParam=h.ChatCompletionToolsParam)
    tree = ast.parse(utils_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "_TYPE_ALIASES":
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(utils_path), "exec"), utils.__dict__)
    for name in ("safe_literal_eval", "partial_tag_overlap", "_extract_tool_info",
                 "find_tool_properties", "extract_types_from_schema", "coerce_to_schema_type"):
        h._load_function_from_source(utils_path, name, utils.__dict__)
    # Qwen imports functions by name, so reload it after the real helpers are bound.
    qtool = f.load_text("vllm.tool_parsers.qwen_patched_compare", f.patched_source(
        "vllm/tool_parsers/qwen3coder_tool_parser.py",
        ROOT / "patches/vllm/v0.23.0/qwen3_coder_parser.patch")).Qwen3CoderToolParser
    qnative = h._load_source("vllm.reasoning.qwen_native_compare", h.VLLM_ROOT / "reasoning/qwen3_reasoning_parser.py").Qwen3ReasoningParser
    qtool_native = h._load_source("vllm.tool_parsers.qwen_native_compare", h.VLLM_ROOT / "tool_parsers/qwen3coder_tool_parser.py").Qwen3CoderToolParser
    sys.modules["vllm.reasoning"].ReasoningParser = sys.modules["vllm.reasoning.abs_reasoning_parsers"].ReasoningParser
    for name in ("deepseek_r1_reasoning_parser", "identity_reasoning_parser", "deepseek_v3_reasoning_parser"):
        mod = h._load_source("vllm.reasoning." + name, h.VLLM_ROOT / "reasoning" / (name + ".py"))
    greason = mod.DeepSeekV3ReasoningWithThinkingParser
    for name in ("glm4_moe_tool_parser", "glm47_moe_tool_parser"):
        mod = h._load_source("vllm.tool_parsers." + name, h.VLLM_ROOT / "tool_parsers" / (name + ".py"))
    return tokenizers, unified, {
        "qwen_native": (qnative, qtool_native, "qwen"),
        "qwen_repo_patched": (qreason, qtool, "qwen"),
        "glm_native": (greason, mod.Glm47MoeModelToolParser, "glm"),
    }


def tools_for(schema_type):
    return [h.ChatCompletionToolsParam(type="function", function=h.FunctionDefinition(
        name="lookup", parameters={"type": "object", "properties": {"x": {"type": schema_type}}}
    ))]


def tool_text(family, value=None):
    if family == "glm":
        args = "" if value is None else f"<arg_key>x</arg_key><arg_value>{value}</arg_value>"
        return f"<tool_call>lookup{args}</tool_call>"
    args = "" if value is None else f"<parameter=x>{value}</parameter>"
    return f"<tool_call><function=lookup>{args}</function></tool_call>"


def normalized_calls(calls):
    result = []
    for call in calls:
        fn = call.function
        raw = fn.arguments or ""
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {"INVALID_JSON": raw}
        result.append({"name": fn.name, "arguments": args})
    return result


def run():
    tokenizers, unified, parsers = load_parsers()
    report = {"scope": "Real local tokenizers; native parser bodies and coercion helpers; stubbed dependency/protocol records. No HTTP, serving finalization, native detokenizer or NPU.",
              "source_sha256": {}, "reasoning": [], "tools": [], "token_only_tool_start": []}
    paths = [h.VLLM_ROOT / "reasoning" / n for n in (
        "qwen3_reasoning_parser.py", "deepseek_v3_reasoning_parser.py",
        "deepseek_r1_reasoning_parser.py", "basic_parsers.py", "identity_reasoning_parser.py")]
    paths += [h.VLLM_ROOT / "tool_parsers" / n for n in (
        "qwen3coder_tool_parser.py", "glm47_moe_tool_parser.py", "glm4_moe_tool_parser.py", "utils.py", "abstract_tool_parser.py")]
    paths += [h.VLLM_ROOT / "parser/abstract_parser.py", f.PATCH,
              ROOT / "patches/vllm/v0.23.0/qwen3_coder_parser.patch"]
    for path in paths:
        report["source_sha256"][path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    for label, (reason_cls, tool_cls, family) in parsers.items():
        tok = tokenizers[family]
        encode = lambda s: tok.encode(s, add_special_tokens=False)
        for case, text, ids in (
            ("explicit_close", "analysis</think>answer", encode("analysis</think>answer")),
            ("implicit_tool", "analysis" + tool_text(family), encode("analysis" + tool_text(family))),
            ("stripped_terminal_close", "analysis", encode("analysis</think>")),
            ("token_only_close", "", encode("</think>")),
        ):
            parser = reason_cls(tok)
            delta = parser.extract_reasoning_streaming("", text, text, [], ids, ids)
            report["reasoning"].append({"parser": label, "case": case,
                "input_text": text, "input_ids": ids,
                "ended": parser.is_reasoning_end_streaming([], ids),
                "delta": asdict(delta) if delta else None})
        # Direct-tool contract: structural start ID retained, marker text absent.
        text = tool_text(family, "ok")
        ids = encode(text)
        assert ids[0] == tok.get_vocab()["<tool_call>"]
        tools = tools_for("string")
        tool = tool_cls(tok, tools)
        request = f.Request(tools=tools)
        first = tool.extract_tool_calls_streaming("", "", "", [], ids[:1], ids[:1], request)
        tail = text[len("<tool_call>"):]
        last = tool.extract_tool_calls_streaming("", tail, tail, ids[:1], ids, ids[1:], request)
        report["token_only_tool_start"].append({"parser": label,
            "source_text": text, "source_token_ids": ids,
            "deltas": [asdict(x) if x else None for x in (first, last)]})
        for schema_type, value in (("string", None), ("string", "a\\b\nline\"2"),
                                    ("array", "[1,2]"), ("number", "1e2"),
                                    ("boolean", "true")):
            text = tool_text(family, value)
            tools = tools_for(schema_type)
            request = f.Request(tools=tools)
            expected = normalized_calls(tool_cls(tok, tools).extract_tool_calls(text, request).tool_calls)
            for mode in ("direct", "unified"):
                source = ("analysis</think>" if mode == "unified" else "") + text
                ids = encode(source)
                for chunk_size in (1, 4, len(ids)):
                    tool = tool_cls(tok, tools)
                    parser = unified(tok, tools=tools) if mode == "unified" else tool
                    if mode == "unified":
                        parser.reasoning_parser = reason_cls(tok)
                        parser.tool_parser = tool
                    prev, prev_ids, calls, content, reasoning, events = "", [], {}, "", "", []
                    for end in range(chunk_size, len(ids) + chunk_size, chunk_size):
                        end = min(end, len(ids))
                        current_ids = ids[:end]
                        current = tok.decode(current_ids, skip_special_tokens=False)
                        assert current.startswith(prev), "ASCII fixture must have prefix-stable decoding"
                        delta_text, delta_ids = current[len(prev):], current_ids[len(prev_ids):]
                        if mode == "direct":
                            delta = parser.extract_tool_calls_streaming(prev, current, delta_text, prev_ids, current_ids, delta_ids, request)
                        else:
                            delta = parser.parse_delta(delta_text, delta_ids, request,
                                prompt_token_ids=encode("<think>"), finished=end == len(ids))
                        if delta:
                            events.append({"at_token": end, "delta": asdict(delta)})
                            content += delta.content or ""
                            reasoning += delta.reasoning or ""
                            for call in delta.tool_calls or []:
                                acc = calls.setdefault(call.index, h.ToolCall(function=h.FunctionCall(name="", arguments="")))
                                acc.function.name += call.function.name or ""
                                acc.function.arguments += call.function.arguments or ""
                        prev, prev_ids = current, current_ids
                    actual = normalized_calls([calls[k] for k in sorted(calls)])
                    report["tools"].append({"parser": label, "case": "zero_args" if value is None else schema_type,
                        "mode": mode, "chunk_size_tokens": chunk_size, "source_text": source,
                        "source_token_ids": ids, "expected": expected, "actual": actual,
                        "content": content, "reasoning": reasoning,
                        "matches_nonstream_tools": expected == actual, "events": events})
    out = ROOT / "docs/tokenizer/parser_comparison.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for label in parsers:
        rows = [r for r in report["tools"] if r["parser"] == label]
        mismatches = [r for r in rows if not r["matches_nonstream_tools"]]
        print(label, "tool replays", len(rows), "mismatches", len(mismatches))
        for row in mismatches:
            print(" ", row["case"], row["mode"], row["chunk_size_tokens"], row["actual"])
    print(out)


if __name__ == "__main__":
    run()
