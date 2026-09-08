"""No-model tests of G0 validation/orchestration; not real tokenizer evidence."""
import copy
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from tools.dtb import g0_experiment as g0
from tools.dtb.g0_runtime import Runtime
from tools.dtb.g0_support import (digest, fresh_directory, load_fixtures,
                                  load_profiles, prepare_inputs, write_json)

INPUTS = g0.REPO / "docs/log/perf/agent_minimum_intervention/input/g0"


class FakeRuntime:
    test_identity = {"decoder_class": "test-double"}
    tokenizer = None

    def __init__(self, corrupt=False):
        self.corrupt = corrupt

    def encode(self, text):
        return list(map(ord, text))

    def decoder(self, prompt, params):
        return None

    def params(self, profile):
        return SimpleNamespace(max_tokens=4096, stop=[])

    def abort_prefix(self, prompt, prefix, trigger, params):
        if len(prefix) >= params.max_tokens:
            return {"natural_before_abort":"length"}
        return {"natural_before_abort":None,"text":"".join(map(chr,prefix)),"token_ids":prefix,"trace":[]}

    def decode(self, prompt, sequence, params):
        return {"text":"".join(map(chr,sequence)),"token_ids":sequence,"trace":[],"finish_reason":"fixture_end"}

    def direct_prefix(self, prompt, prefix, params):
        return ("BAD" if self.corrupt else "")+"".join(map(chr,prefix))


def test_bundled_inputs_and_single_field_control():
    profiles = load_profiles(INPUTS / "legacy_profiles.json")
    left, right = [copy.deepcopy(p["sampling_params"]) for p in profiles]
    assert left.pop("skip_special_tokens") is False
    assert right.pop("skip_special_tokens") is True
    assert left == right
    assert len(load_fixtures(INPUTS / "legacy_fixtures.jsonl")) == 4
    assert len(load_fixtures(INPUTS / "production_fixtures.jsonl")) == 3
    assert load_profiles(INPUTS / "vanilla_log_profile.json")[0]["source"] == "log_derived"
    # Match historical combining character, not an NFC-normalized replacement.
    assert "e\u0301" in load_fixtures(INPUTS / "legacy_fixtures.jsonl")[1]["sample_text"]


@pytest.mark.parametrize("change", [
    {"sampling_params":{"skip_special_tokens":"false","output_kind":"DELTA"}},
    {"sampling_params":{"skip_special_tokens":True,"output_kind":"FINAL_ONLY"}},
    {"sampling_params":{"skip_special_tokens":True,"output_kind":"DELTA","min_tokens":1}},
    {"sampling_params":{"skip_special_tokens":True,"output_kind":"DELTA","repetition_penalty":1.1}},
    {"source":"live_captured"}, {"typo":True}, {"id":"../escape"},
])
def test_bad_profile_rejected(tmp_path, change):
    p = {"id":"x","source":"historical","sampling_params":{"skip_special_tokens":False,"output_kind":"DELTA"}}
    p.update(change)
    file = tmp_path / "profiles.json"
    write_json(file,{"schema":1,"profiles":[p]})
    with pytest.raises(ValueError):
        load_profiles(file)


def test_existing_output_preserved(tmp_path):
    out = fresh_directory(tmp_path / "out")
    (out / "user.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        fresh_directory(out)
    assert (out / "user.txt").read_text() == "keep"


def test_prompt_precedence_and_padding():
    fixture = load_fixtures(INPUTS / "production_fixtures.jsonl")[1]
    p, seq, cuts, source = g0.materialize(FakeRuntime(),fixture,{"prompt_token_ids":[123,456]})
    assert p == [123,456] and source == "captured_engine_prompt"
    assert len(seq) == 848+len(fixture["sample_text"])
    assert 850 in cuts
    fixture["prompt_token_ids"] = [99]
    assert g0.materialize(FakeRuntime(),fixture,{"prompt_token_ids":[123]})[0] == [99]


@pytest.mark.parametrize("corrupt,expected", [(False,"pass"),(True,"fail")])
def test_fixed_suffix_comparison_and_failure_evidence(corrupt, expected):
    runtime = FakeRuntime(corrupt)
    result = g0.trial(runtime,[12],runtime.encode("abc"),1,1,"original",runtime.params({}))
    assert result["status"] == expected
    assert result["token_ids_equal"]
    if corrupt:
        assert result["text_equal"] and not result["direct_decoder_equal"]
        assert result["direct"] == "BADabc"
        assert result["prefix_ids"] == [97,98]


def test_natural_stop_is_not_restarted():
    params = SimpleNamespace(max_tokens=2,stop=[])
    result = g0.trial(FakeRuntime(),[],[97,98,99],2,0,"original",params)
    assert result["status"] == "not_applicable"


@pytest.mark.parametrize("token,stop_terminated,reason", [(9,True,"stop"),(7,True,"stop"),(6,False,None)])
def test_frontend_stop_flag_includes_eos(token, stop_terminated, reason):
    seen = []
    decoder = SimpleNamespace(update=lambda tokens, stop: seen.append(stop))
    params = SimpleNamespace(ignore_eos=False,_eos_token_id=9,stop_token_ids=[7],max_tokens=20)
    assert Runtime.update(decoder,token,1,params) == reason
    assert seen == [stop_terminated]


def args_for(tmp_path):
    fixture = {"id":"abc","source":"test","sample_text":"abc","cuts":"all","pending":[0,1,4],"suffixes":["original","forced_close"]}
    file = tmp_path / "fixtures.jsonl"
    file.write_text(json.dumps(fixture)+"\n",encoding="utf-8")
    return SimpleNamespace(profiles=INPUTS/"legacy_profiles.json",fixtures=file,
        profile_ids="H0-skip-false",require_live_profile=False,stop_control=True,
        output_dir=tmp_path/"output",tokenizer="test-only")


def test_matrix_counts_and_no_production_claim(tmp_path):
    args = args_for(tmp_path)
    assert g0.run_matrix(args,FakeRuntime()) == 0
    result = json.loads((args.output_dir/"summary.json").read_text())
    assert result["counts"] == {"pass":36}
    assert result["production_ready"] is False
    assert len(list((args.output_dir/"inputs").glob("*.json"))) == 1


def test_matrix_preserves_failure_and_returns_one(tmp_path):
    args = args_for(tmp_path)
    assert g0.run_matrix(args,FakeRuntime(True)) == 1
    assert len(list((args.output_dir/"failures").glob("*.json"))) == 36


def test_live_gate_refuses_log_template(tmp_path):
    args = args_for(tmp_path)
    args.require_live_profile = True
    with pytest.raises(ValueError, match="actual captured"):
        g0.run_matrix(args,FakeRuntime())
    assert not args.output_dir.exists()


def test_prepare_checks_locator_and_body(tmp_path):
    (tmp_path/"source.jsonl").write_text(json.dumps({"request_id":"r1","request_body":{"messages":[]}})+"\n")
    record = {"request_id":"r1","source_file":"source.jsonl","source_line":1,"tool_names":["read"]}
    (tmp_path/"ledger.jsonl").write_text(json.dumps(record)+"\n")
    write_json(tmp_path/"spec.json",{"schema":1,"correlation":"ledger.jsonl","proxy_run":".","request_ids":["r1"]})
    out = prepare_inputs(tmp_path,tmp_path/"spec.json",tmp_path/"out")
    row = json.loads((out/"requests.jsonl").read_text())
    assert row["request_body_sha256"] == digest({"messages":[]})
    assert row["replay_expectation"]["latency_eligible"] is False
    record["source_file"] = "../escape"
    (tmp_path/"ledger.jsonl").write_text(json.dumps(record)+"\n")
    with pytest.raises(ValueError,match="escapes"):
        prepare_inputs(tmp_path,tmp_path/"spec.json",tmp_path/"out2")


def test_batch_keeps_running_and_records_codes(tmp_path, monkeypatch):
    calls = []
    codes = iter([1,0,2])
    def launch(command, **kwargs):
        assert kwargs["stdin"] == subprocess.DEVNULL
        calls.append(command)
        return SimpleNamespace(returncode=next(codes))
    monkeypatch.setattr(g0.subprocess,"run",launch)
    out = tmp_path/"batch"
    assert g0.run_batch(SimpleNamespace(output_dir=out,tokenizer="unused")) == 2
    assert len(calls) == 3
    assert [r["exit_code"] for r in json.loads((out/"batch_summary.json").read_text())["runs"]] == [1,0,2]


def test_source_locator_counts_physical_blank_lines(tmp_path):
    record = {"request_id":"r1", "source_file":"source.jsonl", "source_line":2, "tool_names":[]}
    (tmp_path/"source.jsonl").write_text('\n{"request_id":"r1","request_body":{"messages":[]}}\n')
    (tmp_path/"ledger.jsonl").write_text(json.dumps(record)+"\n")
    write_json(tmp_path/"spec.json", {"schema":1,"correlation":"ledger.jsonl",
                                      "proxy_run":".","request_ids":["r1"]})
    out = prepare_inputs(tmp_path,tmp_path/"spec.json",tmp_path/"out")
    assert json.loads((out/"requests.jsonl").read_text())["request_id"] == "r1"
    (tmp_path/"ledger.jsonl").write_text((json.dumps(record)+"\n")*2)
    with pytest.raises(ValueError, match="duplicate"):
        prepare_inputs(tmp_path,tmp_path/"spec.json",tmp_path/"duplicate")


def test_prepared_inputs_exactly_match_archived_sources(tmp_path):
    out = prepare_inputs(g0.REPO,INPUTS/"request_selection.json",tmp_path/"regenerated")
    original = [json.loads(line) for line in (INPUTS/"prepared/requests.jsonl").read_text(encoding="utf-8").splitlines()]
    regenerated = [json.loads(line) for line in (out/"requests.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(original) == 14
    assert original == regenerated
