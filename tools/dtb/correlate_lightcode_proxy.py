#!/usr/bin/env python3
"""Correlate LightCode task/agent sessions with proxy full-log shards."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

try:
    from tools.dtb.extract_tool_call_replay_cases import full_log_files
except ModuleNotFoundError:  # 支持按文档直接执行 tools/dtb/*.py
    from extract_tool_call_replay_cases import full_log_files


BRANCH_RE = re.compile(r"(?m)^\s*-\s*branch:\s*([^\r\n]+?)\s*$")
TOOL_NAME_RE = re.compile(r'"name"\s*:\s*"([^"\\]+)"')
CHINA_STANDARD_TIME = timezone(timedelta(hours=8))


def prompt_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(
        content,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def prompt_sha256(content: Any) -> str:
    return hashlib.sha256(prompt_text(content).encode("utf-8")).hexdigest()


def first_user_prompt(messages: Any) -> str:
    if not isinstance(messages, list):
        return ""
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "user":
            return prompt_text(message.get("content", ""))
    return ""


def branch_from_prompt(prompt: str) -> str:
    match = BRANCH_RE.search(prompt)
    return match.group(1).strip() if match else ""


def epoch_ms_iso(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return datetime.fromtimestamp(
        value / 1000, CHINA_STANDARD_TIME
    ).isoformat()


@dataclass(frozen=True)
class OfficialAgent:
    task_id: str
    case_file: str
    task_status: str
    task_start_time: str
    task_end_time: str
    role: str
    ordinal: int
    title: str
    official_session_id: str
    parent_session_id: str
    invocation_status: str
    prompt_sha256: str
    prompt_preview: str
    started_at: str
    finished_at: str
    branch: str

    @property
    def key(self) -> str:
        identity = self.official_session_id or f"invocation-{self.ordinal}"
        return f"{self.task_id}:{self.role}:{identity}"


@dataclass(frozen=True)
class ProxyRequest:
    proxy_session_id: str
    proxy_turn_index: int | None
    request_id: str
    timestamp: str
    completed_at: str
    root_prompt_sha256: str
    root_prompt_preview: str
    source_file: str
    source_line: int
    metrics: dict[str, Any]
    output_preview: str
    tool_names: tuple[str, ...]


def load_evaluation_results(lightcode_run: Path) -> dict[str, dict[str, Any]]:
    path = lightcode_run / "evaluation_results.json"
    if not path.exists():
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("task_id")): row
        for row in rows
        if isinstance(row, dict) and row.get("task_id")
    }


def _message_span(messages: list[dict[str, Any]]) -> tuple[str, str]:
    timestamps = [
        message.get("timestamp")
        for message in messages
        if isinstance(message, dict)
        and isinstance(message.get("timestamp"), (int, float))
    ]
    if not timestamps:
        return "", ""
    return epoch_ms_iso(min(timestamps)), epoch_ms_iso(max(timestamps))


def load_official_agents(
    lightcode_run: Path,
) -> tuple[list[OfficialAgent], dict[str, dict[str, Any]]]:
    evaluations = load_evaluation_results(lightcode_run)
    agents: list[OfficialAgent] = []
    task_metadata: dict[str, dict[str, Any]] = {}

    task_dirs = {
        path.name: path
        for path in lightcode_run.glob("task_*")
        if path.is_dir()
    }
    for task_id in sorted(set(evaluations) | set(task_dirs)):
        evaluation = evaluations.get(task_id, {})
        task_dir = task_dirs.get(task_id)
        report_files = []
        session_files = []
        if task_dir is not None:
            report_files = sorted(
                str(path.relative_to(lightcode_run).as_posix())
                for path in task_dir.glob("logs/evaluation_report_*.*")
            )
            session_files = sorted(task_dir.glob("logs/sessions/*.json"))
        task_metadata[task_id] = {
            "task_id": task_id,
            "case_file": str(evaluation.get("file") or ""),
            "status": str(evaluation.get("status") or ""),
            "create_time": str(evaluation.get("create_time") or ""),
            "start_time": str(evaluation.get("start_time") or ""),
            "end_time": str(evaluation.get("end_time") or ""),
            "report_files": report_files,
            "session_files": [
                str(path.relative_to(lightcode_run).as_posix())
                for path in session_files
            ],
        }

        for session_file in session_files:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            messages = data.get("messages") or []
            root_prompt = first_user_prompt(messages)
            started_at, finished_at = _message_span(messages)
            official_session_id = str(data.get("sessionId") or "")
            branch = branch_from_prompt(root_prompt)
            agents.append(OfficialAgent(
                task_id=task_id,
                case_file=task_metadata[task_id]["case_file"],
                task_status=task_metadata[task_id]["status"],
                task_start_time=task_metadata[task_id]["start_time"],
                task_end_time=task_metadata[task_id]["end_time"],
                role="main",
                ordinal=0,
                title=str(data.get("title") or "main"),
                official_session_id=official_session_id,
                parent_session_id="",
                invocation_status=task_metadata[task_id]["status"],
                prompt_sha256=prompt_sha256(root_prompt),
                prompt_preview=root_prompt[:240],
                started_at=started_at,
                finished_at=finished_at,
                branch=branch,
            ))

            ordinal = 0
            for message in messages:
                for part in message.get("parts") or []:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") != "tool" or part.get("tool") != "task":
                        continue
                    ordinal += 1
                    state = part.get("state") or {}
                    input_data = state.get("input") or {}
                    metadata = state.get("metadata") or {}
                    timing = state.get("time") or {}
                    child_prompt = str(input_data.get("prompt") or "")
                    agents.append(OfficialAgent(
                        task_id=task_id,
                        case_file=task_metadata[task_id]["case_file"],
                        task_status=task_metadata[task_id]["status"],
                        task_start_time=task_metadata[task_id]["start_time"],
                        task_end_time=task_metadata[task_id]["end_time"],
                        role="subagent",
                        ordinal=ordinal,
                        title=str(
                            input_data.get("description")
                            or state.get("title")
                            or f"subagent-{ordinal}"
                        ),
                        official_session_id=str(metadata.get("sessionId") or ""),
                        parent_session_id=str(
                            metadata.get("parentSessionId") or official_session_id
                        ),
                        invocation_status=str(state.get("status") or ""),
                        prompt_sha256=(
                            prompt_sha256(child_prompt) if child_prompt else ""
                        ),
                        prompt_preview=child_prompt[:240],
                        started_at=epoch_ms_iso(timing.get("start")),
                        finished_at=epoch_ms_iso(timing.get("end")),
                        branch=branch,
                    ))
    return agents, task_metadata


def load_legacy_turns(proxy_run: Path) -> dict[str, dict[str, Any]]:
    turns: dict[str, dict[str, Any]] = {}
    for path in sorted(proxy_run.glob("session_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        session_id = str(data.get("session_id") or "")
        for turn in data.get("turns") or []:
            request_id = str(turn.get("request_id") or "")
            if request_id:
                turns[request_id] = {
                    "proxy_session_id": session_id,
                    "proxy_turn_index": turn.get("turn_index"),
                    "completed_at": turn.get("receive_time"),
                    "metrics": {
                        "e2e_ms": turn.get("e2e_ms"),
                        "prompt_tokens": turn.get("input_prompt_tokens"),
                        "completion_tokens": turn.get("output_completion_tokens"),
                        "reasoning_tokens": turn.get("output_reasoning_tokens"),
                        "tool_tokens": turn.get("output_tool_tokens"),
                        "reasoning_tokens_local": turn.get("reasoning_tokens_local"),
                        "content_tokens_local": turn.get("content_tokens_local"),
                        "has_tool_calls": turn.get("has_tool_calls"),
                        "stop_reason": turn.get("stop_reason"),
                    },
                }
    return turns


def _proxy_full_log_source(proxy_run: Path) -> Path:
    shard_dir = proxy_run / "full_log"
    if shard_dir.is_dir():
        return shard_dir
    legacy = proxy_run / "full_log.jsonl"
    if legacy.is_file():
        return legacy
    raise FileNotFoundError(f"no full log found under {proxy_run}")


def _tool_names(value: Any) -> tuple[str, ...]:
    names = []
    if isinstance(value, list):
        for call in value:
            function = call.get("function") if isinstance(call, dict) else None
            if isinstance(function, dict) and function.get("name"):
                names.append(str(function["name"]))
    elif value:
        names.extend(TOOL_NAME_RE.findall(str(value)))
    return tuple(dict.fromkeys(names))


def load_proxy_requests(proxy_run: Path) -> list[ProxyRequest]:
    legacy_turns = load_legacy_turns(proxy_run)
    source = _proxy_full_log_source(proxy_run)
    requests = []
    for path in full_log_files(source):
        relative_path = str(path.relative_to(proxy_run).as_posix())
        with path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{path}:{line_number}: {exc}") from exc
                request_id = str(record.get("request_id") or "")
                legacy = legacy_turns.get(request_id, {})
                messages = (record.get("request_body") or {}).get("messages") or []
                root_prompt = first_user_prompt(messages)
                correlation = record.get("correlation") or {}
                root_hash = str(
                    correlation.get("root_user_message_sha256")
                    or prompt_sha256(root_prompt)
                )
                proxy_session_id = str(
                    record.get("proxy_session_id")
                    or record.get("session_id")
                    or legacy.get("proxy_session_id")
                    or f"legacy_unfinalized_{root_hash[:12]}"
                )
                response = record.get("response_data") or {}
                content = str(response.get("content") or "")
                reasoning = str(response.get("reasoning") or "")
                preview = (content or reasoning).replace("\r", " ").replace("\n", " ")
                requests.append(ProxyRequest(
                    proxy_session_id=proxy_session_id,
                    proxy_turn_index=(
                        record.get("proxy_turn_index")
                        if record.get("proxy_turn_index") is not None
                        else record.get("turn_index", legacy.get("proxy_turn_index"))
                    ),
                    request_id=request_id,
                    timestamp=str(record.get("timestamp") or ""),
                    completed_at=str(
                        record.get("completed_at")
                        or legacy.get("completed_at")
                        or ""
                    ),
                    root_prompt_sha256=root_hash,
                    root_prompt_preview=str(
                        correlation.get("root_user_message_preview")
                        or root_prompt[:240]
                    ),
                    source_file=relative_path,
                    source_line=line_number,
                    metrics=dict(record.get("metrics") or legacy.get("metrics") or {}),
                    output_preview=preview[:160],
                    tool_names=_tool_names(response.get("tool_calls")),
                ))
    return requests


def _numeric_sum(rows: Iterable[ProxyRequest], field: str) -> float:
    return sum(
        float(row.metrics.get(field) or 0)
        for row in rows
        if isinstance(row.metrics.get(field), (int, float))
    )


def _int_or_float(value: float) -> int | float:
    return int(value) if value.is_integer() else round(value, 3)


def _markdown(value: Any, limit: int = 100) -> str:
    text = str(value or "").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as target:
        for row in rows:
            target.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def correlate(
    proxy_run: Path,
    lightcode_run: Path,
    output_dir: Path,
) -> dict[str, Any]:
    agents, task_metadata = load_official_agents(lightcode_run)
    proxy_requests = load_proxy_requests(proxy_run)
    sessions: dict[str, list[ProxyRequest]] = defaultdict(list)
    for request in proxy_requests:
        sessions[request.proxy_session_id].append(request)
    for requests in sessions.values():
        requests.sort(key=lambda row: (
            row.timestamp,
            row.proxy_turn_index if row.proxy_turn_index is not None else -1,
            row.request_id,
        ))

    agents_by_hash: dict[str, list[OfficialAgent]] = defaultdict(list)
    for agent in agents:
        if agent.prompt_sha256:
            agents_by_hash[agent.prompt_sha256].append(agent)

    request_matches: dict[str, OfficialAgent] = {}
    ambiguous_requests: list[dict[str, Any]] = []
    session_agents: dict[str, dict[str, OfficialAgent]] = defaultdict(dict)
    for request in proxy_requests:
        candidates = agents_by_hash.get(request.root_prompt_sha256, [])
        if len(candidates) == 1:
            agent = candidates[0]
            request_matches[request.request_id] = agent
            session_agents[request.proxy_session_id][agent.key] = agent
        elif len(candidates) > 1:
            ambiguous_requests.append({
                "request_id": request.request_id,
                "proxy_session_id": request.proxy_session_id,
                "candidate_agent_keys": [agent.key for agent in candidates],
            })

    session_tasks: dict[str, str] = {}
    ambiguous_sessions: list[dict[str, Any]] = []
    for session_id, requests in sorted(sessions.items()):
        task_ids = {
            agent.task_id for agent in session_agents.get(session_id, {}).values()
        }
        if len(task_ids) == 1:
            session_tasks[session_id] = next(iter(task_ids))
        elif len(task_ids) > 1:
            ambiguous_sessions.append({
                "proxy_session_id": session_id,
                "candidate_task_ids": sorted(task_ids),
                "anchor_agent_keys": sorted(session_agents[session_id]),
            })

    sessions_by_agent: dict[str, list[str]] = defaultdict(list)
    for session_id, anchored_agents in session_agents.items():
        for agent_key in anchored_agents:
            sessions_by_agent[agent_key].append(session_id)
    for segment_ids in sessions_by_agent.values():
        segment_ids[:] = sorted(
            set(segment_ids), key=lambda item: sessions[item][0].timestamp
        )

    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refusing to overwrite correlation output in {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    intended = [
        output_dir / "SUMMARY.md",
        output_dir / "agent_map.jsonl",
        output_dir / "requests.jsonl",
        output_dir / "unmatched.json",
    ]
    if any(path.exists() for path in intended):
        raise FileExistsError(f"refusing to overwrite correlation output in {output_dir}")
    tasks_dir = output_dir / "tasks"
    tasks_dir.mkdir(exist_ok=True)

    agent_rows = []
    for agent in agents:
        segment_ids = sessions_by_agent.get(agent.key, [])
        requests = [
            request
            for sid in segment_ids
            for request in sessions[sid]
            if request_matches.get(request.request_id) == agent
        ]
        row = asdict(agent)
        row.update({
            "agent_key": agent.key,
            "match_method": (
                "exact_agent_prompt_anchor_in_proxy_segment"
                if segment_ids else ""
            ),
            "proxy_session_ids": segment_ids,
            "proxy_session_count": len(segment_ids),
            "request_count": len(requests),
            "llm_e2e_ms": _int_or_float(_numeric_sum(requests, "e2e_ms")),
            "prompt_tokens": int(_numeric_sum(requests, "prompt_tokens")),
            "completion_tokens": int(_numeric_sum(requests, "completion_tokens")),
        })
        agent_rows.append(row)
    _write_jsonl(output_dir / "agent_map.jsonl", agent_rows)

    request_rows = []
    for request in sorted(proxy_requests, key=lambda row: row.timestamp):
        agent = request_matches.get(request.request_id)
        task_id = agent.task_id if agent else session_tasks.get(
            request.proxy_session_id, ""
        )
        metadata = task_metadata.get(task_id, {})
        task_agents = [item for item in agents if item.task_id == task_id]
        branch = next((item.branch for item in task_agents if item.branch), "")
        row = asdict(request)
        row["tool_names"] = list(request.tool_names)
        row.update({
            "matched": bool(task_id),
            "match_method": (
                "exact_root_user_message_sha256"
                if agent
                else "proxy_segment_task_anchor"
                if task_id else ""
            ),
            "task_id": task_id,
            "case_file": agent.case_file if agent else metadata.get("case_file", ""),
            "branch": agent.branch if agent else branch,
            "agent_role": agent.role if agent else "auxiliary" if task_id else "",
            "agent_title": agent.title if agent else "segment auxiliary" if task_id else "",
            "official_session_id": agent.official_session_id if agent else "",
        })
        request_rows.append(row)
    _write_jsonl(output_dir / "requests.jsonl", request_rows)

    unmatched_proxy = [
        {
            "proxy_session_id": session_id,
            "request_count": len(requests),
            "root_prompt_sha256": requests[0].root_prompt_sha256,
            "root_prompt_preview": requests[0].root_prompt_preview,
        }
        for session_id, requests in sorted(sessions.items())
        if session_id not in session_tasks
    ]
    unmatched_official = [
        asdict(agent) | {"agent_key": agent.key}
        for agent in agents
        if agent.prompt_sha256
        and (agent.role == "main" or bool(agent.official_session_id))
        and agent.key not in sessions_by_agent
    ]
    non_started_subagents = [
        asdict(agent) | {"agent_key": agent.key}
        for agent in agents
        if agent.role == "subagent" and not agent.official_session_id
    ]
    unmatched = {
        "unmatched_proxy_sessions": unmatched_proxy,
        "unmatched_official_agents": unmatched_official,
        "non_started_subagent_invocations": non_started_subagents,
        "ambiguous_requests": ambiguous_requests,
        "ambiguous_proxy_sessions": ambiguous_sessions,
    }
    (output_dir / "unmatched.json").write_text(
        json.dumps(unmatched, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    task_ids = sorted(task_metadata)
    task_summary_rows = []
    for task_id in task_ids:
        task_agents = [agent for agent in agents if agent.task_id == task_id]
        mapped_session_ids = sorted(
            session_id
            for session_id, mapped_task_id in session_tasks.items()
            if mapped_task_id == task_id
        )
        task_requests = [
            request for sid in mapped_session_ids for request in sessions[sid]
        ]
        metadata = task_metadata[task_id]
        branch = next((agent.branch for agent in task_agents if agent.branch), "")
        task_summary_rows.append({
            "task_id": task_id,
            "case_file": metadata["case_file"],
            "branch": branch,
            "status": metadata["status"],
            "official_agent_count": len(task_agents),
            "proxy_session_count": len(mapped_session_ids),
            "request_count": len(task_requests),
            "llm_e2e_ms": _int_or_float(_numeric_sum(task_requests, "e2e_ms")),
            "completion_tokens": int(_numeric_sum(task_requests, "completion_tokens")),
            "report_count": len(metadata["report_files"]),
        })

        lines = [
            f"# {task_id}",
            "",
            "## 评测身份",
            "",
            f"- 用例：`{metadata['case_file'] or '-'}`",
            f"- Branch：`{branch or '-'}`",
            f"- 状态：`{metadata['status'] or '-'}`",
            f"- 平台时间：`{metadata['start_time'] or '-'} → {metadata['end_time'] or '-'}`",
            f"- 报告：{', '.join(f'`{path}`' for path in metadata['report_files']) or '-'}",
            "",
            "## 生命周期",
            "",
            "| Stage | Actor | Start | End | Status | Evidence |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        main_agents = [agent for agent in task_agents if agent.role == "main"]
        for agent in main_agents:
            lines.append(
                "| main orchestration | main | {start} | {end} | {status} | `{session}` |".format(
                    start=_markdown(agent.started_at, 32) or "-",
                    end=_markdown(agent.finished_at, 32) or "-",
                    status=_markdown(agent.invocation_status, 20) or "-",
                    session=agent.official_session_id or "-",
                )
            )
        for agent in sorted(
            (item for item in task_agents if item.role == "subagent"),
            key=lambda item: (item.started_at, item.ordinal),
        ):
            lines.append(
                "| subagent #{ordinal}: {title} | subagent | {start} | {end} | {status} | `{session}` |".format(
                    ordinal=agent.ordinal,
                    title=_markdown(agent.title, 52),
                    start=_markdown(agent.started_at, 32) or "-",
                    end=_markdown(agent.finished_at, 32) or "-",
                    status=_markdown(agent.invocation_status, 20) or "-",
                    session=agent.official_session_id or "not-started",
                )
            )
        lines.append(
            "| report/result handoff | main/platform | {time} | {time} | {status} | {reports} |".format(
                time=_markdown(metadata["end_time"], 32) or "-",
                status=_markdown(metadata["status"], 20) or "-",
                reports=", ".join(
                    f"`{path}`" for path in metadata["report_files"]
                ) or "no report artifact",
            )
        )
        lines.extend([
            "",
            "session epoch 时间已转换为 UTC+08:00；task API 时间保留原字符串。生命周期包含工具和环境耗时；下方 LLM E2E 来自 proxy，二者不能相加或互相替代。",
            "",
            "## Agent 拓扑与覆盖",
            "",
            "| Role | Title | LightCode session | Invocation | Proxy segments | Requests | LLM E2E ms | Completion tokens |",
            "| --- | --- | --- | --- | --- | ---: | ---: | ---: |",
        ])
        for agent in task_agents:
            ids = sessions_by_agent.get(agent.key, [])
            agent_requests = [
                request
                for sid in ids
                for request in sessions[sid]
                if request_matches.get(request.request_id) == agent
            ]
            lines.append(
                "| {role} | {title} | `{official}` | {status} | {segments} | {requests} | {e2e} | {completion} |".format(
                    role=agent.role,
                    title=_markdown(agent.title, 60),
                    official=agent.official_session_id or "-",
                    status=_markdown(agent.invocation_status, 30) or "-",
                    segments=", ".join(f"`{value}`" for value in ids) or "-",
                    requests=len(agent_requests),
                    e2e=_int_or_float(_numeric_sum(agent_requests, "e2e_ms")),
                    completion=int(_numeric_sum(agent_requests, "completion_tokens")),
                )
            )
        auxiliary_requests = [
            request
            for request in task_requests
            if request.request_id not in request_matches
        ]
        if auxiliary_requests:
            auxiliary_segments = sorted({
                request.proxy_session_id for request in auxiliary_requests
            })
            lines.append(
                "| auxiliary | segment auxiliary | `-` | inferred from task-anchored segment | {segments} | {requests} | {e2e} | {completion} |".format(
                    segments=", ".join(
                        f"`{value}`" for value in auxiliary_segments
                    ),
                    requests=len(auxiliary_requests),
                    e2e=_int_or_float(
                        _numeric_sum(auxiliary_requests, "e2e_ms")
                    ),
                    completion=int(
                        _numeric_sum(auxiliary_requests, "completion_tokens")
                    ),
                )
            )
        lines.extend([
            "",
            "## LLM 请求时间线",
            "",
            "| Time | Agent | Proxy session / turn | E2E ms | Prompt | Completion | Stop | Tools | Output preview | Source |",
            "| --- | --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |",
        ])
        for request in sorted(task_requests, key=lambda row: row.timestamp):
            agent = request_matches.get(request.request_id)
            agent_label = (
                f"{agent.role}:{agent.title}" if agent else "auxiliary"
            )
            lines.append(
                "| {time} | {agent} | `{session}/{turn}` | {e2e} | {prompt} | {completion} | {stop} | {tools} | {preview} | `{source}:{line}` |".format(
                    time=_markdown(request.timestamp, 32),
                    agent=_markdown(agent_label, 45),
                    session=request.proxy_session_id,
                    turn=request.proxy_turn_index if request.proxy_turn_index is not None else "-",
                    e2e=request.metrics.get("e2e_ms", "-"),
                    prompt=request.metrics.get("prompt_tokens", "-"),
                    completion=request.metrics.get("completion_tokens", "-"),
                    stop=_markdown(request.metrics.get("stop_reason"), 24) or "-",
                    tools=_markdown(", ".join(request.tool_names), 40) or "-",
                    preview=_markdown(request.output_preview, 100) or "-",
                    source=request.source_file,
                    line=request.source_line,
                )
            )
        (tasks_dir / f"{task_id}.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )

    mapped_request_count = sum(
        len(sessions[session_id]) for session_id in session_tasks
    )
    runnable_agents = [
        agent
        for agent in agents
        if agent.prompt_sha256
        and (agent.role == "main" or bool(agent.official_session_id))
    ]
    matched_agent_count = sum(
        1 for agent in runnable_agents if agent.key in sessions_by_agent
    )
    summary_lines = [
        "# LightCode ↔ proxy 关联报告",
        "",
        "## 结论",
        "",
        f"使用每个请求的首个 user prompt 精确 SHA-256 作为 agent 锚点，再把同一 segment 内的辅助请求归到唯一 task；未修改评测 prompt。已映射 {len(session_tasks)}/{len(sessions)} 个 proxy session、{mapped_request_count}/{len(proxy_requests)} 个请求，以及 {matched_agent_count}/{len(runnable_agents)} 个实际启动的 LightCode agent session。",
        "",
        "一个 LightCode agent session 可以对应多个 proxy session segment，同一 segment 也可能包含同一 task 的主 agent、子 agent 和 title generation 等辅助请求。agent 身份只赋给精确 prompt 命中的请求；其余请求仅在 segment 内所有 agent 锚点都属于同一 task 时标为 auxiliary。segment 命中多个 task 时判为歧义。没有官方子 session ID 的 task 工具记录视为未启动调用，单列但不计入关联分母。",
        "",
        "## 完整性",
        "",
        f"- 未映射 proxy session：{len(unmatched_proxy)}",
        f"- 未映射已启动 LightCode agent：{len(unmatched_official)}",
        f"- 未启动 subagent 调用：{len(non_started_subagents)}",
        f"- prompt 同时命中多个官方 agent 的请求：{len(ambiguous_requests)}",
        f"- 歧义 proxy session：{len(ambiguous_sessions)}",
        "",
        "## 逐任务",
        "",
        "| Task | Case | Branch | Status | Agents | Proxy segments | Requests | LLM E2E ms | Completion tokens | Reports |",
        "| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in task_summary_rows:
        summary_lines.append(
            "| [{task}](tasks/{task}.md) | {case} | `{branch}` | {status} | {agents} | {segments} | {requests} | {e2e} | {completion} | {reports} |".format(
                task=row["task_id"],
                case=_markdown(row["case_file"], 55) or "-",
                branch=_markdown(row["branch"], 45) or "-",
                status=_markdown(row["status"], 20) or "-",
                agents=row["official_agent_count"],
                segments=row["proxy_session_count"],
                requests=row["request_count"],
                e2e=row["llm_e2e_ms"],
                completion=row["completion_tokens"],
                reports=row["report_count"],
            )
        )
    summary_lines.extend([
        "",
        "## 机器可读产物",
        "",
        "- `agent_map.jsonl`：LightCode 主/子 agent 到 proxy segment 的映射和聚合。",
        "- `requests.jsonl`：逐请求指标、任务身份及原始 shard 行号。",
        "- `unmatched.json`：所有未映射、歧义、未启动和一致性异常。",
        "",
    ])
    (output_dir / "SUMMARY.md").write_text(
        "\n".join(summary_lines), encoding="utf-8"
    )

    return {
        "task_count": len(task_ids),
        "official_agent_count": len(agents),
        "runnable_agent_count": len(runnable_agents),
        "matched_agent_count": matched_agent_count,
        "proxy_session_count": len(sessions),
        "matched_proxy_session_count": len(session_tasks),
        "proxy_request_count": len(proxy_requests),
        "matched_proxy_request_count": mapped_request_count,
        "unmatched_proxy_session_count": len(unmatched_proxy),
        "ambiguous_request_count": len(ambiguous_requests),
        "ambiguous_proxy_session_count": len(ambiguous_sessions),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proxy_run", type=Path)
    parser.add_argument("lightcode_run", type=Path)
    parser.add_argument("output_dir", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = correlate(args.proxy_run, args.lightcode_run, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
