"""JSON/SSE interpretation and bounded body decompression."""
from __future__ import annotations

import codecs
import json
import re
import zlib


def decode_body(body, headers, limit):
    encoding = next((v.lower() for k, v in headers if k.lower() == "content-encoding"), "identity")
    for coding in reversed([x.strip() for x in encoding.split(",")]):
        if coding in {"", "identity"}:
            continue
        if coding not in {"gzip", "deflate"}:
            raise ValueError(f"unsupported_content_encoding:{coding}")
        decoder = zlib.decompressobj(31 if coding == "gzip" else zlib.MAX_WBITS)
        body = decoder.decompress(body, limit + 1)
        if len(body) > limit or decoder.unconsumed_tail:
            raise ValueError("decoded_body_limit")
        body += decoder.flush(limit + 1 - len(body))
        if not decoder.eof or decoder.unused_data:
            raise ValueError("incomplete_or_multiple_compressed_members")
    if len(body) > limit:
        raise ValueError("decoded_body_limit")
    return body


def sse_events(chunks, with_offsets=False):
    """Yield complete SSE events; EOF does not dispatch an unterminated event."""
    decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
    buffer, data, event_type = "", [], "message"
    position = 0
    prefix = b""
    initialized = False

    def lines(final=False):
        nonlocal buffer, position, initialized
        while True:
            match = re.search(r"[\r\n]", buffer)
            if not match or (buffer[match.start():] == "\r" and not final):
                return
            end = match.start()
            width = 2 if buffer[end:end + 2] == "\r\n" else 1
            line, buffer = buffer[:end], buffer[end + width:]
            if not initialized:
                position = 3 if prefix == codecs.BOM_UTF8 else 0
                initialized = True
            position += len(line.encode("utf-8")) + width
            yield line

    def process(line):
        nonlocal data, event_type
        if not line:
            result = (event_type, "\n".join(data)) if data else None
            data, event_type = [], "message"
            return result
        if line.startswith(":"):
            return None
        field, sep, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "data":
            data.append(value)
        elif field == "event":
            event_type = value
        return None

    for chunk in chunks:
        prefix = (prefix + chunk)[:3]
        buffer += decoder.decode(chunk)
        for line in lines():
            result = process(line)
            if result is not None:
                yield (*result, position) if with_offsets else result
    buffer += decoder.decode(b"", final=True)
    for line in lines(final=True):
        result = process(line)
        if result is not None:
            yield (*result, position) if with_offsets else result


def first_semantic_offset(body, streaming, reasoning_fields):
    """End byte of the first complete event containing text/tool output."""
    if not streaming:
        try:
            value = json.loads(body)
            if any(any(c.get("message", {}).get(f) for f in ("content", "tool_calls", "function_call", *reasoning_fields)) for c in value.get("choices", [])):
                return len(body)
        except (ValueError, AttributeError, TypeError):
            pass
        return None
    try:
        for _, data, offset in sse_events([body], with_offsets=True):
            if data.strip() == "[DONE]":
                break
            value = json.loads(data)
            for choice in value.get("choices", []):
                delta = choice.get("delta", {})
                if any(delta.get(field) for field in ("content", "tool_calls", "function_call", *reasoning_fields)):
                    return offset
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def parse_response(body, headers, reasoning_fields):
    content_type = next((v for k, v in headers if k.lower() == "content-type"), "")
    streaming = "text/event-stream" in content_type.lower()
    choices, usage, model, warnings = {}, None, None, []
    done = False

    def consume(value):
        nonlocal usage, model
        if not isinstance(value, dict):
            raise ValueError("response_is_not_object")
        if "error" in value:
            warnings.append("api_error")
        if value.get("model"):
            model = value["model"]
        if value.get("usage") is not None:
            if isinstance(value["usage"], dict):
                if usage is not None and usage != value["usage"]:
                    warnings.append("usage_snapshot_changed")
                usage = value["usage"]
            else:
                warnings.append("invalid_usage")
        for raw in value.get("choices", []):
            index = raw.get("index")
            if type(index) is not int or index < 0:
                raise ValueError("invalid_choice_index")
            choice = choices.setdefault(index, {"index": index, "message": {"role": "assistant"},
                      "finish_reason": None, "tools": {}, "warnings": []})
            if choice["finish_reason"] is not None:
                raise ValueError("choice_delta_after_terminal")
            message = raw.get("delta" if streaming else "message", {})
            if not isinstance(message, dict):
                raise ValueError("invalid_message")
            for field, value in message.items():
                if field in {"content", *reasoning_fields}:
                    if value is None:
                        choice["message"].setdefault(field, None)
                    elif isinstance(value, str):
                        choice["message"][field] = (choice["message"].get(field) or "") + value
                    else:
                        choice["warnings"].append(f"nontext_{field}")
                        choice["message"][field] = value
                elif field == "tool_calls":
                    if value is None:
                        continue  # Optional null means no calls, including in SSE deltas.
                    for pos, call in enumerate(value):
                        tool_index = call.get("index") if streaming else pos
                        if type(tool_index) is not int or tool_index < 0:
                            raise ValueError("invalid_tool_index")
                        tool = choice["tools"].setdefault(tool_index, {"id": "", "type": "function",
                                                 "function": {"name": "", "arguments": ""}})
                        for key in ("id", "type"):
                            if call.get(key):
                                if tool[key] and tool[key] != call[key] and key == "id":
                                    raise ValueError("conflicting_tool_id")
                                tool[key] = call[key]
                        for key in ("name", "arguments"):
                            fragment = call.get("function", {}).get(key)
                            if fragment is not None:
                                if not isinstance(fragment, str):
                                    raise ValueError("tool_fragment_not_string")
                                tool["function"][key] += fragment
                elif field == "function_call":
                    if value is None:
                        continue
                    function = choice["message"].setdefault(field, {"name": "", "arguments": ""})
                    for key, fragment in value.items():
                        function[key] = function.get(key, "") + fragment
                else:
                    # Preserve unknown message fields for inspection and exact history matching.
                    choice["message"][field] = value
            if raw.get("finish_reason") is not None:
                choice["finish_reason"] = raw["finish_reason"]

    try:
        if streaming:
            for event, payload in sse_events([body]):
                if done:
                    warnings.append("data_after_done")
                    continue
                if payload.strip() == "[DONE]":
                    done = True
                else:
                    if event not in {"message", ""}:
                        warnings.append(f"unknown_event:{event}")
                    consume(json.loads(payload))
        else:
            consume(json.loads(body))
            done = True
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError) as exc:
        warnings.append(f"parse_error:{type(exc).__name__}:{exc}")

    output = []
    for index in sorted(choices):
        choice = choices[index]
        msg = choice["message"]
        if choice["tools"]:
            msg["tool_calls"] = [choice["tools"][i] for i in sorted(choice["tools"])]
        functions = [t["function"] for t in msg.get("tool_calls", [])]
        if "function_call" in msg:
            functions.append(msg["function_call"])
        for fn in functions:
            try:
                if not fn.get("name") or not isinstance(json.loads(fn["arguments"]), dict):
                    raise ValueError
            except (ValueError, TypeError, KeyError):
                choice["warnings"].append("invalid_tool_arguments_or_name")
        for tool in msg.get("tool_calls", []):
            if not tool["id"]:
                choice["warnings"].append("missing_tool_id")
        present = [(f, msg[f]) for f in reasoning_fields if isinstance(msg.get(f), str)]
        nonempty = [value for _, value in present if value]
        if len(set(nonempty)) > 1:
            choice["warnings"].append("ambiguous_reasoning")
        choice["reasoning"] = present[0][1] if present else None
        choice["reasoning_field"] = present[0][0] if present else None
        choice.pop("tools")
        output.append(choice)
    all_warnings = warnings + [w for c in output for w in c["warnings"]]
    errors = [w for w in all_warnings if w != "usage_snapshot_changed"]
    status = "complete" if done and output and all(c["finish_reason"] for c in output) else "incomplete"
    if errors:
        status = "parse_error"
    return {"streaming": streaming, "parse_status": status, "model": model,
            "choices": output, "usage": usage, "warnings": all_warnings}
