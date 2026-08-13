import uuid

import pytest
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.services import export_lock


class FakeLockRedis:
    def __init__(self, *, acquire: bool = True, fail: bool = False):
        self.acquire = acquire
        self.fail = fail
        self.released = False

    def set(self, *_args, **_kwargs):
        if self.fail:
            raise RedisError("unavailable")
        return self.acquire

    def eval(self, *_args, **_kwargs):
        self.released = True
        return 1


def test_export_lock_is_released(monkeypatch: pytest.MonkeyPatch):
    backend = FakeLockRedis()
    monkeypatch.setattr(export_lock, "redis_client_from_url", lambda *_args, **_kwargs: backend)

    with export_lock.acquire_export_lock(user_id=uuid.uuid4(), settings=get_settings()):
        assert backend.released is False

    assert backend.released is True


def test_export_lock_rejects_overlapping_work(monkeypatch: pytest.MonkeyPatch):
    backend = FakeLockRedis(acquire=False)
    monkeypatch.setattr(export_lock, "redis_client_from_url", lambda *_args, **_kwargs: backend)

    with pytest.raises(export_lock.ExportAlreadyRunningError):
        with export_lock.acquire_export_lock(user_id=uuid.uuid4(), settings=get_settings()):
            pass


def test_export_lock_reports_redis_failure(monkeypatch: pytest.MonkeyPatch):
    backend = FakeLockRedis(fail=True)
    monkeypatch.setattr(export_lock, "redis_client_from_url", lambda *_args, **_kwargs: backend)

    with pytest.raises(export_lock.ExportLockUnavailableError):
        with export_lock.acquire_export_lock(user_id=uuid.uuid4(), settings=get_settings()):
            pass
