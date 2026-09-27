"""Bounded OpenSearch asynchronous hunts with durable launch ambiguity fences.

The action ledger must be retained and backed up independently of async results.
An unresolved launch is never retried. This connector executes read-only indicator
searches, not monitor actions or AI-authored query languages.
"""

from datetime import datetime, timedelta
import hashlib
import json
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_RESPONSE = 1_000_000


class RemoteError(OSError):
    def __init__(self, status: int):
        super().__init__(f"OpenSearch request failed with HTTP {status}")
        self.status = status


class Client:
    def __init__(self, base: str, authorization: str, deadline):
        parsed = urlsplit(base)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "OpenSearch requires an explicit HTTPS origin without embedded credentials"
            )
        if not authorization or "\n" in authorization or "\r" in authorization:
            raise ValueError("OpenSearch authorization header is missing or invalid")
        self.base, self.authorization, self.deadline = (
            base.rstrip("/"),
            authorization,
            deadline,
        )

    def __call__(self, method: str, path: str, payload=None):
        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *_args, **_kwargs):
                raise ValueError("OpenSearch redirects are not permitted")

        request = Request(
            self.base + path,
            method=method,
            data=None
            if payload is None
            else json.dumps(payload, allow_nan=False).encode(),
            headers={
                "Authorization": self.authorization,
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
            },
        )
        try:
            with (
                self.deadline(15),
                build_opener(NoRedirect()).open(request, timeout=15) as response,
            ):
                body = response.read(MAX_RESPONSE + 1)
                if len(body) > MAX_RESPONSE or response.headers.get(
                    "Content-Encoding", "identity"
                ) not in ("", "identity"):
                    raise ValueError(
                        "OpenSearch response exceeded the byte or encoding limit"
                    )
                result = json.loads(body)
                if not isinstance(result, dict):
                    raise ValueError("OpenSearch response must be an object")
                return result
        except HTTPError as exc:
            status = exc.code
            exc.close()  # Never buffer or log potentially sensitive error bodies.
            raise RemoteError(status) from None


def load_config(path: str) -> dict:
    with open(path, "rb") as stream:
        raw = stream.read(65_537)
    if len(raw) > 65_536:
        raise ValueError("OpenSearch configuration exceeds 64 KiB")
    config = json.loads(raw)
    allowed = {
        "ledger_index",
        "indices",
        "timestamp_field",
        "indicator_fields",
        "lookback_hours",
        "allowed_handling_label_ids",
        "team_id",
    }
    if set(config) - allowed:
        raise ValueError("Unknown OpenSearch configuration keys")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{2,120}", config.get("ledger_index", "")):
        raise ValueError("Use a dedicated, exact action ledger index")
    indices = config.get("indices")
    if (
        not isinstance(indices, list)
        or not 1 <= len(indices) <= 20
        or any(
            not isinstance(value, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{2,120}\*?", value)
            for value in indices
        )
    ):
        raise ValueError("Approve one to twenty explicit log index names/prefixes")
    fields = config.get("indicator_fields")
    if not isinstance(fields, dict) or not fields or len(fields) > 20:
        raise ValueError("Configure explicit indicator-to-telemetry fields")
    for field in [config.get("timestamp_field"), *fields.values()]:
        if not isinstance(field, str) or not re.fullmatch(
            r"[@A-Za-z_][A-Za-z0-9_.@-]{0,127}", field
        ):
            raise ValueError("Invalid telemetry field")
    if (
        type(config.get("lookback_hours")) is not int
        or not 1 <= config["lookback_hours"] <= 168
    ):
        raise ValueError("Approve a lookback between one and 168 hours")
    if not isinstance(config.get("allowed_handling_label_ids"), list) or not config.get(
        "team_id"
    ):
        raise ValueError("Explicit team and handling-label approval are required")
    return config


def prepared_query(config: dict, envelope: dict) -> dict:
    data = envelope.get("data", {})
    if (
        envelope.get("event_type") != "hunt.approved"
        or data.get("filter_metadata", {}).get("hunt_review_status") != "accepted"
    ):
        raise ValueError(
            "Only explicitly approved hunt events can launch OpenSearch searches"
        )
    if data.get("team_id") != config["team_id"] or not data.get("indicators_complete"):
        raise ValueError(
            "Hunt team or indicator coverage does not match the approved connector"
        )
    labels = data.get("handling_label_ids")
    if not isinstance(labels, list) or not set(labels) <= set(
        config["allowed_handling_label_ids"]
    ):
        raise ValueError("The connector is not approved for the hunt's handling labels")
    indicators = data.get("indicators")
    if not isinstance(indicators, list) or len(indicators) > 250:
        raise ValueError("Hunt indicators exceed the bounded query contract")
    terms = []
    seen = set()
    for indicator in indicators:
        field, value = (
            config["indicator_fields"].get(indicator.get("type")),
            indicator.get("value"),
        )
        if (
            not field
            or indicator.get("excluded")
            or indicator.get("role")
            in {"reference", "example", "benign", "benign_reference"}
            or indicator.get("analyst_verdict") != "malicious"
        ):
            continue
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 2048
            or "\x00" in value
        ):
            raise ValueError("Invalid normalized indicator")
        key = field, value
        if key not in seen:
            seen.add(key)
            terms.append({"term": {field: value}})
    if not terms:
        raise ValueError("No approved hunt indicators map to available telemetry")
    end = datetime.fromisoformat(envelope["occurred_at"].replace("Z", "+00:00"))
    if end.tzinfo is None:
        raise ValueError("Hunt time requires an explicit timezone")
    return {
        "size": 20,
        "_source": False,
        "track_total_hits": 10000,
        "timeout": "60s",
        "query": {
            "bool": {
                "filter": [
                    {
                        "range": {
                            config["timestamp_field"]: {
                                "gte": (
                                    end - timedelta(hours=config["lookback_hours"])
                                ).isoformat(),
                                "lte": end.isoformat(),
                            }
                        }
                    }
                ],
                "should": terms,
                "minimum_should_match": 1,
            }
        },
    }


def action_identity(envelope: dict) -> str:
    value = f"{envelope['data']['execution']['webhook_id']}:{envelope['action_id']}"
    return hashlib.sha256(value.encode()).hexdigest()


class Connector:
    def __init__(self, config: dict, request):
        self.config, self.request = config, request

    def _path(self, action: str) -> str:
        return f"/{self.config['ledger_index']}/_doc/{action}"

    def lookup(self, action: str):
        try:
            return self.request("GET", self._path(action))
        except RemoteError as exc:
            if exc.status == 404:
                return None
            raise

    def _save(self, action: str, record: dict, current: dict):
        return self.request(
            "PUT",
            self._path(action)
            + "?"
            + urlencode(
                {
                    "if_seq_no": current["_seq_no"],
                    "if_primary_term": current["_primary_term"],
                }
            ),
            record,
        )

    def advance(self, envelope: dict) -> tuple[str, str | None]:
        action = action_identity(envelope)
        query = prepared_query(self.config, envelope)
        digest = hashlib.sha256(
            json.dumps(
                {"query": query, "indices": self.config["indices"]}, sort_keys=True
            ).encode()
        ).hexdigest()
        existing = self.lookup(action)
        if existing is None:
            record = {
                "state": "launching",
                "query_digest": digest,
                "execution_id": envelope["data"]["execution"]["id"],
                "async_id": None,
                "withdrawn": False,
            }
            try:
                version = self.request(
                    "PUT",
                    f"/{self.config['ledger_index']}/_create/{action}?refresh=wait_for",
                    record,
                )
            except RemoteError as exc:
                if exc.status == 409:
                    return (
                        "unknown",
                        None,
                    )  # Another owner may be launching; never submit.
                raise
            # A durable tombstone precedes submission. Any transport/parse/persist
            # failure leaves it in launching, which lookup will never relaunch.
            response = self.request(
                "POST",
                "/_plugins/_asynchronous_search?"
                + urlencode(
                    {
                        "index": ",".join(self.config["indices"]),
                        "wait_for_completion_timeout": "1s",
                        "keep_on_completion": "true",
                        "keep_alive": "1d",
                    }
                ),
                query,
            )
            record["async_id"] = response.get("id")
            status, findings = self._result(response)
            record.update(state=status, findings=findings)
            self._save(action, record, version)
            return status, findings
        record = existing["_source"]
        if record.get("query_digest") != digest:
            raise ValueError(
                "Action already exists with another approved query; restore its original connector configuration"
            )
        if record.get("state") in {"completed", "failed"}:
            return record["state"], record.get("findings")
        if record.get("withdrawn"):
            return "failed", None
        if not record.get("async_id"):
            return "unknown", None
        try:
            response = self.request(
                "GET",
                "/_plugins/_asynchronous_search/" + quote(record["async_id"], safe=""),
            )
        except RemoteError as exc:
            if exc.status == 404:
                return "unknown", None  # Expired results never authorize relaunch.
            raise
        status, findings = self._result(response)
        record.update(state=status, findings=findings)
        self._save(action, record, existing)
        return status, findings

    def withdraw(self, envelope: dict) -> None:
        action = action_identity(envelope)
        existing = self.lookup(action)
        if existing is None:
            # Prevent a different process from launching after local withdrawal.
            try:
                self.request(
                    "PUT",
                    f"/{self.config['ledger_index']}/_create/{action}?refresh=wait_for",
                    {
                        "state": "failed",
                        "withdrawn": True,
                        "async_id": None,
                        "query_digest": None,
                    },
                )
                return
            except RemoteError as exc:
                if exc.status != 409:
                    raise
            existing = self.lookup(action)
        record = existing["_source"]
        if record.get("state") not in {"completed", "failed"}:
            if not record.get("async_id"):
                raise ValueError(
                    "Launch outcome is unknown; reconcile the external search before acknowledging withdrawal"
                )
            try:
                self.request(
                    "DELETE",
                    "/_plugins/_asynchronous_search/"
                    + quote(record["async_id"], safe=""),
                )
            except RemoteError as exc:
                if exc.status != 404:
                    raise
            record["state"] = "failed"
        record["withdrawn"] = True
        self._save(action, record, existing)

    def bind(self, action: str, async_id: str, confirmed_digest: str) -> None:
        """Explicit operator attestation closes the lost-launch-response window."""
        existing = self.lookup(action)
        if existing is None:
            raise ValueError("No ambiguous action exists to reconcile")
        record = existing["_source"]
        if (
            record.get("async_id")
            or record.get("state") != "launching"
            or record.get("query_digest") != confirmed_digest
        ):
            raise ValueError(
                "Only an unresolved launch with the confirmed original query digest may be bound"
            )
        if not isinstance(async_id, str) or not 1 <= len(async_id) <= 2048:
            raise ValueError("Invalid external search ID")
        response = self.request(
            "GET", "/_plugins/_asynchronous_search/" + quote(async_id, safe="")
        )
        status, findings = self._result(response)
        record.update(
            async_id=async_id, state=status, findings=findings, operator_reconciled=True
        )
        self._save(action, record, existing)

    @staticmethod
    def _result(response: dict) -> tuple[str, str | None]:
        state = response.get("state")
        if state in {"FAILED", "PERSIST_FAILED", "CLOSED"} or response.get("error"):
            return "failed", None
        if state in {"RUNNING", "PERSISTING"}:
            return "running", None
        if state not in {"SUCCEEDED", "PERSIST_SUCCEEDED", "STORE_RESIDENT"}:
            raise ValueError("Unsupported OpenSearch execution state")
        result = response.get("response") or {}
        if (
            result.get("error")
            or result.get("timed_out")
            or result.get("_shards", {}).get("failed", 0)
        ):
            return "failed", None
        hits = result.get("hits", {})
        if (
            not isinstance(hits, dict)
            or not isinstance(hits.get("hits"), list)
            or not isinstance(hits.get("total"), (dict, int))
        ):
            raise ValueError(
                "OpenSearch completed response is missing bounded findings"
            )
        total = hits["total"]
        if isinstance(total, int) and not isinstance(total, bool):
            total = {"value": total, "relation": "eq"}
        if (
            not isinstance(total, dict)
            or type(total.get("value")) is not int
            or total["value"] < 0
            or total.get("relation") not in {"eq", "gte"}
        ):
            raise ValueError("OpenSearch returned an invalid match count")
        for hit in hits["hits"][:20]:
            if not isinstance(hit, dict) or any(
                not isinstance(hit.get(key), str) or len(hit[key]) > 512
                for key in ("_index", "_id")
            ):
                raise ValueError("OpenSearch returned an invalid document reference")
        findings = {
            "connector": "opensearch",
            "external_search_id": response.get("id"),
            "total": {"value": total["value"], "relation": total["relation"]},
            "documents": [
                {"index": row.get("_index"), "id": row.get("_id")}
                for row in hits.get("hits", [])[:20]
            ],
            "source_documents_included": False,
        }
        encoded = json.dumps(findings, ensure_ascii=True, allow_nan=False)
        if len(encoded) > 8000:
            findings["documents"] = []
            findings["document_references_omitted"] = True
            encoded = json.dumps(findings, ensure_ascii=True, allow_nan=False)
        if len(encoded) > 8000 or "\\u0000" in encoded:
            raise ValueError("OpenSearch findings are not storage-safe")
        return "completed", encoded


def configured_connector(deadline):
    return Connector(
        load_config(os.environ["OPENSEARCH_CONNECTOR_CONFIG"]),
        Client(
            os.environ["OPENSEARCH_URL"],
            os.environ["OPENSEARCH_AUTHORIZATION"],
            deadline,
        ),
    )
