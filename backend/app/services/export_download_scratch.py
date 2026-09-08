"""Anonymous plaintext downloads whose lifetime is owned by the API response."""
import os
import tempfile
from pathlib import Path

from starlette.responses import FileResponse
from starlette.types import Receive, Scope, Send


class ExportDownloadScratch:
    """Keep an unlinked 0600 file alive until streaming finishes or the process dies.

    Linux TemporaryFile uses O_TMPFILE or unlinks its fallback before returning,
    so publisher plaintext is never written to a named directory entry. The
    process-local descriptor path preserves FileResponse's range support.
    """

    def __init__(self):
        self.file = tempfile.TemporaryFile(mode="w+b", prefix="threatlens-export-download-")
        try:
            descriptor = self.file.fileno()
            self.path = Path(f"/proc/self/fd/{descriptor}")
            metadata = os.fstat(descriptor)
            visible = self.path.stat()
            if metadata.st_nlink != 0 or (metadata.st_dev, metadata.st_ino) != (visible.st_dev, visible.st_ino):
                raise RuntimeError("Anonymous export downloads require Linux process-local descriptors")
        except BaseException:
            self.close()
            raise

    def close(self):
        self.file.close()

    def response(self, *, media_type, filename, headers):
        self.file.flush()
        return _DownloadResponse(self, media_type=media_type, filename=filename, headers=headers)


class _DownloadResponse(FileResponse):
    def __init__(self, scratch, **kwargs):
        self.scratch = scratch
        super().__init__(scratch.path, stat_result=os.fstat(scratch.file.fileno()), **kwargs)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # A process-local FD must be consumed here, not handed to an ASGI
        # path-send implementation that might defer reading until after return.
        extensions = dict(scope.get("extensions", {}))
        extensions.pop("http.response.pathsend", None)
        try:
            await super().__call__({**scope, "extensions": extensions}, receive, send)
        finally:
            self.scratch.close()
