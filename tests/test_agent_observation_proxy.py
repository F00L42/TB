from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path
import tempfile
import threading
import socket
import time

import httpx
import pytest

from proxy.observation.analysis import analyze_capture, read_json
from proxy.observation.capture import Capture
from proxy.observation.chat_completions import parse_response, sse_events
from proxy.observation.config import load_config
from proxy.observation.storage import RollingJsonlWriter
from proxy.observation.tokens import TokenCounter
from proxy.observation.trajectories import build_trajectories
from proxy.observation.trajectories import tool_history_links
from proxy.observation.transport import ObservationProxy, forward_headers


class WordTokenizer:
    def encode(self, text, add_special_tokens=False):
        return text.split()


class BytesStream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, fail=False):
        self.chunks = chunks
        self.fail = fail
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            await asyncio.sleep(0)
            yield chunk
        if self.fail:
            raise httpx.ReadError("simulated upstream failure")

    async def aclose(self):
        self.closed = True


def settings(tmp_path, **extra):
    overrides = {"capture": {"output_root": str(tmp_path)}, "report": {"live_interval_seconds": 0}}
    for key, value in extra.items():
        overrides.setdefault(key, {}).update(value)
    return load_config(overrides=overrides)


def response(message=None):
    return {"model": "test-model", "choices": [{"index": 0, "message": message or {
                "role": "assistant", "content": "done", "reasoning_content": "think first"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 17, "completion_tokens": 4}}


def encode(value):
    return json.dumps(value, ensure_ascii=False).encode()


def stream_for(message):
    pieces = [b": heartbeat\r\n\r\n"]
    for field in ("role", "reasoning_content", "content"):
        if field in message:
            pieces.append(b"data: " + encode({"model": "test-model", "choices": [{"index": 0, "delta": {field: message[field]}}]}) + b"\r\n\r\n")
    pieces += [b"data: " + encode({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}) + b"\n\n",
               b"data: " + encode({"choices": [], "usage": {"prompt_tokens": 17, "completion_tokens": 4}}) + b"\n\n",
               b"data: [DONE]\n\n"]
    return b"".join(pieces)


async def invoke(app, body, *, path="/v1/chat/completions", query=b"", headers=None, disconnect_after=None):
    queue = asyncio.Queue()
    await queue.put({"type": "http.request", "body": body, "more_body": False})
    sent = []

    async def receive():
        return await queue.get()

    async def send(event):
        sent.append(event)
        if disconnect_after is not None and sum(e["type"] == "http.response.body" for e in sent) >= disconnect_after:
            await queue.put({"type": "http.disconnect"})
            await asyncio.sleep(0)

    scope = {"type": "http", "method": "POST", "path": path, "raw_path": path.encode(),
             "query_string": query, "headers": headers or [(b"content-type", b"application/json")],
             "http_version": "1.1", "asgi": {"version": "3.0"}}
    await asyncio.wait_for(app(scope, receive, send), 5)
    return sent


def test_raw_stream_query_headers_and_report(tmp_path):
    async def run():
        body = b'{ "model":"test-model", "stream":true, "messages": [{"role":"user","content":"hi"}] }'
        content = stream_for({"role": "assistant", "content": "中文", "reasoning_content": "think first"})
        seen = []
        streams = []

        async def upstream(request):
            seen.append((request, await request.aread()))
            stream = BytesStream([content[i:i + 3] for i in range(0, len(content), 3)])
            streams.append(stream)
            return httpx.Response(200, headers=[("content-type", "text/event-stream"), ("x-many", "a"), ("x-many", "b")], stream=stream)

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            sent = await invoke(app, body, query=b"q=a%2Fb&q=c+e&api_key=secret", headers=[(b"host", b"old"), (b"authorization", b"Bearer private"), (b"content-type", b"application/json")])
            await app.close()
        assert seen[0][1] == body
        assert seen[0][0].url.query == b"q=a%2Fb&q=c+e&api_key=secret"
        assert seen[0][0].headers["authorization"] == "Bearer private"
        assert b"".join(e.get("body", b"") for e in sent) == content
        assert [(k, v) for k, v in sent[0]["headers"] if k == b"x-many"] == [(b"x-many", b"a"), (b"x-many", b"b")]
        assert streams[0].closed
        counter = TokenCounter({}, {"test-model": WordTokenizer()})
        counter.identities["test-model"] = {"source": "test_fixture", "name": "WordTokenizer"}
        output = analyze_capture(app.capture.root, counter=counter)
        rows = [json.loads(line) for line in (output / "requests.jsonl").read_text().splitlines()]
        assert rows[0]["reasoning_tokens_local"] == 2
        assert rows[0]["prompt_tokens_api"] == 17
        assert rows[0]["exclusions"] == []
        assert read_json(output / "integrity.json")["status"] == "complete"
        all_raw = "".join(p.read_text() for p in app.capture.root.glob("archive/*/*/*.jsonl"))
        assert "Bearer private" not in all_raw and "api_key=secret" not in all_raw
        assert (output / "SUMMARY.md").exists()
    asyncio.run(run())


def test_sse_chunk_invariance_and_multiline():
    wire = ':注释\r\ndata: {"x":\r\ndata: 1}\r\n\r\ndata: [DONE]\r\r'.encode()
    expected = list(sse_events([wire]))
    for width in range(1, 12):
        assert list(sse_events([wire[i:i + width] for i in range(0, len(wire), width)])) == expected
    assert expected == [("message", '{"x":\n1}'), ("message", "[DONE]")]
    assert list(sse_events([b"data: missing blank line"])) == []


def test_json_sse_equivalent_and_missing_done():
    message = {"role": "assistant", "content": "done", "reasoning_content": "think first"}
    normal = parse_response(encode(response(message)), [("content-type", "application/json")], ["reasoning_content", "reasoning"])
    streamed = parse_response(stream_for(message), [("content-type", "text/event-stream")], ["reasoning_content", "reasoning"])
    assert normal["choices"] == streamed["choices"]
    missing = parse_response(stream_for(message).replace(b"data: [DONE]\n\n", b""), [("content-type", "text/event-stream")], ["reasoning_content"])
    assert missing["parse_status"] == "incomplete"


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("null_fields", [("function_call",), ("tool_calls",), ("function_call", "tool_calls")])
def test_null_optional_tool_fields_preserve_response(streaming, null_fields):
    # Put reasoning after the null fields to detect premature parser termination.
    message = {"role": "assistant", "content": "done", **dict.fromkeys(null_fields),
               "reasoning_content": "think first"}
    payload = response(message)
    if streaming:
        payload["choices"][0]["delta"] = payload["choices"][0].pop("message")
    body = b"data: " + encode(payload) + b"\n\ndata: [DONE]\n\n" if streaming else encode(payload)
    parsed = parse_response(body, [("content-type", "text/event-stream" if streaming else "application/json")],
                            ["reasoning_content"])
    assert parsed["parse_status"] == "complete"
    assert parsed["warnings"] == []
    assert parsed["usage"] == payload["usage"]
    choice = parsed["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"] == {"role": "assistant", "content": "done", "reasoning_content": "think first"}
    assert choice["reasoning"] == "think first"
    measured = TokenCounter({}, {"test-model": WordTokenizer()}).measure(parsed, "test-model")
    assert measured["choices"][0]["reasoning"]["value"] == 2


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("field", ["function_call", "tool_calls"])
def test_null_tool_fields_do_not_erase_real_calls(streaming, field):
    function = {"name": "read", "arguments": '{"path":"x"}'}
    tool = {"id": "call1", "type": "function", "function": function}
    value = function if field == "function_call" else [{**tool, **({"index": 0} if streaming else {})}]
    message = {"function_call": None, "tool_calls": None, field: value}
    finish = "function_call" if field == "function_call" else "tool_calls"
    if streaming:
        deltas = [{"function_call": None, "tool_calls": None}, message,
                  {"function_call": None, "tool_calls": None}]
        events = [{"choices": [{"index": 0, "delta": delta}]} for delta in deltas]
        events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        body = b"".join(b"data: " + encode(event) + b"\n\n" for event in events) + b"data: [DONE]\n\n"
    else:
        payload = response(message)
        payload["choices"][0]["finish_reason"] = finish
        body = encode(payload)
    parsed = parse_response(body, [("content-type", "text/event-stream" if streaming else "application/json")], [])
    assert parsed["parse_status"] == "complete"
    assert parsed["warnings"] == []
    assert parsed["choices"][0]["finish_reason"] == finish
    assert parsed["choices"][0]["message"] == {"role": "assistant", field: function if field == "function_call" else [tool]}


@pytest.mark.parametrize("field", ["function_call", "tool_calls"])
def test_null_optional_field_does_not_hide_invalid_tool_arguments(field):
    function = {"name": "read", "arguments": ""}
    value = function if field == "function_call" else [{"id": "call1", "type": "function", "function": function}]
    message = {"function_call": None, "tool_calls": None, field: value}
    parsed = parse_response(encode(response(message)), [("content-type", "application/json")], [])
    assert parsed["parse_status"] == "parse_error"
    assert "invalid_tool_arguments_or_name" in parsed["warnings"]


def test_multichoice_tool_fragments_and_reasoning_conflict():
    deltas = [
        {"choices": [{"index": 0, "delta": {"reasoning_content": "a", "reasoning": "b", "tool_calls": [{"index": 0, "id": "call1", "function": {"name": "read", "arguments": '{"path":'}}]}}]},
        {"choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"x"}'}}]}, "finish_reason": "tool_calls"}, {"index": 1, "delta": {"content": "other"}, "finish_reason": "stop"}]},
    ]
    raw = b"".join(b"data: " + encode(d) + b"\n\n" for d in deltas) + b"data: [DONE]\n\n"
    parsed = parse_response(raw, [("content-type", "text/event-stream")], ["reasoning_content", "reasoning"])
    assert len(parsed["choices"]) == 2
    assert parsed["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] == '{"path":"x"}'
    assert "ambiguous_reasoning" in parsed["warnings"]


def test_unknown_tokenizer_is_not_zero_and_tools_in_denominator():
    msg = {"role": "assistant", "content": "", "reasoning_content": "a b", "tool_calls": [
        {"id": "c", "type": "function", "function": {"name": "read", "arguments": '{"x": "y"}'}}]}
    parsed = parse_response(encode(response(msg)), [], ["reasoning_content"])
    unknown = TokenCounter({}).measure(parsed, "test-model")
    assert unknown["choices"][0]["reasoning"]["value"] is None
    known = TokenCounter({}, {"test-model": WordTokenizer()}).measure(parsed, "test-model")
    assert known["choices"][0]["visible_reasoning_share"] == 2 / 5


def test_unknown_protocol_and_compression_are_still_transparent(tmp_path):
    async def run():
        raw = gzip.compress(b"event: content_block_delta\ndata: {\"x\":1}\n\n")
        async def upstream(request):
            await request.aread()
            return httpx.Response(429, headers={"content-type": "text/event-stream", "content-encoding": "gzip"}, stream=BytesStream([raw[:8], raw[8:]]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            sent = await invoke(app, b'{"messages":[]}', path="/v1/messages")
            await app.close()
        assert sent[0]["status"] == 429
        assert b"".join(e.get("body", b"") for e in sent) == raw
        assert (b"content-encoding", b"gzip") in sent[0]["headers"]
        out = analyze_capture(app.capture.root)
        row = json.loads((out / "requests.jsonl").read_text().splitlines()[0])
        assert row["parse_status"] == "unsupported"
    asyncio.run(run())


def test_upstream_stream_error_keeps_partial_without_done(tmp_path):
    async def run():
        stream = BytesStream([b'data: {"choices":[]}\n\n'], fail=True)
        async def upstream(request):
            await request.aread()
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            with pytest.raises(httpx.ReadError):
                await invoke(app, b'{"messages":[]}')
            await app.close()
        assert stream.closed
        out = analyze_capture(app.capture.root)
        row = json.loads((out / "requests.jsonl").read_text().splitlines()[0])
        assert row["transport_status"] == "upstream_error"
        assert row["parse_status"] == "incomplete"
    asyncio.run(run())


def test_concurrent_reverse_completion(tmp_path):
    async def run():
        async def upstream(request):
            raw = await request.aread()
            await asyncio.sleep(0.06 if b"slow" in raw else 0.005)
            return httpx.Response(200, headers={"content-type": "application/json"}, stream=BytesStream([encode(response())]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            first = asyncio.create_task(invoke(app, b'{"messages":[],"model":"slow"}'))
            await asyncio.sleep(0.01)
            second = asyncio.create_task(invoke(app, b'{"messages":[],"model":"fast"}'))
            await asyncio.gather(first, second)
            await app.close()
        out = analyze_capture(app.capture.root)
        rows = [json.loads(line) for line in (out / "requests.jsonl").read_text().splitlines()]
        assert rows[0]["arrival_seq"] < rows[1]["arrival_seq"]
        assert rows[0]["completed_at_utc"] > rows[1]["completed_at_utc"]
        assert len({r["request_id"] for r in rows}) == 2
    asyncio.run(run())


def test_storage_rollover_and_crash_tail(tmp_path):
    writer = RollingJsonlWriter(tmp_path, 35)
    first = writer.append("2026-09-16/23", {"x": "大" * 30})
    second = writer.append("2026-09-16/23", {"x": 1})
    assert first["oversized"]
    assert first["path"] != second["path"]
    with Path(second["path"]).open("ab") as stream:
        stream.write(b'{"broken":')
    third = RollingJsonlWriter(tmp_path, 35).append("2026-09-16/23", {"x": 2})
    assert third["path"] != second["path"]
    with pytest.raises(ValueError):
        writer.append("../escape", {})


def test_overflow_is_auditable_and_no_zero_tokens(tmp_path):
    cap = Capture(settings(tmp_path, capture={"queue_max_bytes": 4}))
    ctx = cap.begin({"method": "POST", "path": "/v1/chat/completions", "headers": [], "labels": {}})
    cap.body(ctx, "request", b"too large")
    cap.finish(ctx, "completed")
    cap.close()
    out = analyze_capture(cap.root)
    assert read_json(out / "integrity.json")["status"] == "incomplete"
    row = json.loads((out / "requests.jsonl").read_text().splitlines()[0])
    assert row["reasoning_tokens_local"] is None
    assert "capture_incomplete" in row["exclusions"]


def test_trajectory_branch_missing_history_and_ambiguous_retry():
    user = {"role": "user", "content": "fix"}
    tool = {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "read", "arguments": "{}"}}]}
    tool_result = {"role": "tool", "tool_call_id": "c", "content": "file"}
    rows, details = [], {}
    def add(rid, messages, output, second):
        rows.append({"request_id": rid, "started_at_utc": f"2026-09-16T00:00:{second:02d}+00:00",
                     "body_received_at_utc": f"2026-09-16T00:00:{second:02d}+00:00",
                     "last_response_at_utc": f"2026-09-16T00:00:{second+1:02d}+00:00",
                     "process_id": "p", "arrival_seq": second, "labels": {},
                     "capture_status": "complete", "parse_status": "complete"})
        details[rid] = {"request": {"messages": messages}, "response": {"choices": [{"index": 0, "message": output}]}}
    add("a", [user], tool, 0)
    add("b", [user, tool, tool_result], {"role": "assistant", "content": "answer b"}, 5)
    add("c", [user, tool, tool_result], {"role": "assistant", "content": "answer c"}, 6)
    trajectories, edges = build_trajectories(rows, lambda row: details[row["request_id"]])
    assert len(trajectories) == 2
    assert all(t["shared_ancestors"] == ["a"] for t in trajectories)
    add("retry", [user], tool, 2)
    trajectories, edges = build_trajectories(rows, lambda row: details[row["request_id"]])
    assert all(e["status"] == "ambiguous" for e in edges)
    isolated, _ = build_trajectories(rows[1:2], lambda row: details[row["request_id"]])
    assert isolated[0]["history_only_assistant_turns"] == 1


def test_hop_headers_and_config_validation(tmp_path):
    result = forward_headers([(b"connection", b"x-hop"), (b"x-hop", b"secret"), (b"x-end", b"keep")])
    assert result == [(b"x-end", b"keep")]
    with pytest.raises(ValueError):
        settings(tmp_path, upstream={"base_url": "http://user:secret@example.com"})
    with pytest.raises(ValueError):
        settings(tmp_path, upstream={"auto_retry": True})


def test_disconnect_cancels_and_closes_upstream(tmp_path):
    class Endless(BytesStream):
        async def __aiter__(self):
            yield b": first\n\n"
            await asyncio.Event().wait()
    async def run():
        stream = Endless([])
        async def upstream(request):
            await request.aread()
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            await invoke(app, b'{"messages":[]}', disconnect_after=1)
            await app.close()
        assert stream.closed
        out = analyze_capture(app.capture.root)
        row = json.loads((out / "requests.jsonl").read_text().splitlines()[0])
        assert row["transport_status"] == "client_disconnected"
    asyncio.run(run())


def test_connect_failure_is_logged_without_retry(tmp_path):
    async def run():
        calls = []
        async def upstream(request):
            calls.append(request)
            raise httpx.ConnectTimeout("test")
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            sent = await invoke(app, b'{"messages":[]}')
            await app.close()
        assert len(calls) == 1 and sent[0]["status"] == 504
        out = analyze_capture(app.capture.root)
        row = json.loads((out / "requests.jsonl").read_text().splitlines()[0])
        assert row["transport_status"] == "upstream_error"
    asyncio.run(run())


def test_disk_failure_does_not_break_forwarding(tmp_path, monkeypatch):
    async def run():
        async def upstream(request):
            await request.aread()
            return httpx.Response(200, stream=BytesStream([b"original bytes"]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            def fail(*args):
                raise OSError("disk full")
            monkeypatch.setattr(app.capture.writer, "append", fail)
            sent = await invoke(app, b"{}")
            await app.close()
        assert b"".join(e.get("body", b"") for e in sent) == b"original bytes"
        state = read_json(app.capture.process_path)
        assert state["write_errors"] > 0 and state["clean_shutdown"] is False
    asyncio.run(run())


def test_hour_bucket_frozen_at_arrival(tmp_path, monkeypatch):
    import proxy.observation.capture as module
    cap = Capture(settings(tmp_path))
    monkeypatch.setattr(module, "utc_now", lambda: "2026-09-16T23:59:59.000000+00:00")
    ctx = cap.begin({"headers": [], "path": "/health", "labels": {}})
    monkeypatch.setattr(module, "utc_now", lambda: "2026-09-17T00:00:05.000000+00:00")
    cap.finish(ctx, "completed")
    cap.close()
    parts = list(cap.root.glob("archive/*/*/*.jsonl"))
    assert len(parts) == 1 and parts[0].parent.as_posix().endswith("2026-09-16/23")
    events = [json.loads(line) for line in parts[0].read_text().splitlines()]
    assert events[-1]["payload"]["completed_at_utc"].startswith("2026-09-17")


def test_live_report_appears_without_next_request(tmp_path):
    async def run():
        async def upstream(request):
            await request.aread()
            return httpx.Response(200, headers={"content-type": "application/json"}, stream=BytesStream([encode(response())]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path, report={"live_interval_seconds": 0.05}), client=client)
            await app.start()
            await invoke(app, b'{"messages":[{"role":"user","content":"hi"}]}')
            try:
                for _ in range(80):
                    if (app.capture.root / "derived/live/SUMMARY.md").exists():
                        break
                    await asyncio.sleep(0.025)
                assert (app.capture.root / "derived/live/SUMMARY.md").exists()
            finally:
                await app.close()
    asyncio.run(run())


@pytest.mark.parametrize("inject", [False, True])
def test_real_http_server_raw_body_and_cookie_isolation(tmp_path, inject):
    """Exercise uvicorn/httpx wire transport, not only mocked request objects."""
    import uvicorn
    received = []
    wire = gzip.compress(encode(response()))

    async def upstream(scope, receive, send):
        if scope["type"] != "http":
            return
        body = b""
        while True:
            event = await receive()
            body += event.get("body", b"")
            if not event.get("more_body"):
                break
        received.append({"body": body, "query": scope["query_string"], "headers": scope["headers"], "path": scope["raw_path"]})
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json"), (b"content-encoding", b"gzip"),
                                (b"set-cookie", b"session=do-not-leak"), (b"x-repeat", b"a"), (b"x-repeat", b"b")]})
        await send({"type": "http.response.body", "body": wire, "more_body": False})

    def launch(app, lifespan):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, lifespan=lifespan, log_level="critical", access_log=False))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        for _ in range(100):
            if server.started:
                return server, thread, sock, port
            time.sleep(0.02)
        raise AssertionError("server did not start")
    upstream_server = launch(upstream, "off")
    proxy_server = None
    try:
        config = settings(tmp_path, upstream={"base_url": f"http://127.0.0.1:{upstream_server[3]}"})
        if inject:
            config["append_body"] = {"model": "actual-model", "fixed_budget_tokens": 850}
        app = ObservationProxy(config)
        proxy_server = launch(app, "on")
        raw = b'{ "model":"test-model", "messages":[] }'
        for _ in range(2):
            # Different clients; upstream cookie must not enter the proxy's next request.
            with httpx.Client(trust_env=False) as client:
                with client.stream("POST", f"http://127.0.0.1:{proxy_server[3]}/v1/chat/completions?q=%2f&q=+", content=raw) as res:
                    assert b"".join(res.iter_raw()) == wire
                    assert res.headers.get_list("x-repeat") == ["a", "b"]
        assert all(r["query"] == b"q=%2f&q=+" for r in received)
        if inject:
            assert all(json.loads(r["body"]) == {"model": "actual-model", "messages": [], "fixed_budget_tokens": 850} for r in received)
            assert all(int(dict(r["headers"])[b"content-length"]) == len(r["body"]) for r in received)
        else:
            assert all(r["body"] == raw for r in received)
        assert not any(k.lower() == b"cookie" for k, v in received[1]["headers"])
    finally:
        for item in [proxy_server, upstream_server]:
            if item:
                item[0].should_exit = True
                item[1].join(5)
                item[2].close()
                assert not item[1].is_alive()


def test_distribution_formula_and_censored_exclusion():
    from proxy.observation.report import distribution
    rows = [{"request_id": str(i), "reasoning_tokens_local": n, "e2e_ms": n,
             "exclusions": []} for i, n in enumerate([120, 80, 180, 2400, 60, 160])]
    rows.append({"request_id": "cut", "reasoning_tokens_local": 9999, "e2e_ms": 1,
                 "exclusions": ["censored_or_other_finish"]})
    result = distribution(rows, [850])
    assert result["eligible_count"] == 6
    assert result["threshold_scan"][0]["short_fraction"] == 5 / 6
    assert result["threshold_scan"][0]["tail_token_share"] == 0.8
    assert result["threshold_scan"][0]["excess_tokens"] == 1550
    assert result["p50"] == 120 and result["p95"] == 2400


def test_profile_matching_and_tool_result_reordering():
    user = {"role": "user", "content": "inspect"}
    output = {"role": "assistant", "content": None, "reasoning_content": "first inspect files", "tool_calls": [
        {"id": "a", "type": "function", "function": {"name": "read", "arguments": "{}"}},
        {"id": "b", "type": "function", "function": {"name": "grep", "arguments": "{}"}}]}
    history_output = {k: v for k, v in output.items() if k != "reasoning_content"}
    messages = [user, history_output, {"role": "tool", "tool_call_id": "b", "content": "match"},
                {"role": "tool", "tool_call_id": "a", "content": "file"}]
    details = {"a": {"request": {"messages": [user]}, "response": {"choices": [{"index": 0, "message": output}]}},
               "b": {"request": {"messages": messages}, "response": {"choices": []}}}
    rows = [{"request_id": rid, "started_at_utc": f"2026-09-16T00:00:0{i}+00:00", "body_received_at_utc": f"2026-09-16T00:00:0{i}+00:00",
             "process_id": "p", "arrival_seq": i, "capture_status": "complete", "parse_status": "complete", "labels": {}}
            for rid, i in [("a", 0), ("b", 2)]]
    _, strict_edges = build_trajectories(rows, lambda row: details[row["request_id"]])
    assert strict_edges == []
    trajectories, edges = build_trajectories(rows, lambda row: details[row["request_id"]], ["omit_reasoning"])
    assert edges[0]["relation"] == "profile_continuation"
    assert len(trajectories) == 1
    links = tool_history_links(messages)
    assert [(link["tool_call_id"], link["call_message_index"]) for link in links] == [("b", 1), ("a", 1)]


def test_parser_failure_does_not_prevent_other_requests_report(tmp_path):
    async def run():
        async def upstream(request):
            await request.aread()
            return httpx.Response(200, stream=BytesStream([encode(response())]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            await invoke(app, b'{"model":[],"messages":"bad"}')
            await invoke(app, b'{"model":"test-model","messages":[]}')
            await app.close()
        out = analyze_capture(app.capture.root)
        rows = [json.loads(line) for line in (out / "requests.jsonl").read_text().splitlines()]
        assert rows[0]["parse_status"] == "parse_error"
        assert rows[1]["parse_status"] == "complete"
        # Real report links point to files, including nested hour/request paths.
        import re
        for file in out.rglob("*.md"):
            for link in re.findall(r"\]\(([^)]+)\)", file.read_text(encoding="utf-8")):
                assert (file.parent / link).exists(), (file, link)
        again = analyze_capture(app.capture.root)
        assert again == out
    asyncio.run(run())


@pytest.mark.parametrize("streaming", [False, True])
def test_append_body_preserves_original_and_reports_effective_request(tmp_path, streaming):
    async def run():
        original = {"model": "client-alias", "messages": [{"role": "user", "content": "中文"}],
                    "stream": streaming, "temperature": 0.7, "extra": {"old": 1}}
        append = {"model": "test-model", "enable_think_budget": True, "fixed_budget_tokens": 850,
                  "extra": {"new": 2}}
        raw = gzip.compress(encode(original))
        wire = stream_for(response()["choices"][0]["message"]) if streaming else encode(response())
        seen = []
        async def upstream(request):
            seen.append((request, await request.aread()))
            return httpx.Response(200, headers={"content-type": "text/event-stream" if streaming else "application/json"},
                                  stream=BytesStream([wire]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path, append_body=append), client=client,
                                   counter=TokenCounter({}, {"test-model": WordTokenizer()}))
            await app.start()
            sent = await invoke(app, raw, headers=[(b"content-type", b"application/json"),
                                (b"content-encoding", b"gzip"), (b"content-length", str(len(raw)).encode())])
            # Other protocols retain original bytes even with append_body enabled.
            await invoke(app, raw, path="/v1/responses", headers=[(b"content-encoding", b"gzip")])
            await app.close()
        assert b"".join(e.get("body", b"") for e in sent) == wire
        request, sent_body = seen[0]
        assert json.loads(sent_body) == {**original, **append}
        assert int(request.headers["content-length"]) == len(sent_body)
        assert "content-encoding" not in request.headers
        assert seen[1][1] == raw and seen[1][0].headers["content-encoding"] == "gzip"
        rows = [json.loads(line) for line in (app.final_report / "requests.jsonl").read_text().splitlines()]
        detail = read_json(app.final_report / rows[0]["detail_json"])
        assert detail["original_request"] == original
        assert detail["request"] == {**original, **append}
        assert detail["request_transform"]["append_body"] == append
        assert rows[0]["body_modified"] and rows[0]["exclusions"] == []
        assert rows[1]["parse_status"] == "unsupported" and not rows[1]["body_modified"]
        page = (app.final_report / rows[0]["detail_markdown"]).read_text(encoding="utf-8")
        assert "客户端原始请求" in page and "fixed_budget_tokens" in page
        assert original["model"] == "client-alias" and append == app.config["append_body"]
    asyncio.run(run())


@pytest.mark.parametrize("body,headers,limit,expected", [
    (b"not json", [], 128, 400), (b"[]", [], 128, 400),
    (b"{}", [(b"content-encoding", b"br")], 128, 415),
    (b"{}", [(b"content-type", b"text/plain")], 128, 415),
    (b"x" * 129, [], 128, 413),
    (gzip.compress(b"x" * 200), [(b"content-encoding", b"gzip")], 128, 413),
    (b"{}", [], 8, 413),
])
def test_append_body_rejects_invalid_or_oversize_input(tmp_path, body, headers, limit, expected):
    async def run():
        calls = []
        async def upstream(request):
            calls.append(request)
            raise AssertionError("must not forward a request that cannot be modified")
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path, append_body={"model": "fixed"},
                                   upstream={"request_body_max_bytes": limit}), client=client)
            await app.start()
            sent = await invoke(app, body, headers=headers)
            await app.close()
        assert sent[0]["status"] == expected and not calls
        assert read_json(app.capture.process_path)["clean_shutdown"]
    asyncio.run(run())


def test_one_tokenizer_for_aliases_live_and_final_analysis(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    import proxy.observation.analysis as analysis
    import proxy.observation.tokens as tokens
    loads, analyses = [], []
    tokenizer = WordTokenizer()
    def load(*args, **kwargs):
        loads.append((args, kwargs))
        return tokenizer
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoTokenizer=SimpleNamespace(from_pretrained=load)))
    monkeypatch.setattr(tokens.importlib.metadata, "version", lambda name: "test-version")
    real_analyze = analysis.analyze_capture
    def analyze(root, **kwargs):
        analyses.append((kwargs.get("live", False), kwargs["counter"]))
        if not kwargs.get("live"):
            assert read_json(app.capture.process_path)["clean_shutdown"]
            assert not app.live_thread.is_alive()
        return real_analyze(root, **kwargs)
    monkeypatch.setattr(analysis, "analyze_capture", analyze)
    app = None
    async def run():
        nonlocal app
        async def upstream(request):
            request_body = json.loads(await request.aread())
            data = response()
            data["model"] = request_body["model"] + "-served"
            return httpx.Response(200, stream=BytesStream([encode(data)]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path, tokenizer={"path": str(tmp_path)},
                                   report={"live_interval_seconds": 0.05}), client=client)
            await app.start()
            assert len(loads) == 1  # Initialized before any request.
            await asyncio.gather(*(invoke(app, encode({"model": alias, "messages": []})) for alias in ["alias-a", "alias-b"]))
            try:
                for _ in range(100):
                    if (app.capture.root / "derived/live/analysis_manifest.json").exists():
                        break
                    await asyncio.sleep(0.025)
                assert (app.capture.root / "derived/live/analysis_manifest.json").exists()
            finally:
                await asyncio.gather(app.close(), app.close())
                await app.close()
        assert len(loads) == 1
        assert all(counter is app.counter for live, counter in analyses)
        assert sum(not live for live, _ in analyses) == 1 and not analyses[-1][0]
        rows = [json.loads(line) for line in (app.final_report / "requests.jsonl").read_text().splitlines()]
        assert len(rows) == 2 and all(r["reasoning_tokens_local"] == 2 for r in rows)
        assert rows[0]["tokenizer_ref"] == rows[1]["tokenizer_ref"]
        assert read_json(app.capture.root / "shutdown_analysis.json")["status"] == "completed"
    asyncio.run(run())


def test_final_analysis_failure_keeps_capture_and_is_not_retried(tmp_path, monkeypatch):
    import proxy.observation.analysis as module
    calls = []
    def fail(root, **kwargs):
        calls.append(root)
        raise RuntimeError("analysis failure")
    monkeypatch.setattr(module, "analyze_capture", fail)
    async def run():
        app = ObservationProxy(settings(tmp_path))
        await app.start()
        await app.close()
        await app.close()
        assert len(calls) == 1 and app.final_report is None
        assert read_json(app.capture.process_path)["clean_shutdown"]
        assert read_json(app.capture.root / "shutdown_analysis.json") == {"status": "failed", "error": "RuntimeError"}
    asyncio.run(run())


def test_shutdown_waits_for_cancelled_request_cleanup_before_analysis(tmp_path):
    async def run():
        reading, closing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        class SlowStream(BytesStream):
            async def __aiter__(self):
                reading.set()
                yield b'data: {"choices": []}\n\n'
                await asyncio.Event().wait()
            async def aclose(self):
                closing.set()
                await release.wait()
        async def upstream(request):
            await request.aread()
            return httpx.Response(200, stream=SlowStream([]))
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
            app = ObservationProxy(settings(tmp_path), client=client)
            await app.start()
            request_task = asyncio.create_task(invoke(app, b'{"messages":[]}'))
            await asyncio.wait_for(reading.wait(), 2)
            request_task.cancel()
            await asyncio.wait_for(closing.wait(), 2)
            close_task = asyncio.create_task(app.close())
            try:
                await asyncio.sleep(0.025)
                assert not close_task.done() and not app.capture.stopping
                assert app.capture.finished == 0
            finally:
                release.set()
                await asyncio.gather(request_task, return_exceptions=True)
                await close_task
            rows = [json.loads(line) for line in (app.final_report / "requests.jsonl").read_text().splitlines()]
            assert len(rows) == 1 and rows[0]["transport_status"] == "client_disconnected"
            assert rows[0]["capture_status"] == "complete"
            assert read_json(app.final_report / "integrity.json")["status"] == "complete"
    asyncio.run(run())


def test_uvicorn_sigint_runs_final_analysis(tmp_path):
    """SIGINT uses Uvicorn's real signal/lifespan path in the child main thread."""
    import subprocess
    import sys
    script = tmp_path / "sigint_server.py"
    script.write_text('''import signal, sys, threading, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import httpx, uvicorn
from proxy.observation.config import load_config
from proxy.observation.transport import ObservationProxy
root = Path(sys.argv[2])
class ResponseStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
async def upstream(request):
    await request.aread()
    return httpx.Response(200, headers={"content-type": "application/json"}, stream=ResponseStream())
app = ObservationProxy(load_config(overrides={"capture": {"output_root": str(root)}, "report": {"live_interval_seconds": 0}}),
                       client=httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="on", log_level="warning"))
def interrupt():
    while not server.started:
        time.sleep(0.02)
    (root / "ready").write_text(str(server.servers[0].sockets[0].getsockname()[1]))
    while not (root / "stop").exists():
        time.sleep(0.02)
    signal.raise_signal(signal.SIGINT)
threading.Thread(target=interrupt, daemon=True).start()
try:
    server.run()
except KeyboardInterrupt:
    pass
''', encoding="utf-8")
    repo = Path(__file__).resolve().parents[1]
    proc = subprocess.Popen([sys.executable, str(script), str(repo), str(tmp_path)],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for _ in range(300):
            if (tmp_path / "ready").exists() or proc.poll() is not None:
                break
            time.sleep(0.02)
        assert (tmp_path / "ready").exists()
        port = int((tmp_path / "ready").read_text())
        with httpx.Client(trust_env=False) as client:
            result = client.post(f"http://127.0.0.1:{port}/v1/chat/completions", json={"messages": []})
            assert result.status_code == 200
        (tmp_path / "stop").touch()
        stdout, stderr = proc.communicate(timeout=15)
        assert proc.returncode == 0, (stdout, stderr)
        root = next(tmp_path.glob("agent_*"))
        status = read_json(root / "shutdown_analysis.json")
        assert status["status"] == "completed"
        report = root / status["report"]
        assert (report / "SUMMARY.md").is_file()
        assert read_json(report / "integrity.json")["status"] == "complete"
        assert len((report / "requests.jsonl").read_text().splitlines()) == 1
        assert len([p for p in (root / "derived").iterdir() if p.is_dir()]) == 1
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
