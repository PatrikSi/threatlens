"""Observed restart events survive incident resolution and fleet outages."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/operations"))
from monitor import collect, prometheus

NOW = datetime(2026, 9, 27, tzinfo=timezone.utc)
CONFIG = {"deployment": "test", "services": {"worker": {}}}


def observe(previous, minute, count=0, identity="first", *, available=True, config=CONFIG):
    def fleet(_config):
        if not available:
            raise RuntimeError("unavailable")
        return ({}, [], {identity: {"service": "worker", "restarts": count}})
    return collect(config, previous, now=NOW + timedelta(minutes=minute), fleet=fleet,
                   storage=lambda _: ({}, []), recovery=lambda *_args, **_kwargs: ({}, []))


class RestartObservationTests(unittest.TestCase):
    def test_single_restart_remains_observable_after_incident_resolves(self):
        report, state = observe({}, 0)
        self.assertEqual(report["last_restarts"], {})
        report, state = observe(state, 1, 1)
        self.assertEqual(len(report["active_incidents"]), 1)
        timestamp = (NOW + timedelta(minutes=1)).timestamp()
        report, state = observe(state, 2, 1)
        self.assertEqual(report["active_incidents"], {})
        self.assertEqual(report["last_restarts"], {"worker": timestamp})
        self.assertIn(f'threatlens_host_last_restart_timestamp_seconds{{deployment="test",entity="worker"}} {timestamp}', prometheus(report))
        report, state = observe(state, 7, 1)
        self.assertEqual(state["last_restarts"], {"worker": timestamp})

    def test_baselines_resets_and_recreation_do_not_invent_new_events(self):
        _, state = observe({}, 0, 3)
        self.assertEqual(state["last_restarts"], {})
        _, state = observe(state, 1, 0)
        self.assertEqual(state["last_restarts"], {})
        _, state = observe(state, 2, 1)
        timestamp = (NOW + timedelta(minutes=2)).timestamp()
        _, state = observe(state, 3, 0, "replacement")
        self.assertEqual(state["last_restarts"], {"worker": timestamp})
        _, state = observe(state, 4, 1, "replacement")
        self.assertEqual(state["last_restarts"], {"worker": (NOW + timedelta(minutes=4)).timestamp()})

    def test_unavailable_observation_retains_last_event_without_refreshing_it(self):
        _, state = observe({}, 0)
        _, state = observe(state, 1, 1)
        report, state = observe(state, 2, available=False)
        self.assertFalse(report["fleet_available"])
        self.assertEqual(report["last_restarts"], {"worker": (NOW + timedelta(minutes=1)).timestamp()})
        self.assertIn("threatlens_host_last_restart_timestamp_seconds", prometheus(report))
        _, state = observe(state, 3, 1)
        self.assertEqual(state["last_restarts"], report["last_restarts"])

    def test_deployment_change_discards_previous_identity_and_service_removal_prunes_events(self):
        _, state = observe({}, 0)
        _, state = observe(state, 1, 1)
        _, changed = observe(state, 2, 2, config={**CONFIG, "deployment": "another"})
        self.assertEqual(changed["last_restarts"], {})
        report, pruned = collect({**CONFIG, "services": {}}, state, now=NOW,
            fleet=lambda _: ({}, [], {}), storage=lambda _: ({}, []), recovery=lambda *_args, **_kwargs: ({}, []))
        self.assertEqual(pruned["last_restarts"], {})
        self.assertNotIn("threatlens_host_last_restart_timestamp_seconds", prometheus(report))


if __name__ == "__main__":
    unittest.main()
