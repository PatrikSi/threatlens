from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, aliased

from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST
from app.core.token_scopes import SCOPE_WRITE_INVESTIGATIONS
from app.models.iam import (
    IAMGroupMembership,
    IAMGroupRoleAssignment,
    IAMRole,
    IAMRolePermission,
    IAMUserRoleAssignment,
)
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
    InvestigationEvidenceListResponse,
    InvestigationListResponse,
    InvestigationMemberCandidate,
    InvestigationMemberCandidateListResponse,
    InvestigationMemberResponse,
    InvestigationNoteListResponse,
    InvestigationSummaryResponse,
)
from app.services.auth_sessions import lock_user_auth_state, lock_user_auth_states
from app.services.authorization import (
    authorization_context_for_user,
    lock_iam_policy_for_mutation,
)
from app.services.investigation_evidence import (
    EvidenceSourceError,
    build_evidence_snapshot,
)
from app.services.investigation_collections import (
    list_evidence_page,
    list_note_page,
    list_recent_evidence,
    list_recent_notes,
)
from app.services.investigation_read_access import (
    load_composed_investigation_read_access,
)

WRITE_MEMBER_ROLES = frozenset({"owner", "editor"})
OWNER_MEMBER_ROLE = "owner"


class InvestigationNotFoundError(LookupError):
    code = "investigation_not_found"

    def __init__(
        self, detail: str = "Investigation not found.", *, code: str | None = None
    ) -> None:
        super().__init__(detail)
        self.code = code or self.code


class InvestigationPermissionError(PermissionError):
    pass


class InvestigationActorNotEligibleError(InvestigationPermissionError):
    code = "investigation_actor_not_eligible"


class InvestigationReadAuthorizationChangedError(InvestigationPermissionError):
    code = "investigation_read_authorization_changed"


class InvestigationConflictError(RuntimeError):
    code = "investigation_conflict"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.code = code or self.code


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
    visibility_filter = or_(
        Investigation.visibility == "team", membership_role.is_not(None)
    )
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
    _lock_eligible_actor(db, user.id)
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
            InvestigationMemberCandidate(
                id=candidate.id, email=candidate.email, account_role=candidate.role
            )
            for candidate in users
        ],
        total=total,
        page=page,
        page_size=page_size,
    )


def get_investigation_detail(
    db: Session, *, investigation_id: uuid.UUID, user: User
) -> InvestigationDetailResponse:
    investigation, current_role = _get_visible_investigation_for_composed_read(
        db, investigation_id=investigation_id, user=user
    )
    members = _list_members(db, investigation_id)
    evidence, evidence_count = list_recent_evidence(db, investigation_id)
    notes, note_count = list_recent_notes(db, investigation_id)
    assignee_email = db.scalar(
        select(User.email).where(User.id == investigation.assignee_user_id)
    )
    return InvestigationDetailResponse(
        **_summary_response(
            investigation,
            current_user_role=current_role,
            evidence_count=evidence_count,
            member_count=len(members),
            note_count=note_count,
            assignee_email=assignee_email,
        ).model_dump(),
        members=members,
        evidence=evidence,
        evidence_truncated=evidence_count > len(evidence),
        notes=notes,
        notes_truncated=note_count > len(notes),
    )


def list_evidence(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    page: int,
    page_size: int,
) -> InvestigationEvidenceListResponse:
    _get_visible_investigation_for_composed_read(
        db, investigation_id=investigation_id, user=user
    )
    return list_evidence_page(
        db,
        investigation_id=investigation_id,
        page=page,
        page_size=page_size,
    )


def list_notes(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    page: int,
    page_size: int,
) -> InvestigationNoteListResponse:
    _get_visible_investigation_for_composed_read(
        db, investigation_id=investigation_id, user=user
    )
    return list_note_page(
        db,
        investigation_id=investigation_id,
        page=page,
        page_size=page_size,
    )


def update_investigation(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    expected_version: int,
    changes: dict,
) -> tuple[Investigation, list[str]]:
    lock_iam_policy_for_mutation(db)
    requested_assignee_id = changes.get("assignee_user_id")
    locked_accounts = lock_user_auth_states(
        db,
        [user.id]
        + ([requested_assignee_id] if requested_assignee_id is not None else []),
    )
    requested_assignee = (
        locked_accounts.get(requested_assignee_id)
        if requested_assignee_id is not None
        else None
    )
    investigation, member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_expected_version(investigation, expected_version)
    if investigation.status == "archived" and changes.get("status") not in {"open"}:
        raise InvestigationConflictError(
            "Archived investigations are read-only. Reopen the investigation before changing it.",
            code="investigation_archived",
        )
    if changes.get("status") == "archived" and member.role != OWNER_MEMBER_ROLE:
        raise InvestigationPermissionError(
            "Only an investigation owner can archive it."
        )

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
            assignee_membership = db.scalar(
                select(InvestigationMember).where(
                    InvestigationMember.investigation_id == investigation.id,
                    InvestigationMember.user_id == assignee_user_id,
                    InvestigationMember.role.in_(WRITE_MEMBER_ROLES),
                )
            )
            if requested_assignee is None or assignee_membership is None:
                raise InvestigationValidationError(
                    "The assignee must be an owner or editor of this investigation."
                )
            _validate_member_role_for_account(
                db, requested_assignee, assignee_membership.role
            )
        if investigation.assignee_user_id != assignee_user_id:
            investigation.assignee_user_id = assignee_user_id
            changed_fields.append("assignee_user_id")

    status_transition: dict[str, str] | None = None
    if "status" in changes and investigation.status != changes["status"]:
        old_status = investigation.status
        new_status = changes["status"]
        investigation.status = new_status
        if new_status == "closed":
            investigation.closed_at = now
        elif new_status in {"open", "monitoring"}:
            investigation.closed_at = None
        if new_status == "archived":
            investigation.archived_at = now
        elif old_status == "archived":
            investigation.archived_at = None
        status_transition = {"from": old_status, "to": new_status}
        changed_fields.append("status")

    if not changed_fields:
        return investigation, []
    _advance_version(investigation, now=now)
    activity_details: dict[str, object] = {
        "changed_fields": sorted(changed_fields),
        "version": investigation.version,
    }
    if status_transition is not None:
        activity_details["status_transition"] = status_transition
        activity_details["closed_at"] = (
            investigation.closed_at.isoformat() if investigation.closed_at else None
        )
        activity_details["archived_at"] = (
            investigation.archived_at.isoformat() if investigation.archived_at else None
        )
    _record_activity(
        db,
        investigation_id=investigation.id,
        actor_user_id=user.id,
        action="investigation.updated",
        entity_type="investigation",
        entity_id=investigation.id,
        details=activity_details,
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
    lock_iam_policy_for_mutation(db)
    target = lock_user_auth_states(db, [user.id, member_user_id]).get(member_user_id)
    investigation, actor_member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_owner(actor_member)
    _require_expected_version(investigation, expected_version)
    _require_mutable_investigation(investigation, action="managing members")
    if target is None or not target.is_active or not target.is_approved:
        raise InvestigationValidationError(
            "The selected user is not an active, approved ThreatLens account."
        )
    _validate_member_role_for_account(db, target, role)
    existing = db.scalar(
        select(InvestigationMember).where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.user_id == member_user_id,
        )
    )
    if existing is not None:
        raise InvestigationConflictError(
            "The selected user is already an investigation member.",
            code="investigation_member_exists",
        )
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
) -> tuple[InvestigationMember, bool]:
    lock_iam_policy_for_mutation(db)
    target = lock_user_auth_states(db, [user.id, member_user_id]).get(member_user_id)
    investigation, actor_member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_owner(actor_member)
    _require_expected_version(investigation, expected_version)
    _require_mutable_investigation(investigation, action="managing members")
    member = db.scalar(
        select(InvestigationMember).where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.user_id == member_user_id,
        )
    )
    if member is None:
        raise InvestigationNotFoundError(
            "Investigation member not found.",
            code="investigation_member_not_found",
        )
    if target is None:
        raise InvestigationConflictError(
            "The investigation member account no longer exists.",
            code="investigation_member_account_missing",
        )
    _validate_member_role_for_account(db, target, role)
    if member.role == OWNER_MEMBER_ROLE and role != OWNER_MEMBER_ROLE:
        _require_another_owner(db, investigation.id, excluding_user_id=member.user_id)
    if member.role == role:
        return member, False
    old_role = member.role
    member.role = role
    if (
        investigation.assignee_user_id == member.user_id
        and role not in WRITE_MEMBER_ROLES
    ):
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
    return member, True


def remove_member(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    member_user_id: uuid.UUID,
    expected_version: int,
) -> None:
    investigation, actor_member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_owner(actor_member)
    _require_expected_version(investigation, expected_version)
    _require_mutable_investigation(investigation, action="managing members")
    member = db.scalar(
        select(InvestigationMember).where(
            InvestigationMember.investigation_id == investigation.id,
            InvestigationMember.user_id == member_user_id,
        )
    )
    if member is None:
        raise InvestigationNotFoundError(
            "Investigation member not found.",
            code="investigation_member_not_found",
        )
    if member.user_id == user.id and investigation.visibility == "private":
        raise InvestigationConflictError(
            "You cannot remove yourself from a private investigation because you would immediately lose access "
            "to the result. Ask another owner to remove you, or change the investigation visibility to team first.",
            code="investigation_private_self_removal",
        )
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
    investigation, _member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_expected_version(investigation, expected_version)
    if investigation.status == "archived":
        raise InvestigationConflictError(
            "Archived investigations are read-only. Reopen it before adding evidence.",
            code="investigation_archived",
        )
    duplicate = db.scalar(
        select(InvestigationEvidence.id).where(
            InvestigationEvidence.investigation_id == investigation.id,
            InvestigationEvidence.source_type == source_type,
            InvestigationEvidence.source_id == source_id,
        )
    )
    if duplicate is not None:
        raise InvestigationConflictError(
            "This evidence is already included in the investigation.",
            code="investigation_evidence_exists",
        )
    try:
        snapshot = build_evidence_snapshot(
            db,
            source_type=source_type,
            source_id=source_id,
            requesting_user_id=user.id,
            requesting_user_is_admin=user.role == ROLE_ADMIN,
        )
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
    investigation, _member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_expected_version(investigation, expected_version)
    if investigation.status == "archived":
        raise InvestigationConflictError(
            "Archived investigations are read-only. Reopen it before removing evidence.",
            code="investigation_archived",
        )
    evidence = db.scalar(
        select(InvestigationEvidence).where(
            InvestigationEvidence.id == evidence_id,
            InvestigationEvidence.investigation_id == investigation.id,
        )
    )
    if evidence is None:
        raise InvestigationNotFoundError(
            "Investigation evidence not found.",
            code="investigation_evidence_not_found",
        )
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
    investigation, _member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_expected_version(investigation, expected_version)
    if investigation.status == "archived":
        raise InvestigationConflictError(
            "Archived investigations are read-only. Reopen it before adding notes.",
            code="investigation_archived",
        )
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
) -> tuple[InvestigationNote, bool]:
    investigation, member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_expected_version(investigation, expected_investigation_version)
    if investigation.status == "archived":
        raise InvestigationConflictError(
            "Archived investigations are read-only. Reopen it before editing notes.",
            code="investigation_archived",
        )
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
        raise InvestigationNotFoundError(
            "Investigation note not found.",
            code="investigation_note_not_found",
        )
    if note.author_user_id != user.id and member.role != OWNER_MEMBER_ROLE:
        raise InvestigationPermissionError(
            "Only the note author or an investigation owner can edit this note."
        )
    if note.version != expected_note_version:
        raise InvestigationConflictError(
            "The note changed after you loaded it. Refresh and review the latest version.",
            code="investigation_note_version_conflict",
        )
    normalized_body = _required_text(body, "Note")
    if note.body == normalized_body:
        return note, False
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
    return note, True


def delete_note(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    note_id: uuid.UUID,
    user: User,
    expected_note_version: int,
    expected_investigation_version: int,
) -> None:
    investigation, member = _lock_for_write(
        db, investigation_id=investigation_id, user=user
    )
    _require_expected_version(investigation, expected_investigation_version)
    if investigation.status == "archived":
        raise InvestigationConflictError(
            "Archived investigations are read-only. Reopen it before removing notes.",
            code="investigation_archived",
        )
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
        raise InvestigationNotFoundError(
            "Investigation note not found.",
            code="investigation_note_not_found",
        )
    if note.author_user_id != user.id and member.role != OWNER_MEMBER_ROLE:
        raise InvestigationPermissionError(
            "Only the note author or an investigation owner can remove this note."
        )
    if note.version != expected_note_version:
        raise InvestigationConflictError(
            "The note changed after you loaded it. Refresh and review the latest version.",
            code="investigation_note_version_conflict",
        )
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
    _get_visible_investigation_for_composed_read(
        db, investigation_id=investigation_id, user=user
    )
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


def member_response(
    db: Session, member: InvestigationMember
) -> InvestigationMemberResponse:
    email = db.scalar(select(User.email).where(User.id == member.user_id))
    if email is None:
        raise InvestigationConflictError(
            "The investigation member account no longer exists."
        )
    return InvestigationMemberResponse(
        user_id=member.user_id,
        email=email,
        role=member.role,
        created_at=member.created_at,
    )


def _get_visible_investigation_for_composed_read(
    db: Session, *, investigation_id: uuid.UUID, user: User
) -> tuple[Investigation, str | None]:
    access = load_composed_investigation_read_access(
        db,
        investigation_id=investigation_id,
        user=user,
    )
    if access.authorization_changed:
        raise InvestigationReadAuthorizationChangedError(
            "Your account access changed while private investigation data was "
            "loading. Sign in again and retry."
        )
    if access.investigation is None:
        raise InvestigationNotFoundError
    return access.investigation, access.member_role


def _lock_for_write(
    db: Session, *, investigation_id: uuid.UUID, user: User
) -> tuple[Investigation, InvestigationMember]:
    _lock_eligible_actor(db, user.id)
    investigation = db.scalar(
        select(Investigation)
        .where(Investigation.id == investigation_id)
        .with_for_update()
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
        raise InvestigationPermissionError(
            "Join this investigation as an owner or editor before changing it."
        )
    if member.role not in WRITE_MEMBER_ROLES:
        raise InvestigationPermissionError(
            "Your investigation membership is read-only."
        )
    return investigation, member


def _list_members(
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
        raise InvestigationPermissionError(
            "Only an investigation owner can manage members."
        )


def _validate_member_role_for_account(
    db: Session, user: User, member_role: str
) -> None:
    if member_role in WRITE_MEMBER_ROLES and (
        not user.is_active
        or not user.is_approved
        or not authorization_context_for_user(db, user).has(SCOPE_WRITE_INVESTIGATIONS)
    ):
        raise InvestigationValidationError(
            "Owner and editor membership requires an analyst or administrator account, "
            "or an active, approved account with an explicit investigation-write role."
        )


def _lock_membership_account(db: Session, user_id: uuid.UUID) -> User | None:
    """Serialize membership eligibility with IAM access reductions."""
    return lock_user_auth_state(db, user_id)


def _lock_eligible_actor(db: Session, user_id: uuid.UUID) -> User:
    lock_iam_policy_for_mutation(db)
    actor = _lock_membership_account(db, user_id)
    if (
        actor is None
        or not actor.is_active
        or not actor.is_approved
        or not authorization_context_for_user(db, actor).has(SCOPE_WRITE_INVESTIGATIONS)
    ):
        raise InvestigationActorNotEligibleError(
            "Your account is no longer active, approved, authorized as an analyst or "
            "administrator, or granted explicit investigation write access. Sign in "
            "again before retrying."
        )
    return actor


def eligible_investigation_owner_ids_query(
    investigation_id: uuid.UUID,
    *,
    excluding_user_id: uuid.UUID | None = None,
):
    """Return eligible owner IDs for mutation guards and IAM reconciliation."""
    direct_write_grant = exists(
        select(1)
        .select_from(IAMUserRoleAssignment)
        .join(IAMRole, IAMRole.id == IAMUserRoleAssignment.role_id)
        .join(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            IAMUserRoleAssignment.user_id == User.id,
            IAMRole.is_system.is_(False),
            IAMRolePermission.permission == SCOPE_WRITE_INVESTIGATIONS,
        )
    )
    group_write_grant = exists(
        select(1)
        .select_from(IAMGroupMembership)
        .join(
            IAMGroupRoleAssignment,
            IAMGroupRoleAssignment.group_id == IAMGroupMembership.group_id,
        )
        .join(IAMRole, IAMRole.id == IAMGroupRoleAssignment.role_id)
        .join(IAMRolePermission, IAMRolePermission.role_id == IAMRole.id)
        .where(
            IAMGroupMembership.user_id == User.id,
            IAMRole.is_system.is_(False),
            IAMRolePermission.permission == SCOPE_WRITE_INVESTIGATIONS,
        )
    )
    query = (
        select(InvestigationMember.user_id)
        .join(User, User.id == InvestigationMember.user_id)
        .where(
            InvestigationMember.investigation_id == investigation_id,
            InvestigationMember.role == OWNER_MEMBER_ROLE,
            User.is_active.is_(True),
            User.is_approved.is_(True),
            or_(
                User.role.in_((ROLE_ADMIN, ROLE_ANALYST)),
                direct_write_grant,
                group_write_grant,
            ),
        )
    )
    if excluding_user_id is not None:
        query = query.where(InvestigationMember.user_id != excluding_user_id)
    return query


def _require_another_owner(
    db: Session, investigation_id: uuid.UUID, *, excluding_user_id: uuid.UUID
) -> None:
    other_owner = db.scalar(
        eligible_investigation_owner_ids_query(
            investigation_id,
            excluding_user_id=excluding_user_id,
        ).limit(1)
    )
    if other_owner is None:
        raise InvestigationConflictError(
            "An investigation must retain at least one owner who is active, approved, "
            "and has investigation write access. "
            "Promote an eligible member before changing this owner.",
            code="investigation_owner_required",
        )


def _require_expected_version(
    investigation: Investigation, expected_version: int
) -> None:
    if investigation.version != expected_version:
        raise InvestigationConflictError(
            "The investigation changed after you loaded it. Refresh, review the latest changes, and try again.",
            code="investigation_version_conflict",
        )


def _require_mutable_investigation(
    investigation: Investigation, *, action: str
) -> None:
    if investigation.status == "archived":
        raise InvestigationConflictError(
            f"Archived investigations are read-only. Reopen the investigation before {action}.",
            code="investigation_archived",
        )


def _advance_version(
    investigation: Investigation, *, now: datetime | None = None
) -> None:
    investigation.version += 1
    investigation.updated_at = now or datetime.now(timezone.utc)


def _required_text(value: str, label: str) -> str:
    normalized = (
        " ".join(value.strip().split())
        if label == "Investigation title"
        else value.strip()
    )
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
