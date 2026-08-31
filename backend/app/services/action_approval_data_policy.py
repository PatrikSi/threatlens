from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Sequence

from sqlalchemy import String, and_, cast, exists, false, func, or_, select, true
from sqlalchemy.orm import Session, aliased

from app.models.action_approval import ActionApprovalRequest
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
)
from app.services.action_registry import (
    ACTION_DEFINITIONS,
    RegisteredActionDefinition,
)
from app.services.ai_ops_common import AI_TASK_TYPE_CONNECTION_TEST
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    DataAccessDecision,
    DataAccessSourceInput,
    copy_data_access_envelope_lineage,
    data_access_envelope_predicate,
    get_data_access_envelope,
    merge_data_access_envelope_sources,
)
from app.services.data_access_policy import (
    DataAccessContext,
    DataPolicyError,
    fence_data_access_context,
)
from app.services.data_access_runtime import lock_data_policy_revision_for_derivation


ACTION_APPROVAL_SCOPE_SYSTEM = "system"
ACTION_APPROVAL_SCOPE_GOVERNED = "governed"
ACTION_APPROVAL_SOURCE_SYSTEM = "system_control_plane"
ACTION_APPROVAL_SOURCE_AI_TASK_RUN = "ai_task_run"
ACTION_APPROVAL_SOURCE_UNRESOLVED = "unresolved"


@dataclass(frozen=True, slots=True)
class ActionApprovalTargetDataSnapshot:
    target_data_policy_version: int
    data_access_scope: str
    data_access_source_type: str
    data_access_source_id: uuid.UUID | None
    copy_source_lineage: bool
    decision: DataAccessDecision


@dataclass(frozen=True, slots=True)
class ActionApprovalWouldDenySummary:
    affected_count: int
    handling_label_ids: frozenset[uuid.UUID]


def resolve_registered_action_target_data_access(
    db: Session,
    *,
    definition: RegisteredActionDefinition,
    target_resource: object,
    data_access: DataAccessContext,
) -> ActionApprovalTargetDataSnapshot:
    """Resolve and authorize a target before approval metadata can be exposed."""

    fence_data_access_context(db, data_access)
    declaration = definition.target_data_policy
    if declaration.target_kind == ACTION_APPROVAL_SOURCE_SYSTEM:
        return ActionApprovalTargetDataSnapshot(
            target_data_policy_version=declaration.version,
            data_access_scope=ACTION_APPROVAL_SCOPE_SYSTEM,
            data_access_source_type=ACTION_APPROVAL_SOURCE_SYSTEM,
            data_access_source_id=None,
            copy_source_lineage=False,
            decision=_decision_for_labels(data_access, frozenset()),
        )
    if declaration.target_kind != ACTION_APPROVAL_SOURCE_AI_TASK_RUN or not isinstance(
        target_resource, AIProviderAttemptReceipt
    ):
        return _unresolved_target_snapshot(declaration.version, data_access)

    run_id = target_resource.task_run_id_snapshot
    # The registered AI target resolver acquired the run lock before its
    # receipt lock and revalidated this binding. Refresh the protected row
    # without issuing an opposing receipt -> run lock operation here.
    run = db.scalar(
        select(AITaskRun)
        .where(AITaskRun.id == run_id)
        .execution_options(populate_existing=True)
    )
    if run is not None and _is_exact_system_run(db, run):
        return ActionApprovalTargetDataSnapshot(
            target_data_policy_version=declaration.version,
            data_access_scope=ACTION_APPROVAL_SCOPE_SYSTEM,
            data_access_source_type=ACTION_APPROVAL_SOURCE_AI_TASK_RUN,
            data_access_source_id=run_id,
            copy_source_lineage=False,
            decision=_decision_for_labels(data_access, frozenset()),
        )

    if run is not None and run.data_access_scope == ACTION_APPROVAL_SCOPE_GOVERNED:
        if run.data_access_lineage_complete:
            try:
                envelope = get_data_access_envelope(
                    db,
                    resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                    resource_id=run.id,
                )
            except DataPolicyError:
                envelope = None
            if envelope is not None:
                return ActionApprovalTargetDataSnapshot(
                    target_data_policy_version=declaration.version,
                    data_access_scope=ACTION_APPROVAL_SCOPE_GOVERNED,
                    data_access_source_type=ACTION_APPROVAL_SOURCE_AI_TASK_RUN,
                    data_access_source_id=run_id,
                    copy_source_lineage=True,
                    decision=_decision_for_labels(data_access, envelope.label_ids),
                )
    return _unresolved_target_snapshot(
        declaration.version,
        data_access,
        source_id=run_id,
    )


def initialize_action_approval_data_access(
    db: Session,
    *,
    approval: ActionApprovalRequest,
    snapshot: ActionApprovalTargetDataSnapshot,
) -> None:
    """Persist the immutable target classification and normalized lineage."""

    policy_revision = lock_data_policy_revision_for_derivation(db)
    approval.target_data_policy_version = snapshot.target_data_policy_version
    approval.data_access_scope = snapshot.data_access_scope
    approval.data_access_lineage_complete = (
        snapshot.data_access_scope == ACTION_APPROVAL_SCOPE_SYSTEM
    )
    approval.data_access_source_type = snapshot.data_access_source_type
    approval.data_access_source_id = snapshot.data_access_source_id

    if snapshot.data_access_scope == ACTION_APPROVAL_SCOPE_SYSTEM:
        db.add(approval)
        db.flush()
        return

    copied = False
    if snapshot.copy_source_lineage and snapshot.data_access_source_id is not None:
        try:
            copy_data_access_envelope_lineage(
                db,
                source_resource_type=DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                source_resource_id=snapshot.data_access_source_id,
                target_resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
                target_resource_id=approval.id,
            )
            copied = True
        except DataPolicyError:
            copied = False
    if not copied:
        merge_data_access_envelope_sources(
            db,
            resource_type=DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
            resource_id=approval.id,
            sources=(
                DataAccessSourceInput(
                    source_type=ACTION_APPROVAL_SOURCE_UNRESOLVED,
                    source_id=str(snapshot.data_access_source_id or approval.id),
                    source_version=(
                        f"action-approval:{approval.id}:unresolved:v"
                        f"{snapshot.target_data_policy_version}"
                    ),
                    handling_label_id=QUARANTINE_HANDLING_LABEL_ID,
                    captured_policy_revision=policy_revision,
                    captured_at=approval.created_at,
                ),
            ),
        )
    approval.data_access_lineage_complete = True
    db.add(approval)
    db.flush()


def action_approval_access_predicate(data_access: DataAccessContext):
    if not data_access.principal_eligible:
        return false()
    if not data_access.enforced:
        return true()
    envelope_exists = exists(
        select(DataAccessEnvelope.id).where(
            DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
            DataAccessEnvelope.resource_id == ActionApprovalRequest.id,
        )
    )
    return or_(
        and_(
            ActionApprovalRequest.data_access_scope == ACTION_APPROVAL_SCOPE_SYSTEM,
            ActionApprovalRequest.data_access_lineage_complete.is_(True),
            _registered_system_contract_predicate(),
            ~envelope_exists,
        ),
        and_(
            ActionApprovalRequest.data_access_scope == ACTION_APPROVAL_SCOPE_GOVERNED,
            ActionApprovalRequest.data_access_lineage_complete.is_(True),
            _governed_target_lineage_predicate(),
            _normalized_action_approval_envelope_predicate(),
            data_access_envelope_predicate(
                DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
                ActionApprovalRequest.id,
                data_access,
            ),
        ),
    )


def action_approval_access_decision(
    db: Session,
    *,
    approval_id: uuid.UUID,
    data_access: DataAccessContext,
) -> DataAccessDecision:
    fence_data_access_context(db, data_access)
    enforced = replace(data_access, mode="enforced")
    allowed = bool(
        db.scalar(
            select(ActionApprovalRequest.id).where(
                ActionApprovalRequest.id == approval_id,
                action_approval_access_predicate(enforced),
            )
        )
    )
    lineage_valid = bool(
        db.scalar(
            select(ActionApprovalRequest.id).where(
                ActionApprovalRequest.id == approval_id,
                _action_approval_lineage_integrity_predicate(),
            )
        )
    )
    label_ids = _approval_label_ids(db, approval_id)
    envelope_missing = not label_ids and not lineage_valid
    if not lineage_valid:
        label_ids = frozenset(
            {*label_ids, QUARANTINE_HANDLING_LABEL_ID}
        )
    return DataAccessDecision(
        allowed=data_access.principal_eligible
        and (not data_access.enforced or allowed),
        would_deny=data_access.auditing and not allowed,
        envelope_missing=envelope_missing,
        label_ids=label_ids,
        policy_revision=data_access.policy_revision,
    )


def action_approval_would_deny_summary(
    db: Session,
    *,
    data_access: DataAccessContext,
    filters: Sequence[object] = (),
) -> ActionApprovalWouldDenySummary:
    if not data_access.auditing:
        return ActionApprovalWouldDenySummary(0, frozenset())
    enforced = replace(data_access, mode="enforced")
    denied = and_(*filters, ~action_approval_access_predicate(enforced))
    affected_count = int(
        db.scalar(select(func.count(ActionApprovalRequest.id)).where(denied)) or 0
    )
    if not affected_count:
        return ActionApprovalWouldDenySummary(0, frozenset())

    envelope = aliased(DataAccessEnvelope)
    label = aliased(DataAccessEnvelopeLabel)
    handling_label_ids = frozenset(
        db.scalars(
            select(label.label_id)
            .join(envelope, envelope.id == label.envelope_id)
            .join(
                ActionApprovalRequest,
                ActionApprovalRequest.id == envelope.resource_id,
            )
            .where(
                envelope.resource_type == DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
                denied,
            )
            .distinct()
        ).all()
    )
    denied_with_labels = int(
        db.scalar(
            select(func.count(func.distinct(ActionApprovalRequest.id)))
            .join(
                envelope,
                and_(
                    envelope.resource_type
                    == DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
                    envelope.resource_id == ActionApprovalRequest.id,
                ),
            )
            .join(label, label.envelope_id == envelope.id)
            .where(denied)
        )
        or 0
    )
    invalid_lineage_count = int(
        db.scalar(
            select(func.count(ActionApprovalRequest.id)).where(
                denied,
                ~_action_approval_lineage_integrity_predicate(),
            )
        )
        or 0
    )
    if denied_with_labels < affected_count or invalid_lineage_count:
        handling_label_ids = frozenset(
            {*handling_label_ids, QUARANTINE_HANDLING_LABEL_ID}
        )
    return ActionApprovalWouldDenySummary(affected_count, handling_label_ids)


def action_approval_data_policy_blocker_count(db: Session) -> int:
    """Count retained approvals that cannot safely participate in enforcement."""

    return int(
        db.scalar(
            select(func.count(ActionApprovalRequest.id)).where(
                ~_action_approval_lineage_integrity_predicate()
            )
        )
        or 0
    )


def _action_approval_lineage_integrity_predicate():
    envelope_exists = exists(
        select(DataAccessEnvelope.id).where(
            DataAccessEnvelope.resource_type == DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
            DataAccessEnvelope.resource_id == ActionApprovalRequest.id,
        )
    )
    return or_(
        and_(
            ActionApprovalRequest.data_access_scope == ACTION_APPROVAL_SCOPE_SYSTEM,
            ActionApprovalRequest.data_access_lineage_complete.is_(True),
            _registered_system_contract_predicate(),
            ~envelope_exists,
        ),
        and_(
            ActionApprovalRequest.data_access_scope == ACTION_APPROVAL_SCOPE_GOVERNED,
            ActionApprovalRequest.data_access_lineage_complete.is_(True),
            _governed_target_lineage_predicate(),
            _normalized_action_approval_envelope_predicate(),
        ),
    )


def _normalized_action_approval_envelope_predicate():
    envelope = aliased(DataAccessEnvelope)
    source = aliased(DataAccessEnvelopeSource)
    label = aliased(DataAccessEnvelopeLabel)
    active_label = aliased(HandlingLabel)
    counted_source = aliased(DataAccessEnvelopeSource)
    counted_label = aliased(DataAccessEnvelopeLabel)
    unlabeled_source = aliased(DataAccessEnvelopeSource)
    matching_label = aliased(DataAccessEnvelopeLabel)
    source_count = (
        select(func.count(source.id))
        .where(source.envelope_id == envelope.id)
        .scalar_subquery()
    )
    max_source_revision = (
        select(func.max(source.captured_policy_revision))
        .where(source.envelope_id == envelope.id)
        .scalar_subquery()
    )
    label_count = (
        select(func.coalesce(func.sum(label.source_count), 0))
        .where(label.envelope_id == envelope.id)
        .scalar_subquery()
    )
    invalid_source = exists(
        select(source.id).where(
            source.envelope_id == envelope.id,
            ~exists(
                select(active_label.id).where(
                    active_label.id == source.handling_label_id,
                    active_label.is_active.is_(True),
                )
            ),
        )
    )
    per_label_source_count = (
        select(func.count(counted_source.id))
        .where(
            counted_source.envelope_id == envelope.id,
            counted_source.handling_label_id == counted_label.label_id,
        )
        .correlate(envelope, counted_label)
        .scalar_subquery()
    )
    invalid_label_count = exists(
        select(counted_label.envelope_id).where(
            counted_label.envelope_id == envelope.id,
            counted_label.source_count != per_label_source_count,
        )
    )
    missing_source_label = exists(
        select(unlabeled_source.id).where(
            unlabeled_source.envelope_id == envelope.id,
            ~exists(
                select(matching_label.envelope_id).where(
                    matching_label.envelope_id == envelope.id,
                    matching_label.label_id
                    == unlabeled_source.handling_label_id,
                )
            ),
        )
    )
    return exists(
        select(envelope.id).where(
            envelope.resource_type == DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
            envelope.resource_id == ActionApprovalRequest.id,
            envelope.source_count > 0,
            source_count == envelope.source_count,
            label_count == envelope.source_count,
            envelope.policy_revision >= max_source_revision,
            ~invalid_source,
            ~invalid_label_count,
            ~missing_source_label,
        )
    )


def _unresolved_target_snapshot(
    version: int,
    data_access: DataAccessContext,
    *,
    source_id: uuid.UUID | None = None,
) -> ActionApprovalTargetDataSnapshot:
    return ActionApprovalTargetDataSnapshot(
        target_data_policy_version=version,
        data_access_scope=ACTION_APPROVAL_SCOPE_GOVERNED,
        data_access_source_type=(
            ACTION_APPROVAL_SOURCE_AI_TASK_RUN
            if source_id is not None
            else ACTION_APPROVAL_SOURCE_UNRESOLVED
        ),
        data_access_source_id=source_id,
        copy_source_lineage=False,
        decision=_decision_for_labels(
            data_access,
            frozenset({QUARANTINE_HANDLING_LABEL_ID}),
        ),
    )


def _decision_for_labels(
    data_access: DataAccessContext,
    label_ids: frozenset[uuid.UUID],
) -> DataAccessDecision:
    inaccessible = bool(label_ids - data_access.allowed_label_ids)
    allowed = data_access.principal_eligible and (
        not data_access.enforced or not inaccessible
    )
    return DataAccessDecision(
        allowed=allowed,
        would_deny=data_access.auditing and (
            not data_access.principal_eligible or inaccessible
        ),
        envelope_missing=False,
        label_ids=label_ids,
        policy_revision=data_access.policy_revision,
    )


def _is_exact_system_run(db: Session, run: AITaskRun) -> bool:
    if not (
        run.data_access_scope == ACTION_APPROVAL_SCOPE_SYSTEM
        and run.data_access_lineage_complete
        and run.task_type == AI_TASK_TYPE_CONNECTION_TEST
        and run.item_id is None
        and run.daily_brief_id is None
        and run.report_id is None
        and run.parent_run_id is None
    ):
        return False
    return not bool(
        db.scalar(
            select(DataAccessEnvelope.id).where(
                DataAccessEnvelope.resource_type
                == DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                DataAccessEnvelope.resource_id == run.id,
            )
        )
    )


def _registered_system_contract_predicate():
    contracts = []
    for definition in ACTION_DEFINITIONS:
        declaration = definition.target_data_policy
        if declaration.target_kind == ACTION_APPROVAL_SOURCE_SYSTEM:
            source_predicate = and_(
                ActionApprovalRequest.data_access_source_type
                == ACTION_APPROVAL_SOURCE_SYSTEM,
                ActionApprovalRequest.data_access_source_id.is_(None),
            )
        else:
            receipt = aliased(AIProviderAttemptReceipt)
            run = aliased(AITaskRun)
            source_predicate = and_(
                ActionApprovalRequest.data_access_source_type
                == ACTION_APPROVAL_SOURCE_AI_TASK_RUN,
                ActionApprovalRequest.data_access_source_id.is_not(None),
                exists(
                    select(receipt.id)
                    .join(run, run.id == receipt.task_run_id_snapshot)
                    .where(
                        cast(receipt.id, String)
                        == ActionApprovalRequest.target_id,
                        receipt.task_run_id_snapshot
                        == ActionApprovalRequest.data_access_source_id,
                        run.data_access_scope == ACTION_APPROVAL_SCOPE_SYSTEM,
                        run.data_access_lineage_complete.is_(True),
                        run.task_type == AI_TASK_TYPE_CONNECTION_TEST,
                        run.item_id.is_(None),
                        run.daily_brief_id.is_(None),
                        run.report_id.is_(None),
                        run.parent_run_id.is_(None),
                        ~exists(
                            select(DataAccessEnvelope.id).where(
                                DataAccessEnvelope.resource_type
                                == DATA_ACCESS_RESOURCE_AI_TASK_RUN,
                                DataAccessEnvelope.resource_id == run.id,
                            )
                        ),
                    )
                ),
            )
        contracts.append(
            and_(
                ActionApprovalRequest.action_type == definition.key,
                ActionApprovalRequest.action_definition_version
                == definition.version,
                ActionApprovalRequest.target_type == definition.target_type,
                ActionApprovalRequest.target_data_policy_version
                == declaration.version,
                source_predicate,
            )
        )
    return or_(*contracts) if contracts else false()


def _governed_target_lineage_predicate():
    receipt = aliased(AIProviderAttemptReceipt)
    run = aliased(AITaskRun)
    target_envelope = aliased(DataAccessEnvelope)
    child = aliased(DataAccessEnvelopeSource)
    parent = aliased(DataAccessEnvelopeSource)
    parent_envelope = aliased(DataAccessEnvelope)
    run_source = aliased(DataAccessEnvelopeSource)
    run_envelope = aliased(DataAccessEnvelope)
    matching_receipt = exists(
        select(receipt.id)
        .join(run, run.id == receipt.task_run_id_snapshot)
        .where(
            cast(receipt.id, String) == ActionApprovalRequest.target_id,
            receipt.task_run_id_snapshot
            == ActionApprovalRequest.data_access_source_id,
            run.data_access_scope == ACTION_APPROVAL_SCOPE_GOVERNED,
            run.data_access_lineage_complete.is_(True),
        )
    )
    parent_from_source_run = exists(
        select(parent.id)
        .join(parent_envelope, parent_envelope.id == parent.envelope_id)
        .where(
            parent.id == child.source_parent_id,
            parent_envelope.resource_type == DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            parent_envelope.resource_id
            == ActionApprovalRequest.data_access_source_id,
            child.source_type == parent.source_type,
            child.source_id == parent.source_id,
            child.source_version == parent.source_version,
            child.source_feed_id.is_not_distinct_from(parent.source_feed_id),
            child.handling_label_id == parent.handling_label_id,
            child.captured_policy_revision == parent.captured_policy_revision,
            child.source_digest.is_not_distinct_from(parent.source_digest),
            child.captured_at == parent.captured_at,
        )
    )
    matching_run_taint = exists(
        select(run_source.id)
        .join(run_envelope, run_envelope.id == run_source.envelope_id)
        .where(
            run_envelope.resource_type == DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            run_envelope.resource_id
            == ActionApprovalRequest.data_access_source_id,
            run_source.source_type == "feed_taint",
            child.source_type == run_source.source_type,
            child.source_id == run_source.source_id,
            child.source_version == run_source.source_version,
            child.source_feed_id.is_not_distinct_from(run_source.source_feed_id),
            child.handling_label_id == run_source.handling_label_id,
            child.captured_policy_revision == run_source.captured_policy_revision,
            child.source_digest.is_not_distinct_from(run_source.source_digest),
        )
    )
    quarantine_source = and_(
        child.source_parent_id.is_(None),
        child.source_type == ACTION_APPROVAL_SOURCE_UNRESOLVED,
        child.handling_label_id == QUARANTINE_HANDLING_LABEL_ID,
    )
    invalid_child = exists(
        select(child.id).where(
            child.envelope_id == target_envelope.id,
            ~(parent_from_source_run | matching_run_taint | quarantine_source),
        )
    )
    any_quarantine = exists(
        select(child.id).where(
            child.envelope_id == target_envelope.id,
            quarantine_source,
        )
    )
    represented_run_source = exists(
        select(child.id).where(
            child.envelope_id == target_envelope.id,
            or_(
                child.source_parent_id == run_source.id,
                and_(
                    run_source.source_type == "feed_taint",
                    child.source_parent_id.is_(None),
                    child.source_type == run_source.source_type,
                    child.source_id == run_source.source_id,
                    child.source_version == run_source.source_version,
                    child.source_feed_id.is_not_distinct_from(
                        run_source.source_feed_id
                    ),
                    child.handling_label_id == run_source.handling_label_id,
                    child.captured_policy_revision
                    == run_source.captured_policy_revision,
                    child.source_digest.is_not_distinct_from(
                        run_source.source_digest
                    ),
                ),
            ),
        )
    )
    missing_run_source = exists(
        select(run_source.id)
        .join(run_envelope, run_envelope.id == run_source.envelope_id)
        .where(
            run_envelope.resource_type == DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            run_envelope.resource_id
            == ActionApprovalRequest.data_access_source_id,
            ~represented_run_source,
        )
    )
    exact_lineage = exists(
        select(target_envelope.id).where(
            target_envelope.resource_type == DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
            target_envelope.resource_id == ActionApprovalRequest.id,
            ~invalid_child,
            or_(any_quarantine, ~missing_run_source),
        )
    )
    known_contracts = []
    for definition in ACTION_DEFINITIONS:
        declaration = definition.target_data_policy
        if declaration.target_kind != ACTION_APPROVAL_SOURCE_AI_TASK_RUN:
            continue
        known_contracts.append(
            and_(
                ActionApprovalRequest.action_type == definition.key,
                ActionApprovalRequest.action_definition_version
                == definition.version,
                ActionApprovalRequest.target_type == definition.target_type,
                ActionApprovalRequest.target_data_policy_version
                == declaration.version,
                ActionApprovalRequest.data_access_source_type
                == ACTION_APPROVAL_SOURCE_AI_TASK_RUN,
                ActionApprovalRequest.data_access_source_id.is_not(None),
                matching_receipt,
                exact_lineage,
            )
        )
    quarantined_legacy = exists(
        select(target_envelope.id)
        .join(child, child.envelope_id == target_envelope.id)
        .where(
            target_envelope.resource_type == DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
            target_envelope.resource_id == ActionApprovalRequest.id,
            child.source_parent_id.is_(None),
            child.source_type == ACTION_APPROVAL_SOURCE_UNRESOLVED,
            child.handling_label_id == QUARANTINE_HANDLING_LABEL_ID,
        )
    )
    return or_(*(known_contracts or [false()]), quarantined_legacy)


def _approval_label_ids(
    db: Session, approval_id: uuid.UUID
) -> frozenset[uuid.UUID]:
    return frozenset(
        db.scalars(
            select(DataAccessEnvelopeLabel.label_id)
            .join(
                DataAccessEnvelope,
                DataAccessEnvelope.id == DataAccessEnvelopeLabel.envelope_id,
            )
            .where(
                DataAccessEnvelope.resource_type
                == DATA_ACCESS_RESOURCE_ACTION_APPROVAL,
                DataAccessEnvelope.resource_id == approval_id,
            )
        ).all()
    )


__all__ = [
    "ACTION_APPROVAL_SCOPE_GOVERNED",
    "ACTION_APPROVAL_SCOPE_SYSTEM",
    "ACTION_APPROVAL_SOURCE_AI_TASK_RUN",
    "ACTION_APPROVAL_SOURCE_SYSTEM",
    "ACTION_APPROVAL_SOURCE_UNRESOLVED",
    "ActionApprovalTargetDataSnapshot",
    "ActionApprovalWouldDenySummary",
    "action_approval_access_decision",
    "action_approval_access_predicate",
    "action_approval_data_policy_blocker_count",
    "action_approval_would_deny_summary",
    "initialize_action_approval_data_access",
    "resolve_registered_action_target_data_access",
]
