"""Content lineage for reading, never session attribution or request deduplication."""
from __future__ import annotations

from collections import defaultdict
import copy

from .config import digest


def projected(message, profile):
    value = copy.deepcopy(message)
    if profile == "omit_reasoning":
        value.pop("reasoning", None)
        value.pop("reasoning_content", None)
    return value


def prefix_hashes(messages, profile="exact"):
    value = digest([])
    values = [value]
    for message in messages:
        value = digest([value, projected(message, profile)])
        values.append(value)
    return values


def tool_history_links(messages):
    pending, links = {}, []
    for index, message in enumerate(messages):
        if message.get("role") == "assistant":
            calls = message.get("tool_calls", [])
            if not isinstance(calls, list):
                links.append({"call_message_index": index, "status": "invalid_tool_calls"})
                continue
            for call in calls:
                if not isinstance(call, dict):
                    links.append({"call_message_index": index, "status": "invalid_tool_call"})
                    continue
                cid = call.get("id")
                if not isinstance(cid, str) or not cid or cid in pending:
                    links.append({"call_message_index": index, "tool_call_id": cid, "status": "missing_or_duplicate_call_id"})
                    if isinstance(cid, str) and cid in pending:
                        pending[cid] = None
                    continue
                pending[cid] = index
        elif message.get("role") == "tool":
            cid = message.get("tool_call_id")
            source = pending.pop(cid, None) if isinstance(cid, str) else None
            links.append({"call_message_index": source, "result_message_index": index,
                          "tool_call_id": cid, "status": "matched_in_prompt" if source is not None else "orphan_result"})
    links.extend({"call_message_index": index, "tool_call_id": cid, "status": "result_not_in_prompt"} for cid, index in pending.items())
    return links


def build_trajectories(rows, load_detail, profiles=()):
    """Index full prefixes + observed outputs. Bounded detail loading, O(messages)."""
    ordered = sorted(rows, key=lambda r: (r["started_at_utc"], r["process_id"], r["arrival_seq"], r["request_id"]))
    lookup = {row["request_id"]: row for row in ordered}
    modes = ["exact", *profiles]
    indices = {mode: defaultdict(list) for mode in modes}
    histories = {}
    for row in ordered:
        detail = load_detail(row)
        messages = detail.get("request", {}).get("messages")
        if not isinstance(messages, list) or not all(isinstance(m, dict) for m in messages):
            continue
        histories[row["request_id"]] = {mode: prefix_hashes(messages, mode) for mode in modes}
        if row["capture_status"] != "complete" or row["parse_status"] != "complete":
            continue
        for choice in detail.get("response", {}).get("choices", []):
            for mode in modes:
                key = digest([histories[row["request_id"]][mode][-1], projected(choice["message"], mode)])
                indices[mode][key].append((row["request_id"], choice["index"], len(messages)))
    edges = []
    for row in ordered:
        rid = row["request_id"]
        if rid not in histories:
            continue
        child = load_detail(row)
        messages = child["request"]["messages"]
        found = []
        for mode in modes:
            for end, key in enumerate(histories[rid][mode][1:], 1):
                candidates, time_rejected = [], []
                for source, choice_index, _ in indices[mode].get(key, []):
                    if source == rid:
                        continue
                    parent = lookup[source]
                    # Conflicting declared identity must not be silently merged.
                    if any(parent["labels"].get(k) not in (None, "unknown", v)
                           for k, v in row["labels"].items() if v not in (None, "unknown")):
                        continue
                    # Recheck actual structures after hash lookup.
                    parent_detail = load_detail(parent)
                    output = next(c["message"] for c in parent_detail["response"]["choices"] if c["index"] == choice_index)
                    expected = parent_detail["request"]["messages"] + [output]
                    if [projected(m, mode) for m in expected] != [projected(m, mode) for m in messages[:end]]:
                        continue
                    candidate = {"request_id": source, "choice_index": choice_index}
                    # Latest relevant output byte is an upper bound; headers/usage may trail it.
                    if parent.get("last_response_at_utc") and row.get("body_received_at_utc") and parent["last_response_at_utc"] > row["body_received_at_utc"]:
                        candidate["time_status"] = "time_unverified"
                    else:
                        candidate["time_status"] = "compatible"
                    if parent["started_at_utc"] >= row["body_received_at_utc"]:
                        time_rejected.append(candidate)
                    else:
                        candidates.append(candidate)
                if candidates or time_rejected:
                    found.append({"target_request_id": rid, "message_index": end - 1,
                                  "profile": mode, "candidates": candidates,
                                  "time_rejected": time_rejected,
                                  "status": "matched" if len(candidates) == 1 else "ambiguous" if candidates else "time_conflict",
                                  "relation": "exact_continuation" if mode == "exact" else "profile_continuation"})
            if found:
                break
        # Keep historical anchors, not only the most recent one. No anchors means prompt-only history.
        edges.extend(found)
    by_target = defaultdict(list)
    for edge in edges:
        by_target[edge["target_request_id"]].append(edge)
    children = defaultdict(set)
    for target, target_edges in by_target.items():
        matches = [edge for edge in target_edges if edge["status"] == "matched"]
        if matches:
            nearest = max(matches, key=lambda edge: edge["message_index"])
            children[nearest["candidates"][0]["request_id"]].add(target)
    # Render each observed leaf snapshot, plus ambiguous/isolated roots. Shared ancestors are references.
    leaves = [row for row in ordered if row["request_id"] in histories and not children[row["request_id"]]]
    trajectories = []
    for row in leaves:
        rid = row["request_id"]
        anchors = by_target[rid]
        ancestors = {candidate["request_id"] for edge in anchors if edge["status"] == "matched" for candidate in edge["candidates"]}
        detail = load_detail(row)
        messages = detail["request"]["messages"]
        title = next((str(m.get("content", ""))[:100] for m in messages if m.get("role") == "user"), "消息轨迹")
        matched = {e["message_index"] for e in anchors if e["status"] == "matched"}
        trajectories.append({"trajectory_id": "trajectory_" + digest([rid, anchors])[:16],
                             "leaf_request_id": rid, "title": title, "anchors": anchors,
                             "unique_request_ids": sorted(ancestors | {rid}),
                             "history_only_assistant_turns": sum(m.get("role") == "assistant" and i not in matched for i, m in enumerate(messages)),
                             "ambiguous_links": sum(e["status"] != "matched" for e in anchors),
                             "shared_ancestors": sorted(source for source in ancestors if len(children[source]) > 1),
                             "tool_links": tool_history_links(messages),
                             "boundaries": ["task_start_unverified", "task_end_unverified"]})
    return trajectories, edges
