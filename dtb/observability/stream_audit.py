"""Explicitly allowlisted, high-cardinality parser-boundary audit."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from dtb.observability.event_writer import DtbEventWriter


class DtbStreamAudit:
    def __init__(self, writer: DtbEventWriter) -> None:
        self._writer = writer
        self._sequence = 0

    @staticmethod
    def _text_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _token_digest(token_ids: list[int]) -> str:
        payload = json.dumps(token_ids, separators=(",", ":")).encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _first_output(result: Any) -> Any:
        try:
            return result.outputs[0]
        except (AttributeError, IndexError, TypeError):
            return None

    def record(
        self,
        *,
        context: Any,
        result: Any,
        source_result: Any,
        phase: str,
        child_request_id: str,
        event: str,
        controller_state: str,
        next_state: str,
        reasoning_boundary: Any,
        terminal_flush: bool,
        stop_boundary_aligned: bool,
        terminal_origin: str,
        final_terminal_passthrough: bool,
    ) -> None:
        self._sequence += 1
        source_output = self._first_output(source_result)
        forwarded_output = self._first_output(result)
        raw_text = getattr(source_output, "text", "") or ""
        raw_token_ids = list(getattr(source_output, "token_ids", None) or [])
        forwarded_text = getattr(forwarded_output, "text", "") or ""
        forwarded_token_ids = list(
            getattr(forwarded_output, "token_ids", None) or []
        )
        alignment_prefix_valid = (
            len(forwarded_token_ids) < len(raw_token_ids)
            and raw_token_ids[:len(forwarded_token_ids)] == forwarded_token_ids
        )
        dropped_token_ids = (
            raw_token_ids[len(forwarded_token_ids):]
            if stop_boundary_aligned and alignment_prefix_valid
            else []
        )
        state = context.request_state
        record = {
            "schema": 1,
            "source": "controller_pre_parser",
            "event": event,
            "parent_request_id": context.request_id,
            "child_request_id": child_request_id,
            "phase": phase,
            "sequence": self._sequence,
            "completion_epoch": state.assistant_completion_epoch,
            "reasoning_boundary": (
                reasoning_boundary.value if reasoning_boundary else "none"
            ),
            "controller_state": controller_state,
            "next_state": next_state,
            "raw_text": raw_text,
            "raw_text_sha256": self._text_digest(raw_text),
            "token_ids": raw_token_ids,
            "source_token_ids_sha256": self._token_digest(raw_token_ids),
            "source_finish_reason": getattr(source_output, "finish_reason", None),
            "source_stop_reason": getattr(source_output, "stop_reason", None),
            "raw_finished": getattr(source_result, "finished", None),
            "forwarded_text_sha256": self._text_digest(forwarded_text),
            "forwarded_text_length": len(forwarded_text),
            "forwarded_token_ids_sha256": self._token_digest(forwarded_token_ids),
            "forwarded_token_ids": forwarded_token_ids,
            "forwarded_token_count": len(forwarded_token_ids),
            "forwarded_finish_reason": getattr(
                forwarded_output, "finish_reason", None
            ),
            "forwarded_stop_reason": getattr(forwarded_output, "stop_reason", None),
            "forwarded_finished": getattr(result, "finished", None),
            "terminal_flush": terminal_flush,
            "terminal_origin": terminal_origin,
            "final_terminal_passthrough": final_terminal_passthrough,
            "synthetic_terminal": terminal_origin == "synthetic",
            "stop_boundary_aligned": stop_boundary_aligned,
            "alignment_dropped_token_ids": dropped_token_ids,
            "alignment_dropped_token_count": len(dropped_token_ids),
        }
        self._writer.publish("DTB_STREAM_AUDIT", record)
