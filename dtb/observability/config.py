"""Process-level construction of optional DTB telemetry components."""
from __future__ import annotations

from typing import TYPE_CHECKING

from dtb.config.schema import DtbObservabilityConfig, DtbTelemetryMode

if TYPE_CHECKING:
    from dtb.observability.event_writer import DtbEventWriter
    from dtb.observability.phase_recorder import DtbPhaseRecorder
    from dtb.observability.stream_audit import DtbStreamAudit


class DtbObservabilityRuntime:
    def __init__(self, config: DtbObservabilityConfig) -> None:
        self.config = config
        self.writer: DtbEventWriter | None = None
        if config.mode is not DtbTelemetryMode.OFF:
            from dtb.observability.event_writer import DtbEventWriter
            self.writer = DtbEventWriter(config.queue_capacity)

    def create_phase_recorder(
        self, policy_version: str
    ) -> DtbPhaseRecorder | None:
        if (
            self.writer is None
            or self.config.mode is not DtbTelemetryMode.PHASE
        ):
            return None
        from dtb.observability.phase_recorder import DtbPhaseRecorder
        return DtbPhaseRecorder(self.writer, policy_version)

    def create_stream_audit(self, request_id: str) -> DtbStreamAudit | None:
        if (
            self.writer is None
            or self.config.mode is not DtbTelemetryMode.STREAM_AUDIT
            or request_id not in self.config.stream_request_ids
        ):
            return None
        from dtb.observability.stream_audit import DtbStreamAudit
        return DtbStreamAudit(self.writer)

    def flush(self, timeout_s: float = 2.0) -> bool:
        return self.writer is None or self.writer.flush(timeout_s)

    def close(self, timeout_s: float = 2.0) -> bool:
        return self.writer is None or self.writer.close(timeout_s)
