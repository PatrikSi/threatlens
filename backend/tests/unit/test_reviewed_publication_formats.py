"""Wire compatibility: updates stay newer and repeated observations stay unique."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
import uuid

from stix2 import parse

from app.services.indicator_publication_formats import publication_bytes


def publication(format="misp"):
    at = datetime(2026, 9, 26, 12, 0, 0, 100_000, tzinfo=timezone.utc)
    entry = {
        "assessment_id": str(uuid.uuid4()),
        "assessment_version": 1,
        "item_id": str(uuid.uuid4()),
        "ioc_id": str(uuid.uuid4()),
        "type": "domain",
        "value": "malicious.test",
        "reason": "Reviewed primary evidence",
        "source_revision": 1,
        "extraction_revision": 2,
        "source_fingerprint": "a" * 64,
        "evidence": [
            {
                "raw": "malicious[.]test",
                "source": "article",
                "transformations": ["refanged"],
            }
        ],
        "label_ids": [str(uuid.uuid4())],
        "expires_at": None,
        "reviewed_at": at.isoformat(),
        "first_seen_at": at.isoformat(),
        "extraction_confidence": 0.95,
    }
    other = {
        **deepcopy(entry),
        "assessment_id": str(uuid.uuid4()),
        "item_id": str(uuid.uuid4()),
    }
    return SimpleNamespace(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        format=format,
        marking="TLP:AMBER",
        distribution=0,
        created_at=at,
        updated_at=at,
        revision=1,
        status="active",
        snapshot_json={
            "indicators": [entry, other],
            "misp_timestamp": int(at.timestamp()),
        },
    )


def event(row):
    return json.loads(publication_bytes(row))["response"][0]["Event"]


def withdraw(row, index):
    row.revision += 1
    row.snapshot_json["misp_timestamp"] += 1
    row.updated_at += timedelta(microseconds=1)
    row.snapshot_json["indicators"][index].update(
        withdrawn_at=row.updated_at.isoformat(), withdrawal_reason="team_suppression"
    )


def test_misp_duplicates_merge_provenance_and_retract_only_without_remaining_support():
    row = publication()
    original = event(row)
    assert len(original["Attribute"]) == 1
    attribute = original["Attribute"][0]
    assert len(json.loads(attribute["comment"])["reviews"]) == 2
    assert attribute["to_ids"] and not attribute["deleted"]
    withdraw(row, 0)
    partial = event(row)
    assert partial["Attribute"][0]["uuid"] == attribute["uuid"]
    assert partial["Attribute"][0]["to_ids"] and not partial["Attribute"][0]["deleted"]
    assert (
        json.loads(partial["Attribute"][0]["comment"])["reviews"][0][
            "withdrawal_reason"
        ]
        == "team_suppression"
    )
    withdraw(row, 1)
    final = event(row)
    assert final["Attribute"][0]["uuid"] == attribute["uuid"]
    assert not final["Attribute"][0]["to_ids"] and final["Attribute"][0]["deleted"]


def test_misp_same_second_updates_have_strictly_newer_wire_timestamps():
    row = publication()
    original = event(row)
    withdraw(row, 0)
    partial = event(row)
    withdraw(row, 1)
    final = event(row)
    for key in (None, "Attribute"):
        values = [
            int(document["timestamp"] if key is None else document[key][0]["timestamp"])
            for document in (original, partial, final)
        ]
        assert values[0] < values[1] < values[2]
    assert publication_bytes(row) == publication_bytes(row)


def test_large_merged_misp_provenance_remains_complete_in_native_report():
    row = publication()
    for entry in row.snapshot_json["indicators"]:
        entry["evidence"] = [{"raw": "λ" * 18_000}]
    document = event(row)
    comment = json.loads(document["Attribute"][0]["comment"])
    assert len(document["Attribute"][0]["comment"].encode()) <= 60_000
    report = document["EventReport"][0]
    assert report["uuid"] == comment["review_report_uuid"]
    assert (
        json.loads(report["content"])["reviews"][1]["evidence"]
        == row.snapshot_json["indicators"][1]["evidence"]
    )
    assert publication_bytes(row) == publication_bytes(row)


def test_stix_keeps_independent_reviewed_evidence_revisions_and_stable_ids():
    row = publication("stix")
    before = parse(publication_bytes(row).decode(), allow_custom=True)
    indicators = [entry for entry in before.objects if entry.type == "indicator"]
    assert len(indicators) == 2 and indicators[0].id != indicators[1].id
    withdraw(row, 0)
    row.status = "partially_withdrawn"
    after = parse(publication_bytes(row).decode(), allow_custom=True)
    revised = [entry for entry in after.objects if entry.type == "indicator"]
    assert revised[0].id == indicators[0].id and revised[0].revoked
    assert not revised[1].revoked


def test_misp_uses_persisted_wire_clock_after_delayed_same_second_revisions():
    row = publication()
    original_timestamp = row.snapshot_json["misp_timestamp"]
    row.updated_at += timedelta(minutes=10)
    row.snapshot_json["misp_timestamp"] = original_timestamp + 600
    row.revision = 2
    delayed = event(row)
    row.updated_at += timedelta(microseconds=1)
    row.snapshot_json["misp_timestamp"] = original_timestamp + 601
    row.revision = 3
    subsequent = event(row)
    assert int(subsequent["timestamp"]) > int(delayed["timestamp"])
    assert int(subsequent["Attribute"][0]["timestamp"]) == original_timestamp + 601
