from __future__ import annotations

import uuid
from typing import Annotated, Literal, TypeVar

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import require_permissions
from app.api.governance_support import (
    authorize_governance_actor,
    commit_governance_mutation,
    governance_authorization_http_error,
    raise_governance_storage_error,
    record_governance_audit,
    record_rejected_governance_mutation,
)
from app.api.sensitive_action_auth import require_sensitive_browser_session
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import (
    SCOPE_READ_ACCESS_REVIEWS,
    SCOPE_WRITE_ACCESS_REVIEWS,
)
from app.db.session import get_db
from app.models.access_review import AccessReviewCampaign, AccessReviewItem
from app.models.auth_session import AuthSession
from app.models.governance_operation_receipt import GovernanceOperationReceipt
from app.models.user import User
from app.schemas.access_review import (
    AccessReviewApplyItemRequest,
    AccessReviewApplyReceiptResponse,
    AccessReviewBeginApplyRequest,
    AccessReviewCampaignCreate,
    AccessReviewCampaignListResponse,
    AccessReviewCampaignResponse,
    AccessReviewDecisionBatchRequest,
    AccessReviewItemListResponse,
    AccessReviewResolveItemRequest,
    AccessReviewTransitionRequest,
)
from app.services.access_review_apply import (
    apply_access_review_item,
    resolve_access_review_item,
)
from app.services.access_review_mutations import coordinate_access_review_revocation
from app.services.access_review_queries import (
    AccessReviewQueryInvalid,
    get_access_review_campaign,
    list_access_review_campaigns,
    list_access_review_items,
)
from app.services.access_reviews import (
    AccessReviewConflict,
    AccessReviewError,
    AccessReviewForbidden,
    AccessReviewLimitExceeded,
    AccessReviewNotFound,
    AccessReviewSelectionInvalid,
    begin_access_review_apply,
    cancel_access_review_campaign,
    close_access_review_campaign,
    complete_access_review_apply,
    create_access_review_campaign,
    record_access_review_decisions,
)
from app.services.governance_authorization import GovernanceAuthorizationDenied
from app.services.governance_idempotency import (
    GovernanceIdempotencyError,
    GovernanceIdempotencyKeyInvalid,
    GovernanceOperationIdentity,
    build_governance_operation_identity,
    find_governance_operation_replay,
    governance_operation_replay_payload,
    lock_governance_operation_identity,
    record_governance_operation_receipt,
)


router = APIRouter(prefix="/iam/access-reviews", tags=["access reviews"])
_BROWSER_ONLY = {"x-threatlens-browser-session-only": True}
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


@router.get("", response_model=AccessReviewCampaignListResponse)
def get_access_review_campaigns(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    campaign_status: Literal[
        "open", "closed", "applying", "applied", "cancelled", "quarantined"
    ]
    | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_ACCESS_REVIEWS)),
) -> AccessReviewCampaignListResponse:
    try:
        return list_access_review_campaigns(
            db,
            page=page,
            page_size=page_size,
            status=campaign_status,
        )
    except AccessReviewError as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="access_review", operation="list_campaigns", exc=exc
        )


@router.get("/{campaign_id}", response_model=AccessReviewCampaignResponse)
def get_access_review_campaign_route(
    campaign_id: uuid.UUID,
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_ACCESS_REVIEWS)),
) -> AccessReviewCampaignResponse:
    try:
        return get_access_review_campaign(db, campaign_id)
    except AccessReviewError as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="access_review", operation="get_campaign", exc=exc
        )


@router.get(
    "/{campaign_id}/items",
    response_model=AccessReviewItemListResponse,
)
def get_access_review_items(
    campaign_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    item_type: str | None = Query(default=None, max_length=32),
    principal_type: str | None = Query(default=None, max_length=24),
    decision: str | None = Query(default=None, max_length=16),
    apply_outcome: str | None = Query(default=None, max_length=32),
    db: Session = Depends(get_db),
    _reader: User = Depends(require_permissions(SCOPE_READ_ACCESS_REVIEWS)),
) -> AccessReviewItemListResponse:
    try:
        return list_access_review_items(
            db,
            campaign_id=campaign_id,
            page=page,
            page_size=page_size,
            item_type=item_type,
            principal_type=principal_type,
            decision=decision,
            apply_outcome=apply_outcome,
        )
    except AccessReviewError as exc:
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="access_review", operation="list_items", exc=exc
        )


@router.post(
    "",
    response_model=AccessReviewCampaignResponse,
    status_code=status.HTTP_201_CREATED,
    openapi_extra=_BROWSER_ONLY,
)
def post_access_review_campaign(
    payload: AccessReviewCampaignCreate,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ACCESS_REVIEWS)),
) -> AccessReviewCampaignResponse:
    action = "access_reviews.campaign.create"
    try:
        identity = _operation_identity(
            idempotency_key,
            operation="access_review.create",
            payload=payload.model_dump(mode="json"),
        )
        locked_actor, replay, _session = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            action=action,
            operation_label="creating an access-review campaign",
        )
        if replay is not None:
            return _replay_response(response, replay, AccessReviewCampaignResponse)
        campaign = create_access_review_campaign(
            db,
            creator=locked_actor,
            payload=payload,
        )
        rendered = get_access_review_campaign(db, campaign.id)
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            campaign_id=campaign.id,
            rendered=rendered,
            http_status=status.HTTP_201_CREATED,
        )
        _record_campaign_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            rendered=rendered,
            metadata={"scope": rendered.scope_snapshot},
        )
        commit_governance_mutation(db, action=action)
        _set_mutation_headers(response, rendered.revision, changed=True)
        return rendered
    except (
        AccessReviewError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, None, exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="access_review", operation="create_campaign", exc=exc
        )


@router.post(
    "/{campaign_id}/decisions",
    response_model=AccessReviewCampaignResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_access_review_decisions(
    campaign_id: uuid.UUID,
    payload: AccessReviewDecisionBatchRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ACCESS_REVIEWS)),
) -> AccessReviewCampaignResponse:
    action = "access_reviews.decisions.record"
    return _campaign_mutation(
        db,
        request=request,
        response=response,
        actor=actor,
        idempotency_key=idempotency_key,
        campaign_id=campaign_id,
        operation="access_review.decisions",
        action=action,
        operation_label="recording access-review decisions",
        request_payload=payload.model_dump(mode="json"),
        mutate=lambda locked_actor: {
            "decision_count": len(
                record_access_review_decisions(
                    db,
                    campaign_id=campaign_id,
                    reviewer=locked_actor,
                    payload=payload,
                )
            ),
            "item_ids": [str(value.item_id) for value in payload.decisions],
        },
    )


@router.post(
    "/{campaign_id}/close",
    response_model=AccessReviewCampaignResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_access_review_close(
    campaign_id: uuid.UUID,
    payload: AccessReviewTransitionRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ACCESS_REVIEWS)),
) -> AccessReviewCampaignResponse:
    return _campaign_mutation(
        db,
        request=request,
        response=response,
        actor=actor,
        idempotency_key=idempotency_key,
        campaign_id=campaign_id,
        operation="access_review.close",
        action="access_reviews.campaign.close",
        operation_label="closing an access-review campaign",
        request_payload=payload.model_dump(mode="json"),
        mutate=lambda locked_actor: _transition_metadata(
            close_access_review_campaign(
                db,
                campaign_id=campaign_id,
                actor=locked_actor,
                payload=payload,
            ),
            reason=payload.reason,
        ),
    )


@router.post(
    "/{campaign_id}/apply/start",
    response_model=AccessReviewCampaignResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_access_review_apply_start(
    campaign_id: uuid.UUID,
    payload: AccessReviewBeginApplyRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ACCESS_REVIEWS)),
) -> AccessReviewCampaignResponse:
    return _campaign_mutation(
        db,
        request=request,
        response=response,
        actor=actor,
        idempotency_key=idempotency_key,
        campaign_id=campaign_id,
        operation="access_review.apply.start",
        action="access_reviews.apply.start",
        operation_label="starting access-review apply",
        request_payload=payload.model_dump(mode="json"),
        mutate=lambda locked_actor: _transition_metadata(
            begin_access_review_apply(
                db,
                campaign_id=campaign_id,
                actor=locked_actor,
                payload=payload,
            )
        ),
    )


@router.post(
    "/{campaign_id}/apply/items/{item_id}",
    response_model=AccessReviewApplyReceiptResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_access_review_apply_item(
    campaign_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: AccessReviewApplyItemRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ACCESS_REVIEWS)),
) -> AccessReviewApplyReceiptResponse:
    action = "access_reviews.item.apply"
    try:
        identity = _operation_identity(
            idempotency_key,
            operation="access_review.item.apply",
            payload={
                "campaign_id": str(campaign_id),
                "item_id": str(item_id),
                **payload.model_dump(mode="json"),
            },
        )
        locked_actor, replay, _session = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            action=action,
            operation_label="applying an access-review item",
            campaign_id=campaign_id,
        )
        if replay is not None:
            return _replay_response(
                response,
                replay,
                AccessReviewApplyReceiptResponse,
                revision=_campaign_revision(db, campaign_id),
            )
        result = apply_access_review_item(
            db,
            campaign_id=campaign_id,
            item_id=item_id,
            actor=locked_actor,
            expected_revision=payload.expected_revision,
            expected_item_fingerprint=payload.expected_item_fingerprint,
            coordinator=coordinate_access_review_revocation,
        )
        rendered = AccessReviewApplyReceiptResponse.model_validate(result.receipt)
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            campaign_id=campaign_id,
            rendered=rendered,
        )
        item = db.get(AccessReviewItem, item_id)
        metadata = _item_apply_metadata(rendered, item, changed=result.changed)
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="access_review",
            resource_id=str(campaign_id),
            success=rendered.outcome != "failed",
            metadata=metadata,
        )
        if (
            result.changed
            and rendered.outcome == "revoked"
            and rendered.mutation_performed
            and item is not None
        ):
            _record_domain_revocation_audit(
                db,
                request=request,
                actor=locked_actor,
                campaign_id=campaign_id,
                item=item,
                receipt=rendered,
            )
        commit_governance_mutation(db, action=action)
        _set_mutation_headers(
            response,
            result.campaign_revision,
            changed=result.changed,
        )
        return rendered
    except (
        AccessReviewError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, campaign_id, exc, item_id=item_id)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="access_review", operation="apply_item", exc=exc
        )


@router.post(
    "/{campaign_id}/apply/items/{item_id}/resolve",
    response_model=AccessReviewApplyReceiptResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_access_review_resolve_item(
    campaign_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: AccessReviewResolveItemRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ACCESS_REVIEWS)),
) -> AccessReviewApplyReceiptResponse:
    action = "access_reviews.item.resolve"
    try:
        identity = _operation_identity(
            idempotency_key,
            operation="access_review.item.resolve",
            payload={
                "campaign_id": str(campaign_id),
                "item_id": str(item_id),
                **payload.model_dump(mode="json"),
            },
        )
        locked_actor, replay, _session = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            action=action,
            operation_label="resolving an access-review apply result",
            campaign_id=campaign_id,
        )
        if replay is not None:
            return _replay_response(
                response,
                replay,
                AccessReviewApplyReceiptResponse,
                revision=_campaign_revision(db, campaign_id),
            )
        receipt = resolve_access_review_item(
            db,
            campaign_id=campaign_id,
            item_id=item_id,
            actor=locked_actor,
            expected_revision=payload.expected_revision,
            expected_item_fingerprint=payload.expected_item_fingerprint,
            expected_receipt_attempt=payload.expected_receipt_attempt,
            reason=payload.reason,
        )
        rendered = AccessReviewApplyReceiptResponse.model_validate(receipt)
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            campaign_id=campaign_id,
            rendered=rendered,
        )
        item = db.get(AccessReviewItem, item_id)
        record_governance_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            resource_type="access_review",
            resource_id=str(campaign_id),
            metadata=_item_apply_metadata(rendered, item, changed=True),
        )
        commit_governance_mutation(db, action=action)
        _set_mutation_headers(
            response,
            _campaign_revision(db, campaign_id),
            changed=True,
        )
        return rendered
    except (
        AccessReviewError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, campaign_id, exc, item_id=item_id)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="access_review", operation="resolve_item", exc=exc
        )


@router.post(
    "/{campaign_id}/apply/complete",
    response_model=AccessReviewCampaignResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_access_review_apply_complete(
    campaign_id: uuid.UUID,
    payload: AccessReviewBeginApplyRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ACCESS_REVIEWS)),
) -> AccessReviewCampaignResponse:
    return _campaign_mutation(
        db,
        request=request,
        response=response,
        actor=actor,
        idempotency_key=idempotency_key,
        campaign_id=campaign_id,
        operation="access_review.apply.complete",
        action="access_reviews.apply.complete",
        operation_label="completing access-review apply",
        request_payload=payload.model_dump(mode="json"),
        mutate=lambda locked_actor: _transition_metadata(
            complete_access_review_apply(
                db,
                campaign_id=campaign_id,
                actor=locked_actor,
                expected_revision=payload.expected_revision,
            )
        ),
    )


@router.post(
    "/{campaign_id}/cancel",
    response_model=AccessReviewCampaignResponse,
    openapi_extra=_BROWSER_ONLY,
)
def post_access_review_cancel(
    campaign_id: uuid.UUID,
    payload: AccessReviewTransitionRequest,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str, Header(alias="Idempotency-Key", min_length=8, max_length=255)
    ],
    db: Session = Depends(get_db),
    actor: User = Depends(require_permissions(SCOPE_WRITE_ACCESS_REVIEWS)),
) -> AccessReviewCampaignResponse:
    return _campaign_mutation(
        db,
        request=request,
        response=response,
        actor=actor,
        idempotency_key=idempotency_key,
        campaign_id=campaign_id,
        operation="access_review.cancel",
        action="access_reviews.campaign.cancel",
        operation_label="cancelling an access-review campaign",
        request_payload=payload.model_dump(mode="json"),
        mutate=lambda locked_actor: _transition_metadata(
            cancel_access_review_campaign(
                db,
                campaign_id=campaign_id,
                actor=locked_actor,
                payload=payload,
            ),
            reason=payload.reason,
        ),
    )


def _campaign_mutation(
    db: Session,
    *,
    request: Request,
    response: Response,
    actor: User,
    idempotency_key: str,
    campaign_id: uuid.UUID,
    operation: str,
    action: str,
    operation_label: str,
    request_payload: dict[str, object],
    mutate,
) -> AccessReviewCampaignResponse:
    try:
        identity = _operation_identity(
            idempotency_key,
            operation=operation,
            payload={"campaign_id": str(campaign_id), **request_payload},
        )
        locked_actor, replay, _session = _prepare_mutation(
            db,
            request=request,
            actor=actor,
            identity=identity,
            action=action,
            operation_label=operation_label,
            campaign_id=campaign_id,
        )
        if replay is not None:
            return _replay_response(response, replay, AccessReviewCampaignResponse)
        mutation_metadata = mutate(locked_actor)
        rendered = get_access_review_campaign(db, campaign_id)
        _record_receipt(
            db,
            actor=locked_actor,
            identity=identity,
            campaign_id=campaign_id,
            rendered=rendered,
        )
        _record_campaign_audit(
            db,
            request=request,
            actor=locked_actor,
            action=action,
            rendered=rendered,
            metadata=mutation_metadata,
        )
        commit_governance_mutation(db, action=action)
        _set_mutation_headers(response, rendered.revision, changed=True)
        return rendered
    except (
        AccessReviewError,
        GovernanceAuthorizationDenied,
        GovernanceIdempotencyError,
    ) as exc:
        _reject(db, request, actor, action, campaign_id, exc)
        raise _http_error(exc) from exc
    except SQLAlchemyError as exc:
        raise_governance_storage_error(
            db, subsystem="access_review", operation=operation, exc=exc
        )


def _prepare_mutation(
    db: Session,
    *,
    request: Request,
    actor: User,
    identity: GovernanceOperationIdentity,
    action: str,
    operation_label: str,
    campaign_id: uuid.UUID | None = None,
) -> tuple[User, GovernanceOperationReceipt | None, AuthSession]:
    lock_governance_operation_identity(db, actor_user_id=actor.id, identity=identity)
    locked_actor, _authorization = authorize_governance_actor(
        db,
        request=request,
        actor=actor,
        required_permission=SCOPE_WRITE_ACCESS_REVIEWS,
        durable=True,
    )
    try:
        session = require_sensitive_browser_session(
            db,
            request=request,
            user=locked_actor,
            action=identity.operation,
            operation_label=operation_label,
        )
    except ApiHTTPException as exc:
        record_rejected_governance_mutation(
            db,
            request=request,
            actor=actor,
            action=action,
            resource_type="access_review",
            resource_id=str(campaign_id) if campaign_id is not None else None,
            reason=exc.error_code,
        )
        raise
    replay = find_governance_operation_replay(
        db,
        actor_user_id=locked_actor.id,
        identity=identity,
    )
    return locked_actor, replay, session


def _operation_identity(
    idempotency_key: str,
    *,
    operation: str,
    payload: dict[str, object],
) -> GovernanceOperationIdentity:
    return build_governance_operation_identity(
        idempotency_key,
        operation=operation,
        payload=payload,
    )


def _record_receipt(
    db: Session,
    *,
    actor: User,
    identity: GovernanceOperationIdentity,
    campaign_id: uuid.UUID,
    rendered: BaseModel,
    http_status: int = status.HTTP_200_OK,
) -> None:
    record_governance_operation_receipt(
        db,
        actor_user_id=actor.id,
        identity=identity,
        resource_type="access_review",
        resource_id=campaign_id,
        response_json=rendered.model_dump(mode="json"),
        http_status=http_status,
    )


def _replay_response(
    response: Response,
    receipt: GovernanceOperationReceipt,
    response_model: type[_ResponseModel],
    *,
    revision: int | None = None,
) -> _ResponseModel:
    payload = governance_operation_replay_payload(receipt)
    rendered = response_model.model_validate(payload)
    current_revision = revision
    if current_revision is None:
        current_revision = getattr(rendered, "revision", None)
    _set_mutation_headers(response, current_revision, changed=False)
    return rendered


def _record_campaign_audit(
    db: Session,
    *,
    request: Request,
    actor: User,
    action: str,
    rendered: AccessReviewCampaignResponse,
    metadata: dict[str, object],
) -> None:
    record_governance_audit(
        db,
        request=request,
        actor=actor,
        action=action,
        resource_type="access_review",
        resource_id=str(rendered.id),
        metadata={
            "campaign_status": rendered.status,
            "campaign_revision": rendered.revision,
            "item_count": rendered.item_count,
            "decided_item_count": rendered.decided_item_count,
            "revoke_item_count": rendered.revoke_item_count,
            "apply_terminal_item_count": rendered.apply_terminal_item_count,
            **metadata,
        },
    )


def _item_apply_metadata(
    receipt: AccessReviewApplyReceiptResponse,
    item: AccessReviewItem | None,
    *,
    changed: bool,
) -> dict[str, object]:
    return {
        "campaign_id": str(receipt.campaign_id),
        "item_id": str(receipt.item_id),
        "item_type": item.item_type if item is not None else None,
        "assignment_id": str(item.assignment_id) if item is not None else None,
        "principal_type": item.principal_type if item is not None else None,
        "principal_id": (str(item.principal_id_snapshot) if item is not None else None),
        "target_type": item.target_type if item is not None else None,
        "target_id": str(item.target_id_snapshot) if item is not None else None,
        "receipt_id": str(receipt.id),
        "attempt": receipt.attempt,
        "outcome": receipt.outcome,
        "mutation_performed": receipt.mutation_performed,
        "changed": changed,
        "detail_code": receipt.detail_code,
        "result": receipt.result_snapshot,
    }


def _record_domain_revocation_audit(
    db: Session,
    *,
    request: Request,
    actor: User,
    campaign_id: uuid.UUID,
    item: AccessReviewItem,
    receipt: AccessReviewApplyReceiptResponse,
) -> None:
    domain_identity = _domain_revocation_audit_identity(item, receipt)
    if domain_identity is None:
        return
    action, resource_type, resource_id, canonical_metadata = domain_identity
    record_governance_audit(
        db,
        request=request,
        actor=actor,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id),
        metadata={
            "access_review_campaign_id": str(campaign_id),
            "access_review_item_id": str(item.id),
            "access_review_receipt_id": str(receipt.id),
            "target_id": str(item.target_id_snapshot),
            "result": receipt.result_snapshot,
            **canonical_metadata,
        },
    )


def _domain_revocation_audit_identity(
    item: AccessReviewItem,
    receipt: AccessReviewApplyReceiptResponse,
) -> tuple[str, str, uuid.UUID, dict[str, object]] | None:
    identity = {
        "direct_user_role": ("iam.user_role.remove", "user", "principal_id_snapshot"),
        "group_membership": (
            "iam.group_member.remove",
            "iam_group",
            "target_id_snapshot",
        ),
        "service_account_role": (
            "service_accounts.role.remove",
            "service_account",
            "principal_id_snapshot",
        ),
        "live_elevation": (
            "elevations.grant.revoke",
            "temporary_elevation",
            "assignment_id",
        ),
    }.get(item.item_type)
    if identity is None:
        return
    action, resource_type, resource_field = identity
    resource_id = getattr(item, resource_field)
    if item.item_type == "direct_user_role":
        canonical_metadata = {
            "assignment_id": str(item.assignment_id),
            "role_id": str(item.target_id_snapshot),
        }
    elif item.item_type == "group_membership":
        canonical_metadata = {
            "membership_id": str(item.assignment_id),
            "user_id": str(item.principal_id_snapshot),
        }
    elif item.item_type == "service_account_role":
        canonical_metadata = {
            "assignment_id": str(item.assignment_id),
            "role_id": str(item.target_id_snapshot),
            "service_account_revision": receipt.result_snapshot.get(
                "resource_revision"
            ),
        }
    else:
        canonical_metadata = {
            "target_user_id": str(item.principal_id_snapshot),
            "role_id": str(item.target_id_snapshot),
            "previous_status": "approved",
        }
    return action, resource_type, resource_id, canonical_metadata


def _campaign_revision(db: Session, campaign_id: uuid.UUID) -> int:
    campaign = db.get(AccessReviewCampaign, campaign_id)
    if campaign is None:
        raise AccessReviewNotFound("Access-review campaign not found.")
    return int(campaign.revision)


def _transition_metadata(campaign, *, reason: str | None = None) -> dict[str, object]:
    return {
        "status": campaign.status,
        "reason": reason,
        "apply_run_id": str(campaign.apply_run_id) if campaign.apply_run_id else None,
    }


def _reject(
    db: Session,
    request: Request,
    actor: User,
    action: str,
    campaign_id: uuid.UUID | None,
    exc: Exception,
    *,
    item_id: uuid.UUID | None = None,
) -> None:
    context = dict(getattr(exc, "context", {}) or {})
    if item_id is not None:
        context["item_id"] = str(item_id)
    record_rejected_governance_mutation(
        db,
        request=request,
        actor=actor,
        action=action,
        resource_type="access_review",
        resource_id=str(campaign_id) if campaign_id is not None else None,
        reason=str(getattr(exc, "code", "access_review_error")),
        context=context,
    )


def _http_error(exc: Exception) -> ApiHTTPException:
    if isinstance(exc, GovernanceAuthorizationDenied):
        return governance_authorization_http_error(exc)
    if isinstance(exc, AccessReviewNotFound):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, AccessReviewForbidden):
        status_code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, GovernanceIdempotencyKeyInvalid):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(
        exc,
        (
            AccessReviewSelectionInvalid,
            AccessReviewLimitExceeded,
            AccessReviewQueryInvalid,
        ),
    ):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    elif isinstance(exc, (AccessReviewConflict, GovernanceIdempotencyError)):
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    context = dict(getattr(exc, "context", {}) or {})
    current_revision = getattr(exc, "current_revision", None)
    if current_revision is not None:
        context["current_revision"] = current_revision
    headers = {"X-ThreatLens-Mutation-Changed": "false"}
    if current_revision is not None:
        headers["X-Current-Revision"] = str(current_revision)
    return ApiHTTPException(
        status_code=status_code,
        detail=str(exc),
        error_code=str(getattr(exc, "code", "access_review_error")),
        error_context=context or None,
        headers=headers,
    )


def _set_mutation_headers(
    response: Response, revision: int | None, *, changed: bool
) -> None:
    if revision is not None:
        response.headers["X-Current-Revision"] = str(revision)
    response.headers["X-ThreatLens-Mutation-Changed"] = str(changed).lower()


__all__ = ["router"]
