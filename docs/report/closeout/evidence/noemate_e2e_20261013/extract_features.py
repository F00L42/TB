"""Extract compact comparison features from pinned Git capture blobs.

No checkout, network, tokenizer/model loading, or capture reconstruction occurs.
The original strings and complete request inputs are hashed without normalization.
"""
from __future__ import annotations

import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
from runtime_normalization import normalize_runtime

DEFAULT_COMMIT = "60f2d7c01faa3268874893dc42f850ad86086c7c"
DEFAULT_DERIVED = "proxy/results/noemate/agent_20260917T123724Z_7bac2ba7/derived/27c30b983f78c49b86f3"
MAIN_PREFIX = "# Agent 身份\n你是核心网运维 Agent，负责理解用户输入，结合上下文和本轮用户偏好，选择合适的 Skill 处理核心网运维相关问题。"
FOLLOWUP_PREFIX = "你是对话后续问题推荐助手。你的唯一任务是根据用户消息提供的本轮对话上下文，生成 3 个可直接点击追问的问题。"
COMPACTION_PREFIX = "You are the conversation-compaction module of a multi-turn telecom network agent runtime."
CONTEXT_PREFIXES = (
    "以下为当前用户的偏好与特征",
    "以下内容来自用户长期记忆",
    "按如下内容开启技能",
)
PROJECT_FIELDS = (
    "request_id", "started_at_utc", "completed_at_utc", "e2e_ms", "ttfb_ms",
    "first_semantic_delta_ms", "reasoning_tokens_local", "completion_tokens_api",
    "content_tokens_local", "tool_tokens_local", "prompt_tokens_api", "finish_reasons",
    "parse_status", "transport_status", "status_code", "exclusions", "scene",
    "request_hash", "comparable_hash", "messages_tools_hash", "system_hash",
    "root_question_hash", "root_question_preview", "root_question_index", "message_count",
    "detail_git_blob", "detail_sha256", "detail_path", "normalized_runtime_hash",
)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def json_hash(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if isinstance(value, str) else None


def business_question(messages):
    for index, message in enumerate(messages):
        content = message.get("content")
        if message.get("role") == "user" and isinstance(content, str) and not content.startswith(CONTEXT_PREFIXES):
            return index, content
    return None, None


def scene_for(messages):
    system = next((m.get("content") for m in messages if m.get("role") == "system"), None)
    if isinstance(system, str):
        if system.startswith(MAIN_PREFIX):
            return "main", system
        if system.startswith(FOLLOWUP_PREFIX):
            return "followup", system
        if system.startswith(COMPACTION_PREFIX):
            return "compaction", system
    return "other", system


class GitBlobs:
    def __init__(self, repo):
        self.process = subprocess.Popen(
            ["git", "cat-file", "--batch"], cwd=repo,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

    def read(self, expression):
        process = self.process
        process.stdin.write(expression.encode("utf-8") + b"\n")
        process.stdin.flush()
        header = process.stdout.readline().decode("ascii").strip().split()
        if len(header) != 3 or header[1] != "blob":
            raise ValueError(f"Missing blob: {expression}: {header}")
        size = int(header[2])
        pieces = []
        remaining = size
        while remaining:
            piece = process.stdout.read(remaining)
            if not piece:
                raise EOFError(expression)
            pieces.append(piece)
            remaining -= len(piece)
        if process.stdout.read(1) != b"\n":
            raise ValueError("Invalid git batch delimiter")
        return b"".join(pieces), header[0]

    def close(self):
        self.process.stdin.close()
        self.process.wait()
        error = self.process.stderr.read().decode("utf-8", errors="replace")
        if self.process.returncode:
            raise RuntimeError(error)


def make_feature(row, detail, blob_sha, bytes_sha):
    body = detail.get("request") or {}
    messages = body.get("messages") or []
    scene, system = scene_for(messages)
    question_index, question = business_question(messages)
    strict = {k: v for k, v in body.items() if k != "enable_think_budget" and not k.startswith("fixed_")}
    comparable = {k: v for k, v in strict.items() if k != "model"}
    messages_tools = {k: body.get(k) for k in ("messages", "tools", "tool_choice")}
    metadata = detail.get("request_metadata") or {}
    headers = {str(k).lower(): v for k, v in metadata.get("headers", [])}
    parameters = {k: v for k, v in body.items() if k not in ("messages", "tools")}
    feature = {k: row.get(k) for k in (
        "request_id", "arrival_seq", "started_at_utc", "completed_at_utc",
        "e2e_ms", "ttfb_ms", "first_semantic_delta_ms", "timings_ms",
        "reasoning_tokens_local", "completion_tokens_api", "content_tokens_local",
        "tool_tokens_local", "prompt_tokens_api", "visible_reasoning_share",
        "finish_reasons", "parse_status", "capture_status", "transport_status",
        "status_code", "exclusions", "model", "tokenizer_ref", "body_modified",
    )}
    feature.update(
        scene=scene,
        request_hash=json_hash(strict),
        comparable_hash=json_hash(comparable),
        messages_tools_hash=json_hash(messages_tools),
        system_hash=text_hash(system),
        root_question_hash=text_hash(question),
        root_question_preview=question[:240] if question is not None else None,
        root_question_index=question_index,
        root_question_stripped_hash=text_hash(question.strip()) if question is not None else None,
        session_id=headers.get("x-nextagent-session-id"),
        run_id=headers.get("x-nextagent-run-id"),
        agent_id=headers.get("x-nextagent-agent-id"),
        agent_request_id=headers.get("x-nextagent-request-id"),
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"), top_p=body.get("top_p"),
        seed=body.get("seed"), stream=body.get("stream"),
        request_parameters=parameters,
        message_count=len(messages), tools_count=len(body.get("tools") or []),
        tool_result_count=sum(m.get("role") == "tool" for m in messages),
        detail_git_blob=blob_sha, detail_sha256=bytes_sha,
        detail_path=row.get("detail_json"), detail_warnings=detail.get("warnings"),
        response_warnings=(detail.get("response") or {}).get("warnings"),
    )
    normalized = dict(comparable)
    normalized["messages"] = [
        {**message, "content": normalize_runtime(message["content"], alias=True)}
        if message.get("role") == "system" and isinstance(message.get("content"), str)
        else message
        for message in messages
    ]
    feature["normalized_runtime_hash"] = json_hash(normalized) if scene == "main" else None
    return feature


def project_feature(feature):
    """Keep all rows and statistical fields; omit repeated runtime metadata."""
    return {field: feature.get(field) for field in PROJECT_FIELDS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--commit", default=DEFAULT_COMMIT)
    parser.add_argument("--derived", default=DEFAULT_DERIVED)
    parser.add_argument("--output", type=Path, required=True,
                        help="New destination ending in .jsonl.gz; original capture is never written")
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    blobs = GitBlobs(args.repo)
    source, source_blob = blobs.read(f"{args.commit}:{args.derived}/requests.jsonl")
    rows = [json.loads(line) for line in source.splitlines()]
    counts = Counter()
    parameters = Counter()
    scenes = Counter()
    detail_digest = hashlib.sha256()
    if not args.output.name.endswith(".jsonl.gz"):
        parser.error("--output must end in .jsonl.gz")
    if args.output.exists():
        parser.error("--output already exists; choose a new path")
    destination = args.output.with_suffix(".partial")
    projected_digest = hashlib.sha256()
    try:
        with destination.open("wb") as raw_output, gzip.GzipFile(
            fileobj=raw_output, mode="wb", filename="", mtime=0
        ) as output:
            for index, row in enumerate(rows, 1):
                detail_path = f"{args.derived}/{row['detail_json']}"
                raw, git_blob = blobs.read(f"{args.commit}:{detail_path}")
                digest = hashlib.sha256(raw).hexdigest()
                detail_digest.update(f"{detail_path}:{digest}\n".encode("utf-8"))
                feature = make_feature(row, json.loads(raw), git_blob, digest)
                projected = (canonical(project_feature(feature)) + "\n").encode("utf-8")
                projected_digest.update(projected)
                output.write(projected)
                counts[feature["scene"]] += 1
                parameters[canonical(feature["request_parameters"])] += 1
                scenes[(feature["scene"], feature["system_hash"])] += 1
                if index % 2000 == 0:
                    print(f"Scanned {index}/{len(rows)} Git blobs", flush=True)
    finally:
        blobs.close()
    destination.replace(args.output)
    manifest = {
        "source_commit": args.commit,
        "original_capture_commit": args.commit,
        "source_derived": args.derived,
        "requests_git_blob": source_blob,
        "requests_sha256": hashlib.sha256(source).hexdigest(),
        "detail_order_sha256": detail_digest.hexdigest(),
        "detail_hash_recipe": "SHA256(path:SHA256(exact Git blob bytes) plus LF in requests.jsonl order)",
        "features_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
        "uncompressed_features_sha256": projected_digest.hexdigest(),
        "projected_fields": list(PROJECT_FIELDS),
        "projection": "All requests preserved; only columns omitted. No model/tokenizer rerun or response text modification.",
        "rows": len(rows), "scene_counts": dict(counts),
        "parameter_counts": [{"parameters": json.loads(k), "count": v} for k, v in parameters.items()],
        "system_counts": [{"scene": k[0], "system_hash": k[1], "count": v} for k, v in scenes.items()],
        "root_question_rules": list(CONTEXT_PREFIXES),
        "hash_recipe": "UTF8 canonical JSON ensure_ascii=False,sort_keys=True,separators=(',',':'); text exact without strip",
        "analysis_scope": "Capture request-level evidence; no server/model execution or quality judge. Hashes with model removed only explore common inputs.",
    }
    manifest_path = args.output.with_name(args.output.name[:-len(".jsonl.gz")] + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"rows": len(rows), "scene_counts": dict(counts), "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
