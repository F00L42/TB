"""Model-free checks of the diagnostic probe coverage reader."""
import importlib.util
import json
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1]/"docs/log/perf/agent_minimum_intervention/run_server_03/check_probe.py"
spec = importlib.util.spec_from_file_location("g1_probe_check", SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.mark.parametrize("change,code", [("none",0),("semantic",0),("bypass",3),
    ("missing",1),("duplicate",1),("tokens",1),("protocol",1)])
def test_probe_evidence_requires_complete_chain(tmp_path, change, code):
    client = {"server_request_id":"p","variant":"dtb","status_code":200,"terminal_count":1,
              "protocol_anomaly_reasons":[],"semantic_anomaly_reasons":[]}
    summary = {"parent_request_id":"p","completion_status":"success","error":None,
               "hidden_probe_tokens":50,"final_terminal_count":1,
               "intervention":{"intervention_status":"triggered","post_drain_branch":"confidence_probe",
                   "abort_count":1,"abort_terminal_seen":True,"abort_lost_to_native_terminal":False}}
    phases = [{"parent_request_id":"p","phase":p,"generated_tokens":n}
              for p,n in (("native_passthrough",851),("confidence_probe",50),("final_answer",10))]
    if change == "semantic":
        client["semantic_anomaly_reasons"] = ["expected_tool_name_missing"]
    if change == "protocol":
        client["protocol_anomaly_reasons"] = ["invalid_tool_arguments_json"]
    if change == "tokens":
        summary["hidden_probe_tokens"] = 51
    if change == "bypass":
        phases = phases[:1]
        summary["intervention"] = {"intervention_status":"disarmed_boundary"}
        summary["hidden_probe_tokens"] = 0
    summaries = [] if change == "missing" else [summary]*(2 if change == "duplicate" else 1)
    (tmp_path/"summary.jsonl").write_text(json.dumps(client)+"\n",encoding="utf-8")
    log = tmp_path/"server.log"
    log.write_text("".join("(APIServer) DTB_PHASE "+json.dumps(p)+"\n" for p in phases)
                   +"".join("(APIServer) DTB_REQUEST_SUMMARY "+json.dumps(s)+"\n" for s in summaries),encoding="utf-8")
    result = module.inspect_probe(tmp_path,[log])
    assert result["exit_code"] == code
    assert result["full_g1_accepted"] is False
