"""Durable, bounded section extraction using the normal authorization/receipt runtime.

Only completed checkpoints are reusable. A sent request without a checkpoint is
never replayed here: its normal provider receipt determines whether recovery is
safe. Coordinates refer to whitespace-normalized article text, as in v1 prompts.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.article import Article
from app.models.item import Item
from app.schemas.ai_extraction import ExtractionCoverage, StructuredExtractionResponse
from app.services.ai_config import ActiveAISettings
from app.services.ai_extraction import build_verified_extraction
from app.services.ai_provider_client import AICompletionResult, AIIntegrationError
from app.services.ai_provider_protocol import provider_output_ceiling
from app.services.ai_request_runtime import AITaskRunStoppedError
from app.services.ai_request_identity import ai_request_fingerprint
from app.services.report_prompt_budget import estimate_message_tokens

SECTION_CHARS = 8_000
MAX_SECTIONS = 8
TOTAL_TOKEN_BUDGET = 64_000
SECTION_OUTPUT_LIMIT = 8_192
MAX_MERGED_ENTITIES = 96
MAX_MERGED_RELATIONSHIPS = 96


def section_execution_checkpoint(
    db: Session, *, item_id: UUID, task_run_id: UUID | None, claim_updated_at: datetime,
    snapshot: dict, observe_stop: Callable[..., str | None],
) -> Callable[[], None]:
    def checkpoint() -> None:
        reason = observe_stop(db, task_run_id=task_run_id, stage="extraction_section_checkpoint", lock=True)
        if reason is not None:
            raise AITaskRunStoppedError(reason)
        current = db.scalar(select(ItemAIEnrichment.item_id).join(Item, Item.id == ItemAIEnrichment.item_id)
            .join(Article, Article.item_id == Item.id).where(
                Item.id == item_id, ItemAIEnrichment.status == "pending",
                ItemAIEnrichment.updated_at == claim_updated_at,
                Item.classification_required_version == snapshot["source_version"],
                Article.id == snapshot["article_id"], Article.retrieved_at == snapshot["article_retrieved_at"],
            ))
        if current is None:
            db.execute(update(ItemAIEnrichment).where(
                ItemAIEnrichment.item_id == item_id, ItemAIEnrichment.status == "pending",
                ItemAIEnrichment.updated_at == claim_updated_at,
            ).values(status="error", updated_at=claim_updated_at,
                     error="Source evidence changed during extraction. Reprocess the current article."))
            raise AITaskRunStoppedError("stale_result_discarded")
    return checkpoint


def plan_sections(text: str) -> list[dict]:
    """Use sentence boundaries where available without leaving holes or looping."""
    sections = []
    start = 0
    while start < len(text) and len(sections) < MAX_SECTIONS:
        end = min(len(text), start + SECTION_CHARS)
        if end < len(text):
            boundary = text.rfind(". ", start + SECTION_CHARS * 3 // 4, end)
            if boundary >= 0:
                end = boundary + 2
        sections.append({"index": len(sections), "start": start, "end": end, "status": "pending"})
        start = end
    return sections


def extraction_progress_response(progress: dict | None) -> ExtractionCoverage | None:
    """Never expose checkpoint provider payloads or task IDs in public progress."""
    if not progress:
        return None
    try:
        sections = [{key: section[key] for key in ("index", "start", "end", "status")}
                    for section in progress["sections"]]
        processed = sum(section["end"] - section["start"] for section in sections
                        if section["status"] == "completed")
        return ExtractionCoverage(
            source_hash=progress["source_hash"], normalized_text_chars=progress["text_chars"],
            processed_chars=processed, uncovered_chars=max(0, progress["text_chars"] - processed),
            reserved_tokens=progress["reserved_tokens"], token_budget=TOTAL_TOKEN_BUDGET,
            call_limit=MAX_SECTIONS, sections=sections,
            output_limited=progress.get("output_limited", False),
        )
    except (KeyError, TypeError, ValueError):
        return None


def section_messages(messages: list[dict[str, str]], text: str, section: dict) -> list[dict[str, str]]:
    result = copy.deepcopy(messages)
    payload = json.loads(result[-1]["content"])
    payload["item"]["article_text"] = text[section["start"]:section["end"]]
    payload["extraction_section"] = {
        "index": section["index"], "start": section["start"], "end": section["end"],
        "instructions": "This is one article section. Extract only supplied evidence; missing context is an information gap.",
    }
    result[-1]["content"] = json.dumps(payload)
    return result


def section_plan_fingerprint(
    active: ActiveAISettings, *, item_id: UUID, messages: list[dict[str, str]],
    text: str, snapshot: dict,
) -> str:
    """Bind every planned request, including unsent sections, to one logical run."""
    requests = []
    for section in plan_sections(text):
        prompt = section_messages(messages, text, section)
        output_tokens = min(active.max_completion_tokens, SECTION_OUTPUT_LIMIT, provider_output_ceiling(active, prompt))
        requests.append({
            "section": section, "output_tokens": output_tokens,
            "request_fingerprint": ai_request_fingerprint(
                active=active, feature_type="item_enrichment", messages=prompt,
                item_id=item_id, daily_brief_id=None, report_id=None,
                requested_max_tokens=output_tokens,
            ),
        })
    identity = {
        "version": 1, "source_hash": snapshot["source_hash"],
        "article_id": str(snapshot["article_id"]), "source_version": snapshot["source_version"],
        "article_retrieved_at": snapshot["article_retrieved_at"].isoformat(),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "token_budget": TOTAL_TOKEN_BUDGET, "call_limit": MAX_SECTIONS,
        "section_chars": SECTION_CHARS, "output_limit": SECTION_OUTPUT_LIMIT,
        "requests": requests,
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _save_progress(db: Session, *, item_id: UUID, claim_updated_at: datetime,
                   progress: dict, checkpoint: Callable[[], None]) -> None:
    checkpoint()
    saved = db.execute(update(ItemAIEnrichment).where(
        ItemAIEnrichment.item_id == item_id, ItemAIEnrichment.status == "pending",
        ItemAIEnrichment.updated_at == claim_updated_at,
    ).values(extraction_progress_json=copy.deepcopy(progress), updated_at=claim_updated_at))
    if saved.rowcount != 1:
        raise AITaskRunStoppedError("stale_result_discarded")
    db.commit()


def _completion_snapshot(completion: AICompletionResult, *, retain_content: bool) -> dict:
    snapshot = {key: getattr(completion, key) for key in (
        "provider", "model", "latency_ms", "prompt_tokens", "completion_tokens",
        "total_tokens", "prompt_char_count", "response_char_count",
    )}
    snapshot["payload"] = ({key: completion.payload.get(key) for key in (
        "summary_text", "relevance_score", "relevance_reasons",
    )} if retain_content else {})
    return snapshot


def run_section_extraction(
    db: Session, active: ActiveAISettings, *, item_id: UUID, task_run_id: UUID | None,
    claim_updated_at: datetime, messages: list[dict[str, str]], article_text: str,
    snapshot: dict, request: Callable[..., AICompletionResult], checkpoint: Callable[[], None],
) -> tuple[AICompletionResult, dict]:
    text = " ".join(article_text.split())
    checkpoint()
    previous = db.scalar(select(ItemAIEnrichment.extraction_progress_json).where(ItemAIEnrichment.item_id == item_id))
    # Explicit new jobs can make a new attempt. Redeliveries of the same logical
    # task retain both successful sections and their conservative reservations.
    run_key = str(task_run_id) if task_run_id is not None else claim_updated_at.isoformat()
    plan_fingerprint = section_plan_fingerprint(
        active, item_id=item_id, messages=messages, text=text, snapshot=snapshot,
    )
    if previous and previous.get("task_run_id") == run_key:
        if previous.get("plan_fingerprint") != plan_fingerprint:
            raise AIIntegrationError(
                "The article extraction plan changed since this task started. "
                "Its completed checkpoints and token reservations were retained. "
                "Review the prior task outcome, then start a new reprocessing task for the current evidence and settings.",
                provider_io_outcome="not_sent", failure_category="extraction_plan_changed", retryable=False,
            )
        progress = copy.deepcopy(previous)
    else:
        progress = {
            "task_run_id": run_key, "plan_fingerprint": plan_fingerprint,
            "source_hash": snapshot["source_hash"],
            "article_retrieved_at": snapshot["article_retrieved_at"].isoformat(),
            "text_chars": len(text), "reserved_tokens": 0, "sections": plan_sections(text),
        }
    def save() -> None:
        _save_progress(db, item_id=item_id, claim_updated_at=claim_updated_at,
                       progress=progress, checkpoint=checkpoint)
    save()
    for section in progress["sections"]:
        checkpoint()
        if section["status"] == "completed":
            continue
        prompt = section_messages(messages, text, section)
        output_tokens = min(active.max_completion_tokens, SECTION_OUTPUT_LIMIT, provider_output_ceiling(active, prompt))
        reservation = estimate_message_tokens(prompt) + max(output_tokens, 0)
        if section["status"] == "pending":
            if output_tokens < 128 or progress["reserved_tokens"] + reservation > TOTAL_TOKEN_BUDGET:
                break
            progress["reserved_tokens"] += reservation
            section["status"] = "started"
            section["output_tokens"] = output_tokens
            section["request_fingerprint"] = ai_request_fingerprint(
                active=active, feature_type="item_enrichment", messages=prompt,
                item_id=item_id, daily_brief_id=None, report_id=None,
                requested_max_tokens=output_tokens,
            )
            save()  # Reserve the complete call budget before any external I/O.
        try:
            completion = request(
                db, active, feature_type="item_enrichment", item_id=item_id, task_run_id=task_run_id,
                provider_operation_scope=f"item_extraction_section:{section['index']}", messages=prompt,
                max_completion_tokens=section["output_tokens"], max_retry_completion_tokens=section["output_tokens"],
                max_provider_attempts=1, execution_checkpoint=checkpoint,
            )
        except AIIntegrationError as error:
            if error.failure_category == "truncated_output":
                raise AIIntegrationError(
                    f"Article extraction section {section['index'] + 1} exhausted its "
                    f"{section['output_tokens']:,}-token completion budget. Section output is capped at "
                    f"{SECTION_OUTPUT_LIMIT:,} tokens. Review default tokens, model context, and reasoning controls; "
                    "increasing defaults above the section cap will not increase this request. "
                    "Completed section checkpoints have been retained.",
                    provider_io_outcome=error.provider_io_outcome, failure_category=error.failure_category,
                    retryable=False, prompt_tokens=error.prompt_tokens, completion_tokens=error.completion_tokens,
                    total_tokens=error.total_tokens, latency_ms=error.latency_ms,
                ) from error
            raise
        verified = build_verified_extraction(completion.payload.get("structured_extraction"),
                                             messages=prompt, **snapshot)
        for entity in [*verified["entities"], *verified["relationships"]]:
            for passage in entity["evidence"]:
                if passage["source"] == "article_text":
                    passage["start"] += section["start"]
                    passage["end"] += section["start"]
        section.update(status="completed", extraction=verified,
                       completion=_completion_snapshot(completion, retain_content=section["index"] == 0))
        save()
    completed = [section for section in progress["sections"] if section["status"] == "completed"]
    if not completed:
        raise AIIntegrationError(
            "Article extraction could not fit a section within the model context and total token budget. "
            "Review the selected model context and output limits.", provider_io_outcome="not_sent", retryable=False,
        )
    merged, limited = merge_extractions([section["extraction"] for section in completed])
    progress["output_limited"] = limited
    save()
    coverage = extraction_progress_response(progress)
    assert coverage is not None
    merged["coverage"] = coverage.model_dump(mode="json")
    merged["truncated"] = coverage.uncovered_chars > 0
    merged["input_sha256"] = hashlib.sha256("".join(
        section["extraction"]["input_sha256"] for section in completed
    ).encode("ascii")).hexdigest()
    if coverage.uncovered_chars or limited:
        merged["information_gaps"] = [
            "Extraction coverage or output was limited by the bounded section budget; review uncovered evidence.",
            *merged["information_gaps"],
        ][:8]
    first = AICompletionResult(**completed[0]["completion"])
    totals = {}
    for key in ("latency_ms", "prompt_tokens", "completion_tokens", "total_tokens", "prompt_char_count", "response_char_count"):
        values = [section["completion"][key] for section in completed]
        totals[key] = sum(values) if all(value is not None for value in values) else None
    return replace(first, **totals), merged


def merge_extractions(extractions: list[dict]) -> tuple[dict, bool]:
    """Deduplicate equivalent identities without promoting contradictory roles."""
    merged = {**extractions[0], "entities": [], "relationships": [], "information_gaps": []}
    entities: dict[tuple, dict] = {}
    relations: dict[tuple, dict] = {}
    limited = False
    for extraction in extractions:
        identifiers = {}
        for entity in extraction["entities"]:
            key = (entity["kind"], entity["name"].casefold(), entity["assertion"],
                   entity["indicator_role"], tuple(sorted(entity["versions"])), entity["description"].casefold())
            existing = entities.get(key)
            if existing is None:
                if len(entities) >= MAX_MERGED_ENTITIES:
                    limited = True
                    continue
                existing = {**copy.deepcopy(entity), "id": f"e{len(entities) + 1}"}
                entities[key] = existing
            else:
                _merge_passages(existing, entity)
            identifiers[entity["id"]] = existing["id"]
        for relation in extraction["relationships"]:
            source = identifiers.get(relation["source_entity_id"])
            target = identifiers.get(relation["target_entity_id"])
            if source is None or target is None or source == target:
                continue
            key = (source, target, relation["relationship"].casefold(), relation["assertion"],
                   relation["description"].casefold())
            if key in relations:
                _merge_passages(relations[key], relation)
            elif len(relations) < MAX_MERGED_RELATIONSHIPS:
                relations[key] = {**copy.deepcopy(relation), "source_entity_id": source, "target_entity_id": target}
            else:
                limited = True
        for gap in extraction["information_gaps"]:
            if gap not in merged["information_gaps"] and len(merged["information_gaps"]) < 8:
                merged["information_gaps"].append(gap)
    merged["entities"] = list(entities.values())
    merged["relationships"] = list(relations.values())
    return merged, limited


def _merge_passages(existing: dict, incoming: dict) -> None:
    for passage in incoming["evidence"]:
        if passage not in existing["evidence"] and len(existing["evidence"]) < 3:
            existing["evidence"].append(copy.deepcopy(passage))


def checkpointed_receipt_fingerprints(resource: ItemAIEnrichment | None, *, run_id: UUID) -> set[str] | None:
    """Recovery may bypass successful receipts only with matching durable output."""
    progress = resource.extraction_progress_json if resource is not None else None
    if (not isinstance(progress, dict) or progress.get("task_run_id") != str(run_id)
            or progress.get("source_hash") != resource.source_hash
            or extraction_progress_response(progress) is None):
        return None
    fingerprints = set()
    try:
        for section in progress["sections"]:
            if section["status"] != "completed":
                continue
            parsed = StructuredExtractionResponse.model_validate(section["extraction"])
            AICompletionResult(**section["completion"])
            fingerprint = section["request_fingerprint"]
            if (parsed.source_hash != progress["source_hash"] or not isinstance(fingerprint, str)
                    or len(fingerprint) != 64):
                return None
            fingerprints.add(fingerprint)
    except (TypeError, ValueError, KeyError):
        return None
    return fingerprints
