"""Small storage admission tests; no large artifacts or filesystem exhaustion."""

import errno
import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.services import export_download_capacity as capacity
from app.services.export_download_capacity import ExportDownloadCapacityUnavailable
from app.services.export_download_scratch import ExportDownloadScratch


@pytest.fixture(autouse=True)
def isolated_download_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


def test_storage_is_physically_reserved_until_the_anonymous_file_closes(tmp_path):
    scratch = ExportDownloadScratch(reserved_bytes=8192)
    descriptor = scratch.file.fileno()
    assert os.fstat(descriptor).st_size == 8192
    assert os.fstat(descriptor).st_blocks * 512 >= 8192
    assert os.fstat(descriptor).st_nlink == 0
    scratch.close()
    with pytest.raises(OSError):
        os.fstat(descriptor)
    # Only the non-sensitive coordination inode persists, never plaintext.
    assert [path.name for path in tmp_path.iterdir()] == ["threatlens-export-download-admission.lock"]


def test_admission_accounts_for_other_allocations_and_keeps_headroom(monkeypatch):
    available = [5]
    monkeypatch.setattr(get_settings(), "export_download_scratch_headroom_bytes", 8192)
    monkeypatch.setattr(capacity.os, "fstatvfs", lambda _fd: SimpleNamespace(f_frsize=4096, f_bavail=available[0]))
    allocate = os.posix_fallocate

    def reserve(fd, offset, size):
        allocate(fd, offset, size)
        available[0] -= (size + 4095) // 4096

    monkeypatch.setattr(capacity.os, "posix_fallocate", reserve)
    scratch = ExportDownloadScratch(reserved_bytes=8192)
    try:
        with pytest.raises(ExportDownloadCapacityUnavailable, match="Insufficient"):
            ExportDownloadScratch(reserved_bytes=8192)
        assert available[0] == 3
    finally:
        scratch.close()


def test_another_api_process_cannot_pass_an_in_progress_admission(tmp_path):
    lock_path = tmp_path / "threatlens-export-download-admission.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    code = """
from app.services.export_download_scratch import ExportDownloadScratch
from app.services.export_download_capacity import ExportDownloadCapacityUnavailable
try:
    ExportDownloadScratch(reserved_bytes=4096)
except ExportDownloadCapacityUnavailable:
    print('busy')
else:
    raise RuntimeError('cross-process admission was not serialized')
"""
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        child = subprocess.run(
            [sys.executable, "-c", code], cwd=tmp_path,
            env={"TMPDIR": str(tmp_path), "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
            text=True, capture_output=True, timeout=10, check=True,
        )
        assert child.stdout.strip() == "busy"
    finally:
        os.close(lock_fd)
    scratch = ExportDownloadScratch(reserved_bytes=4096)
    scratch.close()


@pytest.mark.parametrize("error", [errno.ENOSPC, errno.EOPNOTSUPP])
def test_failed_reservation_closes_file_and_releases_admission(monkeypatch, error):
    handles = []
    create = tempfile.TemporaryFile
    allocate = os.posix_fallocate

    def capture(**kwargs):
        handle = create(**kwargs)
        handles.append(handle)
        return handle

    def unavailable(*_args):
        raise OSError(error, "storage unavailable")

    monkeypatch.setattr(tempfile, "TemporaryFile", capture)
    monkeypatch.setattr(capacity.os, "posix_fallocate", unavailable)
    with pytest.raises(ExportDownloadCapacityUnavailable):
        ExportDownloadScratch(reserved_bytes=4096)
    assert handles[0].closed
    monkeypatch.setattr(capacity.os, "posix_fallocate", allocate)
    scratch = ExportDownloadScratch(reserved_bytes=4096)
    scratch.close()


def test_admission_never_follows_a_symlink(tmp_path):
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("keep")
    (tmp_path / "threatlens-export-download-admission.lock").symlink_to(unrelated)
    with pytest.raises(ExportDownloadCapacityUnavailable):
        ExportDownloadScratch(reserved_bytes=4096)
    assert unrelated.read_text() == "keep"
