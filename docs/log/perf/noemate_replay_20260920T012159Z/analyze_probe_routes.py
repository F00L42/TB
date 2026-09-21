"""Offline route candidates and length-based probe candidates; no inference."""
from collections import Counter
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))

from dtb.budgeting.scene_router import classify_scene
from dtb.config.loader import load_dtb_service_config
from dtb.integration.vllm.request_adapter import extract_prompt_text_views

OUT = Path(__file__).resolve().parent
POOL = ROOT / "docs/log/perf/noemate_analysis_60f2d7/replay_dataset/pool.jsonl"
SKILLS = ("knowledge-qa", "cn-query-ops-monitoring-data", "cn-fault-spirit")
RESOURCE = "Skill resource root: .nextagent/skills/"
IDENTITY = "你是核心网运维 Agent，负责理解用户输入，结合上下文和本轮用户偏好，选择合适的 Skill 处理核心网运维相关问题。"
SCENE = "noemate_loaded_skill"
INDICATORS = {
    "required_all": [RESOURCE],
    "required_any": [f'<skill_content name="{name}">' for name in SKILLS],
    "strong": [], "weak": [],
}


def main():
    service = load_dtb_service_config()
    candidate = replace(service, scenes={
        **service.scenes, SCENE: {"indicators": INDICATORS},
    }, scene_priority=(SCENE, *service.scene_priority))
    counts, rows = Counter(), []
    for line in POOL.read_text(encoding="utf-8").splitlines():
        record = json.loads(line)
        messages = record["request_body"]["messages"]
        text = extract_prompt_text_views(messages, None).classification_text
        before = classify_scene(text, service_config=service)
        after = classify_scene(text, service_config=candidate)
        skill = next((name for name in SKILLS if f'<skill_content name="{name}">' in text), None)
        has_identity = any(m.get("role") == "system" and IDENTITY in m.get("content", "") for m in messages)
        matched = after["scene"] == SCENE
        counts["cases"] += 1
        counts["system_identity"] += has_identity
        counts["candidate_matched"] += matched
        if matched:
            counts[skill] += 1
            # Removing either required feature must reject the new scene.
            for mutated in (text.replace(RESOURCE, ""), text.replace(f'<skill_content name="{skill}">', "")):
                assert classify_scene(mutated, service_config=candidate)["scene"] != SCENE
        else:
            assert before["scene"] == after["scene"]
        rows.append({"case_id": record["case_id"], "skill": skill,
                     "old_scene": before["scene"], "candidate_scene": after["scene"],
                     "system_identity": has_identity})
    pairs = json.loads((OUT / "pool_h750_pairs.json").read_text(encoding="utf-8"))
    selected = [p for p in pairs if p["d_reasoning"] >= 750 + 2 * 256]
    repeats = Counter(p["case_id"] for p in selected)
    result = {
        "scope": "Offline current router + in-memory candidate; not server routing. Length is not measured probe count.",
        "pool_sha256": sha256(POOL.read_bytes()).hexdigest(),
        "config_sha256": sha256((ROOT / "dtb/config/budget_config.yaml").read_bytes()).hexdigest(),
        "candidate_indicators": INDICATORS, "counts": dict(counts),
        "negative_check": "Removed either required marker from each positive; all rejected the candidate. No external negative corpus tested.",
        "probe_length_candidates": {
            "threshold": 1262, "requests": len(selected), "request_denominator": len(pairs),
            "cases": len(repeats), "case_denominator": counts["cases"],
            "both_repetitions": sum(n == 2 for n in repeats.values()),
            "rows": [{k: p[k] for k in ("case_id", "line_no", "attempt", "d_reasoning")} for p in selected],
        }, "routes": rows,
    }
    (OUT / "probe_route_candidates.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"counts": result["counts"], "probe_candidates": len(selected)}, ensure_ascii=True))


if __name__ == "__main__":
    main()
