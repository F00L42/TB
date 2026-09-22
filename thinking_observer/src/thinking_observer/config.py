from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ObserverConfig:
    enabled: bool = False
    answer_cue: str = "\nProvide the final answer based on the reasoning so far.\n"
    callback_limit_ms: float = 10.0
    abort_timeout_s: float = 10.0
    max_cue_tokens: int = 256
    max_drain_chunks: int = 1024
    telemetry: str = "off"

    def __post_init__(self):
        if self.telemetry not in ("off", "summary"):
            raise ValueError("telemetry must be off or summary")
        for value in (self.callback_limit_ms, self.abort_timeout_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("callback and abort limits must be finite and positive")
        if self.max_cue_tokens < 0 or self.max_drain_chunks < 1:
            raise ValueError("invalid cue/drain limit")
        if not isinstance(self.answer_cue, str) or len(self.answer_cue) > 4096:
            raise ValueError("answer_cue must be at most 4096 characters")
