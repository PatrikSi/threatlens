import signal
import subprocess

from app.services.beat_heartbeat import BeatHeartbeatSnapshot
from app.tasks import beat_watchdog


class _Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Process:
    def __init__(self, poll_results):
        self.poll_results = iter(poll_results)
        self.return_code = None
        self.signals = []
        self.killed = False
        self.wait_error = None

    def poll(self):
        if self.return_code is not None:
            return self.return_code
        return next(self.poll_results, None)

    def send_signal(self, signal_number):
        self.signals.append(signal_number)

    def wait(self, timeout):
        if self.wait_error is not None:
            error = self.wait_error
            self.wait_error = None
            raise error
        self.return_code = -signal.SIGTERM
        return self.return_code

    def kill(self):
        self.killed = True
        self.return_code = -signal.SIGKILL


def _snapshot(*, ok, reason):
    return BeatHeartbeatSnapshot(ok=ok, heartbeat_at=None, age_seconds=None, reason=reason)


def test_monitor_stops_beat_and_exits_nonzero_when_heartbeat_is_unhealthy():
    clock = _Clock()
    process = _Process([None])

    result = beat_watchdog.monitor_beat_process(
        process,
        heartbeat_loader=lambda: _snapshot(ok=False, reason="stale"),
        startup_grace_seconds=0,
        check_interval_seconds=15,
        terminate_timeout_seconds=10,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result == 1
    assert process.signals == [signal.SIGTERM]


def test_monitor_allows_startup_grace_before_checking_heartbeat():
    clock = _Clock()
    process = _Process([None, None, 7])
    heartbeat_checks = []

    result = beat_watchdog.monitor_beat_process(
        process,
        heartbeat_loader=lambda: heartbeat_checks.append(True) or _snapshot(ok=False, reason="missing"),
        startup_grace_seconds=30,
        check_interval_seconds=15,
        terminate_timeout_seconds=10,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )

    assert result == 7
    assert heartbeat_checks == []
    assert process.signals == []


def test_monitor_stops_cleanly_when_container_shutdown_is_requested():
    process = _Process([None])

    result = beat_watchdog.monitor_beat_process(
        process,
        heartbeat_loader=lambda: _snapshot(ok=True, reason="healthy"),
        startup_grace_seconds=240,
        check_interval_seconds=15,
        terminate_timeout_seconds=10,
        shutdown_requested=lambda: True,
    )

    assert result == 0
    assert process.signals == [signal.SIGTERM]


def test_stop_process_force_kills_beat_after_shutdown_timeout():
    process = _Process([None])
    process.wait_error = subprocess.TimeoutExpired(cmd="celery beat", timeout=10)

    beat_watchdog.stop_process(process, timeout_seconds=10)

    assert process.signals == [signal.SIGTERM]
    assert process.killed is True


def test_check_beat_returns_failure_for_unhealthy_snapshot(monkeypatch):
    monkeypatch.setattr(
        beat_watchdog,
        "load_scheduler_heartbeat",
        lambda _settings: _snapshot(ok=False, reason="redis_unavailable"),
    )

    assert beat_watchdog.check_beat(object()) == 1
