"""Deterministic pacing controls and bounded shared-start coordination."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import inspect
import threading

import pytest

from tests.capacity import sustained


class Clock:
    def __init__(self, now=100.0):
        self.now = now

    def monotonic(self):
        return self.now

    def sleep(self, delay):
        self.now += delay


def test_delayed_executor_entry_does_not_rebase_the_phase(monkeypatch):
    clock = Clock(now=107.0)
    monkeypatch.setattr(sustained, "time", clock)
    stopped = threading.Event()
    starts = []
    gate = SimpleNamespace(wait=lambda: 100.0, abort=lambda: None)
    # Exercise the old independent-epoch implementation for before-fix proof,
    # too: its delayed executor entry incorrectly adds another half second.
    arguments = {"start_gate": gate} if "start_gate" in inspect.signature(sustained.paced_lane).parameters else {}

    def operation(_index):
        starts.append(clock.now)
        stopped.set()

    assert sustained.paced_lane(operation, duration_seconds=10, interval_seconds=2,
                                initial_delay_seconds=0.5, stop=stopped, **arguments) == 1
    assert starts == [107.0]


def test_late_executor_participant_cannot_start_other_lanes(monkeypatch):
    monkeypatch.setattr(sustained.time, "monotonic", lambda: 100.0)
    gate = sustained.SharedPacedLaneStart(participants=5, timeout_seconds=1)
    entered = [threading.Event() for _ in range(4)]
    completed = [threading.Event() for _ in range(4)]

    def waiting(index):
        entered[index].set()
        epoch = gate.wait()
        completed[index].set()
        return epoch

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(waiting, index) for index in range(4)]
        try:
            assert all(event.wait(timeout=1) for event in entered)
            assert not any(event.is_set() for event in completed)
            last = executor.submit(gate.wait)
            assert [future.result(timeout=2) for future in [*futures, last]] == [100.0] * 5
        finally:
            gate.abort()


@pytest.mark.parametrize("offset,interval", [(0, 2), (0.5, 2), (0.25, 2), (0, 10), (0, 5)])
def test_all_five_lane_offsets_use_the_common_epoch(monkeypatch, offset, interval):
    clock = Clock()
    monkeypatch.setattr(sustained, "time", clock)
    starts = []
    gate = SimpleNamespace(wait=lambda: 100.0, abort=lambda: None)

    def operation(index):
        starts.append((index, clock.now))
        clock.now += 0.1

    count = sustained.paced_lane(operation, duration_seconds=6, interval_seconds=interval,
                                initial_delay_seconds=offset, start_gate=gate)
    expected = []
    due = 100.0 + offset
    while due < 106.0:
        expected.append((len(expected), due))
        due += interval
    assert starts == expected
    assert count == len(expected)
    assert clock.now == 106.0


def test_delayed_wakeup_does_not_extend_the_common_duration(monkeypatch):
    clock = Clock(now=107.0)
    monkeypatch.setattr(sustained, "time", clock)
    starts = []
    gate = SimpleNamespace(wait=lambda: 100.0, abort=lambda: None)
    assert sustained.paced_lane(starts.append, duration_seconds=6, interval_seconds=2,
                                start_gate=gate) == 0
    assert starts == []


def test_shared_epoch_preserves_no_catchup_after_slow_operations(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sustained, "time", clock)
    starts = []
    gate = SimpleNamespace(wait=lambda: 100.0, abort=lambda: None)

    def operation(index):
        starts.append((index, clock.now))
        clock.now += 3.0

    assert sustained.paced_lane(operation, duration_seconds=6, interval_seconds=2,
                                start_gate=gate) == 2
    assert starts == [(0, 100.0), (1, 103.0)]


def test_missing_lane_breaks_the_start_gate_within_its_timeout():
    gate = sustained.SharedPacedLaneStart(participants=5, timeout_seconds=0.05)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(gate.wait) for _ in range(4)]
        for future in futures:
            with pytest.raises(RuntimeError, match="shared start gate"):
                future.result(timeout=1)
    with pytest.raises(RuntimeError, match="shared start gate"):
        gate.wait()


def test_cancelled_lane_aborts_waiting_participants():
    gate = sustained.SharedPacedLaneStart(participants=5, timeout_seconds=1)
    stopped = threading.Event()
    stopped.set()
    starts = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(gate.wait) for _ in range(4)]
        assert sustained.paced_lane(starts.append, duration_seconds=6, interval_seconds=2,
                                    start_gate=gate, stop=stopped) == 0
        for future in futures:
            with pytest.raises(RuntimeError, match="shared start gate"):
                future.result(timeout=1)
    assert starts == []


def test_default_independent_lane_start_and_stop_are_preserved(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sustained, "time", clock)
    starts = []
    stopped = threading.Event()

    def operation(index):
        starts.append((index, clock.now))
        stopped.set()

    assert sustained.paced_lane(operation, duration_seconds=6, interval_seconds=2,
                                stop=stopped) == 1
    assert starts == [(0, 100.0)]


def test_stop_after_admission_prevents_the_first_operation(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sustained, "time", clock)
    stopped = threading.Event()
    starts = []

    def admit():
        stopped.set()
        return 100.0

    gate = SimpleNamespace(wait=admit, abort=lambda: None)
    assert sustained.paced_lane(starts.append, duration_seconds=6, interval_seconds=2,
                                start_gate=gate, stop=stopped) == 0
    assert starts == []


def test_epoch_action_failure_breaks_waiting_participants(monkeypatch):
    def unavailable_clock():
        raise ValueError("epoch unavailable")

    monkeypatch.setattr(sustained.time, "monotonic", unavailable_clock)
    gate = sustained.SharedPacedLaneStart(participants=2, timeout_seconds=1)
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(gate.wait) for _ in range(2)]
        errors = []
        for future in futures:
            with pytest.raises((ValueError, RuntimeError)) as error:
                future.result(timeout=1)
            errors.append(type(error.value))
    assert set(errors) == {ValueError, RuntimeError}


def test_shared_start_does_not_swallow_operation_failure(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(sustained, "time", clock)
    gate = SimpleNamespace(wait=lambda: 100.0, abort=lambda: None)

    def failed(_index):
        raise ValueError("operation failed")

    with pytest.raises(ValueError, match="operation failed"):
        sustained.paced_lane(failed, duration_seconds=6, interval_seconds=2, start_gate=gate)
