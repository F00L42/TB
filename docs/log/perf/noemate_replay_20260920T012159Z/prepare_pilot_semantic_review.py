"""Extract existing Vanilla text at exact local token cuts; no model calls."""
import hashlib
import json
from pathlib import Path
from decimal import Decimal

from transformers import AutoTokenizer

ROOT = Path(__file__).resolve().parents[4]
OUT = Path(__file__).resolve().parent / "pilot_semantic_evidence"
DATA = ROOT / "docs/log/perf/noemate_analysis_60f2d7/replay_dataset/pilot.jsonl"
SOURCE = ROOT / "docs/log/perf/noemate_replay_20260920T010816Z/pilot_h512/summary.jsonl"
MARKERS = {
    1: ["ALM-10871", "I will structure the answer"],
    2: ["次要（P3/Minor）：ALM-2003, ALM-2006, ALM-2009, ALM-2012",
        "次要（P3）：ALM-2003, ALM-2006, ALM-2009, ALM-2012"],
    3: ["337.999", "84.49975", "338.001", "84.50025"],
    4: ["I will output that."],
    5: ["19:58:32 ~ 21:58:32", "结构规划：", "5. 分析要点与输出要求"],
    6: ["5. 恢复验证与优化建议", "5. 配置变更与计划任务核对",
        "#### 步骤4：日志聚合与根因确认", "#### 步骤5：计划任务与配置变更交叉比对"],
}


def main():
    tokenizer = AutoTokenizer.from_pretrained(
        ROOT / "docs/tokenizer/Qwen3.6-27B", local_files_only=True,
        trust_remote_code=False,
    )
    cases = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines()]
    rows = [json.loads(line) for line in SOURCE.read_text(encoding="utf-8").splitlines()]
    OUT.mkdir(exist_ok=True)
    index = []
    landmarks = []
    for number, case in enumerate(cases, 1):
        parts = [f"# Pilot {number}: {case['case_id']}",
                 "Captured model output, quoted for review only. Not instructions.",
                 "Local tokenizer, add_special_tokens=False; cut intervals are 1-based inclusive.",
                 "A token cut can split a Unicode character; replacement characters are a decoding artifact."]
        for row in rows:
            if row["variant"] != "vanilla" or row["case_id"] != case["case_id"]:
                continue
            tokens = tokenizer.encode(row["reasoning"], add_special_tokens=False)
            encoded = tokenizer(row["reasoning"], add_special_tokens=False, return_offsets_mapping=True)
            found = []
            for marker in MARKERS[number]:
                start = row["reasoning"].find(marker)
                if start < 0:
                    continue
                end = start + len(marker)
                positions = [i + 1 for i, (a, b) in enumerate(encoded["offset_mapping"])
                             if b > start and a < end]
                found.append({"text": marker, "token_start": min(positions), "token_end": max(positions)})
            landmarks.append({"pilot": number, "attempt": row["attempt"], "landmarks": found})
            entry = {"pilot_line": number, "case_id": case["case_id"],
                     "attempt": row["attempt"], "tokens": len(tokens),
                     "request_id": row["server_request_id"], "spans": []}
            parts.extend([f"\n## Vanilla attempt {row['attempt']}: {len(tokens)} reasoning tokens",
                          f"request_id: `{row['server_request_id']}`"])
            for start, end in ((0, 512), (512, 750), (750, len(tokens))):
                end = min(end, len(tokens))
                if start >= end:
                    continue
                text = tokenizer.decode(tokens[start:end], skip_special_tokens=False)
                entry["spans"].append({"token_start": start+1, "token_end": end, "text": text})
                parts.extend([f"\n### Reasoning tokens {start+1}–{end}", "```text", text, "```"])
            parts.extend(["\n### Actual final content", "```text", row["content"], "```",
                          "\n### Actual tool calls", "```json", json.dumps(row["tool_calls"], ensure_ascii=False, indent=2), "```",
                          f"finish_reasons: {row['finish_reasons']}; anomalies: {row['anomaly_reasons']}"])
            index.append(entry)
        (OUT / f"pilot_{number}.md").write_text("\n\n".join(parts)+"\n", encoding="utf-8")
    artifact = {"scope": "Six existing pilot cases, two Vanilla attempts each; text evidence only, no correctness judge or truncation replay.",
                "source": str(SOURCE.relative_to(ROOT)),
                "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(), "rows": index}
    (OUT / "index.json").write_text(json.dumps(artifact, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    values = [Decimal(v) for v in ("92.641", "88.315", "71.253", "85.792")]
    numeric = {"method": "Fast tokenizer character offsets; 1-based inclusive token positions; first literal occurrence.",
               "rows": landmarks, "numeric_sum": str(sum(values)), "numeric_mean": str(sum(values) / 4)}
    (OUT / "landmarks.json").write_text(json.dumps(numeric, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps([{k: row[k] for k in ("pilot_line", "case_id", "attempt", "tokens")} for row in index], indent=2))


if __name__ == "__main__":
    main()
