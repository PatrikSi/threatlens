import json
import os
from pathlib import Path
import select
import stat
import subprocess
import sys
import tempfile

import anyio
import pytest

from app.services.export_download_scratch import ExportDownloadScratch


@pytest.fixture(autouse=True)
def isolated_download_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


@pytest.mark.parametrize("fallback", [False, True])
def test_plaintext_has_no_directory_entry_and_cannot_touch_other_files(tmp_path, monkeypatch, fallback):
    if fallback:
        monkeypatch.setattr(tempfile, "_O_TMPFILE_WORKS", False)
    unrelated = tmp_path / "unrelated"
    unrelated.write_text("keep me")
    link = tmp_path / "threatlens-export-download-symlink"
    link.symlink_to(unrelated)
    scratch = ExportDownloadScratch()
    try:
        scratch.file.write(b"private article")
        scratch.file.flush()
        metadata = os.fstat(scratch.file.fileno())
        assert metadata.st_nlink == 0
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert not os.get_inheritable(scratch.file.fileno())
        assert scratch.path.read_bytes() == b"private article"
        assert set(tmp_path.iterdir()) == {unrelated, link}
    finally:
        scratch.close()
    assert unrelated.read_text() == "keep me"
    assert link.is_symlink()


async def receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def test_stream_keeps_its_descriptor_until_all_bytes_are_sent(tmp_path):
    scratch = ExportDownloadScratch()
    scratch.file.write(b"private article contents")
    response = scratch.response(media_type="text/plain", filename="export.txt", headers={"Cache-Control": "no-store"})
    response.chunk_size = 4
    messages = []

    async def send(message):
        assert not scratch.file.closed
        # Another concurrent download's creation/cleanup cannot affect this one.
        other = ExportDownloadScratch()
        other.file.write(b"another active download")
        other.close()
        assert scratch.path.read_bytes() == b"private article contents"
        messages.append(message)

    anyio.run(response, {"type": "http", "method": "GET", "headers": [],
                         "extensions": {"http.response.pathsend": {}}}, receive, send)
    assert scratch.file.closed
    assert all(message["type"] != "http.response.pathsend" for message in messages)
    assert b"".join(message.get("body", b"") for message in messages) == b"private article contents"
    assert dict(messages[0]["headers"])[b"cache-control"] == b"no-store"
    assert list(tmp_path.iterdir()) == []


def test_range_download_preserves_file_response_semantics():
    scratch = ExportDownloadScratch()
    scratch.file.write(b"0123456789")
    response = scratch.response(media_type="text/plain", filename="export.txt", headers={})
    messages = []

    async def send(message):
        messages.append(message)

    anyio.run(response, {"type": "http", "method": "GET", "headers": [(b"range", b"bytes=3-7")]}, receive, send)
    assert messages[0]["status"] == 206
    assert dict(messages[0]["headers"])[b"content-range"] == b"bytes 3-7/10"
    assert b"".join(message.get("body", b"") for message in messages) == b"34567"
    assert scratch.file.closed


def test_disconnect_closes_the_descriptor():
    scratch = ExportDownloadScratch()
    scratch.file.write(b"private article")
    response = scratch.response(media_type="text/plain", filename="export.txt", headers={})

    async def send(message):
        if message["type"] == "http.response.body":
            raise RuntimeError("client disconnected")

    with pytest.raises(RuntimeError, match="client disconnected"):
        anyio.run(response, {"type": "http", "method": "GET", "headers": []}, receive, send)
    assert scratch.file.closed


def test_missing_process_descriptor_support_fails_before_any_plaintext(monkeypatch):
    real_temporary_file = tempfile.TemporaryFile
    owners = []

    def temporary_file(**kwargs):
        handle = real_temporary_file(**kwargs)
        owners.append(handle)
        return handle

    monkeypatch.setattr(tempfile, "TemporaryFile", temporary_file)
    monkeypatch.setattr(Path, "stat", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("procfs unavailable")))
    with pytest.raises(FileNotFoundError, match="procfs unavailable"):
        ExportDownloadScratch()
    assert len(owners) == 1 and owners[0].closed


def test_linked_file_fallback_fails_closed(tmp_path, monkeypatch):
    owners = []

    def named_file(**_kwargs):
        handle = tempfile.NamedTemporaryFile(mode="w+b", dir=tmp_path)
        owners.append(handle)
        return handle

    monkeypatch.setattr(tempfile, "TemporaryFile", named_file)
    with pytest.raises(RuntimeError, match="Anonymous export downloads require"):
        ExportDownloadScratch()
    assert len(owners) == 1 and owners[0].closed
    assert list(tmp_path.iterdir()) == []


def test_hard_process_death_releases_plaintext_without_a_cleanup_sweep(tmp_path):
    code = """
import json, os, signal
from app.services.export_download_scratch import ExportDownloadScratch
scratch = ExportDownloadScratch()
scratch.file.write(b'private article left during a killed API request')
scratch.file.flush()
print(json.dumps({'pid': os.getpid(), 'fd': scratch.file.fileno(),
                  'links': os.fstat(scratch.file.fileno()).st_nlink}), flush=True)
signal.pause()
"""
    child = subprocess.Popen([sys.executable, "-c", code], cwd=tmp_path,
                             env={"TMPDIR": str(tmp_path), "PYTHONPATH": str(Path(__file__).resolve().parents[2])},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert select.select([child.stdout], [], [], 10)[0], "scratch subprocess did not become ready"
        line = child.stdout.readline()
        assert line, child.stderr.read()
        record = json.loads(line)
        descriptor_path = Path(f"/proc/{record['pid']}/fd/{record['fd']}")
        assert descriptor_path.read_bytes().startswith(b"private article")
        assert record["links"] == 0
        assert list(tmp_path.iterdir()) == []
        child.kill()
        child.wait(timeout=5)
        assert not descriptor_path.exists()
        assert list(tmp_path.iterdir()) == []
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=5)
        child.stdout.close()
        child.stderr.close()
