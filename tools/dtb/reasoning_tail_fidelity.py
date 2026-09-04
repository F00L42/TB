#!/usr/bin/env python3
"""No-model Qwen terminal-stop reasoning fidelity gate.

Executes native parser sources and the native detokenizer base classes, with
dependency/data stubs and a character tokenizer. Optional archived deltas use
their recorded text and real token IDs, never a re-tokenized approximation.
This is not an EngineCore, HTTP serving, or NPU integration test.
"""
from __future__ import annotations

import argparse
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
import types
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.dtb import parser_partition_equivalence as harness

VLLM = harness.VLLM_CHECKOUT
REASONER = "vllm/reasoning/qwen3_reasoning_parser.py"
TESTS = "tests/reasoning/test_qwen3_reasoning_parser.py"
PATCH = ROOT / "patches/vllm/v0.23.0/qwen3_reasoning_parser.patch"


def patched_source(relative_path: str, patch_path: Path) -> str:
    """Apply verified unified hunks in memory without changing the checkout."""
    source = (VLLM / relative_path).read_text(encoding="utf-8").splitlines(True)
    lines = patch_path.read_text(encoding="utf-8").splitlines(True)
    result, cursor, active, index, hunks = [], 0, False, 0, 0
    while index < len(lines):
        line = lines[index]
        if line.startswith("--- "):
            active = line[4:].split()[0] == "a/" + relative_path
        if active and line.startswith("@@ "):
            start = int(re.match(r"@@ -(\d+)", line).group(1)) - 1
            result.extend(source[cursor:start])
            cursor = start
            index += 1
            hunks += 1
            while index < len(lines) and lines[index][:1] in (" ", "+", "-"):
                if lines[index].startswith("--- "):
                    break
                kind, text = lines[index][0], lines[index][1:]
                if kind in (" ", "-"):
                    assert source[cursor] == text, (relative_path, cursor + 1)
                    cursor += 1
                if kind in (" ", "+"):
                    result.append(text)
                index += 1
            continue
        index += 1
    assert hunks, (relative_path, "no patch hunks")
    result.extend(source[cursor:])
    return "".join(result)


class Tokenizer(harness.FakeTokenizer):
    def get_vocab(self):
        return {
            **super().get_vocab(), "<think>": 248068, "</think>": 248069,
            "<tool_call>": 248058, "</tool_call>": 248059,
        }

    def tokenize(self, text):
        return [
            token
            for part in re.split(r"(</?think>|</?tool_call>)", text)
            for token in ([part] if part in self.get_vocab() else list(part))
            if token
        ]

    def encode(self, text, add_special_tokens=False):
        del add_special_tokens
        vocab = self.get_vocab()
        return [vocab[token] if token in vocab else ord(token)
                for token in self.tokenize(text)]

    def decode(self, ids):
        inverse = {value: key for key, value in self.get_vocab().items()}
        return "".join(inverse[token] if token in inverse else chr(token)
                       for token in ids)


class Request(harness.ChatCompletionRequest):
    def __init__(self, **kwargs):
        super().__init__(tools=kwargs.pop("tools", []),
                         tool_choice=kwargs.pop("tool_choice", "auto"))
        self.__dict__.update(kwargs)


def load_text(name, source):
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(source, name, "exec"), module.__dict__)
    return module


def load_native(source, tool_parser_source=None):
    harness._install_import_boundary()
    harness._package("vllm.entrypoints.mcp")
    harness._module("vllm.entrypoints.mcp.tool_server", ToolServer=SimpleNamespace)
    for name in ("abs_reasoning_parsers", "basic_parsers"):
        harness._load_source(f"vllm.reasoning.{name}",
                             VLLM / f"vllm/reasoning/{name}.py")
    reasoner = load_text("vllm.reasoning.qwen3_reasoning_parser", source)
    base = harness._load_source("vllm.tool_parsers.abstract_tool_parser",
                               VLLM / "vllm/tool_parsers/abstract_tool_parser.py")
    sys.modules["vllm.tool_parsers"].ToolParser = base.ToolParser
    tool_source = (tool_parser_source.read_text(encoding="utf-8")
                   if tool_parser_source else patched_source(
                       "vllm/tool_parsers/qwen3coder_tool_parser.py",
                       ROOT / "patches/vllm/v0.23.0/qwen3_coder_parser.patch"))
    tools = load_text("vllm.tool_parsers.qwen3coder_tool_parser", tool_source)
    unified = harness._load_source("vllm.parser.abstract_parser",
                                  VLLM / "vllm/parser/abstract_parser.py")
    return reasoner.Qwen3ReasoningParser, tools.Qwen3CoderToolParser, unified.DelegatingParser


def run(source, replay_dir=None, tool_parser_source=None, server_logs=None,
        require_public_fidelity=False):
    reasoner_cls, tool_cls, unified_cls = load_native(source, tool_parser_source)
    tokenizer = Tokenizer()
    failures = []
    checks = defaultdict(int)

    def check(group, name, function):
        checks[group] += 1
        try:
            function()
        except AssertionError as error:
            failures.append({"group": group, "case": name, "error": str(error)})

    # Execute the regression test functions shipped in the upstream patch.
    tree = ast.parse(patched_source(TESTS, PATCH))
    namespace = {
        "Qwen3ReasoningParser": reasoner_cls, "DelegatingParser": unified_cls,
        "ChatCompletionRequest": Request,
    }
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith((
            "test_reasoning_terminal_stop", "test_reasoning_stripped_nonterminal",
        )):
            node.decorator_list = []  # Parameter values are supplied below.
            exec(compile(ast.Module(body=[node], type_ignores=[]), TESTS, "exec"),
                 namespace)
    body = ("<tool_call>\n<function=bash>\n<parameter=command>\npwd\n"
            "</parameter>\n</function>\n</tool_call>")
    cases = [
        ("test_reasoning_terminal_stop_id_preserves_prefix", (tail,))
        for tail in (" different approach.\n", "时间戳。\n", "\n", "")
    ] + [("test_reasoning_stripped_nonterminal_end_id_keeps_existing_behavior", ())]
    cases += [("test_reasoning_terminal_stop_marker_present", (tail,))
              for tail in ("", "answer", body)]
    cases += [("test_reasoning_terminal_stop_id_delegating", (include,))
              for include in (False, True)]
    for name, args in cases:
        check("upstream_regression_functions", name + repr(args),
              lambda name=name, args=args: namespace[name](tokenizer, *args))

    def extract(cls, text, ids):
        parser = cls(tokenizer)
        return parser.extract_reasoning_streaming("", text, text, [], ids, ids)

    original_cls = load_text("original_qwen_reasoner",
                            (VLLM / REASONER).read_text(encoding="utf-8")).Qwen3ReasoningParser
    controls = [
        "reasoning", "<think>reasoning", "reasoning</think>",
        "<think>reasoning</think>answer", "reasoning</think>" + body,
        "reasoning" + body, "<think>reasoning" + body,
        "</think>", "",
    ]
    for text in controls:
        def control(text=text):
            ids = tokenizer.encode(text)
            assert extract(reasoner_cls, text, ids) == extract(original_cls, text, ids)
        check("unchanged_controls", text[:70], control)
    plain = "quoted </think> text"
    check("unchanged_controls", "ordinary_ids_literal_marker",
          lambda: assert_equal(extract(reasoner_cls, plain, list(map(ord, plain))),
                               extract(original_cls, plain, list(map(ord, plain)))))

    # Actual BaseIncrementalDetokenizer methods, only decode_next is a fixture.
    detok_path = VLLM / "vllm/v1/engine/detokenizer.py"
    nodes = [node for node in ast.parse(detok_path.read_text(encoding="utf-8")).body
             if isinstance(node, ast.ClassDef) and node.name in {
                 "IncrementalDetokenizer", "BaseIncrementalDetokenizer"}]
    import __future__
    from abc import ABC, abstractmethod
    ns = {"ABC": ABC, "abstractmethod": abstractmethod}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(detok_path), "exec",
                 flags=__future__.annotations.compiler_flag), ns)

    class Detokenizer(ns["BaseIncrementalDetokenizer"]):
        def decode_next(self, token_id):
            return tokenizer.decode([token_id])

    def detokenizer_integration(include, separate):
        params = SimpleNamespace(stop=[], min_tokens=0,
                                 stop_token_ids=[248069],
                                 include_stop_str_in_output=include)
        detok = Detokenizer(SimpleNamespace(sampling_params=params))
        parser = unified_cls(tokenizer)
        parser.reasoning_parser = reasoner_cls(tokenizer)
        end_id = parser.reasoning_parser.end_token_id
        request = Request(stream=True, stop_token_ids=[end_id],
                          include_stop_str_in_output=include)
        prefix, tail = "Let me try a", " different approach.\n"
        batches = [tokenizer.encode(prefix), tokenizer.encode(tail) + [end_id]]
        if separate:
            batches = [tokenizer.encode(prefix), tokenizer.encode(tail), [end_id]]
        messages, ids_seen = [], []
        for index, ids in enumerate(batches):
            finished = index == len(batches) - 1
            assert detok.update(list(ids), stop_terminated=finished) is None
            text = detok.get_next_output_text(finished=finished, delta=True)
            messages.append(parser.parse_delta(text, ids, request, finished=finished))
            ids_seen.extend(ids)
        assert "".join(m.reasoning or "" for m in messages if m) == prefix + tail
        assert "".join(m.content or "" for m in messages if m) == ""
        assert detok.output_token_ids == ids_seen
        assert len(detok.output_token_ids) == sum(map(len, batches))
        assert detok.output_text == prefix + tail + ("</think>" if include else "")
        assert parser._stream_state.reasoning_ended

    for include in (False, True):
        for separate in (False, True):
            check("native_detokenizer_unified", f"include={include},separate={separate}",
                  lambda i=include, s=separate: detokenizer_integration(i, s))

    archive_count = 0
    if replay_dir:
        rows = [json.loads(line) for line in (replay_dir / "summary.jsonl").read_text(
            encoding="utf-8").splitlines() if line.strip()]
        raw = defaultdict(list)
        log_paths = server_logs or sorted(replay_dir.glob("server_filtered_part_*"))
        for path in log_paths:
            for line in path.read_text(encoding="utf-8").splitlines():
                if "DTB_STREAM_AUDIT" not in line:
                    continue
                record = json.loads(line.split("DTB_STREAM_AUDIT", 1)[1])
                if record["phase"] in ("initial_think", "final_answer"):
                    raw[record["parent_request_id"]].append(record)
        archive_count = sum(row["variant"] == "dtb" for row in rows)
        cases_path = replay_dir.parent.parent / "input/cases.jsonl"
        selected_lines = {row["line_no"] for row in rows if row["variant"] == "dtb"}
        cases_by_line = {
            number: json.loads(line)
            for number, line in enumerate(
                cases_path.read_text(encoding="utf-8").splitlines(), 1)
            if number in selected_lines
        }
        for row in rows:
            if row["variant"] != "dtb":
                continue
            def archived(row=row):
                records = sorted(raw[row["server_request_id"]], key=lambda r: r["sequence"])
                assert records, "No source records"
                parser = unified_cls(tokenizer)
                parser.reasoning_parser = reasoner_cls(tokenizer)
                # All archived tool parameters are strings or numbers; use the
                # original request schemas to retain native parameter coercion.
                case = cases_by_line[row["line_no"]]
                tools = [harness.ChatCompletionToolsParam(
                    type="function", function=harness.FunctionDefinition(**t["function"]))
                    for t in case["request_body"]["tools"]]
                parser.tool_parser = tool_cls(tokenizer, tools=tools)
                messages = [parser.parse_delta(
                    r["forwarded_text"], list(r["forwarded_token_ids"]),
                    Request(tools=tools), finished=bool(r["forwarded_finished"]))
                    for r in records]
                expected = "".join(r["forwarded_text"] for r in records
                                   if r["phase"] == "initial_think")
                actual = "".join(m.reasoning or "" for m in messages if m)
                assert actual == expected, f"reasoning missing {len(expected) - len(actual)} chars"
                if require_public_fidelity:
                    assert row["reasoning"] == expected, "Public reasoning differs from source prefix"
                assembled = harness._assemble(messages)
                assert assembled["content"] == row["content"]
                assert assembled["tool_calls"] == [
                    {"name": t["name"], "arguments": t["arguments"]}
                    for t in row["tool_calls"]]
            check("archived_full_parser_chain", row["client_request_id"], archived)

    return {
        "reasoner_sha256_lf": hashlib.sha256(source.encode()).hexdigest(),
        "scope": "native source / stub dependencies; no model, HTTP or NPU",
        "checks": dict(checks), "archived_dtb_requests": archive_count,
        "public_fidelity_required": require_public_fidelity,
        "failure_count": len(failures), "failures": failures,
        "passed": not failures,
    }


def assert_equal(actual, expected):
    assert actual == expected, (actual, expected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply-repo-patch", action="store_true")
    group.add_argument("--reasoning-parser-source", type=Path)
    parser.add_argument("--tool-parser-source", type=Path)
    parser.add_argument("--replay-dir", type=Path)
    parser.add_argument("--server-logs", nargs="+", type=Path,
                        help="Default: replay-dir/server_filtered_part_*")
    parser.add_argument("--require-public-fidelity", action="store_true",
                        help="New server runs: also check recorded public reasoning")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    source = (patched_source(REASONER, PATCH) if args.apply_repo_patch else
              (args.reasoning_parser_source or VLLM / REASONER).read_text(encoding="utf-8"))
    if (args.server_logs or args.require_public_fidelity) and not args.replay_dir:
        parser.error("--server-logs/--require-public-fidelity requires --replay-dir")
    result = run(source, args.replay_dir, args.tool_parser_source, args.server_logs,
                 args.require_public_fidelity)
    payload = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
