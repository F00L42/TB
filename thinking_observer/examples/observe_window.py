"""Bounded observation wiring only; no repetition algorithm or model request.

Run after installing this SDK: python thinking_observer/examples/observe_window.py
Server factory: observe_window:make_detector (put examples on PYTHONPATH).
"""
from collections import deque

from thinking_observer import (
    CONTINUE, BoundaryProtocol, ObserverContext, Outcome, ReasoningDelta,
)


class WindowDetector:
    def __init__(self, token_limit=256, character_limit=1024):
        if token_limit < 1 or character_limit < 1:
            raise ValueError("window limits must be positive")
        self.token_limit = token_limit
        self.character_limit = character_limit

    def start(self, context):
        # No request content is stored on this shared factory.
        return WindowState(self.token_limit, self.character_limit)


class WindowState:
    def __init__(self, token_limit, character_limit):
        self.tokens = deque(maxlen=token_limit)
        self.characters = deque(maxlen=character_limit)
        self.token_end = 0
        self.saw_unavailable_text = False
        self.closed = False

    @property
    def token_start(self):
        return self.token_end - len(self.tokens)

    def observe(self, delta):
        # IDs are authoritative. Never reconstruct them by encoding delta.text.
        self.tokens.extend(delta.token_ids[-self.tokens.maxlen:])
        self.token_end = delta.end
        if delta.text is None:
            self.saw_unavailable_text = True
        else:
            # This is a character suffix, NOT text aligned with the token window.
            self.characters.extend(delta.text[-self.characters.maxlen:])
        # Plug bounded incremental algorithm state here. This example only observes.
        return CONTINUE

    def close(self, outcome):
        self.tokens.clear()
        self.characters.clear()
        self.closed = True


def make_detector():
    return WindowDetector()


class _SmokeTokenizer:
    def encode(self, text, *, add_special_tokens=False):
        raise AssertionError("observing original IDs must not re-tokenize text")


def smoke():
    context = ObserverContext(
        request_id="synthetic", model="no-model",
        protocol=BoundaryProtocol(90, 91), completion_limit=100,
        max_model_len=200, prompt_tokens=10, tokenizer=_SmokeTokenizer(),
    )
    factory = WindowDetector(token_limit=4, character_limit=8)
    first, second = factory.start(context), factory.start(context)
    events = (
        ReasoningDelta((1, 2), "abc", 0, 2, 2),
        ReasoningDelta((), "de", 2, 2, 2),  # text-only; no token advance
        ReasoningDelta((3, 4, 5), "fghij", 2, 5, 5),
        ReasoningDelta((6,), None, 5, 6, 7, boundary="think_end"),
    )
    for event in events:
        assert first.observe(event) is CONTINUE
    assert tuple(first.tokens) == (3, 4, 5, 6)
    assert (first.token_start, first.token_end) == (2, 6)
    assert "".join(first.characters) == "cdefghij"
    assert first.saw_unavailable_text
    assert tuple(second.tokens) == () and second.token_end == 0
    outcome = Outcome(
        status="native_complete", finish_reason="stop", emitted_tokens=7,
        reasoning_tokens=6, detected_at=None, repeat_start=None,
        actual_handoff=None, emitted_at_detection=None, overshoot_tokens=None,
        decision_reason=None, detector_error=None, skipped_reason=None,
        callback_ms=0.0, abort_drain_ms=None, handoff_ms=None,
        cue_tokens=0, answer_limit=None, children=(),
    )
    first.close(outcome)
    assert first.closed and not first.tokens and not first.characters
    second.close(outcome)
    print("PASS: bounded token/text windows, token offsets, missing text, request isolation, cleanup")


if __name__ == "__main__":
    smoke()
