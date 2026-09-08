"""Bound artifact transfer lifetime and release response-owned resources."""
from __future__ import annotations

import logging
import anyio
from starlette.responses import FileResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

logger = logging.getLogger(__name__)
_TRANSFER_DEADLINE = "threatlens_export_transfer_deadline"


class ExportTransferDeadlineExceeded(TimeoutError):
    """An incomplete response must be closed, never replaced after headers."""


class ExportTransferDeadlineMiddleware:
    """Cover downstream sends outside middleware that buffers response chunks.

    Export responses publish a monotonic deadline in shared request state. The
    outer ASGI boundary arms that deadline when headers arrive, so preparation
    time and unrelated API responses do not consume a transfer allowance.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        with anyio.CancelScope() as transfer:
            async def bounded_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    deadline = state.get(_TRANSFER_DEADLINE)
                    if isinstance(deadline, float):
                        transfer.deadline = deadline
                await send(message)

            await self.app(scope, receive, bounded_send)
        if transfer.cancel_called:
            logger.warning("export_transfer_deadline_exceeded")
            raise ExportTransferDeadlineExceeded("Export transfer deadline exceeded")


class DisconnectSafeFileResponse(FileResponse):
    """Keep transfer cancellation, file cleanup, and ASGI ownership together."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        deadline = anyio.current_time() + get_settings().export_transfer_timeout_seconds
        scope.setdefault("state", {})[_TRANSFER_DEADLINE] = deadline
        # A path-send implementation may defer reading beyond this response's
        # lifetime. Stream body chunks here so the deadline owns all reads.
        extensions = dict(scope.get("extensions", {}))
        extensions.pop("http.response.pathsend", None)
        background = self.background
        self.background = None
        try:
            with anyio.CancelScope(deadline=deadline) as transfer:
                await super().__call__({**scope, "extensions": extensions}, receive, send)
            if transfer.cancel_called:
                logger.warning("export_transfer_deadline_exceeded")
                raise ExportTransferDeadlineExceeded("Export transfer deadline exceeded")
        finally:
            if background is not None:
                # A surrounding transfer/disconnect cancellation must not skip
                # the offloaded unlink operation or another cleanup callback.
                with anyio.CancelScope(shield=True):
                    await background()
