"""Additional extraction is explicit, source-pinned, bounded and idempotent."""
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.api.deps import get_admin_user, get_authorization_context, get_data_access_context, require_token_scopes
from app.api.routes.ai_route_helpers import require_ai_enabled
from app.api.routes.ai_providers import provider_operation
from app.api.routes.team_assessments import publish_accepted_assessment
from app.core.api_errors import ApiHTTPException
from app.db.session import get_db
from app.models.ai_article_continuation import AIArticleContinuation
from app.models.ai_task_run import AITaskRun
from app.models.feed import Feed
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.user import User
from app.services.ai_article_continuation import (
    MAX_AUTHORIZED_SECTIONS, MAX_AUTHORIZED_TOKENS, progress_digest, recover_unsent_sections,
)
from app.services.ai_extraction_sections import MAX_SECTIONS, TOTAL_TOKEN_BUDGET, extraction_progress_response
from app.services.ai_ops import queue_ai_task_run
from app.services.ai_provider_selection import PROVIDER_SELECTION_KEY
from app.services.audit import record_audit
from app.services.data_access_policy import DataAccessContext, handling_label_access_predicate
from app.services.export_job_access import capture_export_authorization, ExportJobAccessDenied
from app.services.secret_storage import encrypt_json

router = APIRouter(prefix="/ai/articles", tags=["ai"], dependencies=[Depends(require_ai_enabled)])


class ContinuationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    progress_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class ContinuationResponse(BaseModel):
    run_id: uuid.UUID
    section_limit: int
    token_budget: int


def conflict(message: str) -> ApiHTTPException:
    return ApiHTTPException(status_code=409, error_code="extraction_continuation_conflict", detail=message)


@router.post("/{item_id}/continue", response_model=ContinuationResponse, status_code=202)
def continue_article_extraction(
    item_id: uuid.UUID, payload: ContinuationRequest, request: Request,
    db: Session = Depends(get_db), admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes("write:ai", "read:items")),
    access: DataAccessContext = Depends(get_data_access_context),
) -> ContinuationResponse:
    with provider_operation(db, request):
        item = db.scalar(select(Item).join(Feed, Feed.id == Item.feed_id).where(
            Item.id == item_id, handling_label_access_predicate(Feed.handling_label_id, access))
            .with_for_update(of=Item))
        if item is None:
            raise ApiHTTPException(status_code=404, error_code="item_not_found", detail="Article is unavailable.")
        existing = db.scalar(select(AIArticleContinuation).where(AIArticleContinuation.request_id == payload.request_id))
        if existing is not None:
            if (existing.principal_id != admin.id or existing.item_id != item_id
                    or existing.expected_progress_digest != payload.progress_revision):
                raise conflict("This request ID belongs to a different continuation. Refresh and retry.")
            return ContinuationResponse(run_id=existing.run_id, section_limit=existing.section_limit, token_budget=existing.token_budget)
        row = db.scalar(select(ItemAIEnrichment).where(ItemAIEnrichment.item_id == item_id).with_for_update())
        progress = row.extraction_progress_json if row else None
        coverage = extraction_progress_response(progress)
        if not progress or not coverage or coverage.uncovered_chars == 0:
            raise conflict("No uncovered section progress is available. Refresh article evidence.")
        if progress_digest(progress) != payload.progress_revision:
            raise conflict("Extraction progress changed. Refresh before authorizing additional sections.")
        pending = db.scalar(select(AITaskRun.id).where(AITaskRun.item_id == item_id,
            AITaskRun.task_type == "item_enrichment", AITaskRun.status.in_(["queued", "running"])).limit(1))
        if pending is not None:
            raise conflict("Article enrichment is already queued or running. Wait for it to finish.")
        try:
            recover_unsent_sections(db, item_id=item_id, progress=progress)
        except ValueError as exc:
            raise conflict(str(exc)) from exc
        section_limit = min(MAX_AUTHORIZED_SECTIONS, coverage.call_limit + MAX_SECTIONS)
        token_budget = min(MAX_AUTHORIZED_TOKENS, coverage.token_budget + TOTAL_TOKEN_BUDGET)
        if section_limit == coverage.call_limit and token_budget == coverage.token_budget:
            raise conflict("This article reached the 32-section / 256,000-token authorization ceiling. Review the remaining source manually.")
        try:
            previous = db.get(AITaskRun, uuid.UUID(progress["task_run_id"]))
        except (KeyError, ValueError, TypeError):
            previous = None
        if previous is None or PROVIDER_SELECTION_KEY not in (previous.metadata_json or {}):
            raise conflict("The original provider selection is unavailable. Start a new article reprocessing task.")
        try:
            authorization = capture_export_authorization(request, get_authorization_context(request), access)
        except ExportJobAccessDenied as exc:
            raise conflict("A current durable credential is required for continuation.") from exc
        run = queue_ai_task_run(db, task_type="item_enrichment", trigger_source="manual", actor_user_id=admin.id,
            item_id=item_id, metadata={"force": True, "extraction_continuation": True,
                PROVIDER_SELECTION_KEY: previous.metadata_json[PROVIDER_SELECTION_KEY]})
        db.add(AIArticleContinuation(run_id=run.id, item_id=item_id, request_id=payload.request_id,
            principal_type="user", principal_id=admin.id, authorization_encrypted=encrypt_json(authorization.model_dump(mode="json")),
            expected_progress_digest=payload.progress_revision, section_limit=section_limit, token_budget=token_budget))
        record_audit(db, actor_user_id=admin.id, action="ai.extraction.continue", resource_type="item", resource_id=str(item_id),
            metadata={"run_id": str(run.id), "section_limit": section_limit, "token_budget": token_budget})
        response = ContinuationResponse(run_id=run.id, section_limit=section_limit, token_budget=token_budget)
        db.commit()
    publish_accepted_assessment(db, response.run_id)
    return response
