from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from app.models.investigation import InvestigationEvidence, InvestigationNote
from app.models.user import User
from app.schemas.investigation import (
    InvestigationEvidenceListResponse,
    InvestigationEvidenceResponse,
    InvestigationNoteListResponse,
    InvestigationNoteResponse,
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
