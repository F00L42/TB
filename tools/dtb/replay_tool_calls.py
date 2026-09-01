#!/usr/bin/env python3
"""Paired, repeatable streaming tool-call probe for a vLLM endpoint."""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import os
import re
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:  # The offline analyzer/tests do not require the client.
    httpx = None


FIXED_FIELDS = (
    "fixed_budget_tokens",
    "fixed_check_length",
    "fixed_think_stride",
    "fixed_num_thinking",
    "fixed_min_think_tokens",
    "fixed_prob_th",
)
REQUEST_ID_RUN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
LEAKED_XML_RE = re.compile(
    r"(?:<\/?tool_call>|<\/?function(?:=|>)|<\/?parameter(?:=|>))"
    r"|(?:^|\n)function=[^\n>]+>",
    re.MULTILINE,
)


def build_request_id(
    line_no: int,
    attempt: int,
    variant: str,
    request_id_run: str,
) -> str:
    return f"dtbtool-l{line_no}-a{attempt}-{variant}-{request_id_run}"


ROLE_TAIL_RE = re.compile(
    r"(?:^|\n)(?:system|user|assistant|tool)\s*(?:\n|$)|<\|im_(?:start|end)\|>"
)


@dataclass
class ToolCallState:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass(frozen=True)
class ReplayCase:
    line_no: int
    case_id: str
    category: str
    request_body: dict[str, Any]
    expected_tool_call: bool
    expected_tool_names: tuple[str, ...]
    latency_eligible: bool


@dataclass
class Observation:
    line_no: int
    case_id: str
    category: str
    attempt: int
    variant: str
    client_request_id: str
    server_request_id: str
    status_code: int
    elapsed_ms: float
    expected_tool_call: bool
    expected_tool_names: list[str]
    latency_eligible: bool
    has_tool_call: bool
    finish_reasons: list[str]
    content: str
    reasoning: str
    tool_calls: list[dict[str, Any]]
    tool_call_indexes_contiguous: bool
    missing_tool_name_indexes: list[int]
    invalid_argument_indexes: list[int]
    invalid_schema_indexes: list[int]
    schema_errors: list[str]
    unexpected_tool_names: list[str]
    terminal_count: int
    terminal_event_indexes: list[int]
    tool_calls_finish_reason_count: int
    leaked_tool_xml: bool
    role_tail: bool
    content_after_tool_delta: bool
    chunk_after_terminal: bool
    protocol_anomaly_reasons: list[str]
    semantic_anomaly_reasons: list[str]
    anomaly_reasons: list[str]
    error: str = ""
    first_chunk_ms: float = 0.0
    first_output_ms: float = 0.0
    first_reasoning_ms: float = 0.0
    first_content_ms: float = 0.0
    first_tool_call_ms: float = 0.0
    chunk_count: int = 0
    usage: dict[str, Any] = field(default_factory=dict)


def json_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_cases(path: Path, start: int, end: int) -> list[ReplayCase]:
    cases: list[ReplayCase] = []
    with path.open("r", encoding="utf-8") as source:
        for line_no, line in enumerate(source, start=1):
            if line_no < start:
                continue
            if end > 0 and line_no > end:
                break
            if not line.strip():
                continue
            record = json.loads(line)
            body = record.get("request_body")
            if isinstance(body, str):
                body = json.loads(body)
            if not isinstance(body, dict):
                raise ValueError(f"line {line_no} has no object request_body")
            expectation = record.get("replay_expectation") or {}
            archived_expected = response_has_tool_call(record.get("response_data"))
            expected_value = expectation.get("expected_tool_call")
            expected = (
                expected_value if isinstance(expected_value, bool)
                else archived_expected
            )
            expected_names_value = expectation.get("expected_tool_names") or []
            if not isinstance(expected_names_value, list) or not all(
                isinstance(name, str) and name for name in expected_names_value
            ):
                raise ValueError(
                    f"line {line_no} expected_tool_names must be non-empty strings"
                )
            conversation_errors = validate_conversation(body.get("messages", []))
            if conversation_errors:
                joined = "; ".join(conversation_errors)
                raise ValueError(f"line {line_no} invalid conversation: {joined}")
            cases.append(ReplayCase(
                line_no=line_no,
                case_id=str(record.get("case_id") or f"line-{line_no}"),
                category=str(expectation.get("category") or "archived_request"),
                request_body=body,
                expected_tool_call=expected,
                expected_tool_names=tuple(expected_names_value),
                latency_eligible=bool(expectation.get("latency_eligible", True)),
            ))
    if not cases:
        raise ValueError("no request cases selected")
    return cases


def response_has_tool_call(response: Any) -> bool:
    if isinstance(response, str):
        try:
            response = json.loads(response)
        except json.JSONDecodeError:
            return "tool_calls" in response and "function" in response
    if not isinstance(response, dict):
        return False
    tool_calls = response.get("tool_calls")
    if isinstance(tool_calls, str):
        return bool(tool_calls.strip())
    return bool(tool_calls)


def validate_conversation(messages: Any) -> list[str]:
    if not isinstance(messages, list):
        return ["messages is not a list"]
    pending: set[str] = set()
    errors: list[str] = []
    for index, message in enumerate(messages):
        if not isinstance(message, dict):
            errors.append(f"message[{index}] is not an object")
            continue
        role = message.get("role")
        if role == "assistant":
            pending = {
                str(call.get("id"))
                for call in message.get("tool_calls") or []
                if isinstance(call, dict) and call.get("id")
            }
        elif role == "tool":
            tool_call_id = str(message.get("tool_call_id") or "")
            if not tool_call_id:
                errors.append(f"message[{index}] tool role has no tool_call_id")
            elif tool_call_id not in pending:
                errors.append(
                    f"message[{index}] tool_call_id={tool_call_id!r} "
                    "does not match the preceding assistant tool calls"
                )
            else:
                pending.remove(tool_call_id)
        elif pending:
            errors.append(
                f"message[{index}] role={role!r} appears before tool results "
                f"for {sorted(pending)}"
            )
            pending.clear()
    return errors


def tool_schemas(tools: Any) -> dict[str, dict[str, Any]]:
    schemas: dict[str, dict[str, Any]] = {}
    if not isinstance(tools, list):
        return schemas
    for tool in tools:
        if not isinstance(tool, dict) or tool.get("type") != "function":
            continue
        function = tool.get("function") or {}
        name = function.get("name")
        parameters = function.get("parameters")
        if isinstance(name, str) and isinstance(parameters, dict):
            schemas[name] = parameters
    return schemas


def _json_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def validate_json_schema(
    value: Any,
    schema: dict[str, Any],
    path: str = "$",
) -> list[str]:
    """Validate the JSON-schema subset used by OpenAI function tools."""
    errors: list[str] = []
    expected_type = schema.get("type")
    expected_types = (
        expected_type if isinstance(expected_type, list) else [expected_type]
    )
    expected_types = [item for item in expected_types if isinstance(item, str)]
    if expected_types and not any(
        _json_type_matches(value, item) for item in expected_types
    ):
        return [
            f"{path}: expected {'|'.join(expected_types)}, "
            f"got {type(value).__name__}"
        ]

    if isinstance(value, dict):
        required = schema.get("required") or []
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in value:
                    errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties") or {}
        if isinstance(properties, dict):
            for name, child_schema in properties.items():
                if name in value and isinstance(child_schema, dict):
                    errors.extend(validate_json_schema(
                        value[name], child_schema, f"{path}.{name}"
                    ))
    elif isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(validate_json_schema(
                item, schema["items"], f"{path}[{index}]"
            ))
    return errors


def build_body(
    source: dict,
    *,
    variant: str,
    client_request_id: str,
    args: argparse.Namespace,
) -> dict:
    body = copy.deepcopy(source)
    body["stream"] = True
    body["stream_options"] = {"include_usage": True}
    body["request_id"] = client_request_id
    if args.model:
        body["model"] = args.model
    if args.max_tokens is not None and args.max_tokens > 0:
        body["max_tokens"] = args.max_tokens
        body.pop("max_completion_tokens", None)
    if args.temperature is not None:
        body["temperature"] = args.temperature
    if args.top_p is not None:
        body["top_p"] = args.top_p

    if variant == "dtb":
        body["enable_think_budget"] = True
        for field in FIXED_FIELDS:
            value = getattr(args, field)
            if value is not None:
                body[field] = value
    else:
        body["enable_think_budget"] = False
        for field in FIXED_FIELDS:
            body.pop(field, None)
    return body


def parse_sse_data(line: str) -> dict | None:
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload or payload == "[DONE]":
        return None
    return json.loads(payload)


def assemble_observation(
    *,
    line_no: int,
    attempt: int,
    variant: str,
    client_request_id: str,
    status_code: int,
    elapsed_ms: float,
    expected_tool_call: bool,
    chunks: list[dict],
    case_id: str = "",
    category: str = "archived_request",
    expected_tool_names: tuple[str, ...] = (),
    request_tools: Any = None,
    latency_eligible: bool = True,
    error: str = "",
    first_chunk_ms: float = 0.0,
    first_output_ms: float = 0.0,
    first_reasoning_ms: float = 0.0,
    first_content_ms: float = 0.0,
    first_tool_call_ms: float = 0.0,
) -> Observation:
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reasons: list[str] = []
    calls: dict[int, ToolCallState] = {}
    server_request_id = ""
    saw_tool_delta = False
    content_after_tool_delta = False
    chunk_after_terminal = False
    terminal_event_indexes: list[int] = []
    terminal_seen = False
    usage: dict[str, Any] = {}

    for event_index, chunk in enumerate(chunks, start=1):
        server_request_id = server_request_id or str(chunk.get("id") or "")
        if isinstance(chunk.get("usage"), dict) and chunk["usage"]:
            usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            finish_reason = choice.get("finish_reason")
            if terminal_seen and (finish_reason or delta):
                chunk_after_terminal = True
            if finish_reason:
                finish_reasons.append(str(finish_reason))
                terminal_event_indexes.append(event_index)
                terminal_seen = True
            content = delta.get("content")
            if isinstance(content, str):
                content_parts.append(content)
                if saw_tool_delta and content:
                    content_after_tool_delta = True
            reasoning = delta.get("reasoning")
            if isinstance(reasoning, str):
                reasoning_parts.append(reasoning)
            for call_delta in delta.get("tool_calls") or []:
                saw_tool_delta = True
                index = int(call_delta.get("index", 0))
                state = calls.setdefault(index, ToolCallState(index=index))
                if call_delta.get("id"):
                    state.id = str(call_delta["id"])
                function = call_delta.get("function") or {}
                if function.get("name"):
                    state.name = str(function["name"])
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    state.arguments += arguments

    tool_calls = [asdict(calls[index]) for index in sorted(calls)]
    tool_indexes = [int(call["index"]) for call in tool_calls]
    tool_call_indexes_contiguous = tool_indexes == list(range(len(tool_calls)))
    missing_tool_name_indexes = [
        int(call["index"]) for call in tool_calls if not call["name"]
    ]
    invalid_argument_indexes: list[int] = []
    invalid_schema_indexes: list[int] = []
    schema_errors: list[str] = []
    schemas = tool_schemas(request_tools)
    for call in tool_calls:
        try:
            arguments = json.loads(call["arguments"])
        except (json.JSONDecodeError, TypeError):
            invalid_argument_indexes.append(int(call["index"]))
            continue
        schema = schemas.get(str(call["name"]))
        if schema is not None:
            call_errors = validate_json_schema(arguments, schema)
            if call_errors:
                invalid_schema_indexes.append(int(call["index"]))
                schema_errors.extend(
                    f"tool[{call['index']}] {call['name']}: {message}"
                    for message in call_errors
                )

    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    leaked_tool_xml = bool(LEAKED_XML_RE.search(content))
    role_tail = bool(ROLE_TAIL_RE.search(content))
    has_tool_call = bool(tool_calls)
    returned_tool_names = sorted({
        str(call["name"]) for call in tool_calls if call["name"]
    })
    unexpected_tool_names = (
        returned_tool_names
        if expected_tool_names
        and not set(returned_tool_names).intersection(expected_tool_names)
        else []
    )
    terminal_count = len(terminal_event_indexes)
    tool_calls_finish_reason_count = finish_reasons.count("tool_calls")
    anomaly_reasons: list[str] = []
    if error:
        anomaly_reasons.append("request_error")
    if expected_tool_call and not has_tool_call:
        anomaly_reasons.append("expected_tool_call_missing")
    if has_tool_call and unexpected_tool_names:
        anomaly_reasons.append("expected_tool_name_missing")
    if leaked_tool_xml:
        anomaly_reasons.append("tool_xml_leaked_to_content")
    if role_tail:
        anomaly_reasons.append("role_tail_in_content")
    if content_after_tool_delta:
        anomaly_reasons.append("content_after_tool_delta")
    if invalid_argument_indexes:
        anomaly_reasons.append("invalid_tool_arguments_json")
    if invalid_schema_indexes:
        anomaly_reasons.append("invalid_tool_arguments_schema")
    if missing_tool_name_indexes:
        anomaly_reasons.append("missing_tool_name")
    if not tool_call_indexes_contiguous:
        anomaly_reasons.append("non_contiguous_tool_call_indexes")
    if has_tool_call and tool_calls_finish_reason_count == 0:
        anomaly_reasons.append("tool_call_without_tool_calls_finish_reason")
    if has_tool_call and tool_calls_finish_reason_count > 1:
        anomaly_reasons.append("multiple_tool_calls_finish_reasons")
    if not error and status_code == 200 and terminal_count != 1:
        anomaly_reasons.append(
            "missing_terminal" if terminal_count == 0 else "multiple_terminals"
        )
    if chunk_after_terminal:
        anomaly_reasons.append("chunk_after_terminal")
    semantic_reason_names = {
        "expected_tool_call_missing",
        "expected_tool_name_missing",
        "invalid_tool_arguments_schema",
    }
    semantic_anomaly_reasons = [
        reason for reason in anomaly_reasons
        if reason in semantic_reason_names
    ]
    protocol_anomaly_reasons = [
        reason for reason in anomaly_reasons
        if reason not in semantic_reason_names
    ]

    return Observation(
        line_no=line_no,
        case_id=case_id or f"line-{line_no}",
        category=category,
        attempt=attempt,
        variant=variant,
        client_request_id=client_request_id,
        server_request_id=server_request_id,
        status_code=status_code,
        elapsed_ms=round(elapsed_ms, 3),
        expected_tool_call=expected_tool_call,
        expected_tool_names=list(expected_tool_names),
        latency_eligible=latency_eligible,
        has_tool_call=has_tool_call,
        finish_reasons=finish_reasons,
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        tool_call_indexes_contiguous=tool_call_indexes_contiguous,
        missing_tool_name_indexes=missing_tool_name_indexes,
        invalid_argument_indexes=invalid_argument_indexes,
        invalid_schema_indexes=invalid_schema_indexes,
        schema_errors=schema_errors,
        unexpected_tool_names=unexpected_tool_names,
        terminal_count=terminal_count,
        terminal_event_indexes=terminal_event_indexes,
        tool_calls_finish_reason_count=tool_calls_finish_reason_count,
        leaked_tool_xml=leaked_tool_xml,
        role_tail=role_tail,
        content_after_tool_delta=content_after_tool_delta,
        chunk_after_terminal=chunk_after_terminal,
        protocol_anomaly_reasons=protocol_anomaly_reasons,
        semantic_anomaly_reasons=semantic_anomaly_reasons,
        anomaly_reasons=anomaly_reasons,
        error=error,
        first_chunk_ms=round(first_chunk_ms, 3),
        first_output_ms=round(first_output_ms, 3),
        first_reasoning_ms=round(first_reasoning_ms, 3),
        first_content_ms=round(first_content_ms, 3),
        first_tool_call_ms=round(first_tool_call_ms, 3),
        chunk_count=len(chunks),
        usage=usage,
    )


async def run_one(
    client: Any,
    *,
    url: str,
    headers: dict[str, str],
    body: dict,
    line_no: int,
    case_id: str,
    category: str,
    attempt: int,
    variant: str,
    expected_tool_call: bool,
    expected_tool_names: tuple[str, ...],
    latency_eligible: bool,
    chunks_file,
) -> Observation:
    client_request_id = str(body["request_id"])
    chunks: list[dict] = []
    status_code = 0
    error = ""
    started = time.perf_counter()
    first_chunk_ms = 0.0
    first_output_ms = 0.0
    first_reasoning_ms = 0.0
    first_content_ms = 0.0
    first_tool_call_ms = 0.0
    try:
        request_headers = dict(headers)
        request_headers["X-Request-Id"] = client_request_id
        async with client.stream(
            "POST", url, json=body, headers=request_headers
        ) as response:
            status_code = response.status_code
            if status_code != 200:
                error = (await response.aread()).decode("utf-8", errors="replace")
            else:
                event_index = 0
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    event_index += 1
                    parsed = None
                    parse_error = ""
                    try:
                        parsed = parse_sse_data(line)
                    except json.JSONDecodeError as exc:
                        parse_error = str(exc)
                    record = {
                        "client_request_id": client_request_id,
                        "line_no": line_no,
                        "attempt": attempt,
                        "variant": variant,
                        "event_index": event_index,
                        "raw_sse_line": line,
                        "chunk": parsed,
                        "parse_error": parse_error,
                    }
                    chunks_file.write(
                        json.dumps(record, ensure_ascii=False) + "\n"
                    )
                    chunks_file.flush()
                    if parsed is not None:
                        chunks.append(parsed)
                        elapsed_ms = (time.perf_counter() - started) * 1000
                        if first_chunk_ms == 0.0:
                            first_chunk_ms = elapsed_ms
                        for choice in parsed.get("choices") or []:
                            delta = choice.get("delta") or {}
                            reasoning = delta.get("reasoning")
                            content = delta.get("content")
                            tool_calls = delta.get("tool_calls")
                            if (
                                first_output_ms == 0.0
                                and (reasoning or content or tool_calls)
                            ):
                                first_output_ms = elapsed_ms
                            if first_reasoning_ms == 0.0 and reasoning:
                                first_reasoning_ms = elapsed_ms
                            if first_content_ms == 0.0 and content:
                                first_content_ms = elapsed_ms
                            if first_tool_call_ms == 0.0 and tool_calls:
                                first_tool_call_ms = elapsed_ms
                    if parse_error and not error:
                        error = f"invalid SSE JSON: {parse_error}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return assemble_observation(
        line_no=line_no,
        case_id=case_id,
        category=category,
        attempt=attempt,
        variant=variant,
        client_request_id=client_request_id,
        status_code=status_code,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        expected_tool_call=expected_tool_call,
        expected_tool_names=expected_tool_names,
        request_tools=body.get("tools"),
        latency_eligible=latency_eligible,
        chunks=chunks,
        error=error,
        first_chunk_ms=first_chunk_ms,
        first_output_ms=first_output_ms,
        first_reasoning_ms=first_reasoning_ms,
        first_content_ms=first_content_ms,
        first_tool_call_ms=first_tool_call_ms,
    )


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def metric_summary(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "median": round(statistics.median(values), 3) if values else 0.0,
        "p95": round(percentile(values, 0.95), 3),
    }


def usage_int(observation: Observation, key: str) -> int:
    value = observation.usage.get(key, 0)
    return int(value) if isinstance(value, (int, float)) else 0


def build_paired_summary(observations: list[Observation]) -> dict[str, Any]:
    variants: dict[str, Any] = {}
    for variant in sorted({row.variant for row in observations}):
        rows = [row for row in observations if row.variant == variant]
        variants[variant] = {
            "requests": len(rows),
            "protocol_passes": sum(
                not row.protocol_anomaly_reasons for row in rows
            ),
            "protocol_failures": sum(
                bool(row.protocol_anomaly_reasons) for row in rows
            ),
            "semantic_clean": sum(
                not row.semantic_anomaly_reasons for row in rows
            ),
            "semantic_deviations": sum(
                bool(row.semantic_anomaly_reasons) for row in rows
            ),
            "overall_passes": sum(not row.anomaly_reasons for row in rows),
            "overall_failures": sum(bool(row.anomaly_reasons) for row in rows),
            "request_errors": sum(bool(row.error) for row in rows),
            "elapsed_ms": metric_summary([row.elapsed_ms for row in rows]),
            "first_output_ms": metric_summary([
                row.first_output_ms for row in rows if row.first_output_ms > 0
            ]),
            "prompt_tokens": sum(usage_int(row, "prompt_tokens") for row in rows),
            "completion_tokens": sum(
                usage_int(row, "completion_tokens") for row in rows
            ),
        }

    grouped: dict[tuple[int, int], dict[str, Observation]] = {}
    for row in observations:
        grouped.setdefault((row.line_no, row.attempt), {})[row.variant] = row
    pairs = [
        rows for rows in grouped.values()
        if "vanilla" in rows and "dtb" in rows
    ]
    protocol_clean_pairs = [
        rows for rows in pairs
        if not rows["vanilla"].protocol_anomaly_reasons
        and not rows["dtb"].protocol_anomaly_reasons
        and rows["vanilla"].latency_eligible
        and rows["dtb"].latency_eligible
    ]
    fully_clean_pairs = [
        rows for rows in protocol_clean_pairs
        if not rows["vanilla"].semantic_anomaly_reasons
        and not rows["dtb"].semantic_anomaly_reasons
        and rows["vanilla"].latency_eligible
        and rows["dtb"].latency_eligible
    ]

    def paired_metrics(rows: list[dict[str, Observation]]) -> dict[str, Any]:
        deltas = [
            pair["dtb"].elapsed_ms - pair["vanilla"].elapsed_ms
            for pair in rows
        ]
        speedups = [
            (pair["vanilla"].elapsed_ms - pair["dtb"].elapsed_ms)
            / pair["vanilla"].elapsed_ms
            * 100
            for pair in rows
            if pair["vanilla"].elapsed_ms > 0
        ]
        return {
            "pairs": len(rows),
            "dtb_minus_vanilla_ms": metric_summary(deltas),
            "dtb_speedup_percent": metric_summary(speedups),
            "dtb_faster_pairs": sum(
                pair["dtb"].elapsed_ms < pair["vanilla"].elapsed_ms
                for pair in rows
            ),
        }

    per_case = []
    for line_no in sorted({row.line_no for row in observations}):
        line_rows = [row for row in observations if row.line_no == line_no]
        case_pairs = [
            rows for (case_line, _), rows in grouped.items()
            if case_line == line_no and "vanilla" in rows and "dtb" in rows
        ]
        per_case.append({
            "line_no": line_no,
            "case_id": line_rows[0].case_id,
            "category": line_rows[0].category,
            **paired_metrics(case_pairs),
        })

    return {
        "request_count": len(observations),
        "variants": variants,
        "all_pairs": paired_metrics(pairs),
        "protocol_clean_pairs": paired_metrics(protocol_clean_pairs),
        "fully_clean_pairs": paired_metrics(fully_clean_pairs),
        "per_case": per_case,
        "latency_ranking_valid": (
            bool(pairs) and len(fully_clean_pairs) == len(pairs)
        ),
    }


def write_result_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# Tool-call paired streaming replay",
        "",
        "## Protocol",
        "",
        "| Variant | Requests | Protocol pass | Protocol failure | Semantic deviation | Overall failure | E2E median ms | E2E P95 ms | First output median ms |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for variant, values in summary["variants"].items():
        lines.append(
            "| {variant} | {requests} | {passes} | {failures} | {semantic} | {overall} | {median} | {p95} | {first} |".format(
                variant=variant,
                requests=values["requests"],
                passes=values["protocol_passes"],
                failures=values["protocol_failures"],
                semantic=values["semantic_deviations"],
                overall=values["overall_failures"],
                median=values["elapsed_ms"]["median"],
                p95=values["elapsed_ms"]["p95"],
                first=values["first_output_ms"]["median"],
            )
        )
    clean = summary["protocol_clean_pairs"]
    fully_clean = summary["fully_clean_pairs"]
    lines.extend([
        "",
        "## Paired latency",
        "",
        f"- All pairs: {summary['all_pairs']['pairs']}",
        f"- Protocol-clean pairs: {clean['pairs']}",
        f"- Fully clean pairs: {fully_clean['pairs']}",
        f"- Median DTB minus Vanilla: {fully_clean['dtb_minus_vanilla_ms']['median']} ms",
        f"- Median DTB speedup: {fully_clean['dtb_speedup_percent']['median']}%",
        f"- DTB faster fully clean pairs: {fully_clean['dtb_faster_pairs']}/{fully_clean['pairs']}",
        f"- Latency ranking valid: {summary['latency_ranking_valid']}",
        "",
        "Latency ranking is valid only when every latency-eligible pair is free "
        "of protocol anomalies; run quality probes separately from ranking.",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=0, help="0 means EOF")
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument(
        "--request-id-run",
        default="",
        help="stable suffix used to pre-allowlist stream-audit request IDs",
    )
    parser.add_argument(
        "--list-dtb-request-ids",
        action="store_true",
        help="print deterministic DTB request IDs and exit without sending",
    )
    parser.add_argument(
        "--mode", choices=("both", "dtb", "vanilla"), default="both"
    )
    parser.add_argument(
        "--order",
        choices=("alternating", "fixed"),
        default="alternating",
        help="counterbalance variant order across repetitions",
    )
    parser.add_argument(
        "--url", default="http://127.0.0.1:9000/v1/chat/completions"
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--max-tokens", type=int, default=None,
        help="override the archived value; default preserves each source request",
    )
    parser.add_argument(
        "--temperature", type=float, default=None,
        help="override the archived value; default preserves each source request",
    )
    parser.add_argument(
        "--top-p", type=float, default=None,
        help="override the archived value; default preserves each source request",
    )
    parser.add_argument("--fixed-budget-tokens", type=int, default=20000)
    parser.add_argument("--fixed-check-length", type=int, default=850)
    parser.add_argument("--fixed-think-stride", type=int, default=500)
    parser.add_argument("--fixed-num-thinking", type=int, default=5)
    parser.add_argument("--fixed-min-think-tokens", type=int, default=1)
    parser.add_argument("--fixed-prob-th", type=float, default=0.93)
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    cases = load_cases(args.file, args.start, args.end)
    variants = (
        ("vanilla", "dtb")
        if args.mode == "both"
        else (args.mode,)
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    request_id_run = args.request_id_run or timestamp
    if args.list_dtb_request_ids:
        if "dtb" in variants:
            for case in cases:
                for attempt in range(1, args.repetitions + 1):
                    print("chatcmpl-" + build_request_id(
                        case.line_no, attempt, "dtb", request_id_run
                    ))
        return 0
    if httpx is None:
        raise RuntimeError(
            "httpx is required to send requests: python -m pip install httpx"
        )
    output_dir = args.output_dir or Path(__file__).parent / f"run_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    chunks_path = output_dir / "chunks.jsonl"
    summary_path = output_dir / "summary.jsonl"
    manifest_path = output_dir / "manifest.json"
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input_file": str(args.file.resolve()),
        "input_sha256": hashlib.sha256(args.file.read_bytes()).hexdigest(),
        "line_range": [args.start, args.end],
        "repetitions": args.repetitions,
        "request_id_run": request_id_run,
        "variants": list(variants),
        "variant_order": args.order,
        "request_id_header": "X-Request-Id",
        "stream_audit_parent_request_id_prefix": "chatcmpl-",
        "url": args.url,
        "model_override": args.model or None,
        "request_overrides": {
            key: value
            for key, value in vars(args).items()
            if key.startswith("fixed_")
            or key in {"max_tokens", "temperature", "top_p"}
        },
        "cases": [
            {
                "line_no": case.line_no,
                "case_id": case.case_id,
                "category": case.category,
                "expected_tool_call": case.expected_tool_call,
                "expected_tool_names": list(case.expected_tool_names),
                "latency_eligible": case.latency_eligible,
            }
            for case in cases
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    failures = 0
    observations: list[Observation] = []
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        with chunks_path.open("w", encoding="utf-8") as chunks_file, summary_path.open(
            "w", encoding="utf-8"
        ) as summary_file:
            for case in cases:
                line_no = case.line_no
                for attempt in range(1, args.repetitions + 1):
                    attempt_variants = variants
                    if (
                        args.order == "alternating"
                        and len(variants) == 2
                        and attempt % 2 == 0
                    ):
                        attempt_variants = tuple(reversed(variants))
                    for variant in attempt_variants:
                        request_id = build_request_id(
                            line_no, attempt, variant, request_id_run
                        )
                        body = build_body(
                            case.request_body,
                            variant=variant,
                            client_request_id=request_id,
                            args=args,
                        )
                        chunks_file.write(json.dumps({
                            "record_type": "request",
                            "client_request_id": request_id,
                            "line_no": line_no,
                            "case_id": case.case_id,
                            "category": case.category,
                            "attempt": attempt,
                            "variant": variant,
                            "request_body_sha256": json_hash(body),
                        }, ensure_ascii=False) + "\n")
                        chunks_file.flush()
                        observation = await run_one(
                            client,
                            url=args.url,
                            headers=headers,
                            body=body,
                            line_no=line_no,
                            case_id=case.case_id,
                            category=case.category,
                            attempt=attempt,
                            variant=variant,
                            expected_tool_call=case.expected_tool_call,
                            expected_tool_names=case.expected_tool_names,
                            latency_eligible=case.latency_eligible,
                            chunks_file=chunks_file,
                        )
                        observations.append(observation)
                        summary_file.write(
                            json.dumps(asdict(observation), ensure_ascii=False) + "\n"
                        )
                        summary_file.flush()
                        failed = bool(observation.anomaly_reasons)
                        failures += int(failed)
                        status = "FAIL" if failed else "PASS"
                        reasons = ",".join(observation.anomaly_reasons) or "none"
                        print(
                            f"{status} case={case.case_id} line={line_no} "
                            f"attempt={attempt} "
                            f"variant={variant} request_id="
                            f"{observation.server_request_id or request_id} "
                            f"tool_calls={len(observation.tool_calls)} "
                            f"reasons={reasons}",
                            flush=True,
                        )

    paired_summary = build_paired_summary(observations)
    (output_dir / "paired_summary.json").write_text(
        json.dumps(paired_summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_result_markdown(output_dir / "RESULT.md", paired_summary)
    print(
        f"completed output={output_dir} failures={failures}",
        flush=True,
    )
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.start < 1 or args.end < 0 or (args.end and args.end < args.start):
        raise ValueError("invalid --start/--end range")
    if args.repetitions < 1:
        raise ValueError("--repetitions must be positive")
    if args.request_id_run and not REQUEST_ID_RUN_RE.fullmatch(
        args.request_id_run
    ):
        raise ValueError(
            "--request-id-run may contain only letters, digits, dot, dash, underscore"
        )
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
