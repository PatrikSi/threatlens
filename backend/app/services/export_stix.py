from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from typing import Any

from stix2 import Bundle, Identity, Indicator, Report, Software, Vulnerability
from stix2.v21 import TLP_AMBER, TLP_GREEN, TLP_RED, TLP_WHITE

from app.schemas.exports import ArticleExportOptions
from app.services.export_models import ExportIOC, ExportRecord

TLP_MARKINGS = {
    "TLP:WHITE": TLP_WHITE,
    "TLP:GREEN": TLP_GREEN,
    "TLP:AMBER": TLP_AMBER,
    "TLP:RED": TLP_RED,
}


def build_stix_bundle(
    records: Iterable[ExportRecord],
    *,
    options: ArticleExportOptions,
) -> Bundle:
    objects = list(iter_stix_objects(records, options=options))
    return Bundle(*objects)


def iter_stix_objects(
    records: Iterable[ExportRecord],
    *,
    options: ArticleExportOptions,
) -> Iterator[Any]:
    marking = TLP_MARKINGS.get(options.stix_marking)
    marking_refs = [marking.id] if marking is not None else []
    if marking is not None:
        yield marking

    identity = Identity(
        name="ThreatLens",
        identity_class="system",
        object_marking_refs=marking_refs,
    )
    yield identity

    emitted_iocs: dict[str, str] = {}
    for record in records:
        referenced_objects = [identity.id]
        if options.include_iocs:
            for ioc in record.iocs:
                cache_key = f"{ioc.type}:{ioc.value}"
                object_id = emitted_iocs.get(cache_key)
                if object_id is None:
                    stix_object = _build_ioc_object(
                        ioc,
                        identity_id=identity.id,
                        marking_refs=marking_refs,
                    )
                    if stix_object is None:
                        continue
                    emitted_iocs[cache_key] = stix_object.id
                    object_id = stix_object.id
                    yield stix_object
                referenced_objects.append(object_id)

        report_kwargs: dict[str, Any] = {
            "name": record.title[:250] or "Untitled ThreatLens article",
            "report_types": ["threat-report"],
            "published": _aware_datetime(record.published_at or record.first_seen_at),
            "object_refs": referenced_objects,
            "created_by_ref": identity.id,
            "object_marking_refs": marking_refs,
        }
        external_reference = {
            "source_name": record.feed_name[:250] or "ThreatLens source",
            "external_id": str(record.id),
        }
        if record.url:
            external_reference["url"] = record.url
        report_kwargs["external_references"] = [external_reference]
        description = _report_description(record, include_ai=options.include_ai_details)
        if description:
            report_kwargs["description"] = description
        labels = [tag.name[:256] for tag in record.tags if tag.name.strip()]
        if labels:
            report_kwargs["labels"] = labels
        if record.classification is not None:
            report_kwargs["confidence"] = round(max(0, min(1, record.classification.confidence)) * 100)
        yield Report(**report_kwargs)


def _build_ioc_object(
    ioc: ExportIOC,
    *,
    identity_id: str,
    marking_refs: list[str],
):
    common = {
        "created_by_ref": identity_id,
        "object_marking_refs": marking_refs,
    }
    if ioc.type == "cve":
        return Vulnerability(
            name=ioc.value,
            external_references=[{"source_name": "cve", "external_id": ioc.value}],
            **common,
        )
    if ioc.type == "vendor":
        return Identity(name=ioc.value[:250], identity_class="organization", **common)
    if ioc.type == "program":
        return Software(name=ioc.value[:250], object_marking_refs=marking_refs)

    pattern = _indicator_pattern(ioc)
    if pattern is None:
        return None
    return Indicator(
        name=f"{ioc.type}: {ioc.value}"[:250],
        pattern=pattern,
        pattern_type="stix",
        valid_from=_aware_datetime(ioc.first_seen_at),
        confidence=round(max(0, min(1, ioc.confidence)) * 100),
        **common,
    )


def _indicator_pattern(ioc: ExportIOC) -> str | None:
    escaped = _escape_pattern_value(ioc.value)
    patterns = {
        "ipv4": f"[ipv4-addr:value = '{escaped}']",
        "domain": f"[domain-name:value = '{escaped}']",
        "hash_md5": f"[file:hashes.MD5 = '{escaped}']",
        "hash_sha1": f"[file:hashes.'SHA-1' = '{escaped}']",
        "hash_sha256": f"[file:hashes.'SHA-256' = '{escaped}']",
    }
    return patterns.get(ioc.type)


def _escape_pattern_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _aware_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _report_description(record: ExportRecord, *, include_ai: bool) -> str | None:
    parts: list[str] = []
    if include_ai and record.ai and record.ai.summary:
        parts.append(record.ai.summary.strip())
    if record.summary and record.summary.strip() not in parts:
        parts.append(record.summary.strip())
    return "\n\n".join(parts)[:20_000] or None
