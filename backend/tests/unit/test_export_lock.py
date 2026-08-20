import uuid
import time

import pytest
from redis.exceptions import RedisError

from app.core.config import get_settings
from app.services import export_lock


class FakeLockRedis:
    def __init__(self, *, acquire: bool = True, fail: bool = False):
        self.acquire = acquire
        self.fail = fail
        self.released = False
        self.renewals = 0

    def set(self, *_args, **_kwargs):
        if self.fail:
            raise RedisError("unavailable")
        return self.acquire

    def eval(self, script, *_args, **_kwargs):
        if "expire" in script:
            self.renewals += 1
        else:
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


def test_export_lock_is_renewed_during_slow_generation(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = FakeLockRedis()
    monkeypatch.setattr(
        export_lock, "redis_client_from_url", lambda *_args, **_kwargs: backend
    )
    monkeypatch.setattr(
        export_lock, "_lock_renewal_interval_seconds", lambda _ttl: 0.01
    )

    with export_lock.acquire_export_lock(
        user_id=uuid.uuid4(), settings=get_settings()
    ):
        time.sleep(0.03)

    assert backend.renewals >= 2
    assert backend.released is True


def test_export_lock_rejects_artifact_when_ownership_is_lost(
    monkeypatch: pytest.MonkeyPatch,
):
    backend = FakeLockRedis()
    monkeypatch.setattr(
        export_lock, "redis_client_from_url", lambda *_args, **_kwargs: backend
    )

    def _lost_lock(*_args, **_kwargs):
        return False

    monkeypatch.setattr(export_lock, "_renew_export_lock", _lost_lock)

    with pytest.raises(
        export_lock.ExportLockUnavailableError, match="expired before generation"
    ):
        with export_lock.acquire_export_lock(
            user_id=uuid.uuid4(), settings=get_settings()
        ):
            pass

    assert backend.released is True
