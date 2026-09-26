"""Transactional, revisioned intelligence events with explicit completeness.

Callers commit source state and this outbox together. A shared snapshot row
serializes deterministic and AI publications without taking a new Item lock
from an AI worker that already owns its execution/enrichment locks.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.intel_assessment import ItemIntelState, TeamIntelState
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.tag import ItemTag, Tag
from app.services.indicator_evidence import (
    current_ai_indicator_links,
    current_ai_attack_techniques,
    indicator_exclusion,
)
from app.services.integration_events import emit_integration_event
from app.services.url_utils import redact_feed_url

PROCESSOR_VERSION = "ioc-evidence-v1"
MAX_EVENT_INDICATORS = 250
MAX_EVENT_BYTES = 256 * 1024


def _json(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def source_fingerprint(item: Item, article) -> str:
    return hashlib.sha256(
        _json(
            {
                "revision": item.classification_required_version,
                "article_id": str(article.id) if article else None,
                "article_at": article.retrieved_at.isoformat() if article else None,
                "purged_at": article.content_purged_at.isoformat()
                if article and article.content_purged_at
                else None,
            }
        )
    ).hexdigest()


def _locked_state(db: Session, item_id: uuid.UUID) -> ItemIntelState:
    db.execute(insert(ItemIntelState).values(item_id=item_id).on_conflict_do_nothing())
    return db.scalar(
        select(ItemIntelState)
        .where(ItemIntelState.item_id == item_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _indicator_snapshot(
    db: Session, item_id: uuid.UUID
) -> tuple[list[dict], int, str, str]:
    ai_links = current_ai_indicator_links(db, item_id)
    rows = db.execute(
        select(
            IOC.id,
            IOC.type,
            IOC.value_norm,
            ItemIOC.confidence,
            ItemIOC.occurrences,
            ItemIOC.evidence_json,
        )
        .join(ItemIOC, ItemIOC.ioc_id == IOC.id)
        .where(ItemIOC.item_id == item_id)
        .order_by(IOC.type, IOC.value_norm)
        .execution_options(yield_per=500)
    )
    set_digest, extraction_digest = hashlib.sha256(), hashlib.sha256()
    indicators: list[dict] = []
    count = 0
    for row in rows:
        ai = ai_links.get((row.type, row.value_norm))
        reasons = indicator_exclusion(row.type, row.value_norm, ai)
        evidence = row.evidence_json or []
        indicator = {
            "id": str(row.id),
            "type": row.type,
            "value": row.value_norm,
            "raw": evidence[0]["raw"] if evidence else row.value_norm,
            "extraction_confidence": row.confidence,
            "occurrences": row.occurrences,
            "evidence_truncated": row.occurrences > len(evidence),
            "role": ai["role"] if ai else "unknown",
            "assertion": ai["assertion"] if ai else None,
            "maliciousness_confidence": None,
            "evidence": evidence,
            "ai_evidence": ai["evidence"] if ai else [],
            "excluded": bool(reasons),
            "reasons": reasons,
        }
        semantic = {
            key: indicator[key]
            for key in (
                "type",
                "value",
                "extraction_confidence",
                "role",
                "assertion",
                "excluded",
                "reasons",
            )
        }
        set_digest.update(_json(semantic) + b"\n")
        extraction_digest.update(
            _json({"type": row.type, "value": row.value_norm, "evidence": evidence})
            + b"\n"
        )
        count += 1
        if count <= MAX_EVENT_INDICATORS:
            indicators.append(indicator)
    return (
        indicators if count <= MAX_EVENT_INDICATORS else [],
        count,
        set_digest.hexdigest(),
        extraction_digest.hexdigest(),
    )


def _base_payload(
    db: Session, item: Item, article, state: ItemIntelState, *, reason: str
) -> dict:
    feed = db.get(Feed, item.feed_id)
    tags = db.execute(
        select(Tag.id, Tag.name)
        .join(ItemTag, ItemTag.tag_id == Tag.id)
        .where(ItemTag.item_id == item.id)
        .order_by(Tag.name)
        .limit(251)
    ).all()
    tags_complete = len(tags) <= 250
    occurred_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema_version": 1,
        "item_id": str(item.id),
        "feed_id": str(item.feed_id),
        "item": {
            "id": str(item.id),
            "title": item.title[:512],
            "url": item.url[:2048],
            "feed_id": str(item.feed_id),
        },
        "feed": {
            "id": str(feed.id),
            "name": feed.name[:512],
            "url": redact_feed_url(feed.url)[:2048],
        },
        "source_revision": item.classification_required_version,
        "article_id": str(article.id) if article else None,
        "article_retrieved_at": article.retrieved_at.isoformat() if article else None,
        "article_content_purged_at": article.content_purged_at.isoformat()
        if article and article.content_purged_at
        else None,
        "extraction_revision": state.revision,
        "indicator_set_hash": state.indicator_set_hash,
        "processor_version": PROCESSOR_VERSION,
        "reason": reason,
        "occurred_at": occurred_at,
        "handling_label_ids": sorted(
            {
                str(feed.handling_label_id),
                *([str(state.handling_label_id)] if state.handling_label_id else []),
            }
        ),
        "filter_metadata": {
            "feed_id": str(item.feed_id),
            "tags": [row.name for row in tags[:250]],
            "tag_ids": [str(row.id) for row in tags[:250]],
            "tags_complete": tags_complete,
            "alert_rule_ids": [],
            "attack_techniques": current_ai_attack_techniques(db, item.id),
            "occurred_at": occurred_at,
        },
    }


def _bound_payload(payload: dict) -> dict:
    if len(_json(payload)) > MAX_EVENT_BYTES:
        payload["indicators"] = []
        payload["indicators_complete"] = False
        payload["incomplete_reason"] = "event_byte_limit"
    return payload


def emit_intel_events(
    db: Session, *, item_id: uuid.UUID, deterministic: bool
) -> list[uuid.UUID]:
    state = _locked_state(db, item_id)
    item = db.scalar(
        select(Item).where(Item.id == item_id).execution_options(populate_existing=True)
    )
    if item is None:
        return []
    article = db.execute(
        select(Article.id, Article.retrieved_at, Article.content_purged_at).where(
            Article.item_id == item_id
        )
    ).one_or_none()
    source = source_fingerprint(item, article)
    # AI cannot call an old deterministic inventory current after a refresh.
    if not deterministic and state.source_fingerprint != source:
        return []
    indicators, count, digest, extraction_hash = _indicator_snapshot(db, item_id)
    extraction_key = hashlib.sha256(
        f"{source}:{PROCESSOR_VERSION}:{extraction_hash}".encode()
    ).hexdigest()
    ready_changed = deterministic and state.extraction_fingerprint != extraction_key
    set_changed = state.indicator_set_hash != digest
    if not ready_changed and not set_changed:
        return []
    state.revision += 1
    state.source_revision = item.classification_required_version
    if deterministic:
        state.handling_label_id = db.scalar(
            select(Feed.handling_label_id).where(Feed.id == item.feed_id)
        )
    state.source_fingerprint = source
    state.indicator_set_hash = digest
    if deterministic:
        state.extraction_fingerprint = extraction_key
    db.flush()
    payload = _base_payload(
        db,
        item,
        article,
        state,
        reason="deterministic_extraction" if deterministic else "ai_enrichment",
    )
    payload.update(
        indicators=indicators,
        indicator_count=count,
        indicators_complete=count <= MAX_EVENT_INDICATORS,
    )
    if count > MAX_EVENT_INDICATORS:
        payload["incomplete_reason"] = "indicator_count_limit"
    metadata = payload["filter_metadata"]
    metadata.update(
        ioc_types=sorted({row["type"] for row in indicators}),
        ioc_roles=sorted({row["role"] for row in indicators}),
        extraction_confidence=min(
            (row["extraction_confidence"] for row in indicators if not row["excluded"]),
            default=None,
        ),
        maliciousness_confidence=None,
    )
    payload = _bound_payload(payload)
    event_ids = []
    for event_type, enabled in (
        ("intel.extraction.ready", ready_changed),
        ("intel.indicators.changed", set_changed),
    ):
        if enabled:
            key = f"{event_type}:{item_id}:{state.revision}"
            event = emit_integration_event(
                db,
                event_type=event_type,
                source_type="item",
                source_id=item_id,
                idempotency_key=key,
                payload={
                    **payload,
                    "action_id": hashlib.sha256(key.encode()).hexdigest(),
                },
                schema_version=1,
            )
            _retain_extraction_label(db, event.id, item, state)
            event_ids.append(event.id)
    return event_ids


def emit_hunt_approved(
    db: Session, *, row, item: Item, hunt: dict, actor_user_id: uuid.UUID
) -> uuid.UUID:
    state = db.scalar(
        select(ItemIntelState)
        .where(ItemIntelState.item_id == item.id)
        .with_for_update(read=True)
    )
    if state is None:
        state = ItemIntelState(item_id=item.id, revision=0, indicator_set_hash="")
    article = db.execute(
        select(Article.id, Article.retrieved_at, Article.content_purged_at).where(
            Article.item_id == item.id
        )
    ).one_or_none()
    payload = _base_payload(db, item, article, state, reason="analyst_approval")
    indicators, count, _digest, _extraction = _indicator_snapshot(db, item.id)
    current = state.source_fingerprint == source_fingerprint(item, article)
    from app.services.team_indicator_policy import team_indicator_snapshot

    indicators, policy_hash = team_indicator_snapshot(
        db,
        team_id=row.team_id,
        item_id=item.id,
        indicators=indicators if current else [],
        source_revision=item.classification_required_version,
        extraction_revision=state.revision,
    )
    payload["team_indicator_policy_hash"] = policy_hash
    payload.update(
        team_id=str(row.team_id),
        assessment_id=str(row.id),
        assessment_version=row.version,
        context_version=row.result_context_version,
        hunt_id=hunt["id"],
        hunt=hunt,
        indicators=indicators if current else [],
        indicator_count=count,
        indicators_complete=current and count <= MAX_EVENT_INDICATORS,
        indicator_scope="article",
        hunt_revision=row.version,
    )
    if not current:
        payload["incomplete_reason"] = "extraction_not_current"
    elif count > MAX_EVENT_INDICATORS:
        payload["incomplete_reason"] = "indicator_count_limit"
    payload["filter_metadata"].update(
        team_id=str(row.team_id),
        ioc_types=sorted({entry["type"] for entry in payload["indicators"]}),
        ioc_roles=sorted({entry["role"] for entry in payload["indicators"]}),
        hunt_review_status="accepted",
        attack_techniques=hunt.get("attack_technique_ids", []),
    )
    from app.services.hunt_approval import hunt_approval_fingerprint

    payload["hunt_approval_fingerprint"] = hunt_approval_fingerprint(row, hunt)
    payload["approval_id"] = hunt["approval_id"]
    from app.services.export_job_access import load_export_sources
    from app.services.team_assessment_access import RequestPrincipal

    sources = load_export_sources(
        RequestPrincipal(row.principal_id, source_encrypted=row.result_source_encrypted)
    )
    payload["handling_label_ids"] = sorted(
        set(payload["handling_label_ids"])
        | {str(source.captured_label_id) for source in sources}
    )
    key = f"hunt.approved:{row.id}:{hunt['id']}:{hunt['approval_id']}"
    payload["action_id"] = hashlib.sha256(key.encode()).hexdigest()
    _apply_assessment_boundary(payload)
    _bound_payload(payload)
    event = emit_integration_event(
        db,
        event_type="hunt.approved",
        source_type="item",
        source_id=item.id,
        idempotency_key=key,
        payload=payload,
        schema_version=1,
        actor_user_id=actor_user_id,
    )
    _retain_hunt_source_labels(db, event.id, row)
    _retain_extraction_label(db, event.id, item, state)
    _retain_assessment_boundary(db, event.id, item, payload)
    return event.id


def _retain_hunt_source_labels(db: Session, event_id: uuid.UUID, row) -> None:
    # The reviewed result can retain a stricter source label after a feed relabel.
    # Copy that lineage as well as the current Item envelope before publication.
    from app.services.data_access_envelopes import (
        DataAccessSourceInput,
        merge_data_access_envelope_sources,
    )
    from app.services.data_access_runtime import (
        lock_data_policy_revision_for_derivation,
    )
    from app.services.export_job_access import load_export_sources
    from app.services.team_assessment_access import RequestPrincipal

    sources = load_export_sources(
        RequestPrincipal(row.principal_id, source_encrypted=row.result_source_encrypted)
    )
    revision = lock_data_policy_revision_for_derivation(db)
    merge_data_access_envelope_sources(
        db,
        resource_type="integration_event",
        resource_id=event_id,
        sources=tuple(
            DataAccessSourceInput(
                source_type="item",
                source_id=str(source.item_id),
                source_version=f"hunt:{row.id}:{row.version}",
                # Historical labels are independent snapshots. The event's
                # direct item lineage already tracks the current feed label.
                handling_label_id=source.captured_label_id,
                captured_policy_revision=revision,
            )
            for source in sources
        ),
    )


def _retain_extraction_label(
    db: Session, event_id: uuid.UUID, item: Item, state: ItemIntelState
) -> None:
    from app.services.data_access_envelopes import (
        DataAccessSourceInput,
        merge_data_access_envelope_sources,
    )
    from app.services.data_access_runtime import (
        lock_data_policy_revision_for_derivation,
    )

    if state.handling_label_id is None:
        return
    merge_data_access_envelope_sources(
        db,
        resource_type="integration_event",
        resource_id=event_id,
        sources=(
            DataAccessSourceInput(
                source_type="item",
                source_id=str(item.id),
                source_version=f"extraction:{state.revision}",
                # The direct event source retains the live feed association.
                handling_label_id=state.handling_label_id,
                captured_policy_revision=lock_data_policy_revision_for_derivation(db),
            ),
        ),
    )


def emit_team_indicator_change(
    db: Session, *, team_id: uuid.UUID, item: Item, actor_user_id: uuid.UUID
) -> uuid.UUID | None:
    """A targeted analyst change emits one team event, never a cross-team fanout."""
    from app.services.team_indicator_policy import team_indicator_snapshot

    state = db.get(ItemIntelState, item.id)
    if state is None:
        return None
    indicators, count, _digest, _extraction = _indicator_snapshot(db, item.id)
    indicators, policy_hash = team_indicator_snapshot(
        db,
        team_id=team_id,
        item_id=item.id,
        indicators=indicators,
        source_revision=item.classification_required_version,
        extraction_revision=state.revision,
    )
    semantic = [
        {
            key: entry.get(key)
            for key in (
                "type",
                "value",
                "role",
                "assertion",
                "analyst_verdict",
                "excluded",
                "reasons",
                "assessment_label_ids",
                "assessment_lineage_complete",
            )
        }
        for entry in indicators
    ]
    digest = hashlib.sha256(_json(semantic)).hexdigest()
    db.execute(
        insert(TeamIntelState)
        .values(team_id=team_id, item_id=item.id)
        .on_conflict_do_nothing()
    )
    team_state = db.scalar(
        select(TeamIntelState)
        .where(TeamIntelState.team_id == team_id, TeamIntelState.item_id == item.id)
        .with_for_update()
    )
    if team_state.indicator_set_hash == digest:
        return None
    team_state.indicator_set_hash, team_state.revision = digest, team_state.revision + 1
    db.flush()
    article = db.execute(
        select(Article.id, Article.retrieved_at, Article.content_purged_at).where(
            Article.item_id == item.id
        )
    ).one_or_none()
    payload = _base_payload(db, item, article, state, reason="analyst_verdict")
    payload.update(
        team_id=str(team_id),
        team_intel_revision=team_state.revision,
        team_indicator_policy_hash=policy_hash,
        indicators=indicators,
        indicator_count=count,
        indicators_complete=count <= MAX_EVENT_INDICATORS,
        team_indicator_set_hash=digest,
    )
    if count > MAX_EVENT_INDICATORS:
        payload["incomplete_reason"] = "indicator_count_limit"
    payload["filter_metadata"].update(
        team_id=str(team_id),
        ioc_types=sorted({entry["type"] for entry in indicators}),
        ioc_roles=sorted({entry["role"] for entry in indicators}),
    )
    key = f"intel.indicators.changed:{team_id}:{item.id}:{team_state.revision}"
    payload["action_id"] = hashlib.sha256(key.encode()).hexdigest()
    _apply_assessment_boundary(payload)
    _bound_payload(payload)
    event = emit_integration_event(
        db,
        event_type="intel.indicators.changed",
        source_type="item",
        source_id=item.id,
        idempotency_key=key,
        payload=payload,
        schema_version=1,
        actor_user_id=actor_user_id,
    )
    _retain_extraction_label(db, event.id, item, state)
    _retain_assessment_boundary(db, event.id, item, payload)
    return event.id


def _apply_assessment_boundary(payload: dict) -> None:
    labels = set(payload["handling_label_ids"])
    for indicator in payload["indicators"]:
        labels.update(indicator.get("assessment_label_ids", []))
        if indicator.get("assessment_lineage_complete") is False:
            payload["indicators_complete"] = False
            payload["incomplete_reason"] = "assessment_lineage_unavailable"
    payload["handling_label_ids"] = sorted(labels)


def _retain_assessment_boundary(
    db: Session,
    event_id: uuid.UUID,
    item: Item,
    payload: dict,
) -> None:
    """Keep review-derived actions protected even if their source is relabeled."""
    from app.services.data_access_envelopes import (
        DataAccessSourceInput,
        merge_data_access_envelope_sources,
    )
    from app.services.data_access_runtime import (
        lock_data_policy_revision_for_derivation,
    )

    revision = lock_data_policy_revision_for_derivation(db)
    merge_data_access_envelope_sources(
        db,
        resource_type="integration_event",
        resource_id=event_id,
        sources=tuple(
            DataAccessSourceInput(
                source_type="item",
                source_id=str(item.id),
                source_version=f"indicator-review:{payload['extraction_revision']}:{label}",
                handling_label_id=uuid.UUID(label),
                captured_policy_revision=revision,
            )
            for label in payload["handling_label_ids"]
        ),
    )
