"""DTB reasoning state-machine driver."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, Type

from dtb.reasoning.generation_states import _BaseState, _ThinkingState
from dtb.reasoning.request_context import DtbRequestContext


_LOGGER = logging.getLogger(__name__)


class DtbStateMachine:
    def __init__(self, context: DtbRequestContext):
        self.context = context

    async def run(self) -> None:
        current: Optional[Type[_BaseState]] = _ThinkingState
        try:
            while current is not None:
                state_name = current.__name__
                self.context.log_stream_event(
                    event="state_enter",
                    phase=state_name,
                    controller_state=state_name,
                    reasoning_boundary=(
                        self.context.request_state.reasoning_boundary
                    ),
                )
                current = await current.handle(self.context)
        except asyncio.CancelledError:
            self.context.request_state.completion_status = "cancelled"
            raise
        except Exception as exc:
            self.context.request_state.completion_status = "error"
            self.context.orchestration_error = str(exc)
            _LOGGER.exception(
                "DTB orchestration failed request_id=%s",
                self.context.request_id,
            )
        finally:
            await self.context.stream_output(None, phase="controller_end")
