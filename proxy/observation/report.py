"""Human-readable views and distributions of eligible observations."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import html
import json
import math
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from .config import digest


def escaped(value):
    return html.escape(str(value)).replace("|", "&#124;").replace("\n", " ").replace("\r", " ").replace("[", "&#91;").replace("]", "&#93;")


def fenced(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    fence = "`" * max(3, max((len(m.group()) + 1 for m in re.finditer(r"`+", text)), default=3))
    return f"{fence}\n{text}\n{fence}\n"


def stamp(value, zone):
    return datetime.fromisoformat(value).astimezone(ZoneInfo(zone)).isoformat(timespec="milliseconds") if value else "未知"


def metric(value):
    return "—（未知）" if value is None else str(round(value, 3) if isinstance(value, float) else value)


def message_md(message, title):
    lines = [f"### {title}\n"]
    for key, value in message.items():
        if key == "role":
            continue
        lines.extend([f"**{escaped(key)}**\n", fenced(value)])
    return "\n".join(lines)


def request_markdown(row, detail, zone):
    text = [f"# 请求 {row['request_id']}\n",
            f"{stamp(row['started_at_utc'], zone)} → {stamp(row.get('completed_at_utc'), zone)}\n",
            f"耗时：{metric(row['e2e_ms'])} ms；采集：{row['capture_status']}；解析：{row['parse_status']}\n",
            "## 本次 Token（来源与状态）\n", fenced(detail["tokens"]),
            "## 输入历史\n", "<details><summary>展开完整输入 messages</summary>\n"]
    request = detail.get("request", {})
    messages = request.get("messages", []) if isinstance(request, dict) else []
    for index, message in enumerate(messages if isinstance(messages, list) else []):
        text.append(message_md(message, f"{index} · {escaped(message.get('role'))}") if isinstance(message, dict) else fenced(message))
    text.extend(["</details>\n", "## 本次响应\n"])
    for choice in detail.get("response", {}).get("choices", []):
        text.append(message_md(choice["message"], f"choice {choice['index']} · {escaped(choice['finish_reason'])}"))
    text.extend(["## 完整请求参数\n", "<details><summary>展开请求</summary>\n", fenced(request), "</details>\n",
                 "## 完整性及原始位置\n", fenced({"warnings": detail["warnings"], "raw_refs": detail["raw_refs"]})])
    return "\n".join(text)


def distribution(rows, thresholds):
    eligible = [r for r in rows if not r["exclusions"]]
    lengths = sorted(r["reasoning_tokens_local"] for r in eligible)
    total = sum(lengths)
    n = len(lengths)
    quantiles = {f"p{p}": lengths[math.ceil(p / 100 * n) - 1] if n else None for p in (50, 75, 90, 95, 99)}
    scans = [{"threshold": h, "n": n,
              "short_fraction": sum(x <= h for x in lengths) / n if n else None,
              "tail_fraction": sum(x > h for x in lengths) / n if n else None,
              "at_boundary_count": lengths.count(h),
              "tail_token_share": sum(x for x in lengths if x > h) / total if total else None,
              "excess_tokens": sum(max(x - h, 0) for x in lengths)} for h in thresholds]
    bounds = sorted(set([0, *thresholds]))
    histogram = [{"lower_exclusive": lo, "upper_inclusive": hi,
                  "count": sum((lo is None or x > lo) and (hi is None or x <= hi) for x in lengths)}
                 for lo, hi in zip([None, *bounds], [*bounds, None])]
    tail = []
    ranked = sorted(eligible, key=lambda r: (-r["reasoning_tokens_local"], r["request_id"]))
    wall = sum(r["e2e_ms"] or 0 for r in eligible)
    for fraction in (0.01, 0.05):
        sample = ranked[:math.ceil(n * fraction)]
        tail.append({"fraction": fraction, "count": len(sample), "request_ids": [r["request_id"] for r in sample],
                     "reasoning_share": sum(r["reasoning_tokens_local"] for r in sample) / total if total else None,
                     "observed_time_share": sum(r["e2e_ms"] or 0 for r in sample) / wall if wall else None})
    return {"request_count": len(rows), "eligible_count": n,
            "excluded_reasons": dict(Counter(reason for row in rows for reason in row["exclusions"])),
            "zero_count": lengths.count(0), "mean": total / n if n else None,
            "max": max(lengths) if n else None, **quantiles,
            "histogram": histogram, "tail_contribution": tail, "threshold_scan": scans}


def render_views(output, rows, trajectories, load_detail, zone, integrity, grouped):
    output = Path(output)
    by_id = {row["request_id"]: row for row in rows}
    timeline = defaultdict(list)
    for row in rows:
        path = output / row["detail_markdown"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(request_markdown(row, load_detail(row), zone), encoding="utf-8")
        timeline[row["bucket"]].append(row)
    intro = ["# Agent 调用观测\n", f"请求：{len(rows)}；完整性：{integrity['status']}\n",
             "所有耗时为代理视角；历史文本不计入本次生成量。\n", "## 分层分布\n",
             "| 分层 | 有效/请求 | P50 | P95 | 最大思考 Token |", "| --- | ---: | ---: | ---: | ---: |"]
    for key, stats in grouped.items():
        identity = json.loads(key)
        label = " / ".join(str(identity.get(k) or "unknown") for k in ("agent", "scenario", "model", "variant"))
        name = digest(identity)[:16] + ".md"
        intro.append(f"| [{escaped(label)}](groups/{name}) | {stats['eligible_count']}/{stats['request_count']} | {metric(stats['p50'])} | {metric(stats['p95'])} | {metric(stats['max'])} |")
        lines = [f"# {escaped(label)}\n", "## 长度直方表\n", "| 下界（不含） | 上界（含） | 请求数 |", "| ---: | ---: | ---: |"]
        for bucket in stats["histogram"]:
            lines.append(f"| {metric(bucket['lower_exclusive'])} | {metric(bucket['upper_inclusive'])} | {bucket['count']} |")
        lines.extend(["\n## 候选阈值\n", "| 阈值 | 有效 N | 短请求比例 | 长尾 Token 占比 | 超阈值文本量 |", "| ---: | ---: | ---: | ---: | ---: |"])
        for scan in stats["threshold_scan"]:
            lines.append(f"| {scan['threshold']} | {scan['n']} | {metric(scan['short_fraction'])} | {metric(scan['tail_token_share'])} | {scan['excess_tokens']} |")
        lines.extend(["\n超阈值文本量不表示已验证可节省的 Token。\n", "## 长尾样本\n"])
        for tail in stats["tail_contribution"]:
            lines.append(f"Top {tail['fraction']:.0%}（{tail['count']} 条），思考贡献 {metric(tail['reasoning_share'])}：\n")
            for rid in tail["request_ids"]:
                lines.append(f"- [{rid[:8]}](../{by_id[rid]['detail_markdown']})")
        lines.extend(["\n## 排除项\n", fenced(stats["excluded_reasons"]), "## 实验身份\n", fenced(identity)])
        group_path = output / "groups" / name
        group_path.parent.mkdir(parents=True, exist_ok=True)
        group_path.write_text("\n".join(lines), encoding="utf-8")
    intro.extend(["\n## 按时间阅读\n"])
    for bucket, requests in timeline.items():
        target = output / "timeline" / (bucket + ".md")
        target.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# UTC 时间桶 {bucket}\n", f"显示时区：{zone}\n",
                 "| 开始时间 | 请求 | Agent | 耗时 ms | API 输入/输出 | 本地思考/正文/工具 | 工具名 | 状态 |",
                 "| --- | --- | --- | ---: | --- | --- | --- | --- |"]
        for row in requests:
            # timeline/YYYY-MM-DD/HH.md -> derived root
            link = "../../" + row["detail_markdown"]
            lines.append(f"| {stamp(row['started_at_utc'], zone)} | [{row['request_id'][:8]}]({link}) | {escaped(row['labels'].get('agent', 'unknown'))} | {metric(row['e2e_ms'])} | {metric(row['prompt_tokens_api'])}/{metric(row['completion_tokens_api'])} | {metric(row['reasoning_tokens_local'])}/{metric(row['content_tokens_local'])}/{metric(row['tool_tokens_local'])} | {escaped(', '.join(row['tool_names']))} | {escaped(', '.join(row['exclusions']) or '完整')} |")
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        intro.append(f"- [{bucket}](timeline/{bucket}.md) · {len(requests)} 个请求")
    intro.append("\n## 消息轨迹\n")
    for trajectory in trajectories:
        row = by_id[trajectory["leaf_request_id"]]
        detail = load_detail(row)
        lines = [f"# {escaped(trajectory['title'])}\n",
                 f"实采请求：{len(trajectory['unique_request_ids'])}；仅历史 assistant 轮次：{trajectory['history_only_assistant_turns']}；歧义连接：{trajectory['ambiguous_links']}\n",
                 "任务起止未经确认；历史时刻及原调用 usage 未知。\n"]
        if trajectory["shared_ancestors"]:
            lines.append("共享前缀请求：" + ", ".join(trajectory["shared_ancestors"]) + "；本页为其中一个延续分支。\n")
            for sibling in trajectories:
                if sibling is not trajectory and set(sibling["shared_ancestors"]) & set(trajectory["shared_ancestors"]):
                    lines.append(f"- [另一分支 · {sibling['leaf_request_id'][:8]}]({sibling['trajectory_id']}.md)")
        anchors = {e["message_index"]: e for e in trajectory["anchors"]}
        tool_links = {link["result_message_index"]: link for link in trajectory["tool_links"] if "result_message_index" in link}
        messages = detail["request"]["messages"]
        fold_end = max((e["message_index"] for e in trajectory["anchors"] if e["status"] == "matched" and e["candidates"][0]["request_id"] in trajectory["shared_ancestors"]), default=-1)
        if fold_end >= 0:
            lines.append("\n<details><summary>展开共享历史前缀</summary>\n")
        for index, message in enumerate(messages):
            edge = anchors.get(index)
            source = "prompt 历史；原始时间与 usage 未知"
            if edge:
                source = f"{edge['status']} · {edge['relation']}"
                if edge["status"] == "matched":
                    parent = by_id[edge["candidates"][0]["request_id"]]
                    source += f" · [{parent['request_id'][:8]}](../{parent['detail_markdown']}) · 思考 {metric(parent['reasoning_tokens_local'])} tokens"
            if message.get("role") in {"system", "developer"}:
                lines.extend(["<details><summary>公共系统上下文</summary>\n", message_md(message, f"{index} · {message['role']}"), "</details>\n"])
            else:
                lines.extend([f"来源：{source}\n", message_md(message, f"{index} · {escaped(message.get('role'))}")])
            if edge and edge["status"] == "matched" and edge["profile"] == "omit_reasoning":
                parent = by_id[edge["candidates"][0]["request_id"]]
                original = load_detail(parent)["response"]["choices"]
                choice = next(c for c in original if c["index"] == edge["candidates"][0]["choice_index"])
                if choice.get("reasoning") is not None:
                    lines.extend(["**实采响应中的思考（后续 prompt 未携带）**\n", fenced(choice["reasoning"])])
            if edge and edge["status"] != "matched":
                lines.append(fenced(edge))
            if index in tool_links:
                link = tool_links[index]
                lines.append(f"工具结果关联：{escaped(link['tool_call_id'])} → 消息 {metric(link['call_message_index'])} · {link['status']}；执行时刻未知。\n")
            if index == fold_end:
                lines.append("</details>\n")
        lines.append(f"## 当前实采响应 · [{row['request_id'][:8]}](../{row['detail_markdown']})\n")
        for choice in detail.get("response", {}).get("choices", []):
            lines.append(message_md(choice["message"], f"choice {choice['index']}"))
        path = output / "trajectories" / (trajectory["trajectory_id"] + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        intro.append(f"- [{escaped(trajectory['title'])}](trajectories/{path.name})")
    intro.extend(["\n## 完整性\n", fenced(integrity)])
    (output / "SUMMARY.md").write_text("\n".join(intro) + "\n", encoding="utf-8")
