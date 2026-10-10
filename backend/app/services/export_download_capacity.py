"""Reserve anonymous download storage across processes sharing a directory."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path
from typing import BinaryIO


class ExportDownloadCapacityUnavailable(RuntimeError):
    """The durable export can be retried when temporary storage is available."""


def reserve_download_storage(
    file: BinaryIO, *, directory: str, size: int, headroom: int
) -> None:
    """Allocate real blocks under a shared lock, without a reservation ledger.

    The allocation remains owned by the anonymous file through transfer. Closing
    it, including process death, releases the reservation. A nonblocking lock
    prevents another API process from checking the same free bytes concurrently.
    Other temporary-file users are accounted for by filesystem free space.
    """
    if size <= 0:
        raise ValueError("Download storage reservation must be positive")
    lock_fd = None
    try:
        lock_fd = os.open(
            Path(directory) / "threatlens-export-download-admission.lock",
            os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
        )
        metadata = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise ExportDownloadCapacityUnavailable("Invalid download admission lock")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        space = os.fstatvfs(file.fileno())
        block_size = space.f_frsize
        required = ((size + block_size - 1) // block_size) * block_size
        if required + headroom > space.f_bavail * block_size:
            raise ExportDownloadCapacityUnavailable("Insufficient download storage")
        # A sparse truncate is insufficient: its bytes could be promised to
        # several downloads before any of them writes its first chunk.
        os.posix_fallocate(file.fileno(), 0, size)
    except OSError as exc:
        raise ExportDownloadCapacityUnavailable("Download storage is unavailable") from exc
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
