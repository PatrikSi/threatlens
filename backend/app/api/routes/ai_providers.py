"""Administrative provider catalog; credentials are write-only."""

from __future__ import annotations

import uuid
from dataclasses import replace
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.exc import (
    IntegrityError,
    OperationalError,
    TimeoutError as PoolTimeoutError,
)
from sqlalchemy.orm import Session

from app.api.deps import get_admin_user, require_token_scopes
from app.api.routes.ai_route_helpers import require_ai_enabled
from app.api.routes.ai_policy_helpers import require_ai_authorization_context
from app.core.token_scopes import SCOPE_READ_AI, SCOPE_WRITE_AI
from app.core.api_errors import ApiHTTPException
from app.db.budgets import DatabaseDeadlineExceeded, database_operation
from app.db.session import get_db
from app.models.user import User
from app.schemas.ai import AIProviderTestConnectionRequest, AITestConnectionResponse
from app.services.ai_config import load_active_ai_settings
from app.services.ai_integration import test_ai_connection
from app.services.ai_ops import (
    AI_TASK_TYPE_CONNECTION_TEST,
    AI_TRIGGER_MANUAL,
    AI_STATUS_READY,
    AI_STATUS_ERROR,
    queue_ai_task_run,
    start_ai_task_run,
    finish_ai_task_run,
)
from app.services.ai_provider_selection import PROVIDER_SELECTION_KEY
from app.schemas.ai_providers import (
    AIProviderCreate,
    AIProviderListResponse,
    AIProviderResponse,
    AIProviderRoutingResponse,
    AIProviderRoutingUpdate,
    AIProviderUpdate,
)
from app.services.ai_providers import (
    AIProviderError,
    create_provider,
    delete_provider,
    get_provider,
    get_provider_routing,
    list_providers,
    provider_response,
    routing_response,
    update_provider,
    update_provider_routing,
)
from app.services.audit import record_audit
from app.services.authorization import (
    AuthorizationStateUnavailable,
    fence_authorization_context,
)

router = APIRouter(
    prefix="/ai", tags=["ai"], dependencies=[Depends(require_ai_enabled)]
)


@contextmanager
def provider_operation(db: Session, request: Request | None = None) -> Iterator[None]:
    try:
        with database_operation(db, operation="interactive"):
            if request is not None:
                fence_authorization_context(
                    db, require_ai_authorization_context(request)
                )
            yield
    except AIProviderError as exc:
        raise ApiHTTPException(
            status_code=exc.status_code,
            error_code=exc.code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    except IntegrityError as exc:
        raise ApiHTTPException(
            status_code=409,
            error_code="provider_configuration_conflict",
            detail={
                "code": "provider_configuration_conflict",
                "message": "AI provider configuration changed concurrently. Refresh settings before retrying.",
            },
        ) from exc
    except AuthorizationStateUnavailable as exc:
        raise ApiHTTPException(
            status_code=409,
            error_code="provider_authorization_changed",
            detail={
                "code": "provider_authorization_changed",
                "message": "Your permissions changed while updating AI settings. Refresh and retry.",
            },
        ) from exc
    except (DatabaseDeadlineExceeded, OperationalError, PoolTimeoutError) as exc:
        raise ApiHTTPException(
            status_code=503,
            error_code="provider_configuration_unavailable",
            detail={
                "code": "provider_configuration_unavailable",
                "message": "AI provider settings are temporarily busy or unavailable. Retry shortly.",
            },
            headers={"Retry-After": "2"},
        ) from exc


@router.get("/providers", response_model=AIProviderListResponse)
def list_ai_providers_route(
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str = Query(default="", max_length=200),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    with provider_operation(db):
        providers, total = list_providers(db, search=search, limit=limit, offset=offset)
        return AIProviderListResponse(
            items=[provider_response(provider) for provider in providers],
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get("/providers/{provider_id}", response_model=AIProviderResponse)
def get_ai_provider_route(
    provider_id: uuid.UUID,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    with provider_operation(db):
        return provider_response(get_provider(db, provider_id))


@router.post("/providers", response_model=AIProviderResponse, status_code=201)
def create_ai_provider_route(
    payload: AIProviderCreate,
    response: Response,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    with provider_operation(db, request):
        provider, created = create_provider(db, payload)
        if created:
            _audit_provider(
                db,
                actor,
                action="create",
                provider_id=provider.id,
                version=provider.version,
            )
        else:
            response.status_code = 200
        result = provider_response(provider)
        db.commit()
        return result


@router.put("/providers/{provider_id}", response_model=AIProviderResponse)
def update_ai_provider_route(
    provider_id: uuid.UUID,
    payload: AIProviderUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    with provider_operation(db, request):
        provider = update_provider(db, provider_id, payload)
        _audit_provider(
            db,
            actor,
            action="update",
            provider_id=provider.id,
            version=provider.version,
        )
        result = provider_response(provider)
        db.commit()
        return result


@router.delete("/providers/{provider_id}", status_code=204)
def delete_ai_provider_route(
    provider_id: uuid.UUID,
    request: Request,
    version: int = Query(ge=1),
    db: Session = Depends(get_db),
    actor: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    with provider_operation(db, request):
        delete_provider(db, provider_id, version=version)
        _audit_provider(
            db, actor, action="delete", provider_id=provider_id, version=version
        )
        db.commit()
    return Response(status_code=204)


@router.get("/provider-routing", response_model=AIProviderRoutingResponse)
def get_ai_provider_routing_route(
    db: Session = Depends(get_db),
    _admin: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_READ_AI)),
):
    with provider_operation(db):
        return routing_response(get_provider_routing(db))


@router.put("/provider-routing", response_model=AIProviderRoutingResponse)
def update_ai_provider_routing_route(
    payload: AIProviderRoutingUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(get_admin_user),
    _scope: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):
    with provider_operation(db, request):
        routing = update_provider_routing(db, payload)
        result = routing_response(routing)
        record_audit(
            db,
            actor_user_id=actor.id,
            action="ai.provider_routing.update",
            resource_type="ai_provider_routing",
            resource_id="1",
            metadata=result.model_dump(mode="json"),
        )
        db.commit()
        return result


def _audit_provider(
    db: Session, actor: User, *, action: str, provider_id: uuid.UUID, version: int
) -> None:
    record_audit(
        db,
        actor_user_id=actor.id,
        action=f"ai.provider.{action}",
        resource_type="ai_provider_configuration",
        resource_id=str(provider_id),
        metadata={"version": version},
    )


@router.post(
    "/providers/{provider_id}/test-connection",
    response_model=AITestConnectionResponse,
    dependencies=[Depends(require_ai_enabled)],
)
def test_ai_provider_connection_route(
    provider_id: uuid.UUID,
    payload: AIProviderTestConnectionRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: User = Depends(get_admin_user),
    _scope_user: User = Depends(require_token_scopes(SCOPE_WRITE_AI)),
):

    authorization = require_ai_authorization_context(request)
    with provider_operation(db, request):
        provider = get_provider(db, provider_id)
        if provider.version != payload.version:
            raise AIProviderError(
                "provider_version_changed",
                "The provider changed. Reload it before testing.",
                status_code=409,
            )
        active = load_active_ai_settings(db, provider_id=provider_id)
        if not active.ai_configured:
            raise AIProviderError(
                active.configuration_error_code or "provider_not_configured",
                active.configuration_error or "The provider is not configured.",
                status_code=422,
            )
        run = queue_ai_task_run(
            db,
            task_type=AI_TASK_TYPE_CONNECTION_TEST,
            trigger_source=AI_TRIGGER_MANUAL,
            actor_user_id=admin.id,
            model=active.model,
            metadata={
                PROVIDER_SELECTION_KEY: {
                    "provider_id": str(provider.id),
                    "version": provider.version,
                    "model": provider.model,
                }
            },
        )
        start_ai_task_run(db, run_id=run.id, worker_name="api")
        db.commit()

    # This small, explicit test does not wait for unrelated provider workloads.
    active = replace(
        active,
        max_completion_tokens=128,
        request_max_retries=0,
        request_timeout_seconds=min(active.request_timeout_seconds, 30),
    )
    result = test_ai_connection(
        db,
        task_run_id=run.id,
        active_settings=active,
        request_authorization=authorization,
    )
    finish_ai_task_run(
        db,
        run_id=run.id,
        status=AI_STATUS_READY if result.success else AI_STATUS_ERROR,
        reason=None if result.success else "connection_test_failed",
        error=result.error,
        worker_name="api",
        model=result.model,
        latency_ms=result.latency_ms,
    )
    record_audit(
        db,
        actor_user_id=admin.id,
        action="ai.provider.test",
        resource_type="ai_provider",
        resource_id=str(provider_id),
        success=result.success,
        metadata={"version": payload.version, "run_id": str(run.id)},
    )
    db.commit()
    return result
