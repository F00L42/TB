#!/usr/bin/env python3
"""Build an auditable paired-replay corpus from correlated agent runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_hash(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(payload)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as source:
        for line_no, line in enumerate(source, start=1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_no} is not a JSON object")
            rows.append(value)
    return rows


def request_index(correlation_dir: Path) -> dict[str, dict[str, Any]]:
    path = correlation_dir / "requests.jsonl"
    rows = read_jsonl(path)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        request_id = str(row.get("request_id") or "")
        if not request_id:
            raise ValueError(f"{path} contains a row without request_id")
        if request_id in result:
            raise ValueError(f"duplicate request_id {request_id!r} in {path}")
        if not row.get("matched") or not row.get("task_id"):
            continue
        result[request_id] = row
    return result


def load_source_record(
    run_dir: Path,
    index_row: dict[str, Any],
    line_cache: dict[Path, list[str]],
) -> dict[str, Any]:
    relative = Path(str(index_row["source_file"]))
    source_path = run_dir / relative
    if source_path not in line_cache:
        line_cache[source_path] = source_path.read_text(encoding="utf-8").splitlines()
    source_line = int(index_row["source_line"])
    lines = line_cache[source_path]
    if source_line < 1 or source_line > len(lines):
        raise ValueError(f"invalid source line {source_path}:{source_line}")
    record = json.loads(lines[source_line - 1])
    if str(record.get("request_id") or "") != str(index_row["request_id"]):
        raise ValueError(
            f"request ID mismatch at {source_path}:{source_line}: "
            f"{record.get('request_id')!r} != {index_row['request_id']!r}"
        )
    if not isinstance(record.get("request_body"), (dict, str)):
        raise ValueError(f"no request_body at {source_path}:{source_line}")
    return record


def archived_tool_names(index_row: dict[str, Any]) -> list[str]:
    names = index_row.get("tool_names") or []
    if not isinstance(names, list):
        return []
    return sorted({str(name) for name in names if name})


def build_case(
    *,
    pair: dict[str, Any],
    origin: str,
    run_dir: Path,
    index: dict[str, dict[str, Any]],
    line_cache: dict[Path, list[str]],
) -> dict[str, Any]:
    request_key = f"{origin}_request_id"
    request_id = str(pair.get(request_key) or "")
    if request_id not in index:
        raise ValueError(
            f"pair {pair.get('pair_id')!r}: {request_key}={request_id!r} "
            "is absent from the matched correlation ledger"
        )
    index_row = index[request_id]
    record = load_source_record(run_dir, index_row, line_cache)
    expected_by_origin = pair.get("expected_tool_names_by_origin") or {}
    explicit_names = expected_by_origin.get(origin)
    if explicit_names is None:
        explicit_names = pair.get("expected_tool_names")
    expected_names = (
        [str(name) for name in explicit_names]
        if isinstance(explicit_names, list)
        else archived_tool_names(index_row)
    )
    body = record["request_body"]
    if isinstance(body, str):
        body = json.loads(body)
    case_id = f"{pair['pair_id']}--{origin}-context"
    return {
        **record,
        "case_id": case_id,
        "semantic_pair_id": pair["pair_id"],
        "context_origin": origin,
        "source_evidence": {
            "run_dir": str(run_dir.as_posix()),
            "request_id": request_id,
            "task_id": index_row.get("task_id"),
            "case_file": index_row.get("case_file"),
            "agent_role": index_row.get("agent_role"),
            "agent_title": index_row.get("agent_title"),
            "official_session_id": index_row.get("official_session_id"),
            "source_file": index_row.get("source_file"),
            "source_line": index_row.get("source_line"),
            "archived_tool_names": archived_tool_names(index_row),
            "request_body_sha256": json_hash(body),
        },
        "replay_expectation": {
            "category": pair["category"],
            "expected_tool_call": bool(pair.get("expected_tool_call", True)),
            "expected_tool_names": expected_names,
            "latency_eligible": bool(pair.get("latency_eligible", True)),
            "quality_eligible": bool(pair.get("quality_eligible", False)),
            "rationale": str(pair.get("rationale") or ""),
        },
    }


def validate_pair(pair: dict[str, Any]) -> None:
    required = ("pair_id", "category", "dtb_request_id", "vanilla_request_id")
    missing = [name for name in required if not pair.get(name)]
    if missing:
        raise ValueError(f"replay pair is missing {missing}: {pair!r}")


def write_result(path: Path, cases: list[dict[str, Any]]) -> None:
    lines = [
        "# Agent failure paired-replay input",
        "",
        "每个历史上下文各自使用完全相同的 request body 执行 Vanilla/DTB A/B。",
        "`semantic_pair_id` 只表示 DTB 故障上下文与 Vanilla 健康上下文属于同一语义阶段；",
        "两份不同历史 body 不直接互算时延。",
        "",
        "| Pair | Origin | Category | Case | Expected tools | Archived request |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in cases:
        evidence = case["source_evidence"]
        expectation = case["replay_expectation"]
        lines.append(
            f"| {case['semantic_pair_id']} | {case['context_origin']} | "
            f"{expectation['category']} | {evidence['case_file']} | "
            f"{','.join(expectation['expected_tool_names'])} | "
            f"`{evidence['request_id']}` |"
        )
    lines.extend([
        "",
        "## 使用边界",
        "",
        "- `protocol_failure` 用于系统可用性门禁，可做协议通过率和协议通过后的 paired latency。",
        "- `quality_probe` 只回放生成代码的 mutation turn；它能保留真实上下文并采集工具参数，",
        "  但不会执行工具或编译代码，不能替代完整 LightCode 质量评测。",
        "- 所有 latency 只在同一行、同一 request body 的 Vanilla/DTB 之间配对。",
        "",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtb-run", type=Path, required=True)
    parser.add_argument("--dtb-correlation", type=Path, required=True)
    parser.add_argument("--vanilla-run", type=Path, required=True)
    parser.add_argument("--vanilla-correlation", type=Path, required=True)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output_dir.exists():
        raise FileExistsError(f"refusing to overwrite {args.output_dir}")
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    pairs = spec.get("pairs") if isinstance(spec, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("spec must contain a non-empty pairs list")
    for pair in pairs:
        if not isinstance(pair, dict):
            raise ValueError("every replay pair must be an object")
        validate_pair(pair)

    dtb_index = request_index(args.dtb_correlation)
    vanilla_index = request_index(args.vanilla_correlation)
    cache: dict[Path, list[str]] = {}
    cases: list[dict[str, Any]] = []
    for pair in pairs:
        dtb_case = build_case(
            pair=pair,
            origin="dtb",
            run_dir=args.dtb_run,
            index=dtb_index,
            line_cache=cache,
        )
        vanilla_case = build_case(
            pair=pair,
            origin="vanilla",
            run_dir=args.vanilla_run,
            index=vanilla_index,
            line_cache=cache,
        )
        if (
            dtb_case["source_evidence"]["case_file"]
            != vanilla_case["source_evidence"]["case_file"]
        ):
            raise ValueError(
                f"pair {pair['pair_id']!r} crosses LightCode cases: "
                f"{dtb_case['source_evidence']['case_file']!r} vs "
                f"{vanilla_case['source_evidence']['case_file']!r}"
            )
        cases.extend((dtb_case, vanilla_case))

    args.output_dir.mkdir(parents=True, exist_ok=False)
    cases_path = args.output_dir / "cases.jsonl"
    cases_path.write_text(
        "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases),
        encoding="utf-8",
    )
    manifest = {
        "schema": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "spec": str(args.spec.as_posix()),
        "spec_sha256": sha256_bytes(args.spec.read_bytes()),
        "dtb_run": str(args.dtb_run.as_posix()),
        "dtb_correlation": str(args.dtb_correlation.as_posix()),
        "vanilla_run": str(args.vanilla_run.as_posix()),
        "vanilla_correlation": str(args.vanilla_correlation.as_posix()),
        "semantic_pair_count": len(pairs),
        "replay_case_count": len(cases),
        "cases_sha256": sha256_bytes(cases_path.read_bytes()),
        "pairing_rule": (
            "A/B only within one exact request body; semantic pairs from "
            "different historical origins are controls, not latency pairs"
        ),
    }
    (args.output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_result(args.output_dir / "RESULT.md", cases)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
