from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models.investigation import (
    Investigation,
    InvestigationActivity,
    InvestigationEvidence,
    InvestigationMember,
    InvestigationNote,
)
from app.models.user import User
from app.schemas.investigation import (
    InvestigationActivityListResponse,
    InvestigationActivityResponse,
    InvestigationEvidenceListResponse,
    InvestigationEvidenceResponse,
    InvestigationMemberResponse,
    InvestigationNoteListResponse,
    InvestigationNoteResponse,
    InvestigationSummaryResponse,
)

INVESTIGATION_DETAIL_COLLECTION_LIMIT = 200


def list_evidence_page(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    page: int,
    page_size: int,
) -> InvestigationEvidenceListResponse:
    rows, total = _query_evidence(
        db,
        investigation_id=investigation_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return InvestigationEvidenceListResponse(
        evidence=rows,
        total=total,
        page=page,
        page_size=page_size,
    )


def list_note_page(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    page: int,
    page_size: int,
) -> InvestigationNoteListResponse:
    rows, total = _query_notes(
        db,
        investigation_id=investigation_id,
        offset=(page - 1) * page_size,
        limit=page_size,
    )
    return InvestigationNoteListResponse(
        notes=rows,
        total=total,
        page=page,
        page_size=page_size,
    )


def list_activity_page(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    page: int,
    page_size: int,
) -> InvestigationActivityListResponse:
    actor = aliased(User)
    query = (
        select(InvestigationActivity, actor.email.label("actor_email"))
        .outerjoin(actor, actor.id == InvestigationActivity.actor_user_id)
        .where(InvestigationActivity.investigation_id == investigation_id)
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(
            InvestigationActivity.created_at.desc(), InvestigationActivity.id.desc()
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return InvestigationActivityListResponse(
        activities=[
            InvestigationActivityResponse(
                id=row.InvestigationActivity.id,
                actor_user_id=row.InvestigationActivity.actor_user_id,
                actor_email=row.actor_email,
                action=row.InvestigationActivity.action,
                entity_type=row.InvestigationActivity.entity_type,
                entity_id=row.InvestigationActivity.entity_id,
                details=dict(row.InvestigationActivity.details_json or {}),
                created_at=row.InvestigationActivity.created_at,
            )
            for row in rows
        ],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def list_recent_evidence(
    db: Session, investigation_id: uuid.UUID
) -> tuple[list[InvestigationEvidenceResponse], int]:
    return _query_evidence(
        db,
        investigation_id=investigation_id,
        offset=0,
        limit=INVESTIGATION_DETAIL_COLLECTION_LIMIT,
    )


def list_recent_notes(
    db: Session, investigation_id: uuid.UUID
) -> tuple[list[InvestigationNoteResponse], int]:
    return _query_notes(
        db,
        investigation_id=investigation_id,
        offset=0,
        limit=INVESTIGATION_DETAIL_COLLECTION_LIMIT,
    )


def list_members(
    db: Session, investigation_id: uuid.UUID
) -> list[InvestigationMemberResponse]:
    rows = db.execute(
        select(InvestigationMember, User.email.label("email"))
        .join(User, User.id == InvestigationMember.user_id)
        .where(InvestigationMember.investigation_id == investigation_id)
        .order_by(InvestigationMember.created_at.asc(), User.email.asc())
    ).all()
    return [
        InvestigationMemberResponse(
            user_id=row.InvestigationMember.user_id,
            email=row.email,
            role=row.InvestigationMember.role,
            created_at=row.InvestigationMember.created_at,
        )
        for row in rows
    ]


def summary_response(
    investigation: Investigation,
    *,
    current_user_role: str | None,
    evidence_count: int,
    member_count: int,
    note_count: int,
    assignee_email: str | None,
) -> InvestigationSummaryResponse:
    return InvestigationSummaryResponse(
        id=investigation.id,
        title=investigation.title,
        description=investigation.description,
        status=investigation.status,
        severity=investigation.severity,
        visibility=investigation.visibility,
        disposition=investigation.disposition,
        assignee_user_id=investigation.assignee_user_id,
        assignee_email=assignee_email,
        current_user_role=current_user_role,
        evidence_count=evidence_count,
        member_count=member_count,
        note_count=note_count,
        version=investigation.version,
        created_at=investigation.created_at,
        updated_at=investigation.updated_at,
        closed_at=investigation.closed_at,
        archived_at=investigation.archived_at,
    )


def evidence_response(
    evidence: InvestigationEvidence,
) -> InvestigationEvidenceResponse:
    return InvestigationEvidenceResponse(
        id=evidence.id,
        source_type=evidence.source_type,
        source_id=evidence.source_id,
        title_snapshot=evidence.title_snapshot,
        description_snapshot=evidence.description_snapshot,
        url_snapshot=evidence.url_snapshot,
        metadata_snapshot=dict(evidence.metadata_snapshot_json or {}),
        note=evidence.note,
        added_by_user_id=evidence.added_by_user_id,
        created_at=evidence.created_at,
    )


def _query_evidence(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    offset: int,
    limit: int,
) -> tuple[list[InvestigationEvidenceResponse], int]:
    predicate = InvestigationEvidence.investigation_id == investigation_id
    total = int(
        db.scalar(select(func.count(InvestigationEvidence.id)).where(predicate)) or 0
    )
    rows = db.scalars(
        select(InvestigationEvidence)
        .where(predicate)
        .order_by(
            InvestigationEvidence.created_at.desc(), InvestigationEvidence.id.desc()
        )
        .offset(offset)
        .limit(limit)
    ).all()
    return [evidence_response(row) for row in rows], total


def _query_notes(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    offset: int,
    limit: int,
) -> tuple[list[InvestigationNoteResponse], int]:
    author = aliased(User)
    filters = (
        InvestigationNote.investigation_id == investigation_id,
        InvestigationNote.deleted_at.is_(None),
    )
    total = int(db.scalar(select(func.count(InvestigationNote.id)).where(*filters)) or 0)
    rows = db.execute(
        select(InvestigationNote, author.email.label("author_email"))
        .outerjoin(author, author.id == InvestigationNote.author_user_id)
        .where(*filters)
        .order_by(InvestigationNote.created_at.desc(), InvestigationNote.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return [
        InvestigationNoteResponse(
            id=row.InvestigationNote.id,
            author_user_id=row.InvestigationNote.author_user_id,
            author_email=row.author_email,
            body=row.InvestigationNote.body,
            version=row.InvestigationNote.version,
            created_at=row.InvestigationNote.created_at,
            updated_at=row.InvestigationNote.updated_at,
        )
        for row in rows
    ], total
