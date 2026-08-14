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
import sys
import time
from dataclasses import asdict, dataclass
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
LEAKED_XML_RE = re.compile(
    r"(?:<\/?tool_call>|<\/?function(?:=|>)|<\/?parameter(?:=|>))"
    r"|(?:^|\n)function=[^\n>]+>",
    re.MULTILINE,
)
ROLE_TAIL_RE = re.compile(
    r"(?:^|\n)(?:system|user|assistant|tool)\s*(?:\n|$)|<\|im_(?:start|end)\|>"
)


@dataclass
class ToolCallState:
    index: int
    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class Observation:
    line_no: int
    attempt: int
    variant: str
    client_request_id: str
    server_request_id: str
    status_code: int
    elapsed_ms: float
    expected_tool_call: bool
    has_tool_call: bool
    finish_reasons: list[str]
    content: str
    reasoning: str
    tool_calls: list[dict[str, Any]]
    tool_call_indexes_contiguous: bool
    missing_tool_name_indexes: list[int]
    invalid_argument_indexes: list[int]
    terminal_count: int
    terminal_event_indexes: list[int]
    tool_calls_finish_reason_count: int
    leaked_tool_xml: bool
    role_tail: bool
    content_after_tool_delta: bool
    chunk_after_terminal: bool
    anomaly_reasons: list[str]
    error: str = ""


def json_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_cases(path: Path, start: int, end: int) -> list[tuple[int, dict, bool]]:
    cases: list[tuple[int, dict, bool]] = []
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
            expected = response_has_tool_call(record.get("response_data"))
            conversation_errors = validate_conversation(body.get("messages", []))
            if conversation_errors:
                joined = "; ".join(conversation_errors)
                raise ValueError(f"line {line_no} invalid conversation: {joined}")
            cases.append((line_no, body, expected))
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
    if args.max_tokens > 0:
        body["max_tokens"] = args.max_tokens
        body.pop("max_completion_tokens", None)
    body["temperature"] = args.temperature
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
    error: str = "",
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

    for event_index, chunk in enumerate(chunks, start=1):
        server_request_id = server_request_id or str(chunk.get("id") or "")
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
    invalid_argument_indexes = []
    for call in tool_calls:
        try:
            json.loads(call["arguments"])
        except (json.JSONDecodeError, TypeError):
            invalid_argument_indexes.append(int(call["index"]))

    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    leaked_tool_xml = bool(LEAKED_XML_RE.search(content))
    role_tail = bool(ROLE_TAIL_RE.search(content))
    has_tool_call = bool(tool_calls)
    terminal_count = len(terminal_event_indexes)
    tool_calls_finish_reason_count = finish_reasons.count("tool_calls")
    anomaly_reasons: list[str] = []
    if error:
        anomaly_reasons.append("request_error")
    if expected_tool_call and not has_tool_call:
        anomaly_reasons.append("expected_tool_call_missing")
    if leaked_tool_xml:
        anomaly_reasons.append("tool_xml_leaked_to_content")
    if role_tail:
        anomaly_reasons.append("role_tail_in_content")
    if content_after_tool_delta:
        anomaly_reasons.append("content_after_tool_delta")
    if invalid_argument_indexes:
        anomaly_reasons.append("invalid_tool_arguments_json")
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

    return Observation(
        line_no=line_no,
        attempt=attempt,
        variant=variant,
        client_request_id=client_request_id,
        server_request_id=server_request_id,
        status_code=status_code,
        elapsed_ms=round(elapsed_ms, 3),
        expected_tool_call=expected_tool_call,
        has_tool_call=has_tool_call,
        finish_reasons=finish_reasons,
        content=content,
        reasoning=reasoning,
        tool_calls=tool_calls,
        tool_call_indexes_contiguous=tool_call_indexes_contiguous,
        missing_tool_name_indexes=missing_tool_name_indexes,
        invalid_argument_indexes=invalid_argument_indexes,
        terminal_count=terminal_count,
        terminal_event_indexes=terminal_event_indexes,
        tool_calls_finish_reason_count=tool_calls_finish_reason_count,
        leaked_tool_xml=leaked_tool_xml,
        role_tail=role_tail,
        content_after_tool_delta=content_after_tool_delta,
        chunk_after_terminal=chunk_after_terminal,
        anomaly_reasons=anomaly_reasons,
        error=error,
    )


async def run_one(
    client: Any,
    *,
    url: str,
    headers: dict[str, str],
    body: dict,
    line_no: int,
    attempt: int,
    variant: str,
    expected_tool_call: bool,
    chunks_file,
) -> Observation:
    client_request_id = str(body["request_id"])
    chunks: list[dict] = []
    status_code = 0
    error = ""
    started = time.perf_counter()
    try:
        async with client.stream(
            "POST", url, json=body, headers=headers
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
                    if parse_error and not error:
                        error = f"invalid SSE JSON: {parse_error}"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

    return assemble_observation(
        line_no=line_no,
        attempt=attempt,
        variant=variant,
        client_request_id=client_request_id,
        status_code=status_code,
        elapsed_ms=(time.perf_counter() - started) * 1000,
        expected_tool_call=expected_tool_call,
        chunks=chunks,
        error=error,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=0, help="0 means EOF")
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument(
        "--mode", choices=("both", "dtb", "vanilla"), default="both"
    )
    parser.add_argument(
        "--url", default="http://127.0.0.1:9000/v1/chat/completions"
    )
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key", default=os.getenv("OPENAI_API_KEY", ""))
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--max-tokens", type=int, default=32000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--top-p", type=float, default=0.95)
    parser.add_argument("--fixed-budget-tokens", type=int, default=30000)
    parser.add_argument("--fixed-check-length", type=int, default=256)
    parser.add_argument("--fixed-think-stride", type=int, default=128)
    parser.add_argument("--fixed-num-thinking", type=int, default=3)
    parser.add_argument("--fixed-min-think-tokens", type=int, default=20)
    parser.add_argument("--fixed-prob-th", type=float, default=0.9)
    return parser.parse_args(argv)


async def async_main(args: argparse.Namespace) -> int:
    if httpx is None:
        raise RuntimeError(
            "httpx is required to send requests: python -m pip install httpx"
        )
    cases = load_cases(args.file, args.start, args.end)
    variants = (
        ("vanilla", "dtb")
        if args.mode == "both"
        else (args.mode,)
    )
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
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
        "variants": list(variants),
        "url": args.url,
        "model_override": args.model or None,
        "request_overrides": {
            key: value
            for key, value in vars(args).items()
            if key.startswith("fixed_")
            or key in {"max_tokens", "temperature", "top_p"}
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    failures = 0
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(max_connections=1, max_keepalive_connections=1)
    async with httpx.AsyncClient(timeout=timeout, limits=limits) as client:
        with chunks_path.open("w", encoding="utf-8") as chunks_file, summary_path.open(
            "w", encoding="utf-8"
        ) as summary_file:
            for line_no, source_body, expected in cases:
                for attempt in range(1, args.repetitions + 1):
                    for variant in variants:
                        request_id = (
                            f"tbtool-l{line_no}-a{attempt}-{variant}-{timestamp}"
                        )
                        body = build_body(
                            source_body,
                            variant=variant,
                            client_request_id=request_id,
                            args=args,
                        )
                        chunks_file.write(json.dumps({
                            "record_type": "request",
                            "client_request_id": request_id,
                            "line_no": line_no,
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
                            attempt=attempt,
                            variant=variant,
                            expected_tool_call=expected,
                            chunks_file=chunks_file,
                        )
                        summary_file.write(
                            json.dumps(asdict(observation), ensure_ascii=False) + "\n"
                        )
                        summary_file.flush()
                        failed = bool(observation.anomaly_reasons)
                        failures += int(failed)
                        status = "FAIL" if failed else "PASS"
                        reasons = ",".join(observation.anomaly_reasons) or "none"
                        print(
                            f"{status} line={line_no} attempt={attempt} "
                            f"variant={variant} request_id="
                            f"{observation.server_request_id or request_id} "
                            f"tool_calls={len(observation.tool_calls)} "
                            f"reasons={reasons}",
                            flush=True,
                        )

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
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130)
