from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
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
    InvestigationDetailResponse,
    InvestigationEvidenceResponse,
    InvestigationListResponse,
    InvestigationMemberCandidate,
    InvestigationMemberCandidateListResponse,
    InvestigationMemberResponse,
    InvestigationNoteResponse,
    InvestigationSummaryResponse,
)
from app.services.investigation_evidence import EvidenceSourceError, build_evidence_snapshot

WRITE_MEMBER_ROLES = frozenset({"owner", "editor"})
OWNER_MEMBER_ROLE = "owner"


class InvestigationNotFoundError(LookupError):
    pass


class InvestigationPermissionError(PermissionError):
    pass


class InvestigationConflictError(RuntimeError):
    pass


class InvestigationValidationError(ValueError):
    pass


def list_investigations(
    db: Session,
    *,
    user: User,
    q: str | None,
    statuses: list[str],
    severities: list[str],
    assigned_to_me: bool,
    include_archived: bool,
    page: int,
    page_size: int,
) -> InvestigationListResponse:
    membership_role = (
        select(InvestigationMember.role)
        .where(
            InvestigationMember.investigation_id == Investigation.id,
            InvestigationMember.user_id == user.id,
        )
        .correlate(Investigation)
        .scalar_subquery()
    )
    evidence_count = (
        select(func.count(InvestigationEvidence.id))
        .where(InvestigationEvidence.investigation_id == Investigation.id)
        .correlate(Investigation)
        .scalar_subquery()
    )
    member_count = (
        select(func.count(InvestigationMember.user_id))
        .where(InvestigationMember.investigation_id == Investigation.id)
        .correlate(Investigation)
        .scalar_subquery()
    )
    note_count = (
        select(func.count(InvestigationNote.id))
        .where(
            InvestigationNote.investigation_id == Investigation.id,
            InvestigationNote.deleted_at.is_(None),
        )
        .correlate(Investigation)
        .scalar_subquery()
    )
    assignee = aliased(User)
    visibility_filter = or_(Investigation.visibility == "team", membership_role.is_not(None))
    filters = [visibility_filter]
    if not include_archived:
        filters.append(Investigation.status != "archived")
    if statuses:
        filters.append(Investigation.status.in_(statuses))
    if severities:
        filters.append(Investigation.severity.in_(severities))
    if assigned_to_me:
        filters.append(Investigation.assignee_user_id == user.id)
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        filters.append(
            or_(
                Investigation.title.ilike(pattern, escape="\\"),
                Investigation.description.ilike(pattern, escape="\\"),
            )
        )

    base_query = (
        select(
            Investigation,
            membership_role.label("current_user_role"),
            evidence_count.label("evidence_count"),
            member_count.label("member_count"),
            note_count.label("note_count"),
            assignee.email.label("assignee_email"),
        )
        .outerjoin(assignee, assignee.id == Investigation.assignee_user_id)
        .where(*filters)
    )
    total = db.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    rows = db.execute(
        base_query.order_by(Investigation.updated_at.desc(), Investigation.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return InvestigationListResponse(
        investigations=[
            _summary_response(
                row.Investigation,
                current_user_role=row.current_user_role,
                evidence_count=int(row.evidence_count or 0),
                member_count=int(row.member_count or 0),
                note_count=int(row.note_count or 0),
                assignee_email=row.assignee_email,
            )
            for row in rows
        ],
        total=int(total),
        page=page,
        page_size=page_size,
    )


def create_investigation(
    db: Session,
    *,
    user: User,
    title: str,
    description: str,
    severity: str,
    visibility: str,
    assignee_user_id: uuid.UUID | None,
) -> Investigation:
    normalized_title = _required_text(title, "Investigation title")
    if assignee_user_id is not None and assignee_user_id != user.id:
        raise InvestigationValidationError(
            "The initial assignee must be the creator. Add another member before assigning the investigation to them."
        )
    investigation = Investigation(
        title=normalized_title,
        description=description.strip(),
        severity=severity,
        visibility=visibility,
        assignee_user_id=assignee_user_id,
        created_by_user_id=user.id,
    )
    db.add(investigation)
    db.flush()
    db.add(
        InvestigationMember(
            investigation_id=investigation.id,
            user_id=user.id,
            role=OWNER_MEMBER_ROLE,
            added_by_user_id=user.id,
        )
    )
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.created",
        entity_type="investigation",
        entity_id=investigation.id,
        details={"severity": severity, "visibility": visibility},
    )
    db.flush()
    return investigation


def list_member_candidates(
    db: Session,
    *,
    q: str | None,
    page: int,
    page_size: int,
) -> InvestigationMemberCandidateListResponse:
    filters = [User.is_active.is_(True), User.is_approved.is_(True)]
    if q and q.strip():
        pattern = f"%{_escape_like(q.strip())}%"
        filters.append(User.email.ilike(pattern, escape="\\"))
    query = select(User).where(*filters)
    total = int(db.scalar(select(func.count()).select_from(query.subquery())) or 0)
    users = db.scalars(
        query.order_by(User.email.asc(), User.id.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return InvestigationMemberCandidateListResponse(
        users=[
            InvestigationMemberCandidate(id=candidate.id, email=candidate.email, account_role=candidate.role)
            for candidate in users
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_investigation_detail(db: Session, *, investigation_id: uuid.UUID, user: User) -> InvestigationDetailResponse:
    investigation, current_role = _get_visible_investigation(db, investigation_id=investigation_id, user=user)
    members = _list_members(db, investigation_id)
    evidence = _list_evidence(db, investigation_id)
    notes, note_count = _list_notes(db, investigation_id)
    assignee_email = db.scalar(select(User.email).where(User.id == investigation.assignee_user_id))
    return InvestigationDetailResponse(
        **_summary_response(
            investigation,
            current_user_role=current_role,
            evidence_count=len(evidence),
            member_count=len(members),
            note_count=note_count,
            assignee_email=assignee_email,
        ).model_dump(),
        members=members,
        evidence=evidence,
        notes=notes,
        notes_truncated=note_count > len(notes),
    )


def update_investigation(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    expected_version: int,
    changes: dict,
) -> tuple[Investigation, list[str]]:
    investigation, member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_expected_version(investigation, expected_version)
    if investigation.status == "archived" and changes.get("status") not in {"open"}:
        raise InvestigationConflictError(
            "Archived investigations are read-only. Reopen the investigation before changing it."
        )
    if changes.get("status") == "archived" and member.role != OWNER_MEMBER_ROLE:
        raise InvestigationPermissionError("Only an investigation owner can archive it.")

    changed_fields: list[str] = []
    now = datetime.now(timezone.utc)
    for field_name in ("title", "description", "severity", "visibility", "disposition"):
        if field_name not in changes:
            continue
        value = changes[field_name]
        if field_name == "title":
            value = _required_text(str(value), "Investigation title")
        elif isinstance(value, str):
            value = value.strip()
        if getattr(investigation, field_name) != value:
            setattr(investigation, field_name, value)
            changed_fields.append(field_name)

    if "assignee_user_id" in changes:
        assignee_user_id = changes["assignee_user_id"]
        if assignee_user_id is not None:
            assignee = db.execute(
                select(InvestigationMember, User)
                .join(User, User.id == InvestigationMember.user_id)
                .where(
                    InvestigationMember.investigation_id == investigation.id,
                    InvestigationMember.user_id == assignee_user_id,
                    InvestigationMember.role.in_(WRITE_MEMBER_ROLES),
                    User.is_active.is_(True),
                    User.is_approved.is_(True),
                    User.role.in_((ROLE_ADMIN, ROLE_ANALYST)),
                )
            ).first()
            if assignee is None:
                raise InvestigationValidationError("The assignee must be an owner or editor of this investigation.")
        if investigation.assignee_user_id != assignee_user_id:
            investigation.assignee_user_id = assignee_user_id
            changed_fields.append("assignee_user_id")

    if "status" in changes and investigation.status != changes["status"]:
        new_status = changes["status"]
        investigation.status = new_status
        investigation.closed_at = now if new_status == "closed" else None
        investigation.archived_at = now if new_status == "archived" else None
        changed_fields.append("status")

    if not changed_fields:
        return investigation, []
    _advance_version(investigation, now=now)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.updated",
        entity_type="investigation",
        entity_id=investigation.id,
        details={"changed_fields": sorted(changed_fields), "version": investigation.version},
    )
    db.add(investigation)
    db.flush()
    return investigation, changed_fields


def add_member(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    member_user_id: uuid.UUID,
    role: str,
    expected_version: int,
) -> InvestigationMember:
    investigation, actor_member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_owner(actor_member)
    _require_expected_version(investigation, expected_version)
    target = db.scalar(select(User).where(User.id == member_user_id, User.is_active.is_(True)))
    if target is None or not target.is_approved:
        raise InvestigationValidationError("The selected user is not an active, approved ThreatLens account.")
    _validate_member_role_for_account(target, role)
    existing = db.scalar(
        select(InvestigationMember).where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.user_id == member_user_id,
        )
    )
    if existing is not None:
        raise InvestigationConflictError("The selected user is already an investigation member.")
    member = InvestigationMember(
        investigation_id=investigation.id,
        user_id=member_user_id,
        role=role,
        added_by_user_id=user.id,
    )
    db.add(member)
    _advance_version(investigation)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.member_added",
        entity_type="user",
        entity_id=member_user_id,
        details={"role": role},
    )
    db.flush()
    return member


def update_member(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    member_user_id: uuid.UUID,
    role: str,
    expected_version: int,
) -> InvestigationMember:
    investigation, actor_member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_owner(actor_member)
    _require_expected_version(investigation, expected_version)
    member = db.scalar(
        select(InvestigationMember).where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.user_id == member_user_id,
        )
    )
    if member is None:
        raise InvestigationNotFoundError
    target = db.scalar(select(User).where(User.id == member_user_id))
    if target is None:
        raise InvestigationConflictError("The investigation member account no longer exists.")
    _validate_member_role_for_account(target, role)
    if member.role == OWNER_MEMBER_ROLE and role != OWNER_MEMBER_ROLE:
        _require_another_owner(db, investigation.id, excluding_user_id=member.user_id)
    if member.role == role:
        return member
    old_role = member.role
    member.role = role
    if investigation.assignee_user_id == member.user_id and role not in WRITE_MEMBER_ROLES:
        investigation.assignee_user_id = None
    _advance_version(investigation)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.member_updated",
        entity_type="user",
        entity_id=member_user_id,
        details={"from_role": old_role, "to_role": role},
    )
    db.flush()
    return member


def remove_member(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    member_user_id: uuid.UUID,
    expected_version: int,
) -> None:
    investigation, actor_member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_owner(actor_member)
    _require_expected_version(investigation, expected_version)
    member = db.scalar(
        select(InvestigationMember).where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.user_id == member_user_id,
        )
    )
    if member is None:
        raise InvestigationNotFoundError
    if member.role == OWNER_MEMBER_ROLE:
        _require_another_owner(db, investigation.id, excluding_user_id=member.user_id)
    if investigation.assignee_user_id == member.user_id:
        investigation.assignee_user_id = None
    db.delete(member)
    _advance_version(investigation)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.member_removed",
        entity_type="user",
        entity_id=member_user_id,
        details={"role": member.role},
    )
    db.flush()


def add_evidence(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    source_type: str,
    source_id: uuid.UUID,
    note: str | None,
    expected_version: int,
) -> InvestigationEvidence:
    investigation, _member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_expected_version(investigation, expected_version)
    if investigation.status == "archived":
        raise InvestigationConflictError("Archived investigations are read-only. Reopen it before adding evidence.")
    duplicate = db.scalar(
        select(InvestigationEvidence.id).where(
            InvestigationEvidence.investigation_id == investigation.id,
            InvestigationEvidence.source_type == source_type,
            InvestigationEvidence.source_id == source_id,
        )
    )
    if duplicate is not None:
        raise InvestigationConflictError("This evidence is already included in the investigation.")
    try:
        snapshot = build_evidence_snapshot(db, source_type=source_type, source_id=source_id)
    except EvidenceSourceError as exc:
        raise InvestigationValidationError(str(exc)) from exc
    evidence = InvestigationEvidence(
        investigation_id=investigation.id,
        source_type=source_type,
        source_id=source_id,
        title_snapshot=snapshot.title,
        description_snapshot=snapshot.description,
        url_snapshot=snapshot.url,
        metadata_snapshot_json=snapshot.metadata,
        note=_optional_text(note),
        added_by_user_id=user.id,
    )
    db.add(evidence)
    db.flush()
    _advance_version(investigation)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.evidence_added",
        entity_type="evidence",
        entity_id=evidence.id,
        details={"source_type": source_type, "source_id": str(source_id)},
    )
    db.flush()
    return evidence


def remove_evidence(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    evidence_id: uuid.UUID,
    user: User,
    expected_version: int,
) -> None:
    investigation, _member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_expected_version(investigation, expected_version)
    if investigation.status == "archived":
        raise InvestigationConflictError("Archived investigations are read-only. Reopen it before removing evidence.")
    evidence = db.scalar(
        select(InvestigationEvidence).where(
            InvestigationEvidence.id == evidence_id,
            InvestigationEvidence.investigation_id == investigation.id,
        )
    )
    if evidence is None:
        raise InvestigationNotFoundError
    source_type = evidence.source_type
    source_id = evidence.source_id
    db.delete(evidence)
    _advance_version(investigation)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.evidence_removed",
        entity_type="evidence",
        entity_id=evidence_id,
        details={"source_type": source_type, "source_id": str(source_id)},
    )
    db.flush()


def add_note(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    body: str,
    expected_version: int,
) -> InvestigationNote:
    investigation, _member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_expected_version(investigation, expected_version)
    if investigation.status == "archived":
        raise InvestigationConflictError("Archived investigations are read-only. Reopen it before adding notes.")
    note = InvestigationNote(
        investigation_id=investigation.id,
        author_user_id=user.id,
        body=_required_text(body, "Note"),
    )
    db.add(note)
    db.flush()
    _advance_version(investigation)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.note_added",
        entity_type="note",
        entity_id=note.id,
        details={},
    )
    db.flush()
    return note


def update_note(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    note_id: uuid.UUID,
    user: User,
    body: str,
    expected_note_version: int,
    expected_investigation_version: int,
) -> InvestigationNote:
    investigation, member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_expected_version(investigation, expected_investigation_version)
    if investigation.status == "archived":
        raise InvestigationConflictError("Archived investigations are read-only. Reopen it before editing notes.")
    note = db.scalar(
        select(InvestigationNote)
        .where(
            InvestigationNote.id == note_id,
            InvestigationNote.investigation_id == investigation.id,
            InvestigationNote.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if note is None:
        raise InvestigationNotFoundError
    if note.author_user_id != user.id and member.role != OWNER_MEMBER_ROLE:
        raise InvestigationPermissionError("Only the note author or an investigation owner can edit this note.")
    if note.version != expected_note_version:
        raise InvestigationConflictError("The note changed after you loaded it. Refresh and review the latest version.")
    normalized_body = _required_text(body, "Note")
    if note.body == normalized_body:
        return note
    note.body = normalized_body
    note.version += 1
    _advance_version(investigation)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.note_updated",
        entity_type="note",
        entity_id=note.id,
        details={"note_version": note.version},
    )
    db.flush()
    return note


def delete_note(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    note_id: uuid.UUID,
    user: User,
    expected_note_version: int,
    expected_investigation_version: int,
) -> None:
    investigation, member = _lock_for_write(db, investigation_id=investigation_id, user=user)
    _require_expected_version(investigation, expected_investigation_version)
    if investigation.status == "archived":
        raise InvestigationConflictError("Archived investigations are read-only. Reopen it before removing notes.")
    note = db.scalar(
        select(InvestigationNote)
        .where(
            InvestigationNote.id == note_id,
            InvestigationNote.investigation_id == investigation.id,
            InvestigationNote.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if note is None:
        raise InvestigationNotFoundError
    if note.author_user_id != user.id and member.role != OWNER_MEMBER_ROLE:
        raise InvestigationPermissionError("Only the note author or an investigation owner can remove this note.")
    if note.version != expected_note_version:
        raise InvestigationConflictError("The note changed after you loaded it. Refresh and review the latest version.")
    note.deleted_at = datetime.now(timezone.utc)
    note.version += 1
    _advance_version(investigation)
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.note_removed",
        entity_type="note",
        entity_id=note.id,
        details={"note_version": note.version},
    )
    db.flush()


def list_activity(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    page: int,
    page_size: int,
) -> InvestigationActivityListResponse:
    _get_visible_investigation(db, investigation_id=investigation_id, user=user)
    actor = aliased(User)
    query = (
        select(InvestigationActivity, actor.email.label("actor_email"))
        .outerjoin(actor, actor.id == InvestigationActivity.actor_user_id)
        .where(InvestigationActivity.investigation_id == investigation_id)
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(InvestigationActivity.created_at.desc(), InvestigationActivity.id.desc())
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


def evidence_response(evidence: InvestigationEvidence) -> InvestigationEvidenceResponse:
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


def member_response(db: Session, member: InvestigationMember) -> InvestigationMemberResponse:
    email = db.scalar(select(User.email).where(User.id == member.user_id))
    if email is None:
        raise InvestigationConflictError("The investigation member account no longer exists.")
    return InvestigationMemberResponse(
        user_id=member.user_id,
        email=email,
        role=member.role,
        created_at=member.created_at,
    )


def note_response(db: Session, note: InvestigationNote) -> InvestigationNoteResponse:
    author_email = db.scalar(select(User.email).where(User.id == note.author_user_id))
    return InvestigationNoteResponse(
        id=note.id,
        author_user_id=note.author_user_id,
        author_email=author_email,
        body=note.body,
        version=note.version,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _get_visible_investigation(
    db: Session, *, investigation_id: uuid.UUID, user: User
) -> tuple[Investigation, str | None]:
    row = db.execute(
        select(Investigation, InvestigationMember.role.label("member_role"))
        .outerjoin(
            InvestigationMember,
            (InvestigationMember.investigation_id == Investigation.id)
            & (InvestigationMember.user_id == user.id),
        )
        .where(
            Investigation.id == investigation_id,
            or_(Investigation.visibility == "team", InvestigationMember.user_id.is_not(None)),
        )
    ).first()
    if row is None:
        raise InvestigationNotFoundError
    return row.Investigation, row.member_role


def _lock_for_write(
    db: Session, *, investigation_id: uuid.UUID, user: User
) -> tuple[Investigation, InvestigationMember]:
    investigation = db.scalar(
        select(Investigation).where(Investigation.id == investigation_id).with_for_update()
    )
    if investigation is None:
        raise InvestigationNotFoundError
    member = db.scalar(
        select(InvestigationMember).where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.user_id == user.id,
        )
    )
    if member is None:
        if investigation.visibility == "private":
            raise InvestigationNotFoundError
        raise InvestigationPermissionError("Join this investigation as an owner or editor before changing it.")
    if member.role not in WRITE_MEMBER_ROLES:
        raise InvestigationPermissionError("Your investigation membership is read-only.")
    return investigation, member


def _list_members(db: Session, investigation_id: uuid.UUID) -> list[InvestigationMemberResponse]:
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


def _list_evidence(db: Session, investigation_id: uuid.UUID) -> list[InvestigationEvidenceResponse]:
    rows = db.scalars(
        select(InvestigationEvidence)
        .where(InvestigationEvidence.investigation_id == investigation_id)
        .order_by(InvestigationEvidence.created_at.desc(), InvestigationEvidence.id.desc())
    ).all()
    return [evidence_response(row) for row in rows]


def _list_notes(
    db: Session, investigation_id: uuid.UUID
) -> tuple[list[InvestigationNoteResponse], int]:
    author = aliased(User)
    filters = (
        InvestigationNote.investigation_id == investigation_id,
        InvestigationNote.deleted_at.is_(None),
    )
    total = db.scalar(select(func.count(InvestigationNote.id)).where(*filters)) or 0
    rows = db.execute(
        select(InvestigationNote, author.email.label("author_email"))
        .outerjoin(author, author.id == InvestigationNote.author_user_id)
        .where(*filters)
        .order_by(InvestigationNote.created_at.desc(), InvestigationNote.id.desc())
        .limit(200)
    ).all()
    notes = [
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
    ]
    return notes, int(total)


def _summary_response(
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


def _record_activity(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    action: str,
    entity_type: str | None,
    entity_id: uuid.UUID | None,
    details: dict,
) -> None:
    db.add(
        InvestigationActivity(
            investigation_id=investigation_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details_json=details,
        )
    )


def _require_owner(member: InvestigationMember) -> None:
    if member.role != OWNER_MEMBER_ROLE:
        raise InvestigationPermissionError("Only an investigation owner can manage members.")


def _validate_member_role_for_account(user: User, member_role: str) -> None:
    if member_role in WRITE_MEMBER_ROLES and (
        user.role not in {ROLE_ADMIN, ROLE_ANALYST} or not user.is_active or not user.is_approved
    ):
        raise InvestigationValidationError(
            "Owner and editor membership requires an analyst or administrator account."
        )


def _require_another_owner(db: Session, investigation_id: uuid.UUID, *, excluding_user_id: uuid.UUID) -> None:
    other_owner = db.scalar(
        select(InvestigationMember.user_id).where(
            InvestigationMember.investigation_id == investigation_id,
            InvestigationMember.role == OWNER_MEMBER_ROLE,
            InvestigationMember.user_id != excluding_user_id,
        )
    )
    if other_owner is None:
        raise InvestigationConflictError(
            "An investigation must retain at least one owner. Promote another member before changing this owner."
        )


def _require_expected_version(investigation: Investigation, expected_version: int) -> None:
    if investigation.version != expected_version:
        raise InvestigationConflictError(
            "The investigation changed after you loaded it. Refresh, review the latest changes, and try again."
        )


def _advance_version(investigation: Investigation, *, now: datetime | None = None) -> None:
    investigation.version += 1
    investigation.updated_at = now or datetime.now(timezone.utc)


def _required_text(value: str, label: str) -> str:
    normalized = " ".join(value.strip().split()) if label == "Investigation title" else value.strip()
    if not normalized:
        raise InvestigationValidationError(f"{label} cannot be empty.")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
