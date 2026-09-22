"""Replace this factory/state with your own algorithm; no repeat detector here.

For a deterministic SERVER SMOKE TEST only, set OBSERVER_DEMO_LIMIT=128.
Without that variable the extension observes and always continues natively.
"""
import os

from thinking_observer import CONTINUE, StopAndAnswer


def make_detector():
    value = os.environ.get("OBSERVER_DEMO_LIMIT")
    limit = int(value) if value else None
    if limit is not None and limit < 1:
        raise ValueError("OBSERVER_DEMO_LIMIT must be positive")
    return CustomDetector(limit)


class CustomDetector:
    def __init__(self, demonstration_limit=None):
        self.demonstration_limit = demonstration_limit

    def start(self, context):
        return RequestState(self.demonstration_limit)


class RequestState:
    def __init__(self, demonstration_limit):
        self.demonstration_limit = demonstration_limit
        # Allocate bounded, request-local algorithm state here.

    def observe(self, delta):
        # Feed delta.token_ids into YOUR incremental detector here.
        # delta.text may be None at a natural mixed text/token boundary.
        if self.demonstration_limit and delta.end >= self.demonstration_limit:
            return StopAndAnswer(
                reason="demo_only",
                detected_at=self.demonstration_limit,
                answer_cue="\nGive a self-contained answer using the reasoning so far.\n",
            )
        return CONTINUE

    def close(self, outcome):
        # Release per-request state here. Do not block or perform network I/O.
        pass
