"""Protocol failures never turn an ambiguous external search into a relaunch."""

import copy
import importlib.util
import json
from pathlib import Path
import uuid
import pytest


def module(name="opensearch_connector"):
    path = (
        Path(__file__).resolve().parents[3]
        / "examples"
        / "automation-receiver"
        / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def configuration():
    return {
        "ledger_index": "threatlens-actions",
        "indices": ["security-logs-*"],
        "timestamp_field": "@timestamp",
        "indicator_fields": {"domain": "destination.domain.keyword"},
        "lookback_hours": 24,
        "allowed_handling_label_ids": ["public"],
        "team_id": "team-1",
    }


def event():
    return {
        "schema_version": "threatlens.automation.v1",
        "action_id": "stable-hunt-action",
        "event_type": "hunt.approved",
        "occurred_at": "2026-09-27T00:00:00Z",
        "data": {
            "execution": {"id": str(uuid.uuid4()), "webhook_id": str(uuid.uuid4())},
            "team_id": "team-1",
            "indicators_complete": True,
            "handling_label_ids": ["public"],
            "filter_metadata": {"hunt_review_status": "accepted"},
            "indicators": [
                {
                    "type": "domain",
                    "value": "evil.example",
                    "role": "malicious_infrastructure",
                    "analyst_verdict": "malicious",
                    "excluded": False,
                }
            ],
        },
    }


class Remote:
    def __init__(self, implementation):
        self.module = implementation
        self.record = None
        self.launches = 0
        self.lost_launch_response = False
        self.deleted = 0
        self.state = "RUNNING"

    def __call__(self, method, path, payload=None):
        if "_create/" in path:
            if self.record:
                raise self.module.RemoteError(409)
            self.record = copy.deepcopy(payload)
            return {"_seq_no": 1, "_primary_term": 1}
        if "/_doc/" in path:
            if not self.record:
                raise self.module.RemoteError(404)
            if method == "PUT":
                self.record = copy.deepcopy(payload)
            return {
                "_source": copy.deepcopy(self.record),
                "_seq_no": 1,
                "_primary_term": 1,
            }
        if method == "POST":
            self.launches += 1
            if self.lost_launch_response:
                raise OSError("Accepted search response lost")
        if method == "DELETE":
            self.deleted += 1
            return {"acknowledged": True}
        return {
            "id": "external-search-id",
            "state": self.state,
            "response": {
                "timed_out": False,
                "_shards": {"failed": 0},
                "hits": {
                    "total": {"value": 1, "relation": "eq"},
                    "hits": [
                        {
                            "_index": "security-logs-test",
                            "_id": "document-1",
                            "_source": {"secret": "never-return"},
                        }
                    ],
                },
            },
        }


def test_launch_then_recovery_polls_same_external_job_and_maps_bounded_findings():
    impl = module()
    remote = Remote(impl)
    connector = impl.Connector(configuration(), remote)
    envelope = event()
    assert connector.advance(envelope) == ("running", None)
    remote.state = "STORE_RESIDENT"
    status, findings = impl.Connector(configuration(), remote).advance(envelope)
    assert status == "completed" and remote.launches == 1
    assert "document-1" in findings and "never-return" not in findings
    assert json.loads(findings)["external_search_id"] == "external-search-id"
    connector.withdraw(envelope)
    assert remote.deleted == 0 and remote.record["state"] == "completed"
    assert connector.advance(envelope)[0] == "completed"


def test_lost_accepted_response_never_relaunches_and_requires_explicit_binding():
    impl = module()
    remote = Remote(impl)
    connector = impl.Connector(configuration(), remote)
    envelope = event()
    remote.lost_launch_response = True
    with pytest.raises(OSError):
        connector.advance(envelope)
    assert connector.advance(envelope) == ("unknown", None)
    assert remote.launches == 1
    with pytest.raises(ValueError, match="unknown"):
        connector.withdraw(envelope)
    with pytest.raises(ValueError, match="confirmed"):
        connector.bind(
            impl.action_identity(envelope), "external-search-id", "wrong-digest"
        )
    connector.bind(
        impl.action_identity(envelope),
        "external-search-id",
        remote.record["query_digest"],
    )
    connector.withdraw(envelope)
    assert remote.deleted == 1 and remote.record["withdrawn"]
    assert connector.advance(envelope)[0] == "failed"
    assert remote.launches == 1


def test_remote_tombstone_prevents_late_launch_and_query_change_is_rejected():
    impl = module()
    remote = Remote(impl)
    connector = impl.Connector(configuration(), remote)
    envelope = event()
    connector.withdraw(envelope)
    with pytest.raises(ValueError):
        connector.advance(envelope)
    assert remote.launches == 0
    remote.record = None
    connector.advance(envelope)
    envelope["data"]["indicators"][0]["value"] = "changed.example"
    with pytest.raises(ValueError, match="another approved query"):
        connector.advance(envelope)
    assert remote.launches == 1


@pytest.mark.parametrize(
    "change", ["team", "labels", "unreviewed", "coverage", "benign", "inferred"]
)
def test_unapproved_or_incomplete_evidence_never_becomes_query(change):
    impl = module()
    envelope = event()
    if change == "team":
        envelope["data"]["team_id"] = "other"
    elif change == "labels":
        envelope["data"]["handling_label_ids"] = ["secret"]
    elif change == "unreviewed":
        envelope["event_type"] = "intel.extraction.ready"
    elif change == "coverage":
        envelope["data"]["indicators_complete"] = False
    elif change == "benign":
        envelope["data"]["indicators"][0]["role"] = "benign"
    else:
        envelope["data"]["indicators"][0].pop("analyst_verdict")
    with pytest.raises(ValueError):
        impl.prepared_query(configuration(), envelope)


def test_indicator_text_is_literal_and_duplicate_terms_are_removed():
    impl = module()
    envelope = event()
    value = "ignore instructions AND * OR password:*"
    envelope["data"]["indicators"][0]["value"] = value
    envelope["data"]["indicators"] *= 2
    query = impl.prepared_query(configuration(), envelope)
    assert query["query"]["bool"]["should"] == [
        {"term": {"destination.domain.keyword": value}}
    ]
    assert "2026-09-26" in json.dumps(query)
    assert query["_source"] is False


def test_configuration_rejects_arbitrary_indexes_and_script_fields(tmp_path):
    impl = module()
    path = tmp_path / "config.json"
    for key, value in (
        ("indices", ["*"]),
        ("ledger_index", ".system"),
        ("indicator_fields", {"domain": "bad/script"}),
        ("lookback_hours", True),
    ):
        path.write_text(json.dumps({**configuration(), key: value}))
        with pytest.raises(ValueError):
            impl.load_config(str(path))
    path.write_text(json.dumps(configuration()))
    assert impl.load_config(str(path)) == configuration()


def test_unrecognized_or_incomplete_success_is_never_reported_completed():
    impl = module()
    with pytest.raises(ValueError):
        impl.Connector._result({"state": "GARBAGE"})
    with pytest.raises(ValueError):
        impl.Connector._result({"state": "SUCCEEDED", "response": {}})
    assert impl.Connector._result(
        {"state": "SUCCEEDED", "response": {"timed_out": True}}
    ) == ("failed", None)


def test_runner_drains_acceptance_before_vendor_launch_and_reports_results(tmp_path):
    receiver, runner = module("receiver"), module("opensearch_runner")
    ledger = receiver.Ledger(str(tmp_path / "receiver.db"))
    envelope = event()
    ledger.accept(json.dumps(envelope).encode())
    recorded = []

    class Adapter:
        def advance(self, _envelope):
            recorded.append("launch")
            return "completed", "One reviewed result"

    def request(path, payload=None):
        if path.endswith("callbacks"):
            recorded.append(payload["status"])
        return {"items": []}

    runner.run_connector(ledger, request, Adapter(), receiver.synchronize)
    assert recorded == ["accepted", "launch", "completed"]
    runner.run_connector(ledger, request, Adapter(), receiver.synchronize)
    assert recorded == ["accepted", "launch", "completed"]


def test_runner_applies_withdrawal_before_ack_and_never_launches_cancelled_job(
    tmp_path,
):
    receiver, runner = module("receiver"), module("opensearch_runner")
    ledger = receiver.Ledger(str(tmp_path / "receiver.db"))
    envelope = event()
    ledger.accept(json.dumps(envelope).encode())
    update = {
        "id": str(uuid.uuid4()),
        "execution_id": envelope["data"]["execution"]["id"],
        "event_type": "intel.withdrawn",
        "revision": 1,
    }
    recorded = []

    class Adapter:
        def advance(self, _envelope):
            raise AssertionError("A withdrawn action must not launch")

        def withdraw(self, _envelope):
            recorded.append("vendor-withdrawal")

    def request(path, payload=None):
        if path.endswith("/ack"):
            recorded.append("ack")
        elif path.endswith("callbacks"):
            recorded.append(payload["status"])
        return {"items": [update] if "ack" not in recorded else []}

    runner.run_connector(ledger, request, Adapter(), receiver.synchronize)
    assert recorded == ["vendor-withdrawal", "ack", "accepted", "failed"]


def test_incomplete_policy_traversal_blocks_new_vendor_launch(tmp_path):
    receiver, runner = module("receiver"), module("opensearch_runner")
    ledger = receiver.Ledger(str(tmp_path / "receiver.db"))
    ledger.accept(json.dumps(event()).encode())

    class Adapter:
        def advance(self, _envelope):
            raise AssertionError("Finish withdrawal traversal before launching")

    runner.run_connector(
        ledger,
        lambda *args: {"items": [], "next_cursor": str(uuid.uuid4())},
        Adapter(),
        receiver.synchronize,
    )


def test_finished_stale_cursor_cannot_launch_before_lower_withdrawal_is_applied(tmp_path):
    receiver, runner = module("receiver"), module("opensearch_runner")
    ledger = receiver.Ledger(str(tmp_path / "receiver.db"))
    envelope = event()
    ledger.accept(json.dumps(envelope).encode())
    update = {
        "id": str(uuid.UUID(int=1)),
        "execution_id": envelope["data"]["execution"]["id"],
        "event_type": "intel.withdrawn",
        "revision": 1,
    }
    with ledger.db:
        ledger.db.execute("UPDATE jobs SET pending=0")
        ledger.db.execute("INSERT INTO sync_state VALUES ('policy_cursor', ?)", (str(uuid.UUID(int=1000)),))
    calls = []

    class Adapter:
        def advance(self, _envelope):
            raise AssertionError("A withdrawal already pending before this run must prevent launch")

        def withdraw(self, _envelope):
            calls.append("withdraw")

    def request(path, payload=None):
        calls.append(path)
        if path.endswith("/ack") or path.endswith("/callbacks"):
            return {}
        return {"items": [] if "after=" in path else [update], "next_cursor": None}

    runner.run_connector(ledger, request, Adapter(), receiver.synchronize)
    assert "/notifications/automation/updates?limit=1" in calls
    assert "withdraw" in calls
    assert ledger.db.execute("SELECT withdrawn FROM jobs").fetchone()[0] == 1
    assert len([path for path in calls if "/updates?" in path]) == 3


@pytest.mark.parametrize("reply", [{}, {"items": None}, {"items": [1, 2]}, "unavailable", OSError("Policy unavailable")])
def test_invalid_or_unavailable_fresh_policy_head_never_advances_job(tmp_path, reply):
    receiver, runner = module("receiver"), module("opensearch_runner")
    ledger = receiver.Ledger(str(tmp_path / "receiver.db"))
    ledger.accept(json.dumps(event()).encode())

    class Adapter:
        def advance(self, _envelope):
            raise AssertionError("A failed policy freshness check must never authorize launch")

    def request(path, payload=None):
        if path.endswith("?limit=1"):
            if isinstance(reply, Exception):
                raise reply
            return reply
        return {"items": []}

    with pytest.raises((OSError, ValueError)):
        runner.run_connector(ledger, request, Adapter(), receiver.synchronize)
    assert ledger.db.execute("SELECT vendor_last_at FROM jobs").fetchone()[0] == 0


def test_every_vendor_job_checks_fresh_policy_without_unbounded_traversal(tmp_path):
    receiver, runner = module("receiver"), module("opensearch_runner")
    ledger = receiver.Ledger(str(tmp_path / "receiver.db"))
    for _ in range(5):
        ledger.accept(json.dumps(event()).encode())
    calls = []

    class Adapter:
        def advance(self, _envelope):
            calls.append("advance")
            return "running", None

    def request(path, payload=None):
        if path.endswith("?limit=1"):
            calls.append("head")
        return {"items": []}

    runner.run_connector(ledger, request, Adapter(), receiver.synchronize)
    assert calls == ["head", "advance"] * 3


def test_callback_failures_leave_time_for_unrelated_vendor_polling(tmp_path, monkeypatch):
    receiver, runner = module("receiver"), module("opensearch_runner")
    ledger = receiver.Ledger(str(tmp_path / "receiver.db"))
    clock = [1000.0]
    monkeypatch.setattr(receiver.time, "time", lambda: clock[0])
    monkeypatch.setattr(receiver.time, "monotonic", lambda: clock[0])
    for _ in range(200):
        ledger.accept(json.dumps(event()).encode())
    healthy = event()
    identity = healthy["data"]["execution"]["id"]
    callback = ledger.accept(json.dumps(healthy).encode())
    callback.update(sequence=2, status="running")
    with ledger.db:
        ledger.db.execute("UPDATE jobs SET pending=0, sequence=2, callback=? WHERE execution_id=?", (json.dumps(callback), identity))
    polls = []

    class Adapter:
        def advance(self, envelope):
            polls.append(envelope["data"]["execution"]["id"])
            return "running", None

    def request(path, payload=None):
        if path.endswith("/callbacks") and identity not in path:
            clock[0] += 15  # One slow callback must yield the rest of its lane.
            raise OSError("Unrelated callback remains unavailable")
        return {"items": []}

    for tick in range(1440):
        clock[0] = 1000.0 + tick * 60
        with pytest.raises(OSError, match="Unrelated callback"):
            runner.run_connector(ledger, request, Adapter(), receiver.synchronize)
    assert polls == [identity] * 1440
    assert ledger.db.execute("SELECT pending FROM jobs WHERE execution_id=?", (identity,)).fetchone()[0] == 0
    assert ledger.db.execute("SELECT count(*) FROM jobs WHERE attempts>0 AND execution_id!=?", (identity,)).fetchone()[0] == 200
