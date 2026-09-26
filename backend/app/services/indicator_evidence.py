"""Bounded joins between canonical observations and verified current AI evidence."""

from __future__ import annotations

import re
import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.item_classification import ItemClassification
from app.schemas.ai_extraction import StructuredExtractionResponse
from app.services.ai_enrichment_provenance import current_enrichment_predicate
from app.services.ioc_extraction import extract_iocs


def canonical_indicator(kind: str, value: str) -> str | None:
    """Match the entire supplied indicator; never infer a subdomain/substring link."""
    for match in extract_iocs(title=value, summary=None, article_text=None):
        if match.type == kind and match.value_raw.casefold() == value.casefold():
            return match.value_norm
    return None


def _current_verified_extraction(
    db: Session, item_id: uuid.UUID
) -> StructuredExtractionResponse | None:
    row = db.execute(
        select(
            ItemAIEnrichment.structured_extraction_json,
            ItemAIEnrichment.source_hash,
            Item.classification_required_version,
            Article.id,
            Article.retrieved_at,
        )
        .join(Item, Item.id == ItemAIEnrichment.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .join(Article, Article.item_id == Item.id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(Item.id == item_id, current_enrichment_predicate())
    ).one_or_none()
    if row is None or row.structured_extraction_json is None:
        return None
    try:
        extraction = StructuredExtractionResponse.model_validate(
            row.structured_extraction_json
        )
    except ValidationError:
        return None
    if (
        extraction.source_hash != row.source_hash
        or extraction.source_version != row.classification_required_version
        or extraction.article_id != row.id
        or extraction.article_retrieved_at != row.retrieved_at
    ):
        return None
    return extraction


def current_ai_indicator_links(
    db: Session, item_id: uuid.UUID
) -> dict[tuple[str, str], dict]:
    extraction = _current_verified_extraction(db, item_id)
    if extraction is None:
        return {}
    links: dict[tuple[str, str], dict] = {}
    for entity in extraction.entities[:24]:
        if entity.kind != "indicator":
            continue
        for match in extract_iocs(title=entity.name, summary=None, article_text=None):
            if match.value_raw.casefold() != entity.name.casefold():
                continue
            key = (match.type, match.value_norm)
            evidence = [entry.model_dump(mode="json") for entry in entity.evidence]
            candidate = {
                "role": entity.indicator_role,
                "assertion": entity.assertion,
                "evidence": evidence,
                "maliciousness_confidence": None,
            }
            previous = links.get(key)
            if previous is not None and previous["role"] != candidate["role"]:
                candidate["role"] = "unknown"
                candidate["assertion"] = "inferred"
                candidate["evidence"] = (previous["evidence"] + evidence)[:6]
            links[key] = candidate
    return links


def indicator_exclusion(kind: str, value: str, ai: dict | None) -> list[str]:
    """Conservative example/reference exclusions are not maliciousness scores."""
    reasons: list[str] = []
    if ai and ai["role"] in {"reference", "benign"}:
        reasons.append(f"ai_{ai['role']}")
    from urllib.parse import urlsplit
    import ipaddress

    host = value
    if kind == "url":
        host = urlsplit(value).hostname or ""
    elif kind == "email":
        host = value.rsplit("@", 1)[-1]
    if kind in {"domain", "url", "email"} and (
        host in {"example.com", "example.net", "example.org", "localhost"}
        or host.endswith(
            (
                ".example",
                ".invalid",
                ".test",
                ".localhost",
                ".example.com",
                ".example.net",
                ".example.org",
            )
        )
    ):
        reasons.append("reserved_example")
    if kind in {"ipv4", "ipv6", "url"}:
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return reasons
        networks = (
            ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
            if address.version == 4
            else ("2001:db8::/32",)
        )
        if any(address in ipaddress.ip_network(network) for network in networks):
            reasons.append("reserved_example")
    return reasons


_ATTACK_TECHNIQUE_ID = re.compile(
    r"(?<![A-Za-z0-9])T[0-9]{4}(?:\.[0-9]{3})?(?![A-Za-z0-9]|\.[0-9])", re.IGNORECASE
)


def current_ai_attack_techniques(db: Session, item_id: uuid.UUID) -> list[str]:
    """Return explicit source technique IDs from verified current evidence only.

    The shared extraction contract has no dedicated ATT&CK mapping field.
    Model descriptions and inferred names therefore cannot become route facts.
    More than 128 distinct IDs fails closed instead of hiding a partial list.
    """
    extraction = _current_verified_extraction(db, item_id)
    if extraction is None:
        return []
    ids = {
        match.group(0).upper()
        for entry in [*extraction.entities, *extraction.relationships]
        for evidence in entry.evidence
        for match in _ATTACK_TECHNIQUE_ID.finditer(evidence.quote)
    }
    return sorted(ids) if len(ids) <= 128 else []
