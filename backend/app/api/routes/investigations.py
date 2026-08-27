from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_token_scopes
from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.token_scopes import SCOPE_READ_INVESTIGATIONS, SCOPE_WRITE_INVESTIGATIONS
from app.db.session import get_db
from app.models.user import User
from app.schemas.investigation import (
    InvestigationActivityListResponse,
    InvestigationCreate,
    InvestigationDetailResponse,
    InvestigationEvidenceAdd,
    InvestigationListResponse,
    InvestigationMemberAdd,
    InvestigationMemberCandidateListResponse,
    InvestigationMemberUpdate,
    InvestigationNoteCreate,
    InvestigationNoteUpdate,
    InvestigationUpdate,
)
from app.services.audit import record_audit
from app.services.investigations import (
    InvestigationConflictError,
    InvestigationNotFoundError,
    InvestigationPermissionError,
    InvestigationValidationError,
    add_evidence,
    add_member,
    add_note,
    create_investigation,
    delete_note,
    get_investigation_detail,
    list_activity,
    list_investigations,
    list_member_candidates,
    remove_evidence,
    remove_member,
    update_investigation,
    update_member,
    update_note,
)

router = APIRouter(prefix="/investigations", tags=["investigations"])

VALID_STATUSES = frozenset({"open", "monitoring", "closed", "archived"})
VALID_SEVERITIES = frozenset({"low", "medium", "high", "critical"})


@router.get("", response_model=InvestigationListResponse)
def get_investigations(
    q: str | None = Query(default=None, max_length=255),
    statuses: list[str] = Query(default=[]),
    severities: list[str] = Query(default=[]),
    assigned_to_me: bool = False,
    include_archived: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_INVESTIGATIONS)),
):
    invalid_statuses = sorted(set(statuses) - VALID_STATUSES)
    invalid_severities = sorted(set(severities) - VALID_SEVERITIES)
    if invalid_statuses:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported investigation status: {', '.join(invalid_statuses)}.",
        )
    if invalid_severities:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unsupported investigation severity: {', '.join(invalid_severities)}.",
        )
    return list_investigations(
        db,
        user=user,
        q=q,
        statuses=list(dict.fromkeys(statuses)),
        severities=list(dict.fromkeys(severities)),
        assigned_to_me=assigned_to_me,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )


@router.post("", response_model=InvestigationDetailResponse, status_code=status.HTTP_201_CREATED)
def post_investigation(
    payload: InvestigationCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        investigation = create_investigation(
            db,
            user=user,
            title=payload.title,
            description=payload.description,
            severity=payload.severity,
            visibility=payload.visibility,
            assignee_user_id=payload.assignee_user_id,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.create",
            resource_type="investigation",
            resource_id=str(investigation.id),
            metadata={"severity": investigation.severity, "visibility": investigation.visibility},
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation.id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.get("/member-candidates", response_model=InvestigationMemberCandidateListResponse)
def get_investigation_member_candidates(
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    return list_member_candidates(
        db,
        q=q,
        page=page,
        page_size=page_size,
    )


@router.get("/{investigation_id}", response_model=InvestigationDetailResponse)
def get_investigation(
    investigation_id: uuid.UUID,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_INVESTIGATIONS)),
):
    try:
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.patch("/{investigation_id}", response_model=InvestigationDetailResponse)
def patch_investigation(
    investigation_id: uuid.UUID,
    payload: InvestigationUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        changes = payload.model_dump(exclude={"expected_version"}, exclude_unset=True)
        investigation, changed_fields = update_investigation(
            db,
            investigation_id=investigation_id,
            user=user,
            expected_version=payload.expected_version,
            changes=changes,
        )
        if changed_fields:
            record_audit(
                db,
                actor_user_id=user.id,
                action="investigations.update",
                resource_type="investigation",
                resource_id=str(investigation.id),
                metadata={"changed_fields": sorted(changed_fields), "version": investigation.version},
            )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation.id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/{investigation_id}/members", response_model=InvestigationDetailResponse)
def post_investigation_member(
    investigation_id: uuid.UUID,
    payload: InvestigationMemberAdd,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        add_member(
            db,
            investigation_id=investigation_id,
            user=user,
            member_user_id=payload.user_id,
            role=payload.role,
            expected_version=payload.expected_version,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.member.add",
            resource_type="investigation",
            resource_id=str(investigation_id),
            metadata={"member_user_id": str(payload.user_id), "role": payload.role},
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.patch("/{investigation_id}/members/{member_user_id}", response_model=InvestigationDetailResponse)
def patch_investigation_member(
    investigation_id: uuid.UUID,
    member_user_id: uuid.UUID,
    payload: InvestigationMemberUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        update_member(
            db,
            investigation_id=investigation_id,
            user=user,
            member_user_id=member_user_id,
            role=payload.role,
            expected_version=payload.expected_version,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.member.update",
            resource_type="investigation",
            resource_id=str(investigation_id),
            metadata={"member_user_id": str(member_user_id), "role": payload.role},
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.delete("/{investigation_id}/members/{member_user_id}", response_model=InvestigationDetailResponse)
def delete_investigation_member(
    investigation_id: uuid.UUID,
    member_user_id: uuid.UUID,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        remove_member(
            db,
            investigation_id=investigation_id,
            user=user,
            member_user_id=member_user_id,
            expected_version=expected_version,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.member.remove",
            resource_type="investigation",
            resource_id=str(investigation_id),
            metadata={"member_user_id": str(member_user_id)},
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/{investigation_id}/evidence", response_model=InvestigationDetailResponse)
def post_investigation_evidence(
    investigation_id: uuid.UUID,
    payload: InvestigationEvidenceAdd,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        evidence = add_evidence(
            db,
            investigation_id=investigation_id,
            user=user,
            source_type=payload.source_type,
            source_id=payload.source_id,
            note=payload.note,
            expected_version=payload.expected_version,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.evidence.add",
            resource_type="investigation",
            resource_id=str(investigation_id),
            metadata={
                "evidence_id": str(evidence.id),
                "source_type": evidence.source_type,
                "source_id": str(evidence.source_id),
            },
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.delete("/{investigation_id}/evidence/{evidence_id}", response_model=InvestigationDetailResponse)
def delete_investigation_evidence(
    investigation_id: uuid.UUID,
    evidence_id: uuid.UUID,
    expected_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        remove_evidence(
            db,
            investigation_id=investigation_id,
            evidence_id=evidence_id,
            user=user,
            expected_version=expected_version,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.evidence.remove",
            resource_type="investigation",
            resource_id=str(investigation_id),
            metadata={"evidence_id": str(evidence_id)},
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.post("/{investigation_id}/notes", response_model=InvestigationDetailResponse)
def post_investigation_note(
    investigation_id: uuid.UUID,
    payload: InvestigationNoteCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        note = add_note(
            db,
            investigation_id=investigation_id,
            user=user,
            body=payload.body,
            expected_version=payload.expected_version,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.note.add",
            resource_type="investigation",
            resource_id=str(investigation_id),
            metadata={"note_id": str(note.id)},
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.patch("/{investigation_id}/notes/{note_id}", response_model=InvestigationDetailResponse)
def patch_investigation_note(
    investigation_id: uuid.UUID,
    note_id: uuid.UUID,
    payload: InvestigationNoteUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        note = update_note(
            db,
            investigation_id=investigation_id,
            note_id=note_id,
            user=user,
            body=payload.body,
            expected_note_version=payload.expected_note_version,
            expected_investigation_version=payload.expected_investigation_version,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.note.update",
            resource_type="investigation",
            resource_id=str(investigation_id),
            metadata={"note_id": str(note.id), "note_version": note.version},
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.delete("/{investigation_id}/notes/{note_id}", response_model=InvestigationDetailResponse)
def delete_investigation_note(
    investigation_id: uuid.UUID,
    note_id: uuid.UUID,
    expected_note_version: int = Query(ge=1),
    expected_investigation_version: int = Query(ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_WRITE_INVESTIGATIONS)),
):
    _require_investigation_author(user)
    try:
        delete_note(
            db,
            investigation_id=investigation_id,
            note_id=note_id,
            user=user,
            expected_note_version=expected_note_version,
            expected_investigation_version=expected_investigation_version,
        )
        record_audit(
            db,
            actor_user_id=user.id,
            action="investigations.note.remove",
            resource_type="investigation",
            resource_id=str(investigation_id),
            metadata={"note_id": str(note_id)},
        )
        db.commit()
        return get_investigation_detail(db, investigation_id=investigation_id, user=user)
    except Exception as exc:
        _raise_service_error(exc)


@router.get("/{investigation_id}/activity", response_model=InvestigationActivityListResponse)
def get_investigation_activity(
    investigation_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(require_token_scopes(SCOPE_READ_INVESTIGATIONS)),
):
    try:
        return list_activity(
            db,
            investigation_id=investigation_id,
            user=user,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        _raise_service_error(exc)


def _require_investigation_author(user: User) -> None:
    if user.role not in {ROLE_ADMIN, ROLE_ANALYST}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Investigation changes require the analyst or administrator role.",
        )


def _raise_service_error(exc: Exception):
    if isinstance(exc, InvestigationNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Investigation not found.") from exc
    if isinstance(exc, InvestigationPermissionError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, InvestigationConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, InvestigationValidationError):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    raise exc
