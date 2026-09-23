"""The delivery gate must reject bad results, not merely successful HTTP status."""
import asyncio
import copy
import json
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from tools.dtb import validate_release as gate

DATA = Path(__file__).resolve().parents[1] / "validation/replay/cases.jsonl"


def args():
    return SimpleNamespace(model="test-model", max_tokens=4096, seed=42)


def clean_row(case="a", attempt=1, variant="vanilla", elapsed=100):
    return dict(case_id=case, attempt=attempt, variant=variant, elapsed_ms=elapsed,
                validation_errors=[], status_code=200, error="", anomaly_reasons=[],
                finish_reasons=["stop"], terminal_count=1, content="42", tool_calls=[])


def test_dataset_and_captured_overrides_cannot_pollute_auto():
    cases, checks = gate.read_dataset(DATA)
    assert len(cases) == len(checks) == 7
    before = copy.deepcopy(cases[0].request_body)
    cases[0].request_body["fixed_budget_tokens"] = 3
    for profile in gate.PROFILES:
        for stream in (True, False):
            body = gate.request_body(cases[0], profile, "dtb", "req", args(), stream)
            assert body["enable_think_budget"] is True
            assert body["stream"] == stream
            assert ("stream_options" in body) == stream
            assert {k: body[k] for k in gate.replay.FIXED_FIELDS if k in body} == gate.PROFILES[profile]
            native = gate.request_body(cases[0], profile, "vanilla", "req", args(), stream)
            assert native["enable_think_budget"] is False
            assert not set(gate.replay.FIXED_FIELDS) & native.keys()
    cases[0].request_body.pop("fixed_budget_tokens")
    assert cases[0].request_body == before


def test_packaged_system_identities_match_candidate():
    from dtb.budgeting.scene_router import route_system_request
    from dtb.config.loader import load_dtb_service_config
    service = load_dtb_service_config(gate.ROOT / "dtb/config/budget_config_noemate_750.yaml")
    for line in DATA.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        route = route_system_request(SimpleNamespace(**row["request_body"]), service)
        assert {"scene":route.scene,"action":route.action} == row["auto_route_noemate_750"]


@pytest.mark.parametrize("change", [
    {"finish_reasons": ["length"]}, {"finish_reasons": []}, {"content": "41"},
    {"status_code": 500}, {"error": "timeout"}, {"terminal_count": 2},
    {"elapsed_ms": float("nan")}, {"elapsed_ms": 0}, {"anomaly_reasons": ["leaked_tool_xml"]},
])
def test_fail_closed(change):
    row = clean_row()
    row.update(change)
    assert gate.answer_errors(row, {"kind": "text", "value": "42"})


def test_semantics_not_just_shape():
    row = clean_row()
    row["content"] = '{"cost": true}'
    assert gate.answer_errors(row, {"kind": "json", "value": {"cost": 1}}) == ["answer_mismatch"]
    row.update(content="", finish_reasons=["tool_calls"], tool_calls=[{"name":"lookup_alarm", "arguments":'{"resource":"WRONG"}'}])
    assert gate.answer_errors(row, {"kind":"tool_calls", "value":[{"name":"lookup_alarm", "arguments":{"resource":"AMF01"}}]}) == ["answer_mismatch"]


def test_missing_duplicate_failed_and_small_pairs_never_pass_gain_gate():
    rows = [clean_row(attempt=a, variant=v, elapsed=100 if v=="vanilla" else 80)
            for a in range(1,6) for v in ("vanilla","dtb")]
    result = gate.summarize(rows, ["a"], 5, 5)
    assert result["gain_gate_passed"] and result["total_e2e_gain_percent"] == 20
    for bad in (rows[:-1], rows + rows[:1]):
        assert not gate.summarize(bad, ["a"], 5, 5)["quality_ok"]
    assert not gate.summarize(rows[:2], ["a"], 1, 5)["gain_gate_passed"]
    rows[0]["validation_errors"] = ["wrong"]
    assert not gate.summarize(rows, ["a"], 5, 5)["gain_gate_passed"]


@pytest.mark.parametrize("stream", [True, False])
def test_actual_http_parser_and_answer_gate_with_mock_transport(stream):
    cases, checks = gate.read_dataset(DATA)
    selected = [cases[0], cases[2]]  # Text and tool response in each HTTP mode.

    async def run():
        for case in selected:
            check = checks[case.case_id]
            message = {"role":"assistant", "content":check["value"] if check["kind"]=="text" else None}
            finish = "stop"
            if check["kind"] == "tool_calls":
                finish = "tool_calls"
                message["tool_calls"] = [{"index":0,"id":"call_1","type":"function","function":{"name":"lookup_alarm","arguments":'{"resource":"AMF01"}'}}]

            def respond(request):
                body = json.loads(request.content)
                assert body["model"] == "test-model" and body["stream"] == stream
                payload = {"id":"chatcmpl-test", "choices":[{"index":0,"delta" if stream else "message":message,"finish_reason":finish}]}
                if stream:
                    return httpx.Response(200, text="data: "+json.dumps(payload)+"\n\ndata: [DONE]\n\n", headers={"Content-Type":"text/event-stream"})
                return httpx.Response(200, json=payload)

            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                result = await gate.replay.run_one(client, url="http://test/v1/chat/completions", headers={},
                    body=gate.request_body(case,"cap750","dtb","test",args(),stream),
                    line_no=case.line_no,case_id=case.case_id,category=case.category,attempt=1,variant="dtb",
                    expected_tool_call=case.expected_tool_call,expected_tool_names=case.expected_tool_names,
                    latency_eligible=True,chunks_file=StringIO())
            assert not gate.answer_errors(asdict(result), check), asdict(result)
    asyncio.run(run())


def test_real_manifest_required_and_phase_never_ranks(tmp_path):
    path = tmp_path / "identity.json"
    with pytest.raises(ValueError):
        gate.load_identity(None, False)
    path.write_text(json.dumps({key:"REPLACE" for key in gate.IDENTITY_FIELDS}))
    with pytest.raises(ValueError):
        gate.load_identity(path, False)
    data = {key:"test" for key in gate.IDENTITY_FIELDS}
    data["observability"] = "phase"
    path.write_text(json.dumps(data))
    assert gate.load_identity(path, False)["observability"] == "phase"
    with pytest.raises(ValueError):
        gate.load_identity(path, True)


@pytest.mark.parametrize("broken", [False, True])
def test_full_matrix_orchestrates_http_warmup_and_reports_failures(tmp_path, monkeypatch, broken):
    cases, checks = gate.read_dataset(DATA)
    real_client = httpx.AsyncClient
    seen = []

    def respond(request):
        body = json.loads(request.content)
        case = cases[int(body["request_id"].rsplit("-", 3)[1])-1]
        check = checks[case.case_id]
        seen.append(body)
        content = check["value"] if check["kind"]=="text" else json.dumps(check["value"])
        message, finish = {"role":"assistant","content":content}, "stop"
        if check["kind"]=="tool_calls":
            finish = "tool_calls"
            message.update(content=None,tool_calls=[{"index":0,"id":"call_1","type":"function","function":{
                "name":"lookup_alarm","arguments":'{"resource":"AMF01"}'}}])
        if broken: finish="length"
        payload={"id":"chatcmpl-"+body["request_id"],"choices":[{"index":0,
            "delta" if body["stream"] else "message":message,"finish_reason":finish}]}
        if body["stream"]:
            return httpx.Response(200,text="data: "+json.dumps(payload)+"\n\ndata: [DONE]\n\n")
        return httpx.Response(200,json=payload)

    def client(*a, **kw):
        return real_client(*a, **kw, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(gate.replay.httpx,"AsyncClient",client)
    identity={key:"test" for key in gate.IDENTITY_FIELDS}
    identity["observability"]="off"
    manifest=tmp_path/"identity.json"
    manifest.write_text(json.dumps(identity))
    opts=gate.parse_args(["--model","fake","--server-manifest",str(manifest),"--output",str(tmp_path/"out"),
                          "--warmup","1","--concurrency","4"])
    code=asyncio.run(gate.run_matrix(opts))
    result=json.loads((opts.output/"validation.json").read_text())
    assert code == (1 if broken else 0)
    assert result["functional_pass"] is (not broken)
    if not broken:
        assert len(seen)==168
        assert len(result["cells"])==6
        assert all(r["requests"]==14 for r in result["cells"])
        assert not result["sample_gain_observed"]
        with pytest.raises(FileExistsError):
            asyncio.run(gate.run_matrix(opts))
