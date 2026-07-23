"""Live API client test for ``naie.api_server``.

Usage:
    python naie/api_client_test.py
    python naie/api_client_test.py --api-base http://127.0.0.1:8000/v1
    REMOTE_API=http://75.255.58.65:8000/v1 python naie/api_client_test.py

The script exercises:
    1. GET /health
    2. POST /v1/chat/completions with enable_budget=true
    3. POST /v1/chat/completions with enable_budget=false
    4. GET /v1/config and GET /v1/overrides
    5. POST /v1/chat/completions with full fixed budget overrides

The fixed budget case should not use dynamic budget sizing inside
``budget_forcing`` when ``fixed_budget_tokens > 0``. It still uses the budget
control loop because ``enable_budget=true``.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict

import httpx

os.environ["NO_PROXY"] = "localhost,127.0.0.1,10.44.84.60,141.73.2.25,71.26.76.45"

DEFAULT_API_BASE = os.environ.get("API_BASE", "http://75.255.57.109:8000/v1")
DEFAULT_MODEL = os.environ.get("MODEL", "Qwen3.6-27B")

QUESTION = """You are an expert in QA analysis for telecom and IT operations and maintenance. Your task is to analyze and understand [User question] based on [Context data] and provide a simple summary containing an accurate answer.

## [Terminology Description]
NE is short for Net Element.

## [Task Requirements]
1. Use only the content. Do not guess.

2. For statistics data, each line is one aggregated record; use count as quantity. For raw data, each row/object is one record.

3. Extract exact filters from the question. For count/total questions, sum matched count values or count matched raw records. If no filter is specified, use all data.

4. If the answer cannot be determined from the content, say so.

5. **Note: Reduce thinking, output as quickly as possible.**

6. Answer in the same language as the user question.

## Context data
### Raw data schema

### Statistics
The following data was obtained from the statistics of 338 original data points:
- ne_type:com.huawei.itpaas,ne_count:2
- ne_type:com.huawei.sne.sipserver,ne_count:15
- ne_type:inf.lanswitch.ce6881,ne_count:2
- ne_type:inf.network.fcswitch.SNS2224,ne_count:1
- ne_type:com.huawei.sne.smppadapter,ne_count:6
- ne_type:inf.db.oracle,ne_count:1
- ne_type:inf.host.rs221g1,缃戝厓鐘舵€?Online,ne_count:1
- ne_type:com.huawei.sne.diamadapter,ne_count:7
- ne_type:inf.os.linux,缃戝厓鐘舵€?Online,ne_count:12
- ne_type:com.huawei.sne.sdu,ne_count:1
- ne_type:CX_V3,缃戝厓鐘舵€?Online,ne_count:4
- ne_type:inf.firewall.eudemon.1000e,ne_count:1
- ne_type:com.huawei.sne.numberanalyze,ne_count:7
- ne_type:inf.host.ts2280v2,缃戝厓鐘舵€?Online,ne_count:3
- ne_type:HVSAppunit5,ne_count:2
- ne_type:HVSAppunit4,ne_count:2
- ne_type:CBSAppunit1,ne_count:2
- ne_type:com.huawei.sne.redisproxy,ne_count:7
- ne_type:HVSAppunit7,ne_count:2
- ne_type:HVSAppunit6,ne_count:2
- ne_type:com.huawei.itpaas.platformservice.bam,ne_count:1
- ne_type:HVSAppunit1,ne_count:1
- ne_type:com.huawei.sne.publicconfig,ne_count:7
- ne_type:HVSAppunit3,ne_count:2
- ne_type:HVSAppunit2,ne_count:2
- ne_type:HVSModule3,ne_count:2
- ne_type:HVSModule4,ne_count:2
- ne_type:HACS,ne_count:6
- ne_type:com.huawei.sne.managercenterservice,ne_count:7
- ne_type:inf.lanswitch.ce6850,ne_count:1
- ne_type:HVSAppunit9,ne_count:2
- ne_type:HVSAppunit8,ne_count:2
- ne_type:LIUA,缃戝厓鐘舵€?Online,ne_count:1
- ne_type:CH121V5,缃戝厓鐘舵€?Online,ne_count:14
- ne_type:com.huawei.commsvcs.sum.service,ne_count:1
- ne_type:com.huawei.as.subnet.sne,ne_count:1
- ne_type:inf.db.oracle.service,ne_count:1
- ne_type:com.huawei.customizing.ne,缃戝厓鐘舵€?Online,ne_count:2
- ne_type:com.huawei.sne.httpadapter,ne_count:1
- ne_type:inf.db.oracle.singleDual,ne_count:1
- ne_type:XCUA,缃戝厓鐘舵€?Online,ne_count:2
- ne_type:inf.shelf.e9000h2,ne_count:1
- ne_type:com.huawei.sne.smgwadapter,ne_count:1
- ne_type:HVSAppunit11,ne_count:2
- ne_type:com.huawei.sne.rmservice,ne_count:7
- ne_type:HVSAppunit12,ne_count:2
- ne_type:com.huawei.sne.sipdispatcher,ne_count:7
- ne_type:inf.midderware.backup.x6000,ne_count:2
- ne_type:inf.host.2288hv6,缃戝厓鐘舵€?Online,ne_count:1
- ne_type:inf.host.2288hv5,缃戝厓鐘舵€?Online,ne_count:1
- ne_type:HVSAppunit10,ne_count:2
- ne_type:CM221S,缃戝厓鐘舵€?Online,ne_count:1
- ne_type:inf.storage.san.5500v3,ne_count:1
- ne_type:com.huawei.sne.scf,ne_count:15
- ne_type:inf.host.ts260,缃戝厓鐘舵€?Online,ne_count:1
- ne_type:com.huawei.kubernetes,ne_count:1
- ne_type:com.huawei.sne.cdrserver,ne_count:7
- ne_type:com.huawei.sne.dasrouteproxy,ne_count:1
- ne_type:com.huawei.sne.guid,ne_count:7
- ne_type:com.huawei.itpaas.platformservice.HVSServiceunit,ne_count:4
- ne_type:com.huawei.sne.dnsenumadapter,ne_count:6
- ne_type:NGBilling,ne_count:1
- ne_type:com.huawei.IRes.vm,缃戝厓鐘舵€?Online,ne_count:4
- ne_type:inf.midderware.backup.cdm,ne_count:44
- ne_type:inf.db.oracle.servicetype,ne_count:1
- ne_type:com.huawei.sne.olcagent,ne_count:7
- ne_type:com.huawei.sne.soapadapter,ne_count:7
- ne_type:E9000MM921,ne_count:1
- ne_type:inf.midderware.backup.cv,ne_count:20
- ne_type:com.huawei.sne.enipcore,ne_count:7
- ne_type:com.huawei.sne.licenseservice,ne_count:7
- ne_type:com.huawei.i2000.IRes.pm,缃戝厓鐘舵€?Online,ne_count:3
- ne_type:com.huawei.sne.httpplusadapter,ne_count:7
- ne_type:SH610,ne_count:1
- ne_type:inf.storage.san.doradov6,ne_count:1
- ne_type:inf.storage.san.5800v3,ne_count:1
- ne_type:inf.storage.san.doradov3,ne_count:1
- ne_type:com.huawei.commsvcs.sum.integrateagent,ne_count:1
- ne_type:com.huawei.sne.sauadapter,ne_count:7
- ne_type:inf.network.firewall.E1000en,ne_count:1
- ne_type:com.huawei.sne.lock,ne_count:7

[User question]

how many nes with online?"""


def _print_json(title: str, payload: Any) -> None:
    print(f"\n[{title}]\n{json.dumps(payload, ensure_ascii=False, indent=2)}")


def _root_base(api_base: str) -> str:
    return api_base[:-3] if api_base.rstrip("/").endswith("/v1") else api_base.rstrip("/")


def get_json(api_base: str, path: str, timeout: int = 10) -> Dict[str, Any]:
    url = f"{_root_base(api_base)}{path}" if path == "/health" else f"{api_base.rstrip('/')}{path}"
    print(f"\nGET {url}")
    with httpx.Client(timeout=timeout, verify=False) as client:
        response = client.get(url)
        print(f"HTTP {response.status_code}")
        response.raise_for_status()
        data = response.json()
    _print_json("response", data)
    return data


def chat_completions(
    prompt: str,
    *,
    api_base: str = DEFAULT_API_BASE,
    model: str = DEFAULT_MODEL,
    max_tokens: int = 2000,
    enable_budget: bool = True,
    fixed_budget_tokens: int = 0,
    fixed_num_thinking: int = 0,
    fixed_think_stride: int = 0,
    fixed_check_length: int = 0,
    fixed_prob_th: float = 0.0,
    fixed_min_think_tokens: int = 0,
    **kwargs: Any,
) -> Dict[str, Any]:
    request_json = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "enable_budget": enable_budget,
        "logprobs": True,
        "temperature": 0.01,
        "top_logprobs": 5,
        "fixed_budget_tokens": fixed_budget_tokens,
        "fixed_num_thinking": fixed_num_thinking,
        "fixed_think_stride": fixed_think_stride,
        "fixed_check_length": fixed_check_length,
        "fixed_prob_th": fixed_prob_th,
        "fixed_min_think_tokens": fixed_min_think_tokens,
        **kwargs,
    }
    request_json = {key: value for key, value in request_json.items() if value is not None}
    for fixed_key in (
        "fixed_budget_tokens",
        "fixed_num_thinking",
        "fixed_think_stride",
        "fixed_check_length",
        "fixed_prob_th",
        "fixed_min_think_tokens",
    ):
        if request_json.get(fixed_key, 0) <= 0:
            request_json.pop(fixed_key, None)

    url = f"{api_base.rstrip('/')}/chat/completions"
    print(f"\nPOST {url}")
    _print_json("request", request_json)
    with httpx.Client(timeout=300, verify=False) as client:
        response = client.post(url, json=request_json)
        print(f"HTTP {response.status_code}")
        response.raise_for_status()
        data = response.json()
    _print_json("response", data)
    return data


def _message_content(response: Dict[str, Any]) -> str:
    return response.get("choices", [{}])[0].get("message", {}).get("content") or ""


def _assert_non_empty_content(response: Dict[str, Any]) -> None:
    content = _message_content(response)
    if not content:
        raise AssertionError("empty model response content")


def _assert_no_prompt_leak(response: Dict[str, Any]) -> None:
    content = _message_content(response)
    leaked_markers = ["<|im_start|>", "<|im_end|>", "[user]"]
    leaked = [marker for marker in leaked_markers if marker in content]
    if leaked:
        raise AssertionError(f"baseline response leaked internal prompt markers: {leaked}")


def _assert_fixed_overrides(response: Dict[str, Any], expected: Dict[str, Any]) -> None:
    budget_info = response.get("budget_info") or {}
    if budget_info.get("fixed_budget_mode") is not True:
        raise AssertionError(f"expected fixed_budget_mode=true, got {budget_info.get('fixed_budget_mode')!r}")
    actual = budget_info.get("fixed_overrides") or {}
    missing_or_changed = {key: value for key, value in expected.items() if actual.get(key) != value}
    if missing_or_changed:
        raise AssertionError(f"fixed overrides were not echoed correctly: {missing_or_changed}, actual={actual}")


def run_suite(api_base: str) -> None:
    print("=" * 72)
    print(f"API base: {api_base}")
    print(f"Model: {DEFAULT_MODEL}")
    print("=" * 72)

    print("\n[1] health")
    get_json(api_base, "/health")

    print("\n[2] budget mode, dynamic server defaults")
    budget_response = chat_completions(QUESTION, api_base=api_base, enable_budget=True)
    _assert_non_empty_content(budget_response)
    _print_json("budget_info", budget_response.get("budget_info", {}))

    print("\n[3] baseline mode, no budget")
    baseline_response = chat_completions(QUESTION, api_base=api_base, enable_budget=False)
    _assert_non_empty_content(baseline_response)
    _assert_no_prompt_leak(baseline_response)

    print("\n[4] config and allowed override fields")
    get_json(api_base, "/config")
    get_json(api_base, "/overrides")

    print("\n[5] budget mode with full fixed budget overrides")
    fixed_overrides = {
        "fixed_budget_tokens": 4096,
        "fixed_num_thinking": 3,
        "fixed_think_stride": 128,
        "fixed_check_length": 256,
        "fixed_prob_th": 0.75,
        "fixed_min_think_tokens": 128,
    }
    fixed_response = chat_completions(
        QUESTION,
        api_base=api_base,
        enable_budget=True,
        **fixed_overrides,
    )
    _assert_non_empty_content(fixed_response)
    _assert_fixed_overrides(fixed_response, fixed_overrides)
    _print_json("fixed_budget_info", fixed_response.get("budget_info", {}))

    print("\nAll API client tests passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Live test client for Think Budget API server.")
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help="Base URL ending with /v1")
    parser.add_argument("--remote-api", default=os.environ.get("REMOTE_API"), help="Optional second /v1 API base")
    args = parser.parse_args()

    run_suite(args.api_base)
    if args.remote_api:
        run_suite(args.remote_api)


if __name__ == "__main__":
    main()
