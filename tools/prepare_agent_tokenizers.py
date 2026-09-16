"""Download pinned, weight-free tokenizer snapshots and audit their contracts."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import platform
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "docs" / "tokenizer"
MODELS = {
    "Qwen3.6-27B": ("Qwen/Qwen3.6-27B", "6a9e13bd6fc8f0983b9b99948120bc37f49c13e9"),
    "GLM-5.3": ("zai-org/GLM-5.3", "aca966e4e02791568aa6a4ced368624b3d897f42"),
}
# tokenizer.json contains the vocabulary and merges. No model config or weights.
FILES = ("tokenizer_config.json", "tokenizer.json", "chat_template.jinja")
MARKERS = (
    "<think>", "</think>", "<tool_call>", "</tool_call>",
    "<arg_key>", "</arg_key>", "<arg_value>", "</arg_value>",
    "<function=", "</function>", "<parameter=", "</parameter>",
    "<tool_response>", "</tool_response>", "/nothink", "<eop>",
    "<|im_start|>", "<|im_end|>", "<|endoftext|>",
    "[gMASK]", "<sop>", "<|system|>", "<|user|>", "<|assistant|>",
    "<|observation|>", "<|fim_suffix|>",
    "<|tool_response|>", "<|end_of_text|>", "<|eot_id|>",
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(root: Path) -> None:
    for name, (repo, revision) in MODELS.items():
        directory = root / name
        directory.mkdir(parents=True, exist_ok=True)
        records = []
        for filename in FILES:
            path = directory / filename
            url = f"https://huggingface.co/{repo}/resolve/{revision}/{filename}"
            print(f"Downloading {name}/{filename}", flush=True)
            # Fixed allowlist; never use a full snapshot/model download.
            with urllib.request.urlopen(url, timeout=120) as response:
                data = response.read(100_000_001)
            if len(data) > 100_000_000:
                raise ValueError(f"Unexpectedly large tokenizer artifact: {filename}")
            path.write_bytes(data)
            records.append({"file": filename, "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(), "url": url})
        write_json(directory / "manifest.json", {
            "repo_id": repo, "revision": revision,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "files": records, "weights_downloaded": False,
        })


def audit(root: Path) -> None:
    from transformers import AutoTokenizer

    report = {"python": platform.python_version(), "packages": {
        name: importlib.metadata.version(name)
        for name in ("transformers", "tokenizers", "huggingface-hub", "jinja2")
    }, "models": {}}
    tools = [{"type": "function", "function": {"name": "lookup", "description": "Lookup a key",
             "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}}]
    messages = [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮结论", "reasoning_content": "历史推理样本。"},
        {"role": "user", "content": "查询服务状态"},
        {"role": "assistant", "content": "", "reasoning_content": "先查询实际状态。",
         "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": {"key": "status"}}}]},
        {"role": "tool", "tool_call_id": "call_1", "name": "lookup", "content": "running"},
    ]
    for name in MODELS:
        directory = root / name
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        for item in manifest["files"]:
            assert hashlib.sha256((directory / item["file"]).read_bytes()).hexdigest() == item["sha256"]
        tokenizer = AutoTokenizer.from_pretrained(directory, local_files_only=True, trust_remote_code=False)
        vocab = tokenizer.get_vocab()
        raw = json.loads((directory / "tokenizer.json").read_text(encoding="utf-8"))
        added = {item["content"]: item for item in raw.get("added_tokens", [])}
        backend_added = tokenizer.backend_tokenizer.get_added_tokens_decoder()
        probes = {}
        for marker in dict.fromkeys(MARKERS):
            ids = tokenizer.encode(marker, add_special_tokens=False)
            probes[marker] = {"vocab_id": vocab.get(marker), "encoded_ids": ids,
                              "singleton": len(ids) == 1,
                              "listed_special": len(ids) == 1 and ids[0] in tokenizer.all_special_ids,
                              "artifact_added_special": added.get(marker, {}).get("special"),
                              "backend_special": (backend_added[ids[0]].special
                                                  if len(ids) == 1 and ids[0] in backend_added else None),
                              "decode_keep": tokenizer.decode(ids, skip_special_tokens=False),
                              "decode_skip": tokenizer.decode(ids, skip_special_tokens=True)}
        templates = {}
        for label, kwargs in (("default", {}), ("thinking", {"enable_thinking": True}),
                              ("no_thinking", {"enable_thinking": False}),
                              ("preserve", {"preserve_thinking": True}),
                              ("clear", {"clear_thinking": True}),
                              ("clear_false", {"clear_thinking": False}),
                              ("effort_low", {"reasoning_effort": "low"}),
                              ("effort_high", {"reasoning_effort": "high"}),
                              ("effort_medium", {"reasoning_effort": "medium"})):
            rendered = tokenizer.apply_chat_template(messages, tools=tools, tokenize=False, add_generation_prompt=True, **kwargs)
            ids = tokenizer.apply_chat_template(messages, tools=tools, tokenize=True, add_generation_prompt=True, **kwargs)
            # Transformers 5 can return BatchEncoding by default.
            if isinstance(ids, dict) or hasattr(ids, "keys"):
                ids = ids["input_ids"]
            if ids and isinstance(ids[0], list):
                ids = ids[0]
            assert list(ids) == tokenizer.encode(rendered, add_special_tokens=False)
            templates[label] = {"text": rendered, "token_count": len(ids), "token_ids": list(ids)}
        model_report = {
            "repo_id": manifest["repo_id"], "revision": manifest["revision"],
            "tokenizer_class": type(tokenizer).__name__, "is_fast": tokenizer.is_fast,
            "vocab_size": tokenizer.vocab_size, "len": len(tokenizer),
            "eos_token": tokenizer.eos_token, "eos_token_id": tokenizer.eos_token_id,
            "bos_token": tokenizer.bos_token, "pad_token_id": tokenizer.pad_token_id,
            "all_special_tokens": tokenizer.all_special_tokens,
            "all_special_ids": tokenizer.all_special_ids,
            "added_tokens": raw.get("added_tokens", []), "markers": probes,
            "template_cases": templates,
        }
        report["models"][name] = model_report
        print(name, model_report["tokenizer_class"], "vocab", len(tokenizer), "EOS", tokenizer.eos_token_id, flush=True)
    write_json(root / "tokenizer_audit.json", report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="Fetch only the pinned allowlist before auditing")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.download:
        fetch(args.root)
    audit(args.root)
