"""Raw ASGI reverse proxy. Analysis never participates in forwarding."""
from __future__ import annotations

import asyncio
import http.cookiejar
import json
import logging
import os
import threading
import zlib
from urllib.parse import parse_qsl, urlencode

import httpx

from .capture import Capture
from .chat_completions import decode_body
from .storage import write_json
from .tokens import TokenCounter

HOP = {b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
       b"te", b"trailer", b"transfer-encoding", b"upgrade"}
SECRET = {b"authorization", b"proxy-authorization", b"cookie", b"set-cookie", b"x-api-key", b"api-key"}
log = logging.getLogger(__name__)


def forward_headers(headers, request=False):
    excluded = set(HOP)
    if request:
        excluded.add(b"host")
    for name, value in headers:
        if name.lower() == b"connection":
            excluded.update(part.strip().lower() for part in value.split(b","))
    return [(k.lower(), v) for k, v in headers if k.lower() not in excluded]


def safe_headers(headers):
    return [[k.decode("latin-1"), "[REDACTED]" if k.lower() in SECRET or b"api-key" in k.lower()
             else v.decode("latin-1")] for k, v in headers]


class NoCookies(http.cookiejar.CookieJar):
    def extract_cookies(self, response, request):
        pass


class ObservationProxy:
    def __init__(self, config, *, client=None, capture=None, counter=None):
        self.config = config
        self.client = client
        self.capture = capture
        self.owns_client = client is None
        self.live_stop = threading.Event()
        self.live_thread = None
        self.counter = counter or TokenCounter.from_config(config)
        self._close_task = None
        self.final_report = None
        self.active_requests = set()

    async def start(self):
        await asyncio.to_thread(self.counter.prepare)
        if self.counter.errors:
            log.warning("Local tokenizer unavailable: %s", self.counter.errors)
        if self.capture is None:
            self.capture = Capture(self.config)
        if self.client is None:
            timeout = self.config["upstream"]["timeout"]
            self.client = httpx.AsyncClient(
                timeout=httpx.Timeout(**{key.removesuffix("_seconds"): value for key, value in timeout.items()}),
                trust_env=self.config["upstream"]["trust_env"], cookies=NoCookies(), follow_redirects=False)
        if self.config["report"]["live_interval_seconds"] > 0:
            self.live_thread = threading.Thread(target=self._live_reports, name="observation-reports", daemon=True)
            self.live_thread.start()
        log.info("Observation capture: %s", self.capture.root)

    def _live_reports(self):
        from .analysis import analyze_capture
        last_revision = None
        while not self.live_stop.wait(self.config["report"]["live_interval_seconds"]):
            revision = (self.capture.accepted, self.capture.finished, self.capture.written_events, self.capture.write_errors)
            if self.capture.finished and revision != last_revision:
                try:
                    analyze_capture(self.capture.root, live=True, counter=self.counter)
                    last_revision = revision
                except Exception:
                    log.exception("Live report failed; raw capture remains available")

    async def close(self):
        # Lifespan shutdown and callers can share cleanup without analyzing twice.
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self):
        self.live_stop.set()
        if self.live_thread:
            await asyncio.to_thread(self.live_thread.join)
        # Uvicorn may send lifespan.shutdown immediately after cancelling requests.
        # Their finally blocks must finish (including upstream close) before the writer.
        if self.active_requests:
            await asyncio.gather(*self.active_requests, return_exceptions=True)
        try:
            if self.owns_client and self.client:
                await self.client.aclose()
        finally:
            if self.capture:
                await asyncio.to_thread(self.capture.close)
                await asyncio.to_thread(self._final_analysis)

    def _final_analysis(self):
        from .analysis import analyze_capture
        try:
            log.info("Capture closed; building final observation report")
            self.final_report = analyze_capture(self.capture.root, counter=self.counter)
            result = {"status": "completed", "report": self.final_report.relative_to(self.capture.root).as_posix()}
            log.info("Final observation report: %s", self.final_report / "SUMMARY.md")
        except Exception as exc:
            result = {"status": "failed", "error": type(exc).__name__}
            log.exception("Final analysis failed; retry with python -m vllm_gateway.observation.analysis <capture_dir>")
        try:
            write_json(self.capture.root / "shutdown_analysis.json", result)
        except Exception:
            log.exception("Could not persist final analysis status")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                event = await receive()
                if event["type"] == "lifespan.startup":
                    try:
                        await self.start()
                        await send({"type": "lifespan.startup.complete"})
                    except Exception as exc:
                        await send({"type": "lifespan.startup.failed", "message": type(exc).__name__})
                        return
                elif event["type"] == "lifespan.shutdown":
                    await self.close()
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        elif scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
        elif scope["type"] == "http":
            if self._close_task is not None:
                await send({"type": "http.response.start", "status": 503, "headers": [(b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
                return
            task = asyncio.current_task()
            self.active_requests.add(task)
            try:
                await self.handle(scope, receive, send)
            finally:
                self.active_requests.discard(task)

    async def handle(self, scope, receive, send):
        if self.client is None or self.capture is None:
            raise RuntimeError("ASGI lifespan must be enabled")
        cap = self.config["capture"]
        headers = scope["headers"]
        labels = dict(cap["labels"])
        query = scope.get("query_string", b"")
        sensitive = {k.lower() for k in cap["redact_query_keys"]}
        query_log = urlencode([(k, "[REDACTED]" if k.lower() in sensitive else v)
                               for k, v in parse_qsl(query.decode("latin-1"), keep_blank_values=True)])
        metadata_only = scope["path"] in cap["metadata_only_paths"]
        ctx = self.capture.begin({"method": scope["method"], "path": scope["path"],
                    "query": query_log, "headers": safe_headers(headers), "labels": labels,
                    "metadata_only": metadata_only})
        status = "proxy_error"
        started = False
        body_read = asyncio.Event()
        upstream = None

        async def request_body():
            try:
                while True:
                    event = await receive()
                    if event["type"] == "http.disconnect":
                        raise asyncio.CancelledError
                    data = event.get("body", b"")
                    if data and not metadata_only:
                        self.capture.body(ctx, "request", data)
                    if data:
                        yield data
                    if not event.get("more_body", False):
                        self.capture.emit(ctx, "request_body_end", {})
                        break
            finally:
                body_read.set()

        async def disconnected():
            await body_read.wait()
            while (await receive())["type"] != "http.disconnect":
                pass

        async def local_response(code):
            nonlocal started
            data = f"Proxy error ({code})".encode()
            self.capture.emit(ctx, "proxy_response", {"status_code": code})
            started = True
            await send({"type": "http.response.start", "status": code,
                        "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(data)).encode())]})
            await send({"type": "http.response.body", "body": b"" if scope["method"] == "HEAD" else data})

        async def exchange():
            nonlocal upstream, started, status
            if scope["method"] == "CONNECT" or any(k.lower() == b"upgrade" for k, _ in headers):
                await local_response(501)
                return
            mapping = self.config["upstream"]["path_mapping"]
            incoming = mapping["incoming_prefix"].rstrip("/").encode()
            raw_path = scope.get("raw_path", scope["path"].encode())
            if incoming and raw_path != incoming and not raw_path.startswith(incoming + b"/"):
                await local_response(404)
                return
            base = httpx.URL(self.config["upstream"]["base_url"])
            target_path = base.raw_path.rstrip(b"/") + mapping["upstream_prefix"].rstrip("/").encode() + b"/" + raw_path[len(incoming):].lstrip(b"/")
            url = base.copy_with(raw_path=target_path + (b"?" + query if query else b""))
            forwarded = forward_headers(headers, request=True)
            if self.config["upstream"]["auth_mode"] == "bearer_env":
                value = os.environ.get(self.config["upstream"]["auth_env"])
                if not value:
                    raise RuntimeError("upstream bearer credential is unavailable")
                forwarded = [(k, v) for k, v in forwarded if k != b"authorization"]
                forwarded.append((b"authorization", ("Bearer " + value).encode()))
            body = request_body()
            append = self.config["append_body"]
            if append and scope["method"] == "POST" and scope["path"] in self.config["protocols"]["chat_completions"]["paths"]:
                limit = self.config["upstream"]["request_body_max_bytes"]
                raw = bytearray()
                try:
                    async for chunk in body:
                        if len(raw) + len(chunk) > limit:
                            await local_response(413)
                            return
                        raw.extend(chunk)
                finally:
                    await body.aclose()
                header_map = {k.lower(): v.lower() for k, v in headers}
                encoding = header_map.get(b"content-encoding", b"identity")
                media = header_map.get(b"content-type", b"application/json").split(b";", 1)[0].strip()
                if encoding not in {b"", b"identity", b"gzip", b"deflate"} or (media != b"application/json" and not media.endswith(b"+json")):
                    await local_response(415)
                    return
                try:
                    decoded = decode_body(bytes(raw), safe_headers(headers), limit)
                    original = json.loads(decoded)
                    if not isinstance(original, dict):
                        raise ValueError("request_not_object")
                    # Same top-level override semantics as legacy APPEND_BODY.
                    body = json.dumps({**original, **append}, ensure_ascii=False, allow_nan=False).encode("utf-8")
                except (ValueError, UnicodeError, zlib.error) as exc:
                    await local_response(413 if str(exc) == "decoded_body_limit" else 400)
                    return
                if len(body) > limit:
                    await local_response(413)
                    return
                forwarded = [(k, v) for k, v in forwarded if k not in {
                    b"content-length", b"content-encoding", b"content-type", b"content-md5", b"digest", b"content-digest"}]
                forwarded.extend([(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())])
                self.capture.emit(ctx, "request_body_transform", {
                    "policy": "top_level_override", "append_body": append,
                    "headers": safe_headers(forwarded)})
                if not metadata_only:
                    self.capture.body(ctx, "forwarded_request", body)
            # Construct Request directly: do not merge shared-client cookies/default headers.
            request = httpx.Request(scope["method"], url, headers=forwarded, content=body,
                                    extensions={"timeout": self.client.timeout.as_dict()})
            self.capture.emit(ctx, "upstream_dispatch", {})
            upstream = await self.client.send(request, stream=True, follow_redirects=False)
            self.capture.emit(ctx, "upstream_headers", {"status_code": upstream.status_code,
                               "headers": safe_headers(upstream.headers.raw)})
            started = True
            await send({"type": "http.response.start", "status": upstream.status_code,
                        "headers": forward_headers(upstream.headers.raw)})
            first = True
            async for chunk in upstream.aiter_raw():
                if first and chunk:
                    self.capture.emit(ctx, "first_body_byte", {})
                    first = False
                if not metadata_only:
                    self.capture.body(ctx, "response", chunk)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            self.capture.emit(ctx, "upstream_eof", {})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            self.capture.emit(ctx, "downstream_send_end", {})
            status = "completed"

        work = asyncio.create_task(exchange())
        disconnect = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait({work, disconnect}, return_when=asyncio.FIRST_COMPLETED)
            if disconnect in done and not work.done():
                status = "client_disconnected"
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            else:
                await work
        except asyncio.CancelledError:
            status = "client_disconnected"
            raise
        except Exception as exc:
            status = "upstream_error" if isinstance(exc, httpx.HTTPError) else "proxy_error"
            self.capture.emit(ctx, "transport_error", {"type": type(exc).__name__})
            if started:
                raise  # ASGI server aborts; never manufacture a successful SSE terminator.
            await local_response(504 if isinstance(exc, httpx.TimeoutException) else 502)
        finally:
            work.cancel()
            disconnect.cancel()
            await asyncio.gather(work, disconnect, return_exceptions=True)
            try:
                if upstream:
                    await asyncio.wait_for(upstream.aclose(), 5)
            finally:
                self.capture.finish(ctx, status)
