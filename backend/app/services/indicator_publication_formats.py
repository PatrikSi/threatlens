"""Reviewed STIX/MISP artifacts with stable identities and explicit withdrawals."""

from __future__ import annotations

from datetime import datetime
import json
import uuid

from pymisp import MISPEvent
from stix2 import Bundle, Identity, Indicator, Report

from app.models.indicator_publication import IndicatorPublication
from app.services.export_misp import MISP_IOC_TYPES
from app.services.export_models import ExportIOC
from app.services.export_stix import TLP_MARKINGS, _indicator_pattern
from app.services.indicator_publication_query import canonical_bytes

NAMESPACE = uuid.UUID("91e3161d-c431-4d64-bf60-3a6e91a3e502")


def external_indicator_id(publication_id: uuid.UUID, entry: dict) -> uuid.UUID:
    # A withdrawn STIX object cannot be revived. Each deliberately approved
    # publication gets its own identity, stable across retries and updates.
    return uuid.uuid5(
        NAMESPACE,
        f"{publication_id}:{entry['assessment_id']}:{entry['assessment_version']}",
    )


def publication_bytes(row: IndicatorPublication) -> bytes:
    document = _stix(row) if row.format == "stix" else _misp(row)
    return canonical_bytes(document)


def _stix(row: IndicatorPublication) -> dict:
    marking = TLP_MARKINGS.get(row.marking)
    refs = [marking.id] if marking else []
    identity = Identity(
        id="identity--"
        + str(uuid.uuid5(NAMESPACE, "threatlens-reviewed-publications")),
        name="ThreatLens reviewed intelligence",
        identity_class="system",
        created="2026-01-01T00:00:00Z",
        modified="2026-01-01T00:00:00Z",
    )
    objects = [identity, *([marking] if marking else [])]
    indicators = []
    for entry in row.snapshot_json["indicators"]:
        first_seen = datetime.fromisoformat(entry["first_seen_at"])
        ioc = ExportIOC(
            id=uuid.UUID(entry["ioc_id"]),
            type=entry["type"],
            value=entry["value"],
            source_section="reviewed",
            occurrences=1,
            confidence=entry["extraction_confidence"],
            first_seen_at=first_seen,
            last_seen_at=first_seen,
        )
        pattern = _indicator_pattern(ioc)
        if pattern is None:
            raise ValueError("Unsupported reviewed indicator type")
        external = {"source_name": "ThreatLens", "external_id": entry["item_id"]}
        if entry.get("url"):
            external["url"] = entry["url"]
        fields = {
            "id": "indicator--" + str(external_indicator_id(row.id, entry)),
            "name": f"{entry['type']}: {entry['value']}"[:250],
            "pattern": pattern,
            "pattern_type": "stix",
            "created": row.created_at,
            "modified": datetime.fromisoformat(entry["withdrawn_at"])
            if entry.get("withdrawn_at")
            else row.created_at,
            "valid_from": datetime.fromisoformat(entry["reviewed_at"]),
            "revoked": bool(entry.get("withdrawn_at")),
            "created_by_ref": identity.id,
            "object_marking_refs": refs,
            "labels": ["analyst-reviewed", "verdict:malicious"],
            "description": entry["reason"],
            "external_references": [external],
            "allow_custom": True,
            "x_threatlens_review": {
                key: entry[key]
                for key in (
                    "assessment_id",
                    "assessment_version",
                    "source_revision",
                    "extraction_revision",
                    "source_fingerprint",
                    "evidence",
                    "label_ids",
                    "expires_at",
                )
            },
        }
        if entry.get("expires_at"):
            fields["valid_until"] = datetime.fromisoformat(entry["expires_at"])
        indicator = Indicator(**fields)
        objects.append(indicator)
        indicators.append(indicator.id)
    objects.append(
        Report(
            id="report--" + str(row.id),
            name="Reviewed ThreatLens indicator publication",
            report_types=["threat-report"],
            published=row.created_at,
            created=row.created_at,
            modified=row.updated_at,
            created_by_ref=identity.id,
            object_marking_refs=refs,
            object_refs=indicators,
            allow_custom=True,
            x_threatlens_publication_revision=row.revision,
            x_threatlens_team_id=str(row.team_id),
            x_threatlens_publication_status=row.status,
        )
    )
    return json.loads(
        Bundle(*objects, allow_custom=True, id="bundle--" + str(row.id)).serialize()
    )


def _misp(row: IndicatorPublication) -> dict:
    # MISP ignores supplied revisions whose integer timestamp is not newer.
    # Force timestamps: PyMISP otherwise drops timestamps on edited event objects.
    event = MISPEvent(strict_validation=True, force_timestamps=True)
    event.uuid = str(row.id)
    event.info = "Reviewed ThreatLens indicator publication"
    event.distribution = row.distribution
    event.threat_level_id = 4
    event.analysis = 2
    event.published = False
    event.date = row.created_at.date().isoformat()
    wire_timestamp = int(
        row.snapshot_json.get("misp_timestamp", int(row.created_at.timestamp()))
    )
    event.timestamp = wire_timestamp
    event.add_tag("threatlens:analyst-reviewed")
    if row.marking != "none":
        event.add_tag(row.marking.lower())
    groups: dict[tuple[str, str], list[dict]] = {}
    for entry in row.snapshot_json["indicators"]:
        groups.setdefault((entry["type"], entry["value"]), []).append(entry)
    for (indicator_type, value), entries in groups.items():
        # MISP forbids duplicate top-level category/type/value attributes. Keep
        # per-article approvals in provenance while publishing one effective IOC.
        identity = uuid.uuid5(NAMESPACE, f"{row.id}:{indicator_type}:{value}")
        withdrawn = all(bool(entry.get("withdrawn_at")) for entry in entries)
        reviews = [
            {
                key: entry.get(key)
                for key in (
                    "assessment_id",
                    "assessment_version",
                    "item_id",
                    "source_revision",
                    "extraction_revision",
                    "source_fingerprint",
                    "expires_at",
                    "evidence",
                    "label_ids",
                    "reason",
                    "withdrawn_at",
                    "withdrawal_reason",
                )
            }
            for entry in entries
        ]
        detail = {
            "publication_id": str(row.id),
            "publication_revision": row.revision,
            "verdict": "retracted" if withdrawn else "malicious",
            "reviews": reviews,
        }
        comment = canonical_bytes(detail)
        if len(comment) > 60_000:
            report_id = uuid.uuid5(NAMESPACE, f"{identity}:provenance")
            report = event.add_event_report(
                name=f"ThreatLens reviewed evidence: {indicator_type}",
                content=comment.decode(),
                uuid=str(report_id),
                timestamp=wire_timestamp,
            )
            report.force_timestamp = True
            comment = canonical_bytes(
                {
                    "publication_id": str(row.id),
                    "publication_revision": row.revision,
                    "verdict": detail["verdict"],
                    "review_count": len(reviews),
                    "review_report_uuid": str(report_id),
                }
            )
        attribute = event.add_attribute(
            MISP_IOC_TYPES[indicator_type],
            value,
            uuid=str(identity),
            to_ids=not withdrawn,
            deleted=withdrawn,
            timestamp=wire_timestamp,
            comment=comment.decode(),
        )
        attribute.force_timestamp = True
    return {"response": [{"Event": json.loads(event.to_json())}]}
