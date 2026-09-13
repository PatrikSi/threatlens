import asyncio
from typing import Annotated

import anyio
import pytest
from fastapi import Depends, FastAPI
from starlette.background import BackgroundTask
from starlette.responses import PlainTextResponse

from app.core.config import get_settings
from app.services.export_download_scratch import ExportDownloadScratch
from app.services.export_transport import (
    DisconnectSafeFileResponse,
    ExportTransferDeadlineExceeded,
    ExportTransferDeadlineMiddleware,
)


async def receive():
    await anyio.sleep_forever()


def _scope(**changes):
    return {
        "type": "http", "method": "GET", "path": "/download", "raw_path": b"/download",
        "query_string": b"", "headers": [], "scheme": "http", "server": ("test", 80),
        "client": ("127.0.0.1", 1234), "asgi": {"spec_version": "2.4"},
        **changes,
    }


@pytest.mark.parametrize("blocked_phase", ["http.response.start", "http.response.body"])
@pytest.mark.parametrize("anonymous", [False, True])
def test_deadline_cleans_partial_or_unstarted_file_transfers(
    tmp_path, monkeypatch, blocked_phase, anonymous,
):
    monkeypatch.setattr(get_settings(), "export_transfer_timeout_seconds", .05)
    artifact = tmp_path / "export.txt"
    artifact.write_bytes(b"private evidence" * 20)
    scratch = None
    if anonymous:
        scratch = ExportDownloadScratch()
        scratch.file.write(artifact.read_bytes())
        artifact.unlink()
        response = scratch.response(media_type="text/plain", filename="export.txt", headers={})
    else:
        response = DisconnectSafeFileResponse(artifact, background=BackgroundTask(artifact.unlink))
    response.chunk_size = 4
    sent = []

    async def blocked_send(message):
        sent.append(message)
        if message["type"] == blocked_phase:
            await anyio.sleep_forever()

    with pytest.raises(ExportTransferDeadlineExceeded):
        anyio.run(response, _scope(headers=[(b"range", b"bytes=1-100")]), receive, blocked_send)
    assert len([message for message in sent if message["type"] == "http.response.start"]) == 1
    assert not artifact.exists()
    if scratch is not None:
        assert scratch.file.closed


def test_outer_deadline_cancels_buffered_middleware_send_and_closes_dependency(monkeypatch):
    monkeypatch.setattr(get_settings(), "export_transfer_timeout_seconds", .05)
    application = FastAPI()
    closed = []
    scratch = ExportDownloadScratch()
    scratch.file.write(b"private evidence" * 20_000)

    def dependency():
        try:
            yield
        finally:
            closed.append(True)

    @application.middleware("http")
    async def buffered_logging(request, call_next):
        return await call_next(request)

    @application.get("/download")
    def download(_resource: Annotated[None, Depends(dependency)]):
        return scratch.response(media_type="text/plain", filename="export.txt", headers={})

    application.add_middleware(ExportTransferDeadlineMiddleware)
    sent = []

    async def blocked_send(message):
        sent.append(message)
        if message["type"] == "http.response.body":
            await anyio.sleep_forever()

    with pytest.raises(ExportTransferDeadlineExceeded):
        anyio.run(application, _scope(), receive, blocked_send)
    assert closed == [True]
    assert scratch.file.closed
    assert len([message for message in sent if message["type"] == "http.response.start"]) == 1


def test_unrelated_response_is_not_given_an_export_deadline(monkeypatch):
    monkeypatch.setattr(get_settings(), "export_transfer_timeout_seconds", .01)
    response = PlainTextResponse("ordinary endpoint")
    middleware = ExportTransferDeadlineMiddleware(response)
    sent = []

    async def send(message):
        await anyio.sleep(.02)
        sent.append(message)

    anyio.run(middleware, _scope(), receive, send)
    assert sent[-1]["body"] == b"ordinary endpoint"


def test_external_cancellation_also_runs_file_cleanup(tmp_path):
    artifact = tmp_path / "export.txt"
    artifact.write_text("private evidence")
    response = DisconnectSafeFileResponse(artifact, background=BackgroundTask(artifact.unlink))

    async def cancel_transfer():
        started = asyncio.Event()

        async def send(_message):
            started.set()
            await asyncio.Future()

        task = asyncio.create_task(response(_scope(), receive, send))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_transfer())
    assert not artifact.exists()
