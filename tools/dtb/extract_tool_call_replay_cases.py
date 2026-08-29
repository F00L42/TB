#!/usr/bin/env python3
"""Extract wire-exact tool-call cases from legacy or sharded proxy full logs."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


LEAKED_XML_RE = re.compile(
    r"(?:l_call>|</?tool_call>|</?function(?:=|>)|</?parameter(?:=|>))"
    r"|(?:^|\n)function=[^\n>]+>",
    re.MULTILINE,
)
BRANCH_RE = re.compile(r"(?m)^\s*-\s*branch:\s*([^\r\n]+?)\s*$")
FUNCTION_RE = re.compile(r"<function=([^>\r\n]+)>")


@dataclass(frozen=True)
class Candidate:
    source_file: Path
    source_line: int
    timestamp: str
    request_id: str
    branch: str
    record: dict[str, Any]
    context_clean: bool
    leaked_functions: tuple[str, ...]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def full_log_files(path: Path) -> list[Path]:
    """Resolve one legacy file or a new full_log shard directory."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(path)

    root = path / "full_log" if (path / "full_log").is_dir() else path
    if root.name.startswith(("session_", "proxy_session_")):
        files = sorted(root.glob("part-*.jsonl"))
    else:
        files = sorted({
            *root.glob("proxy_session_*/part-*.jsonl"),
            *root.glob("session_*/part-*.jsonl"),
        })
    if not files:
        raise ValueError(f"no full-log shard files found under {root}")
    return files


def full_log_sha256(path: Path, files: list[Path]) -> str:
    """Hash file names and bytes so a shard collection has one stable digest."""
    if path.is_file():
        return sha256_file(path)

    root = path / "full_log" if (path / "full_log").is_dir() else path
    digest = hashlib.sha256()
    for file_path in files:
        relative = file_path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with file_path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def json_hash(value: Any) -> str:
    data = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def infer_branch(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        match = BRANCH_RE.search(message_text(message.get("content")))
        if match:
            return match.group(1).strip()
    return ""


def assistant_context_has_leak(messages: Any) -> bool:
    if not isinstance(messages, list):
        return False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        text = message_text(message.get("content"))
        reasoning = str(
            message.get("reasoning")
            or message.get("reasoning_content")
            or ""
        )
        if LEAKED_XML_RE.search(text) or LEAKED_XML_RE.search(reasoning):
            return True
    return False


def read_candidates(path: Path, branches: set[str]) -> list[Candidate]:
    candidates: list[Candidate] = []
    for source_file in full_log_files(path):
        with source_file.open("r", encoding="utf-8") as source:
            for line_no, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{source_file}:{line_no}: {exc}") from exc
                body = record.get("request_body")
                response = record.get("response_data")
                if not isinstance(body, dict) or not isinstance(response, dict):
                    continue
                content = str(response.get("content") or "")
                if not LEAKED_XML_RE.search(content):
                    continue
                branch = infer_branch(body.get("messages"))
                if branches and branch not in branches:
                    continue
                functions = tuple(dict.fromkeys(FUNCTION_RE.findall(content)))
                candidates.append(Candidate(
                    source_file=source_file,
                    source_line=line_no,
                    timestamp=str(record.get("timestamp") or ""),
                    request_id=str(record.get("request_id") or ""),
                    branch=branch,
                    record=record,
                    context_clean=not assistant_context_has_leak(body.get("messages")),
                    leaked_functions=functions,
                ))
    return candidates


def select_first_clean(
    candidates: Iterable[Candidate], branches: list[str]
) -> list[Candidate]:
    grouped: dict[str, list[Candidate]] = {branch: [] for branch in branches}
    for candidate in candidates:
        if candidate.branch in grouped:
            grouped[candidate.branch].append(candidate)
    selected: list[Candidate] = []
    for branch in branches:
        clean = [candidate for candidate in grouped[branch] if candidate.context_clean]
        if not clean:
            raise ValueError(
                f"branch {branch!r} has no leaked response with clean assistant context"
            )
        clean.sort(key=lambda item: (
            item.timestamp, item.source_file.as_posix(), item.source_line,
        ))
        selected.append(clean[0])
    return selected


def tool_call_names(response: dict[str, Any]) -> list[str]:
    value = response.get("tool_calls")
    if isinstance(value, str):
        return sorted(set(re.findall(r'"name"\s*:\s*"([^"\\]+)"', value)))
    if not isinstance(value, list):
        return []
    names = []
    for call in value:
        if not isinstance(call, dict):
            continue
        function = call.get("function") or {}
        name = function.get("name") if isinstance(function, dict) else None
        if name:
            names.append(str(name))
    return names


def load_proxy_turn_metrics(run_dir: Path) -> dict[str, dict[str, Any]]:
    metrics: dict[str, dict[str, Any]] = {}
    for path in sorted(run_dir.glob("session_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        session_id = str(data.get("session_id") or "")
        for turn in data.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            request_id = str(turn.get("request_id") or "")
            if not request_id:
                continue
            metrics[request_id] = {
                "session_id": session_id,
                "session_file": path.name,
                "turn_index": turn.get("turn_index"),
                "e2e_ms": turn.get("e2e_ms"),
                "prompt_tokens": turn.get("input_prompt_tokens"),
                "completion_tokens": turn.get("output_completion_tokens"),
                "reasoning_tokens_local": turn.get("reasoning_tokens_local"),
                "content_tokens_local": turn.get("content_tokens_local"),
                "has_tool_calls": turn.get("has_tool_calls"),
                "stop_reason": turn.get("stop_reason"),
            }
    return metrics


def proxy_run_dir(source: Path) -> Path:
    if source.is_file():
        return source.parent
    if (source / "full_log").is_dir():
        return source
    if source.name == "full_log":
        return source.parent
    if source.name.startswith(("session_", "proxy_session_")) and source.parent.name == "full_log":
        return source.parent.parent
    return source.parent


def display_source_file(source: Path, source_file: Path) -> str:
    if source.is_file():
        return source_file.name
    root = source / "full_log" if (source / "full_log").is_dir() else source
    try:
        return source_file.relative_to(root).as_posix()
    except ValueError:
        return str(source_file.resolve())


def write_outputs(
    *,
    source: Path,
    output_dir: Path,
    branches: list[str],
    candidates: list[Candidate],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cases_path = output_dir / "cases.jsonl"
    manifest_path = output_dir / "extraction_manifest.json"
    result_path = output_dir / "RESULT.md"
    for path in (cases_path, manifest_path, result_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite {path}")

    source_files = full_log_files(source)
    turn_metrics = load_proxy_turn_metrics(proxy_run_dir(source))
    case_rows: list[dict[str, Any]] = []
    for index, candidate in enumerate(candidates, start=1):
        row = dict(candidate.record)
        body = row["request_body"]
        response = row["response_data"]
        row["case_id"] = f"run02-tool-boundary-{index:02d}"
        row["source"] = {
            "full_log": str(source.resolve()),
            "source_file": display_source_file(source, candidate.source_file),
            "source_line": candidate.source_line,
            "branch": candidate.branch,
            "original_request_id": candidate.request_id,
            "request_body_sha256": json_hash(body),
            "context_clean": candidate.context_clean,
            "leaked_functions": list(candidate.leaked_functions),
            "structured_tool_names": tool_call_names(response),
            "message_count": len(body.get("messages") or []),
            "original_proxy_turn": turn_metrics.get(candidate.request_id),
        }
        case_rows.append(row)

    with cases_path.open("w", encoding="utf-8") as target:
        for row in case_rows:
            target.write(json.dumps(row, ensure_ascii=False) + "\n")

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "experiment_role": "tool_call_replay_input_extraction",
        "selection": "earliest leaked response per branch with clean assistant context",
        "wire_exact_request_body": True,
        "wire_exact_headers": False,
        "source_full_log": str(source.resolve()),
        "source_full_log_sha256": full_log_sha256(source, source_files),
        "source_full_log_files": [
            {
                "path": display_source_file(source, path),
                "sha256": sha256_file(path),
            }
            for path in source_files
        ],
        "branches": branches,
        "case_count": len(case_rows),
        "cases_file": cases_path.name,
        "cases_sha256": sha256_file(cases_path),
        "cases": [row["source"] | {"case_id": row["case_id"]} for row in case_rows],
        "limitations": [
            "The proxy archive preserves the exact injected JSON request body but not all HTTP headers.",
            "Replay sampling and fixed DTB overrides are controlled by replay_tool_calls.py and must be recorded in its manifest.",
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# run-02 tool-call 流式回放输入抽取",
        "",
        "## 结论",
        "",
        f"从 full-log 归档抽取 {len(case_rows)} 个 request-body 精确样本。每个样本都是对应分支第一次出现工具 XML 污染、且请求历史中的 assistant 消息尚未出现同类污染的轮次。",
        "",
        "HTTP headers 未包含在代理归档中；回放脚本会为每个请求生成确定性的 `X-Request-Id`。",
        "",
        "## 样本",
        "",
        "| Case | Branch | Source part | Line | Messages | Original E2E ms | Prompt tokens | Completion tokens | Reasoning tokens | Content tokens | Leaked functions | Structured tools |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]
    for row in case_rows:
        source_info = row["source"]
        lines.append(
            "| {case} | `{branch}` | `{source_file}` | {line} | {messages} | {e2e} | {prompt} | {completion} | {reasoning} | {content} | `{leaked}` | `{tools}` |".format(
                case=row["case_id"],
                branch=source_info["branch"],
                source_file=source_info["source_file"],
                line=source_info["source_line"],
                messages=source_info["message_count"],
                e2e=(source_info["original_proxy_turn"] or {}).get("e2e_ms", "-"),
                prompt=(source_info["original_proxy_turn"] or {}).get("prompt_tokens", "-"),
                completion=(source_info["original_proxy_turn"] or {}).get("completion_tokens", "-"),
                reasoning=(source_info["original_proxy_turn"] or {}).get("reasoning_tokens_local", "-"),
                content=(source_info["original_proxy_turn"] or {}).get("content_tokens_local", "-"),
                leaked=", ".join(source_info["leaked_functions"]) or "-",
                tools=", ".join(source_info["structured_tool_names"]) or "-",
            )
        )
    lines.extend([
        "",
        "## 使用",
        "",
        "```bash",
        "python tools/dtb/replay_tool_calls.py --file "
        f"{cases_path.as_posix()} --mode both --repetitions 10 "
        "--request-id-run agent-run02 --fixed-budget-tokens 20000 "
        "--fixed-check-length 400 --fixed-think-stride 500 "
        "--fixed-num-thinking 4 --fixed-min-think-tokens 1 "
        "--fixed-prob-th 0.93",
        "```",
        "",
    ])
    result_path.write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "full_log",
        type=Path,
        help="legacy full_log.jsonl, new full_log/ shard directory, or run directory",
    )
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--branch",
        action="append",
        dest="branches",
        required=True,
        help="exact LightCode branch identifier; repeat for multiple cases",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    branches = list(dict.fromkeys(args.branches))
    candidates = read_candidates(args.full_log, set(branches))
    selected = select_first_clean(candidates, branches)
    write_outputs(
        source=args.full_log,
        output_dir=args.output_dir,
        branches=branches,
        candidates=selected,
    )
    print(f"extracted {len(selected)} cases to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
