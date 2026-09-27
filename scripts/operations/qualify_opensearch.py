#!/usr/bin/env python3
"""Qualify the connector against an isolated loopback OpenSearch, never production.

Creates two uniquely named indexes, exercises launch/poll/withdrawal and lost
acceptance response recovery, and deletes only those indexes in finally.
"""

import argparse
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import uuid


def qualify(base: str) -> dict:
    parsed = urlsplit(base)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Qualification requires an isolated HTTP server on 127.0.0.1 with an explicit port"
        )
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "opensearch_connector",
        root / "examples/automation-receiver/opensearch_connector.py",
    )
    connector_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(connector_module)
    calls = []

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *_args, **_kwargs):
            raise ValueError("Qualification redirects are forbidden")

    def request(method, path, payload=None):
        calls.append((method, path))
        req = Request(
            base.rstrip("/") + path,
            method=method,
            headers={"Content-Type": "application/json"},
            data=None if payload is None else json.dumps(payload).encode(),
        )
        try:
            with build_opener(NoRedirect()).open(req, timeout=15) as response:
                body = response.read(1_000_001)
                if len(body) > 1_000_000:
                    raise ValueError("Qualification response exceeded its byte limit")
                return json.loads(body)
        except HTTPError as error:
            status = error.code
            error.close()
            raise connector_module.RemoteError(status) from None

    suffix = uuid.uuid4().hex[:12]
    index, ledger = (
        f"tl-qualification-logs-{suffix}",
        f"tl-qualification-actions-{suffix}",
    )
    created = []
    try:
        request(
            "PUT",
            "/" + index,
            {
                "settings": {"number_of_shards": 1, "number_of_replicas": 0},
                "mappings": {
                    "properties": {
                        "@timestamp": {"type": "date"},
                        "destination": {"properties": {"domain": {"type": "keyword"}}},
                    }
                },
            },
        )
        created.append(index)
        request(
            "PUT",
            "/" + ledger,
            {"settings": {"number_of_shards": 1, "number_of_replicas": 0}},
        )
        created.append(ledger)
        now = datetime.now(timezone.utc)
        request(
            "PUT",
            f"/{index}/_doc/one?refresh=true",
            {
                "@timestamp": (now - timedelta(hours=1)).isoformat(),
                "destination": {"domain": "evil.example"},
                "sensitive": "must not return",
            },
        )
        config = {
            "ledger_index": ledger,
            "indices": [index],
            "timestamp_field": "@timestamp",
            "indicator_fields": {"domain": "destination.domain"},
            "lookback_hours": 24,
            "allowed_handling_label_ids": ["public"],
            "team_id": "test-team",
        }
        envelope = {
            "event_type": "hunt.approved",
            "occurred_at": now.isoformat(),
            "action_id": suffix,
            "data": {
                "execution": {"id": str(uuid.uuid4()), "webhook_id": str(uuid.uuid4())},
                "team_id": "test-team",
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
        connector = connector_module.Connector(config, request)
        result = connector.advance(envelope)
        for _ in range(10):
            if result[0] in ("completed", "failed"):
                break
            time.sleep(0.25)
            result = connector.advance(envelope)
        assert result[0] == "completed", result
        findings = json.loads(result[1])
        assert findings["total"]["value"] == 1, findings
        assert "must not return" not in result[1]
        connector.advance(envelope)
        assert sum(method == "POST" for method, _path in calls) == 1
        connector.withdraw(envelope)
        assert connector.lookup(connector_module.action_identity(envelope))["_source"][
            "withdrawn"
        ]
        envelope["action_id"] = suffix + "-lost-response"
        lost_id = None

        def lose_accepted_response(method, path, payload=None):
            nonlocal lost_id
            response = request(method, path, payload)
            if method == "POST":
                lost_id = response["id"]
                raise OSError(
                    "Simulated response loss after real OpenSearch acceptance"
                )
            return response

        interrupted = connector_module.Connector(config, lose_accepted_response)
        try:
            interrupted.advance(envelope)
            raise AssertionError("The launch response should have been lost")
        except OSError:
            pass
        assert connector.advance(envelope) == ("unknown", None)
        assert sum(method == "POST" for method, _path in calls) == 2
        action = connector_module.action_identity(envelope)
        digest = connector.lookup(action)["_source"]["query_digest"]
        connector.bind(action, lost_id, digest)
        assert connector.advance(envelope)[0] in ("running", "completed")
        assert sum(method == "POST" for method, _path in calls) == 2
        return {
            "measured_at": now.isoformat(),
            "opensearch_version": request("GET", "/")["version"]["number"],
            "scope": "disposable_loopback_vendor_contract",
            "production_qualified": False,
            "matched_documents": 1,
            "distinct_actions": 2,
            "external_launches": 2,
            "external_search_id_persisted": bool(findings["external_search_id"]),
            "lost_acceptance_response_recovered_by_explicit_binding": True,
            "withdrawal_preserved_completed_history": True,
            "source_documents_returned": False,
        }
    finally:
        for name in reversed(created):
            request("DELETE", "/" + name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(qualify(args.url), indent=2) + "\n"
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
