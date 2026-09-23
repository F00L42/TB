import copy
import hashlib
import json

import pytest

from tools.dtb.audit_release_phase import audit


def fixture(tmp_path):
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(json.dumps({"case_id":"x","auto_route_noemate_750":{"scene":"noemate_main","action":"dtb"}})+"\n")
    (tmp_path / "manifest.json").write_text(json.dumps({"server_identity":{"observability":"phase"},
        "dataset":str(dataset),"input_sha256":hashlib.sha256(dataset.read_bytes()).hexdigest(),
        "profiles":["cap750","probe128"],"protocol":"both","cases":["x"],
        "warmup_batches_per_cell_variant":0,"repetitions":1}))
    (tmp_path / "validation.json").write_text('{"functional_pass":true}')
    directory = tmp_path / "probe128-stream"
    directory.mkdir()
    rows, logs = [], []
    for stream, profile in ((True,"cap750"),(True,"probe128"),(False,"cap750"),(False,"probe128")):
        parent = f"{profile}-{stream}"
        rows.append({"case_id":"x","profile":profile,"stream":stream,"variant":"dtb","attempt":1,
                     "server_request_id":parent,"validation_errors":[]})
        rows.append({**rows[-1],"variant":"vanilla","server_request_id":parent+"-vanilla"})
        logs.append(("DTB_ROUTE",{"parent_request_id":parent,"scene":"general","action":"dtb"}))
        hidden = 3 if profile=="probe128" else 0
        if hidden:
            logs.append(("DTB_PHASE",{"parent_request_id":parent,"phase":"confidence_probe","generated_tokens":hidden}))
        if stream:
            logs.append(("DTB_PHASE",{"parent_request_id":parent,"phase":"native_passthrough","generated_tokens":128}))
        logs.append(("DTB_PHASE",{"parent_request_id":parent,"phase":"final_answer","generated_tokens":20}))
        summary = {"parent_request_id":parent,"completion_status":"success","hidden_probe_tokens":hidden}
        if stream:
            summary.update(final_terminal_count=1, intervention={"intervention_status":"triggered",
                "post_drain_branch":"confidence_probe" if hidden else "budget_final","abort_count":1,
                "abort_terminal_seen":True,"abort_lost_to_native_terminal":False})
        logs.append(("DTB_REQUEST_SUMMARY",summary))
    (directory / "summary.jsonl").write_text(''.join(json.dumps(r)+'\n' for r in rows))
    return logs


def run(tmp_path, logs, require=True):
    path = tmp_path / "server.txt"
    path.write_text(''.join(name+' '+json.dumps(row)+'\n' for name,row in logs))
    return audit(tmp_path, [path], require_intervention=require)


def test_complete_ledger_and_actual_intervention(tmp_path):
    result = run(tmp_path, fixture(tmp_path))
    assert result["exit_code"] == 0
    assert result["probe_interventions"] == result["direct_interventions"] == 1


@pytest.mark.parametrize("fault", ["missing_route","duplicate_route","wrong_route","hidden","abort","summary","client_quality"])
def test_phase_audit_rejects_false_success(tmp_path, fault):
    logs = copy.deepcopy(fixture(tmp_path))
    if fault=="missing_route": logs.pop(0)
    if fault=="duplicate_route": logs.append(logs[0])
    if fault=="wrong_route": logs[0][1]["action"]="native"
    summary=next(row for name,row in logs if name=="DTB_REQUEST_SUMMARY")
    if fault=="hidden": summary["hidden_probe_tokens"]=42
    if fault=="abort": summary["intervention"]["abort_count"]=0
    if fault=="summary": summary["completion_status"]="error"
    if fault=="client_quality": (tmp_path/"validation.json").write_text('{"functional_pass":false}')
    assert run(tmp_path,logs)["exit_code"] == 1


def test_uncovered_paths_do_not_silently_pass(tmp_path):
    logs=fixture(tmp_path)
    for name,row in logs:
        if name=="DTB_REQUEST_SUMMARY": row.pop("intervention",None)
    assert run(tmp_path,logs)["exit_code"] == 3
    assert run(tmp_path,logs,require=False)["exit_code"] == 0


def test_deleted_client_rows_cannot_make_missing_ledger_look_clean(tmp_path):
    logs=fixture(tmp_path)
    path=tmp_path/"probe128-stream/summary.jsonl"
    path.write_text('\n'.join(path.read_text().splitlines()[1:])+'\n')
    with pytest.raises(ValueError,match="client matrix"):
        run(tmp_path,logs)
