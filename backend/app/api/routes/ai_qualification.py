"""Opt-in synthetic contract qualification, with explicit cost and revision bounds."""
import uuid
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.api.deps import get_admin_user, get_authorization_context, get_data_access_context, require_token_scopes
from app.api.routes.ai_route_helpers import require_ai_enabled
from app.api.routes.ai_providers import provider_operation
from app.api.routes.team_assessments import publish_accepted_assessment
from app.core.api_errors import ApiHTTPException
from app.db.session import get_db
from app.models.ai_qualification import AIQualification
from app.models.ai_task_run import AITaskRun
from app.models.user import User
from app.services.ai_ops import queue_ai_task_run
from app.services.ai_qualification_cases import qualification_plan_fingerprint
from app.services.ai_provider_selection import PROVIDER_SELECTION_KEY
from app.services.ai_providers import get_provider
from app.services.audit import record_audit
from app.services.data_access_policy import DataAccessContext
from app.services.export_job_access import capture_export_authorization, ExportJobAccessDenied
from app.services.secret_storage import encrypt_json

router = APIRouter(prefix="/ai/providers", tags=["ai"], dependencies=[Depends(require_ai_enabled)])


class QualificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: uuid.UUID
    provider_version: int = Field(ge=1)
    features: list[Literal["extraction", "report", "hunt"]] = Field(min_length=1, max_length=3)
    token_budget: int = Field(default=24000, ge=1024, le=32000)
    authorize_provider_calls: Literal[True]


class QualificationResponse(BaseModel):
    run_id: uuid.UUID
    provider_version: int
    status: str
    token_budget: int
    reserved_tokens: int
    features: list[str]
    results: list[dict]
    error: str | None = None
    semantic_quality_approved: Literal[False] = False


def project(row: AIQualification, run: AITaskRun) -> QualificationResponse:
    return QualificationResponse(run_id=row.run_id, provider_version=row.provider_version, status=run.status,
        token_budget=row.token_budget, reserved_tokens=row.reserved_tokens, features=row.features_json,
        results=row.results_json, error=run.error)


@router.post("/{provider_id}/qualifications", response_model=QualificationResponse, status_code=202)
def queue_provider_qualification(provider_id: uuid.UUID, payload: QualificationRequest, request: Request,
    db: Session = Depends(get_db), admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes("write:ai")),
    access: DataAccessContext = Depends(get_data_access_context),
) -> QualificationResponse:
    with provider_operation(db, request):
        provider = get_provider(db, provider_id)
        db.refresh(provider, with_for_update=True)
        if provider.version != payload.provider_version:
            raise ApiHTTPException(status_code=409, error_code="provider_version_changed", detail="Provider changed. Reload before qualifying it.")
        # Serialize the short admission transaction per provider. Never retain
        # this lock during broker publication or provider I/O.
        existing = db.scalar(select(AIQualification).where(AIQualification.request_id == payload.request_id))
        features = sorted(set(payload.features))
        if existing:
            if (existing.principal_id != admin.id or existing.provider_id != provider_id
                    or existing.provider_version != payload.provider_version or existing.features_json != features
                    or existing.token_budget != payload.token_budget):
                raise ApiHTTPException(status_code=409, error_code="qualification_request_conflict", detail="Request ID was already used for different qualification settings.")
            return project(existing, db.get(AITaskRun, existing.run_id))
        count = db.scalar(select(func.count()).select_from(AIQualification).join(AITaskRun, AITaskRun.id == AIQualification.run_id)
            .where(AIQualification.provider_id == provider_id, AITaskRun.status.in_(["queued", "running"])))
        if count >= 2:
            raise ApiHTTPException(status_code=429, error_code="qualification_capacity", detail="Two qualifications are already pending for this provider. Wait or cancel one in AI Operations.")
        try:
            authorization = capture_export_authorization(request, get_authorization_context(request), access)
        except ExportJobAccessDenied as exc:
            raise ApiHTTPException(status_code=403, error_code="qualification_credential_required", detail="A current durable credential is required.") from exc
        run = queue_ai_task_run(db, task_type="connection_test", trigger_source="manual", actor_user_id=admin.id,
            metadata={"qualification": True, "qualification_plan_sha256": qualification_plan_fingerprint(features), PROVIDER_SELECTION_KEY: {"provider_id": str(provider.id), "version": provider.version, "model": provider.model}})
        row = AIQualification(run_id=run.id, request_id=payload.request_id, provider_id=provider_id, provider_version=provider.version,
            principal_type="user", principal_id=admin.id, authorization_encrypted=encrypt_json(authorization.model_dump(mode="json")),
            token_budget=payload.token_budget, reserved_tokens=0, features_json=features, results_json=[])
        db.add(row)
        record_audit(db, actor_user_id=admin.id, action="ai.provider.qualify", resource_type="ai_provider_configuration", resource_id=str(provider_id),
            metadata={"run_id": str(run.id), "features": features, "token_budget": payload.token_budget, "provider_version": provider.version})
        response = project(row, run)
        db.commit()
    publish_accepted_assessment(db, response.run_id)
    return response


@router.get("/{provider_id}/qualifications", response_model=list[QualificationResponse])
def list_provider_qualifications(provider_id: uuid.UUID, request: Request, db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user), _scope: User = Depends(require_token_scopes("read:ai")),
) -> list[QualificationResponse]:
    with provider_operation(db, request):
        get_provider(db, provider_id)
        rows = db.execute(select(AIQualification, AITaskRun).join(AITaskRun, AITaskRun.id == AIQualification.run_id)
            .where(AIQualification.provider_id == provider_id).order_by(AIQualification.created_at.desc(), AIQualification.run_id.desc()).limit(20)).all()
        return [project(row, run) for row, run in rows]
