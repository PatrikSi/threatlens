import copy
from datetime import datetime, timedelta, timezone
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts/operations"))
from evidence import check_recovery, read_json, read_key, sign, verify
from fleet import docker, memory_bytes, observe_fleet
from monitor import atomic_json, collect, prometheus, reconcile, send_receiver, validate_config
from record_evidence import apply_drill_evidence, key_probe, verify_key_probe
from qualification_workload import validate_export
from qualification_runtime import DisposableTopology

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def config():
    return {"schema_version": 1, "deployment": "test", "compose_project": "test",
            "services": {"api": {"min_instances": 1, "memory_warning_ratio": .8}}}


class HostMonitorTests(unittest.TestCase):
    def test_docker_output_cap_terminates_the_owned_process(self):
        real_popen = subprocess.Popen
        children = []
        def excessive(*_args, **kwargs):
            process = real_popen([sys.executable, "-c",
                "import sys,time; sys.stdout.buffer.write(b'x' * 2097152); sys.stdout.flush(); time.sleep(30)"], **kwargs)
            children.append(process)
            return process
        with patch("fleet.subprocess.Popen", side_effect=excessive), self.assertRaisesRegex(RuntimeError, "size limit"):
            docker("inspect")
        self.assertIsNotNone(children[0].poll())

    def test_malformed_objectives_fail_with_controlled_validation(self):
        for changes in ({"storage": {"disk": None}}, {"storage": {"disk": {"path": 3}}},
                        {"recovery": None}, {"receiver": []}, {"receiver": {"url": None}},
                        {"recovery": {"primary_storage_domain": None}}, {"services": {1: {}}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_config({**config(), **changes})

    def test_configuration_rejects_unbounded_names_and_unsafe_receivers(self):
        self.assertEqual(validate_config(config())["deployment"], "test")
        for value in ("bad\nname", "x" * 65, "../../secret"):
            with self.assertRaises(ValueError):
                validate_config({**config(), "deployment": value})
        for url in ("http://example.com", "https://user:secret@example.com", "https://example.com/?key=secret"):
            with self.assertRaises(ValueError):
                validate_config({**config(), "receiver": {"url": url, "signing_key": "/key"}})

    def test_incidents_are_deduplicated_recovered_and_epoch_protected(self):
        condition = {"scope": "fleet", "entity": "api", "code": "memory_pressure"}
        first, events = reconcile(config(), {}, [condition], now=NOW)
        self.assertEqual(len(events), 1)
        second, events = reconcile(config(), first, [condition], now=NOW + timedelta(minutes=1))
        self.assertEqual(events, [])
        last, events = reconcile(config(), second, [], now=NOW + timedelta(minutes=2))
        self.assertEqual(events[0]["state"], "resolved")
        self.assertEqual(last["active"], {})
        fresh, _ = reconcile(config(), {}, [condition], now=NOW)
        self.assertNotEqual(fresh["epoch"], first["epoch"])

    def test_observation_failure_cannot_falsely_resolve_previous_fleet_incidents(self):
        first, _ = reconcile(config(), {}, [{"scope": "fleet", "entity": "api", "code": "container_oom"}], now=NOW)
        report, state = collect(config(), first, now=NOW, fleet=lambda _: (_ for _ in ()).throw(RuntimeError()),
            storage=lambda _: ({}, []), recovery=lambda *_a, **_kw: ({}, []))
        self.assertFalse(report["fleet_available"])
        self.assertEqual({v["code"] for v in state["active"].values()}, {"container_oom", "observation_unavailable"})
        self.assertNotIn('"state": "resolved"', json.dumps(report))
        self.assertFalse(report["application_health_inferred"])

    def test_fleet_collects_all_replicas_and_discloses_unknown_budget(self):
        rows = [{"id": "a" * 64, "service": "api", "running": True, "status": "running", "oom": False,
                 "restarts": 0, "limit": 100 * 1024**2, "health": "healthy"},
                {"id": "b" * 64, "service": "api", "running": True, "status": "running", "oom": True,
                 "restarts": 3, "limit": 100 * 1024**2, "health": "unhealthy"}]
        def docker(*args):
            if args[0] == "ps":
                return "a" * 12 + "\n" + "b" * 12
            if args[0] == "inspect":
                return "\n".join(json.dumps(row) for row in rows)
            return "\n".join(json.dumps({"ID": letter * 12, "MemUsage": "90MiB / 100MiB"}) for letter in "ab")
        metrics, incidents, counters = observe_fleet(config(), invoke=docker)
        self.assertEqual(metrics["api"]["running"], 2)
        self.assertEqual(metrics["api"]["memory_bytes"], 180 * 1024**2)
        self.assertEqual({row["code"] for row in incidents}, {"memory_pressure", "unhealthy_service", "container_oom"})
        self.assertEqual(len(counters), 2)
        rows[0]["limit"] = 0
        self.assertIn("memory_budget_unknown", [i["code"] for i in observe_fleet(config(), invoke=docker)[1]])

    def test_hot_replica_is_not_hidden_by_idle_replicas(self):
        rows = [{"id": letter * 64, "service": "api", "running": True, "status": "running", "oom": False,
                 "restarts": 0, "limit": 100 * 1024**2, "health": "none"} for letter in "ab"]
        def invoke(*args):
            if args[0] == "ps":
                return "a" * 12 + "\n" + "b" * 12
            if args[0] == "inspect":
                return "\n".join(json.dumps(row) for row in rows)
            return "\n".join(json.dumps({"ID": letter * 12, "MemUsage": f"{used}MiB / 100MiB"})
                             for letter, used in (("a", 99), ("b", 1)))
        metrics, incidents, _ = observe_fleet(config(), invoke=invoke)
        self.assertEqual(metrics["api"]["memory_ratio"], .5)
        self.assertEqual(metrics["api"]["max_instance_memory_ratio"], .99)
        self.assertEqual(incidents[0]["code"], "memory_pressure")

    def test_fifo_and_symlink_artifacts_never_block_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory) / "fifo"
            os.mkfifo(fifo)
            for reader in (read_json, read_key):
                with self.assertRaises(ValueError):
                    reader(fifo)
            target = Path(directory) / "target"
            atomic_json(target, {"schema_version": 1})
            link = Path(directory) / "link"
            link.symlink_to(target)
            with self.assertRaises(OSError):
                read_json(link)

    def test_old_drill_receipts_cannot_refresh_evidence_age(self):
        finished = NOW - timedelta(days=366)
        result = {"schema_version": 1, "kind": "host_loss_qualification", "status": "passed",
            "deployment": "test", "run_id": "old-drill", "archive_sha256": "a" * 64,
            "finished_at": finished.isoformat(), "source_environment_removed": True,
            "restore_verified": True, "outbound_quarantined": True, "independent_key_used": True}
        receipt = {"deployment": "test", "archive_sha256": "a" * 64, "verified_at": NOW.isoformat()}
        apply_drill_evidence(receipt, result, now=NOW)
        self.assertEqual(receipt["verified_at"], finished.isoformat())
        # Repeated verification in the same second or a later release is identical.
        apply_drill_evidence(receipt, result, now=NOW + timedelta(days=2))
        self.assertEqual(receipt["verified_at"], finished.isoformat())
        for changes in ({"deployment": "other"}, {"archive_sha256": "b" * 64},
                        {"run_id": ""}, {"finished_at": (NOW + timedelta(seconds=1)).isoformat()}):
            with self.assertRaises(ValueError):
                apply_drill_evidence(receipt, {**result, **changes}, now=NOW)

    def test_export_qualification_requires_exact_disjoint_source_content(self):
        rows = [{"id": key, "article": {"text": "full text"}} for key in ("a", "b")]
        def body(values):
            return b"\n".join(json.dumps(row).encode() for row in values)
        self.assertEqual(validate_export(body(rows), expected={"a", "b"}, article_chars=9), {"a", "b"})
        for changed in (rows[:1], rows + rows[:1], [rows[0], {"id": "c", "article": {"text": "full text"}}],
                        [rows[0], {"id": "b", "article": {"text": "truncated"[:-1]}}]):
            with self.assertRaises(RuntimeError):
                validate_export(body(changed), expected={"a", "b"}, article_chars=9)

    def test_exited_worker_is_a_failed_qualification_not_zero_memory(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as directory:
            topology = DisposableTopology(Path(directory))
            process = Mock()
            process.poll.return_value = 1
            topology.processes["maintenance"] = process
            with self.assertRaisesRegex(RuntimeError, "exited"):
                topology.memory()

    def test_receiver_timeouts_preserve_outbox_and_redirects_are_not_acknowledged(self):
        selected = {**config(), "receiver": {"url": "https://monitor.example.com/receive", "signing_key": "/key"}}
        state, _ = reconcile(selected, {}, [{"scope": "fleet", "entity": "api", "code": "missing_service"}], now=NOW)
        state2, events = reconcile(selected, state, [{"scope": "fleet", "entity": "api", "code": "missing_service"}], now=NOW)
        self.assertEqual(state["pending"], state2["pending"])
        self.assertEqual(events, [])
        with patch("monitor.read_key", return_value=b"x" * 32), patch("monitor.subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = b"302"
            self.assertFalse(send_receiver(selected["receiver"], {"events": state["pending"]}))
            run.return_value.stdout = b"204"
            self.assertTrue(send_receiver(selected["receiver"], {"events": state["pending"]}))
            self.assertEqual(run.call_args.kwargs["timeout"], 12)

    def test_metrics_never_leak_paths_containers_or_unknown_values_as_zero(self):
        report = {"deployment": "test", "observed_at": NOW.isoformat(), "fleet_available": True,
            "active_incidents": {}, "pending_delivery_count": 0, "fleet": {"api": {"memory_bytes": None, "running": 1}},
            "storage": {"database": {"free_bytes": 2048}}, "recovery": {}}
        text = prometheus(report)
        self.assertNotIn("memory_bytes", text)
        self.assertIn('entity="api"', text)
        self.assertNotIn("container_id", text)

    def test_atomic_state_and_private_key_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            atomic_json(path, {"value": 1})
            self.assertEqual(json.loads(path.read_text()), {"value": 1})
            key = Path(directory) / "key"
            key.write_bytes(b"x" * 32)
            key.chmod(0o644)
            with self.assertRaises(ValueError):
                read_key(key)
            key.chmod(0o600)
            self.assertEqual(read_key(key), b"x" * 32)
            link = Path(directory) / "link"
            link.symlink_to(key)
            with self.assertRaises(OSError):
                read_key(link)

    def test_signed_receipts_reject_tampering_future_dates_and_local_copy_as_offhost(self):
        key = b"x" * 32
        receipt = {"schema_version": 1, "deployment": "test", "issuer": "receiver", "kind": "offhost_copy",
            "storage_domain": "remote", "verified_at": NOW.isoformat(), "archive_sha256": "a" * 64,
            "archive_size_bytes": 100, "backup_snapshot_at": (NOW - timedelta(hours=1)).isoformat()}
        signed = sign(receipt, key)
        verify(signed, key, deployment="test", kind="offhost_copy", now=NOW)
        changed = copy.deepcopy(signed)
        changed["archive_sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            verify(changed, key, deployment="test", kind="offhost_copy", now=NOW)
        with self.assertRaises(ValueError):
            verify(sign({**receipt, "verified_at": (NOW + timedelta(hours=1)).isoformat()}, key), key,
                   deployment="test", kind="offhost_copy", now=NOW)
        with tempfile.TemporaryDirectory() as directory:
            receipt_path, key_path = Path(directory) / "receipt", Path(directory) / "key"
            atomic_json(receipt_path, signed)
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            objective = {"receipt": str(receipt_path), "verification_key": str(key_path),
                         "max_age_seconds": 600, "storage_domain": "remote"}
            metrics, incidents = check_recovery({"primary_storage_domain": "remote", "evidence": {"offhost_copy": objective}}, deployment="test", now=NOW)
            self.assertFalse(metrics["offhost_copy"]["available"])
            self.assertEqual(incidents[0]["code"], "evidence_missing_or_invalid")
            metrics, incidents = check_recovery({"primary_storage_domain": "primary", "evidence": {"offhost_copy": objective}}, deployment="test", now=NOW)
            self.assertTrue(metrics["offhost_copy"]["available"])
            self.assertEqual(incidents[0]["code"], "evidence_overdue")

    def test_recovered_key_must_actually_decrypt_independent_probe(self):
        probe = key_probe(b"a" * 48)
        verify_key_probe(probe, b"a" * 48)
        self.assertNotIn("a" * 48, json.dumps(probe))
        with self.assertRaises(ValueError):
            verify_key_probe(probe, b"b" * 48)

    def test_local_drill_cannot_satisfy_an_independent_deployment_objective(self):
        key = b"x" * 32
        receipt = {"schema_version": 1, "deployment": "test", "issuer": "reviewer", "kind": "host_loss_drill",
            "storage_domain": "recovery", "verified_at": NOW.isoformat(), "archive_sha256": "a" * 64,
            "archive_size_bytes": 100, "backup_snapshot_at": NOW.isoformat(),
            "source_environment_removed": True, "restore_verified": True, "outbound_quarantined": True,
            "independent_key_used": True, "qualification_scope": "disposable_local_fault_domain_simulation",
            "production_qualified": False, "backend_image_id": "sha256:" + "a" * 64,
            "backend_source_sha256": "b" * 64, "migration_head": "0115_reviewed_publications"}
        with tempfile.TemporaryDirectory() as directory:
            receipt_path, key_path = Path(directory) / "receipt", Path(directory) / "key"
            atomic_json(receipt_path, sign(receipt, key))
            key_path.write_bytes(key)
            key_path.chmod(0o600)
            objective = {"receipt": str(receipt_path), "verification_key": str(key_path),
                         "max_age_seconds": 600, "storage_domain": "recovery"}
            policy = {"primary_storage_domain": "primary", "evidence": {"host_loss_drill": objective}}
            metrics, _ = check_recovery(policy, deployment="test", now=NOW)
            self.assertFalse(metrics["host_loss_drill"]["available"])
            objective["qualification_scope"] = "disposable_local_fault_domain_simulation"
            metrics, incidents = check_recovery(policy, deployment="test", now=NOW)
            self.assertTrue(metrics["host_loss_drill"]["available"])
            self.assertEqual(incidents, [])
            objective["backend_source_sha256"] = "c" * 64
            self.assertFalse(check_recovery(policy, deployment="test", now=NOW)[0]["host_loss_drill"]["available"])
        with self.assertRaises(ValueError):
            verify(sign({**receipt, "production_qualified": True}, key), key, deployment="test", kind="host_loss_drill", now=NOW)

    def test_memory_units_are_explicit(self):
        self.assertEqual(memory_bytes("1.5GiB"), int(1.5 * 1024**3))
        self.assertEqual(memory_bytes("1GB"), 10**9)
        with self.assertRaises(ValueError):
            memory_bytes("not a measurement")


if __name__ == "__main__":
    unittest.main()
