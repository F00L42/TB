"""No-model gates for identity routing, automatic budgets and native serving."""
from __future__ import annotations

import ast
import asyncio
import copy
import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml

from dtb.config.loader import load_dtb_service_config
from dtb.config.schema import DtbObservabilityConfig, DtbTelemetryMode
from dtb.integration.vllm.request_adapter import system_identity_view
from dtb.request_controller import DtbRequestController
from tools.extract_dtb_phase_metrics import (
    ClientResultRow, TestResultSpec as ResultSpec, scan_server_log, write_bound_outputs,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "dtb/config/budget_config_noemate_750.yaml"


def request(prefix=None, **kwargs):
    if prefix is None:
        prefix = load_dtb_service_config(CONFIG).scene_rules[0].system_prefix
    return NS(messages=[{"role": "system", "content": prefix},
                        {"role": "user", "content": "question"}],
              **{"stream": True, "enable_think_budget": True, "tools": [], **kwargs})


def controller(service=None):
    return DtbRequestController(service_config=service or load_dtb_service_config(CONFIG))


def load_variant(tmp_path, mutate):
    raw = yaml.safe_load(CONFIG.read_text(encoding="utf8"))
    mutate(raw)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf8")
    return load_dtb_service_config(path)


@pytest.mark.parametrize("mutate", [
    lambda c: c["ROUTING"].update(mode="typo"),
    lambda c: c["ROUTING"].update(on_unmatched="typo"),
    lambda c: c["ROUTING"].update(extra=True),
    lambda c: c["SCENES"]["noemate_main"].update(action="typo"),
    lambda c: c["SCENES"]["noemate_main"].update(action="native"),
    lambda c: c["SCENES"]["noemate_main"].update(indicators={}),
    lambda c: c["SCENES"]["noemate_main"].update(extra=1),
    lambda c: c["SCENES"]["noemate_main"].pop("prob_th"),
    lambda c: c["SCENES"]["noemate_main"].update(prob_th=float("nan")),
    lambda c: c["SCENES"]["noemate_main"].update(check_length=True),
    lambda c: c["SCENES"]["noemate_main"].update(check_length=751),
    lambda c: c["SCENES"]["noemate_main"]["match"].update(system_prefix=""),
    lambda c: c["SCENES"]["noemate_main"]["match"].update(system_prefix="a" * 513),
    lambda c: c["SCENES"]["noemate_main"]["match"].update(first_user_prefix="a" * 257),
    lambda c: c["SCENES"]["noemate_main"]["match"].update(tools_empty=1),
    lambda c: c["SCENES"].update(duplicate=copy.deepcopy(c["SCENES"]["noemate_main"])),
    lambda c: c["SCENES"]["noemate_followup"]["match"].update(system_prefix="# Agent"),
    lambda c: c["RUNTIME"].update(enable_scene_budget=False),
    lambda c: c["RUNTIME"].update(enable_history_budget=True),
    lambda c: c["RUNTIME"].update(enable_difficulty_estimator=True),
    lambda c: c["RUNTIME"].update(scene_override="noemate_main"),
    lambda c: c["ROUTING"].update(mode="legacy_text"),
])
def test_invalid_configuration_fails_at_startup(tmp_path, mutate):
    with pytest.raises(ValueError):
        load_variant(tmp_path, mutate)


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("stream", [False, True])
def test_automatic_snapshot_is_reused_without_legacy_classifier(typed, stream):
    ctl = controller()
    req = request(stream=stream)
    if typed:
        req.messages = [NS(**message) for message in req.messages]
    with patch("dtb.budgeting.budget_resolver.classify_scene", side_effect=AssertionError("legacy scan")):
        prepared = ctl.prepare_request_route(req, [])
        decision = ctl.budget_resolver.resolve("", prepared.config, None, threshold=prepared.threshold)
    assert prepared.route.scene == "noemate_main"
    assert prepared.threshold.tokens == 750
    assert decision.hard_think_token_limit == 750
    assert decision.budget.check_length == 750
    assert decision.budget.think_stride == 256
    assert decision.budget.num_thinking == 4
    assert decision.budget.min_think_budget == 1
    assert decision.source_by_field["hard_think_token_limit"]["source"] == "scene.max_think_tokens"
    assert decision.source_by_field["check_length"]["threshold_source"] == "scene"


@pytest.mark.parametrize("mutate, reason", [
    (lambda r: setattr(r, "messages", []), "missing_messages"),
    (lambda r: r.messages.pop(0), "missing_leading_system"),
    (lambda r: r.messages[0].update(content=[]), "unsupported_system_content"),
    (lambda r: r.messages[0].update(content=" " + r.messages[0]["content"]), "unknown_system_prefix"),
    (lambda r: r.messages.append({"role": "system", "content": "override"}), "conflicting_instruction_roles"),
    (lambda r: r.messages.append({"role": "developer", "content": "override"}), "conflicting_instruction_roles"),
    (lambda r: setattr(r, "messages", r.messages + [{"role": "tool"}] * 4095), "message_limit"),
])
def test_unsupported_shapes_reject_to_native(mutate, reason):
    req = request()
    mutate(req)
    prepared = controller().prepare_request_route(req, [])
    assert prepared.route.action == "native"
    assert prepared.route.reason == reason
    assert prepared.threshold is None


def test_history_content_is_not_read_and_system_head_is_bounded():
    class RoleOnly:
        role = "tool"

        @property
        def content(self):
            raise AssertionError("history body read")

    class BoundedText(str):
        def __getitem__(self, key):
            assert isinstance(key, slice) and key.stop <= 512
            return super().__getitem__(key)

    req = request()
    req.messages[0]["content"] = BoundedText(req.messages[0]["content"] + "x" * 1_000_000)
    req.messages += [RoleOnly()] * 4094
    assert controller().prepare_request_route(req, []).route.scene == "noemate_main"
    assert len(system_identity_view(req.messages).prefix) == 512
    # An identity quoted in a user/tool body never establishes system identity.
    req.messages[0]["content"] = "unknown"
    req.messages[1]["content"] = load_dtb_service_config(CONFIG).scene_rules[0].system_prefix
    assert controller().prepare_request_route(req, []).route.action == "native"


def test_first_user_and_tools_guards(tmp_path):
    service = load_variant(tmp_path, lambda c: c["SCENES"]["noemate_main"]["match"].update(
        first_user_prefix="title:", tools_empty=True))
    ctl = controller(service)
    req = request()
    req.messages[1]["content"] = "title: correct"
    req.messages.append({"role": "user", "content": "different last user"})
    assert ctl.prepare_request_route(req, []).route.action == "dtb"
    req.messages[1]["content"] = [{"type": "text", "text": "title:"}]
    assert ctl.prepare_request_route(req, []).route.reason == "first_user_guard"
    req.messages[1]["content"] = "title: correct"
    req.tools = [{}]
    assert ctl.prepare_request_route(req, []).route.reason == "tools_guard"


def test_fixed_overrides_and_disabled_switches():
    ctl = controller()
    req = request("unknown", fixed_check_length=99)
    assert ctl.prepare_request_route(req, []).route.action == "native"
    req.fixed_budget_tokens = 100
    with patch("dtb.budgeting.scene_router.system_identity_view", side_effect=AssertionError("scan")):
        prepared = ctl.prepare_request_route(req, [])
    assert prepared.route.reason == "fixed_budget"
    assert prepared.threshold.tokens == 99
    req.fixed_prob_th = -1
    with pytest.raises(ValueError):
        ctl.prepare_request_route(req, [])
    req.enable_think_budget = False
    assert ctl.prepare_request_route(req, []).route.reason == "disabled"
    disabled = replace(ctl.service_config, algorithm=replace(ctl.service_config.algorithm, budget_enabled=False))
    assert controller(disabled).prepare_request_route(request(), []).route.action == "native"
    matched = ctl.prepare_request_route(request(fixed_check_length=600), [])
    assert matched.threshold.tokens == 600
    assert matched.threshold.scene_info["scene"] == "noemate_main"


def test_unknown_budget_fallback_and_legacy_native(tmp_path):
    service = load_variant(tmp_path, lambda c: c["ROUTING"].update(on_unmatched="budget"))
    prepared = controller(service).prepare_request_route(request("unknown"), [])
    assert prepared.route.action == "dtb" and not prepared.route.matched
    assert prepared.threshold.source == "runtime"
    raw = yaml.safe_load((ROOT / "dtb/config/budget_config.yaml").read_text(encoding="utf8"))
    raw["SCENES"]["native_test"] = {"action": "native", "indicators": {"required_all": ["exact identity"]}}
    path = tmp_path / "legacy.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf8")
    ctl = controller(load_dtb_service_config(path))
    for fixed in (0, 77):
        assert ctl.prepare_request_route(request(fixed_check_length=fixed), [
            {"role": "user", "content": "exact identity"}]).route.action == "native"
    assert ctl.prepare_request_route(request(fixed_budget_tokens=100), [
        {"role": "user", "content": "exact identity"}]).route.action == "dtb"


def test_phase_route_only_and_off_no_clock_or_event():
    ctl = controller()
    with patch("dtb.request_controller.time.perf_counter", side_effect=AssertionError("clock")), \
         patch.object(ctl.observability, "record_route", side_effect=AssertionError("event")):
        ctl.prepare_request_route(request("unknown"), [])
    service = replace(ctl.service_config, observability=DtbObservabilityConfig(mode=DtbTelemetryMode.PHASE))
    ctl = controller(service)
    try:
        with patch.object(ctl.observability.writer, "publish") as publish, \
             patch.object(ctl.observability, "create_phase_recorder", side_effect=AssertionError("recorder")):
            ctl.prepare_request_route(request("private system body"), [], request_id="parent")
            publish.assert_called_once()
            event, record = publish.call_args.args
            assert event == "DTB_ROUTE" and record["action"] == "native"
            assert record["parent_request_id"] == "parent"
            assert "private system body" not in json.dumps(record)
    finally:
        ctl.observability.close()


@pytest.fixture(scope="module")
def patched_chat_sources(tmp_path_factory):
    directory = tmp_path_factory.mktemp("serving")
    relative = Path("vllm/entrypoints/openai/chat_completion")
    (directory / relative).mkdir(parents=True)
    for name in ("protocol.py", "serving.py"):
        shutil.copyfile(ROOT / "third_party/vllm" / relative / name, directory / relative / name)
    subprocess.run(["git", "apply", str(ROOT / "patches/vllm/v0.23.0/dtb_serving.patch")],
                   cwd=directory, stdin=subprocess.DEVNULL, check=True, capture_output=True)
    return NS(**{name: ast.parse((directory / relative / f"{name}.py").read_text(encoding="utf8"))
                 for name in ("protocol", "serving")})


@pytest.fixture(scope="module")
def patched_serving(patched_chat_sources):
    cls = next(node for node in patched_chat_sources.serving.body
               if isinstance(node, ast.ClassDef) and node.name == "OpenAIServingChat")
    method = next(node for node in cls.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_create_chat_completion")
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), method], type_ignores=[])
    ns = {"ErrorResponse": type("ErrorResponse", (), {}), "RequestResponseMetadata": NS,
          "BeamSearchParams": type("BeamSearchParams", (), {}), "get_max_tokens": lambda *a, **k: 100}
    exec(compile(ast.fix_missing_locations(module), "patched-serving", "exec"), ns)
    return ns["_create_chat_completion"]


def test_patched_serving_initializes_without_deer_and_protocol_has_no_deer_fields(patched_chat_sources):
    tree = patched_chat_sources.serving
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            assert not (node.module or "").startswith("deer")
        elif isinstance(node, ast.Import):
            assert all(alias.name.split(".")[0] != "deer" for alias in node.names)
    protocol = next(node for node in patched_chat_sources.protocol.body
                    if isinstance(node, ast.ClassDef) and node.name == "ChatCompletionRequest")
    fields = {node.target.id for node in protocol.body
              if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)}
    assert "enable_think_budget" in fields
    assert not {"enable_deer", "deer_initial_thought_limit", "deer_think_stride", "deer_task_is_code"} & fields

    # Execute the actual patched initializer with native dependencies stubbed;
    # there is deliberately no DEER symbol or module in this environment.
    class NativeServing:
        def __init__(self, **kwargs):
            self.model_config = NS(model="model", generation_config="vllm",
                                   hf_config=NS(model_type="qwen3"), get_diff_sampling_param=lambda: {})

    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "OpenAIServingChat")
    initializer = next(node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == "__init__")
    minimal_class = ast.ClassDef(name=cls.name, bases=[ast.Name(id="OpenAIServing", ctx=ast.Load())],
                                keywords=[], body=[initializer], decorator_list=[])
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
                              minimal_class], type_ignores=[])
    constructor = Mock(return_value=object())
    ns = {"OpenAIServing": NativeServing, "DtbRequestController": constructor,
          "ParserManager": NS(get_reasoning_parser=lambda **kwargs: None, get_tool_parser=lambda **kwargs: None,
                              get_parser=lambda **kwargs: None),
          "is_mistral_tool_parser": lambda parser: False, "get_tool_call_id_type": lambda config: "random"}
    exec(compile(ast.fix_missing_locations(module), "patched-serving-init", "exec"), ns)
    serving = ns[cls.name](object(), object(), "assistant", openai_serving_render=object(),
                           request_logger=None, chat_template=None, chat_template_content_format="auto")
    constructor.assert_called_once_with()
    assert serving.dtb_request_controller is constructor.return_value
    assert not hasattr(serving, "cot_controller")


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("prefix", ["unknown", "你是对话后续问题推荐助手。你的唯一任务是根据用户消息提供的本轮对话上下文，生成 3 个可直接点击追问的问题。"])
@pytest.mark.parametrize("failure", [None, "error", "cancel"])
@pytest.mark.parametrize("retired_fields", [False, True])
def test_actual_patched_native_branch_matches_disabled(patched_serving, stream, prefix, failure, retired_fields):
    async def run(mode):
        ctl = controller()
        ctl.handle_request = AsyncMock(side_effect=AssertionError("DTB handle"))
        ctl.budget_resolver.resolve = Mock(side_effect=AssertionError("budget"))
        native_outputs = [object(), object()]
        closed = []

        async def generate_outputs():
            try:
                if failure == "error":
                    raise RuntimeError("engine error")
                if failure == "cancel":
                    raise asyncio.CancelledError()
                for output in native_outputs:
                    yield output
            finally:
                closed.append(True)

        async def render(req):
            return req.messages, [{"prompt_token_ids": [1]}]

        def streaming(req, outputs, *args, **kwargs):
            return outputs

        async def full(req, outputs, *args):
            return [output async for output in outputs]

        parser = NS(is_reasoning_end=lambda ids: False)
        engine = NS(generate=Mock(side_effect=lambda *a, **k: generate_outputs()))
        serving = NS(dtb_request_controller=ctl, renderer=NS(tokenizer=object()),
                     _effective_chat_template_kwargs=lambda r: {"enable_thinking": True},
                     reasoning_parser_cls=lambda *a, **k: parser, render_chat_request=render,
                     _base_request_id=lambda *a: "test", _maybe_get_adapters=lambda *a, **k: "lora",
                     models=NS(model_name=lambda l: "model"), _get_data_parallel_rank=lambda r: 3,
                     model_config=NS(max_model_len=2048), default_sampling_params={}, override_max_tokens=None,
                     _extract_prompt_components=lambda p: NS(token_ids=p["prompt_token_ids"]),
                     _extract_prompt_len=lambda p: 1, _log_inputs=lambda *a, **k: None,
                     _get_trace_headers=AsyncMock(return_value={"traceparent": "trace"}),
                     engine_client=engine, parser_cls=Mock(return_value=object()),
                     chat_completion_stream_generator=streaming, chat_completion_full_generator=full)
        req = request(prefix, stream=stream, enable_think_budget=mode != "request_disabled",
                      request_id="test", max_completion_tokens=None, max_tokens=100,
                      truncate_prompt_tokens=None, use_beam_search=False, priority=7,
                      include_reasoning=True, _grammar_from_tool_parser=False,
                      to_sampling_params=lambda *a: NS(max_tokens=100))
        if retired_fields:
            # Native OpenAIBaseModel permits extras. Old DEER extras have no
            # serving behavior and cannot enable DTB or another controller.
            req.enable_deer, req.deer_initial_thought_limit = True, 1
            req.deer_think_stride, req.deer_task_is_code = 1, True
        # Disabled DTB retains the true request flag when only the service switch is off.
        if mode == "server_disabled":
            ctl.service_config = replace(ctl.service_config, algorithm=replace(ctl.service_config.algorithm, budget_enabled=False))
        with patch("dtb.request_controller.DtbRequestContext", side_effect=AssertionError("context")), \
             patch.object(ctl.observability, "create_phase_recorder", side_effect=AssertionError("recorder")):
            try:
                result = await patched_serving(serving, req, NS(headers={}, state=NS()))
                outputs = [o async for o in result] if stream else result
                assert outputs == native_outputs
                status = "ok"
            except (RuntimeError, asyncio.CancelledError) as exc:
                assert failure
                status = type(exc).__name__
        assert closed == [True]
        ctl.handle_request.assert_not_called()
        ctl.budget_resolver.resolve.assert_not_called()
        engine.generate.assert_called_once()
        kwargs = engine.generate.call_args.kwargs
        assert kwargs == {"lora_request": "lora", "trace_headers": {"traceparent": "trace"},
                          "priority": 7, "data_parallel_rank": 3, "reasoning_ended": False,
                          "reasoning_parser_kwargs": {"chat_template_kwargs": {"enable_thinking": True}}}
        assert serving.parser_cls.call_count == 1
        return status, kwargs

    expected = asyncio.run(run("native"))
    assert expected == asyncio.run(run("server_disabled"))
    assert expected == asyncio.run(run("request_disabled"))


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("fixed_budget", [0, 100])
def test_actual_patched_dtb_branch_keeps_prepared_context(patched_serving, stream, fixed_budget):
    async def run():
        ctl = controller()
        ctl.prepare_request_route = Mock(wraps=ctl.prepare_request_route)
        expected = object()
        ctl.handle_request = AsyncMock(return_value=expected)

        async def render(req):
            return req.messages, [{"prompt_token_ids": [1]}]

        parser = NS(is_reasoning_end=lambda ids: False)
        engine = NS(generate=Mock(side_effect=AssertionError("native generate before DTB")))
        serving = NS(dtb_request_controller=ctl, renderer=NS(tokenizer=object()),
                     _effective_chat_template_kwargs=lambda r: {"enable_thinking": True},
                     reasoning_parser_cls=lambda *a, **k: parser, render_chat_request=render,
                     _base_request_id=lambda *a: "test", _maybe_get_adapters=lambda *a, **k: "lora",
                     models=NS(model_name=lambda l: "model"), _get_data_parallel_rank=lambda r: 3,
                     model_config=NS(max_model_len=2048), default_sampling_params={}, override_max_tokens=None,
                     _extract_prompt_components=lambda p: NS(token_ids=p["prompt_token_ids"], text="prompt"),
                     _extract_prompt_len=lambda p: 1,
                     _get_trace_headers=AsyncMock(return_value={"traceparent": "trace"}),
                     engine_client=engine, parser_cls=Mock(return_value=object()))
        req = request("unknown" if fixed_budget else None, stream=stream,
                      fixed_budget_tokens=fixed_budget, request_id="test", max_completion_tokens=None,
                      max_tokens=100, truncate_prompt_tokens=None, include_reasoning=True,
                      _grammar_from_tool_parser=False)
        assert await patched_serving(serving, req, NS(headers={}, state=NS())) is expected
        ctl.prepare_request_route.assert_called_once()
        ctl.handle_request.assert_awaited_once()
        prepared = ctl.handle_request.call_args.kwargs["prepared"]
        assert prepared.route.action == "dtb"
        assert prepared.route.reason == ("fixed_budget" if fixed_budget else "scene_match")
        assert req._dtb_route_identity["completion_token_limit"] == 100
        assert req._dtb_route_identity["lora_request"] == "lora"
        assert req._dtb_route_identity["data_parallel_rank"] == 3
        assert req._dtb_route_identity["trace_headers"] == {"traceparent": "trace"}
        assert req._dtb_route_identity["reasoning_parser"] is parser
        engine.generate.assert_not_called()

    asyncio.run(run())


def test_route_extraction_keeps_native_missing_and_duplicate_distinct(tmp_path):
    ids = ["native", "dtb", "absent", "duplicate"]
    lines = ["DTB_ROUTE " + json.dumps({"schema": 1, "parent_request_id": rid,
              "action": action, "scene": "s", "reason": "scene_match"}) + "\n"
             for rid, action in [("native", "native"), ("dtb", "dtb"),
                                 ("duplicate", "native"), ("duplicate", "native"), ("unrelated", "native")]]
    routes = []
    entries, ranges = scan_server_log(lines, set(ids), route_entries=routes)
    specs = [ResultSpec(tmp_path / "result.csv", "result.csv",
                        tuple(ClientResultRow(i + 1, rid) for i, rid in enumerate(ids)), {})]
    manifest = write_bound_outputs(entries, ranges, specs, tmp_path, tmp_path / "server.log",
                                   tmp_path / "_SUMMARY.csv", routes)
    assert manifest["selected_route_records"] == 4
    counts = manifest["results"][0]["route_status_counts"]
    assert counts == {"native_no_phase_expected": 1, "dtb_phase_missing": 1,
                      "route_missing": 1, "duplicate_route": 1}
    assert len((tmp_path / "extracted/result/routes.jsonl").read_text().splitlines()) == 4


@pytest.mark.parametrize("stream", [False, True])
def test_prepared_dtb_handle_does_not_reclassify_or_resolve_short_stream(stream):
    from test_dtb_state_machine import make_context, result, fake_reasoning_protocol, THINK_END_ID

    async def run():
        source = result("short" if stream else "short</think>",
                        [10, THINK_END_ID] if stream else [10, *map(ord, "</think>")],
                        stop_reason=THINK_END_ID if stream else "</think>")
        context, engine = make_context([[source], [result("answer", [99])]])
        req = context.request
        req.messages = request().messages
        req.enable_think_budget = True
        req.stream = stream
        req._dtb_route_identity = {"model_name": "model", "lora_request": "lora",
                                   "completion_token_limit": 100, "prompt_text": "original"}
        sampling = req.to_sampling_params

        def params(*args):
            value = sampling(*args)
            value.stop, value.min_tokens = [], 0
            return value

        req.to_sampling_params = params

        def streaming(_req, outputs, *args, **kwargs):
            return outputs

        async def full(_req, outputs, *args, **kwargs):
            return [item async for item in outputs]

        context.serving_chat.chat_completion_stream_generator = streaming
        context.serving_chat.chat_completion_full_generator = full
        ctl = controller()
        ctl.prepare_request_route = Mock(wraps=ctl.prepare_request_route)
        ctl.budget_resolver.resolve = Mock(wraps=ctl.budget_resolver.resolve)
        with patch("dtb.request_controller.route_system_request", wraps=__import__(
                "dtb.budgeting.scene_router", fromlist=["route_system_request"]).route_system_request) as route, \
             patch("dtb.request_controller.extract_prompt_text_views", side_effect=AssertionError("full text scan")), \
             patch("dtb.request_controller.bind_reasoning_boundary_protocol", return_value=fake_reasoning_protocol()):
            prepared = ctl.prepare_request_route(req, [])
            response = await ctl.handle_request(context.serving_chat, [], [{"prompt_token_ids": [1]}],
                                                req, "parent", context.tokenizer, NS(), prepared=prepared)
            outputs = [item async for item in response] if stream else response
            assert outputs
            ctl.prepare_request_route.assert_called_once()
            route.assert_called_once()
        assert len(engine.calls) == (1 if stream else 2)
        assert ctl.budget_resolver.resolve.call_count == (0 if stream else 1)
        if not stream:
            assert ctl.budget_resolver.resolve.call_args.kwargs["threshold"].scene_info["scene"] == "noemate_main"

    asyncio.run(run())
