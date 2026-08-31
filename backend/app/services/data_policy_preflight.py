from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import and_, exists, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.core.data_policy_route_attestation import (
    RouteGovernanceAttestation,
    installed_route_governance_attestation,
)
from app.core.data_policy_route_manifest import (
    ROUTE_GOVERNANCE_MANIFEST,
    ROUTE_GOVERNANCE_MANIFEST_SHA256,
    ROUTE_GOVERNANCE_MANIFEST_VERSION,
)
from app.core.permissions import SYSTEM_ROLE_IDS
from app.models.ai_daily_brief import AIDailyBrief
from app.models.ai_task_run import AITaskRun
from app.models.ai_usage_event import AIUsageEvent
from app.models.alert_occurrence import (
    AlertOccurrence,
    AlertOccurrenceMetricCohortCapturedLabel,
    AlertOccurrenceMetricCohortLabel,
    AlertOccurrenceMetricCohortTaintLabel,
)
from app.models.audit_log import (
    AuditLogDataAccessLabel,
)
from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
    DataPolicyRoleGrant,
    DataPolicyState,
    HandlingLabel,
    QUARANTINE_HANDLING_LABEL_ID,
    UNRESTRICTED_HANDLING_LABEL_ID,
)
from app.models.feed import Feed
from app.models.integration import (
    IntegrationDelivery,
    IntegrationDeliveryMetricCohortCapturedLabel,
    IntegrationDeliveryMetricCohortLabel,
    IntegrationDeliveryMetricCohortTaintLabel,
    IntegrationEvent,
)
from app.models.investigation import Investigation
from app.models.report import Report
from app.schemas.data_policy import (
    DataPolicyBlockerResponse,
    DataPolicyPreflightResponse,
    DataPolicyRouteManifestEvidenceResponse,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    DATA_ACCESS_RESOURCE_AI_TASK_RUN,
    DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
    DATA_ACCESS_RESOURCE_DAILY_BRIEF,
    DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY,
    DATA_ACCESS_RESOURCE_INTEGRATION_EVENT,
    DATA_ACCESS_RESOURCE_INVESTIGATION,
    DATA_ACCESS_RESOURCE_REPORT,
    SUPPORTED_DATA_ACCESS_RESOURCE_TYPES,
)
from app.services.data_access_policy import (
    APPLICATION_DATA_POLICY_COVERAGE_VERSION,
    REQUIRED_ENFORCEMENT_COVERAGE_VERSION,
)
from app.services.metric_cohort_provenance import MetricCohortIntegritySummary


def runtime_data_policy_preflight(
    db: Session,
    *,
    state: DataPolicyState,
) -> DataPolicyPreflightResponse:
    """Evaluate only invariants safe for every request dependency."""

    blockers, route_evidence = _runtime_blockers(db, state=state)
    return _response(
        state=state,
        blockers=blockers,
        route_evidence=route_evidence,
        full=False,
    )


def full_data_policy_preflight(
    db: Session,
    *,
    state: DataPolicyState,
) -> DataPolicyPreflightResponse:
    """Evaluate authoritative activation readiness, including retained data."""

    blockers, route_evidence = _runtime_blockers(db, state=state)
    blockers.extend(_registry_blockers())
    blockers.extend(_retained_data_blockers(db))
    return _response(
        state=state,
        blockers=blockers,
        route_evidence=route_evidence,
        full=True,
    )


def database_data_policy_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    """Return database-only blockers used by the coverage migration guard."""

    return [*_built_in_label_blockers(db), *_retained_data_blockers(db)]


def _retained_data_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    return [
        *_label_and_feed_blockers(db),
        *_envelope_blockers(db),
        *_resource_completeness_blockers(db),
        *_ai_telemetry_scope_blockers(db),
        *_audit_lineage_blockers(db),
        *_inactive_normalized_label_blockers(db),
        *_metric_cohort_blockers(db),
        *_action_approval_blockers(db),
    ]


def _runtime_blockers(
    db: Session,
    *,
    state: DataPolicyState,
) -> tuple[
    list[DataPolicyBlockerResponse],
    DataPolicyRouteManifestEvidenceResponse,
]:
    blockers: list[DataPolicyBlockerResponse] = []
    if (
        min(
            state.coverage_version,
            APPLICATION_DATA_POLICY_COVERAGE_VERSION,
        )
        < REQUIRED_ENFORCEMENT_COVERAGE_VERSION
    ):
        blockers.append(
            _blocker(
                "coverage_incomplete",
                "The database and installed application have not both declared complete data-policy coverage.",
            )
        )
    elif state.coverage_version > APPLICATION_DATA_POLICY_COVERAGE_VERSION:
        blockers.append(
            _blocker(
                "coverage_incompatible",
                "The database data-policy coverage is newer than this application process.",
            )
        )

    blockers.extend(_built_in_label_blockers(db))

    route_evidence = _route_manifest_evidence()
    if not route_evidence.installed:
        blockers.append(
            _blocker(
                "route_attestation_missing",
                "This process has not installed its validated canonical route-manifest attestation.",
            )
        )
    elif not route_evidence.valid:
        blockers.append(
            _blocker(
                "route_attestation_invalid",
                "The installed route attestation does not match the exact application manifest.",
            )
        )
    return blockers, route_evidence


def _built_in_label_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    blockers: list[DataPolicyBlockerResponse] = []
    unrestricted = db.get(HandlingLabel, UNRESTRICTED_HANDLING_LABEL_ID)
    if not (
        unrestricted is not None
        and unrestricted.key == "unrestricted"
        and unrestricted.is_unrestricted
        and unrestricted.is_system
        and unrestricted.is_active
    ):
        blockers.append(
            _blocker(
                "unrestricted_label_invalid",
                "The required unrestricted handling label is missing or invalid.",
            )
        )

    quarantine = db.get(HandlingLabel, QUARANTINE_HANDLING_LABEL_ID)
    if not (
        quarantine is not None
        and quarantine.key == "quarantine"
        and not quarantine.is_unrestricted
        and quarantine.is_system
        and quarantine.is_active
    ):
        blockers.append(
            _blocker(
                "quarantine_label_invalid",
                "The required quarantine handling label is missing or invalid.",
            )
        )

    return blockers


def _route_manifest_evidence() -> DataPolicyRouteManifestEvidenceResponse:
    expected_counts = Counter(
        entry.governance_class.value for entry in ROUTE_GOVERNANCE_MANIFEST.entries
    )
    expected_request_context = sum(
        entry.governance_class in ROUTE_GOVERNANCE_MANIFEST.request_context_classes
        for entry in ROUTE_GOVERNANCE_MANIFEST.entries
    )
    installed = installed_route_governance_attestation()
    if installed is None:
        return DataPolicyRouteManifestEvidenceResponse(
            installed=False,
            valid=False,
            version=ROUTE_GOVERNANCE_MANIFEST_VERSION,
            digest=ROUTE_GOVERNANCE_MANIFEST_SHA256,
            declared_operation_count=len(ROUTE_GOVERNANCE_MANIFEST.entries),
            validated_operation_count=0,
            request_context_operation_count=0,
            governance_class_counts=dict(sorted(expected_counts.items())),
        )
    valid = _attestation_matches_manifest(
        installed,
        expected_counts=expected_counts,
        expected_request_context=expected_request_context,
    )
    return DataPolicyRouteManifestEvidenceResponse(
        installed=True,
        valid=valid,
        version=installed.manifest_version,
        digest=installed.manifest_sha256,
        declared_operation_count=installed.declared_operation_count,
        validated_operation_count=installed.validated_operation_count,
        request_context_operation_count=installed.request_context_operation_count,
        governance_class_counts=dict(installed.governance_class_counts),
    )


def _attestation_matches_manifest(
    attestation: RouteGovernanceAttestation,
    *,
    expected_counts: Counter[str],
    expected_request_context: int,
) -> bool:
    declared_count = len(ROUTE_GOVERNANCE_MANIFEST.entries)
    return bool(
        attestation.manifest_version == ROUTE_GOVERNANCE_MANIFEST_VERSION
        and attestation.manifest_sha256 == ROUTE_GOVERNANCE_MANIFEST_SHA256
        and attestation.canonical_prefix == ROUTE_GOVERNANCE_MANIFEST.canonical_prefix
        and attestation.declared_operation_count == declared_count
        and attestation.validated_operation_count == declared_count
        and attestation.request_context_operation_count == expected_request_context
        and dict(attestation.governance_class_counts) == expected_counts
    )


def _label_and_feed_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    blockers: list[DataPolicyBlockerResponse] = []
    inactive_feed_count = _count(
        db,
        select(func.count(Feed.id))
        .join(HandlingLabel, HandlingLabel.id == Feed.handling_label_id)
        .where(HandlingLabel.is_active.is_(False)),
    )
    if inactive_feed_count:
        blockers.append(
            _blocker(
                "feeds_use_inactive_labels",
                "One or more feeds use archived handling labels.",
                inactive_feed_count,
            )
        )

    admin_role_id = SYSTEM_ROLE_IDS["admin"]
    missing_admin = _count(
        db,
        select(func.count(HandlingLabel.id)).where(
            HandlingLabel.is_active.is_(True),
            HandlingLabel.is_unrestricted.is_(False),
            ~select(DataPolicyRoleGrant.label_id)
            .where(
                DataPolicyRoleGrant.label_id == HandlingLabel.id,
                DataPolicyRoleGrant.role_id == admin_role_id,
            )
            .exists(),
        ),
    )
    if missing_admin:
        blockers.append(
            _blocker(
                "restricted_labels_missing_admin_grant",
                "Every active restricted label must grant the built-in administrator role.",
                missing_admin,
            )
        )

    labels_without_roles = _count(
        db,
        select(func.count(HandlingLabel.id)).where(
            HandlingLabel.is_active.is_(True),
            HandlingLabel.is_unrestricted.is_(False),
            ~select(DataPolicyRoleGrant.label_id)
            .where(DataPolicyRoleGrant.label_id == HandlingLabel.id)
            .exists(),
        ),
    )
    if labels_without_roles:
        blockers.append(
            _blocker(
                "restricted_labels_without_roles",
                "Every active restricted label must grant at least one role.",
                labels_without_roles,
            )
        )
    return blockers


def _registry_blockers() -> list[DataPolicyBlockerResponse]:
    from app.services.action_registry import validate_action_registry

    try:
        validate_action_registry()
    except (AttributeError, KeyError, RuntimeError, TypeError, ValueError):
        return [
            _blocker(
                "action_registry_target_policy_invalid",
                "One or more approval-backed actions lack a valid target data-policy declaration.",
            )
        ]
    return []


def _envelope_blockers(db: Session) -> list[DataPolicyBlockerResponse]:
    blockers: list[DataPolicyBlockerResponse] = []
    unsupported = _count(
        db,
        select(func.count(DataAccessEnvelope.id)).where(
            DataAccessEnvelope.resource_type.not_in(
                sorted(SUPPORTED_DATA_ACCESS_RESOURCE_TYPES)
            )
        ),
    )
    if unsupported:
        blockers.append(
            _blocker(
                "unsupported_envelope_resource_types",
                "One or more data-access envelopes use an unsupported resource type.",
                unsupported,
            )
        )

    inconsistent = _envelope_parity_mismatch_count(db)
    if inconsistent:
        blockers.append(
            _blocker(
                "envelope_lineage_parity_invalid",
                "Normalized envelope sources, labels, revisions, and aggregate counts do not agree.",
                inconsistent,
            )
        )
    return blockers


def _envelope_parity_mismatch_count(db: Session) -> int:
    source_totals = (
        select(
            DataAccessEnvelopeSource.envelope_id.label("envelope_id"),
            func.count(DataAccessEnvelopeSource.id).label("source_count"),
            func.max(DataAccessEnvelopeSource.captured_policy_revision).label(
                "max_revision"
            ),
        )
        .group_by(DataAccessEnvelopeSource.envelope_id)
        .subquery()
    )
    label_totals = (
        select(
            DataAccessEnvelopeLabel.envelope_id.label("envelope_id"),
            func.sum(DataAccessEnvelopeLabel.source_count).label("label_count"),
        )
        .group_by(DataAccessEnvelopeLabel.envelope_id)
        .subquery()
    )
    source_labels = (
        select(
            DataAccessEnvelopeSource.envelope_id.label("envelope_id"),
            DataAccessEnvelopeSource.handling_label_id.label("label_id"),
            func.count(DataAccessEnvelopeSource.id).label("source_count"),
        )
        .group_by(
            DataAccessEnvelopeSource.envelope_id,
            DataAccessEnvelopeSource.handling_label_id,
        )
        .subquery()
    )
    aggregate_label = aliased(DataAccessEnvelopeLabel)
    source_side_mismatch = exists(
        select(source_labels.c.envelope_id)
        .outerjoin(
            aggregate_label,
            and_(
                aggregate_label.envelope_id == source_labels.c.envelope_id,
                aggregate_label.label_id == source_labels.c.label_id,
            ),
        )
        .where(
            source_labels.c.envelope_id == DataAccessEnvelope.id,
            or_(
                aggregate_label.envelope_id.is_(None),
                aggregate_label.source_count != source_labels.c.source_count,
            ),
        )
    )
    normalized_source = aliased(DataAccessEnvelopeSource)
    label_side_source_count = (
        select(func.count(normalized_source.id))
        .where(
            normalized_source.envelope_id == DataAccessEnvelopeLabel.envelope_id,
            normalized_source.handling_label_id == DataAccessEnvelopeLabel.label_id,
        )
        .scalar_subquery()
    )
    label_side_mismatch = exists(
        select(DataAccessEnvelopeLabel.envelope_id).where(
            DataAccessEnvelopeLabel.envelope_id == DataAccessEnvelope.id,
            DataAccessEnvelopeLabel.source_count != label_side_source_count,
        )
    )
    return _count(
        db,
        select(func.count(DataAccessEnvelope.id))
        .outerjoin(
            source_totals,
            source_totals.c.envelope_id == DataAccessEnvelope.id,
        )
        .outerjoin(
            label_totals,
            label_totals.c.envelope_id == DataAccessEnvelope.id,
        )
        .where(
            or_(
                DataAccessEnvelope.source_count
                != func.coalesce(source_totals.c.source_count, 0),
                DataAccessEnvelope.source_count
                != func.coalesce(label_totals.c.label_count, 0),
                DataAccessEnvelope.policy_revision
                < func.coalesce(source_totals.c.max_revision, 1),
                source_side_mismatch,
                label_side_mismatch,
            )
        ),
    )


def _resource_completeness_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    resources = (
        (Report, DATA_ACCESS_RESOURCE_REPORT, None),
        (AIDailyBrief, DATA_ACCESS_RESOURCE_DAILY_BRIEF, None),
        (Investigation, DATA_ACCESS_RESOURCE_INVESTIGATION, None),
        (AlertOccurrence, DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE, None),
        (IntegrationEvent, DATA_ACCESS_RESOURCE_INTEGRATION_EVENT, None),
        (IntegrationDelivery, DATA_ACCESS_RESOURCE_INTEGRATION_DELIVERY, None),
    )
    missing = 0
    for model, resource_type, predicate in resources:
        envelope = aliased(DataAccessEnvelope)
        filters = [] if predicate is None else [predicate]
        missing += _count(
            db,
            select(func.count(model.id)).where(
                *filters,
                ~exists(
                    select(envelope.id).where(
                        envelope.resource_type == resource_type,
                        envelope.resource_id == model.id,
                    )
                ),
            ),
        )
    if not missing:
        return []
    return [
        _blocker(
            "governed_resources_missing_envelopes",
            "One or more retained governed resources lack a normalized data-access envelope.",
            missing,
        )
    ]


def _ai_telemetry_scope_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    run_envelope = aliased(DataAccessEnvelope)
    run_has_envelope = exists(
        select(run_envelope.id).where(
            run_envelope.resource_type == DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            run_envelope.resource_id == AITaskRun.id,
        )
    )
    valid_run = or_(
        and_(
            AITaskRun.data_access_scope == "system",
            AITaskRun.task_type == "connection_test",
            AITaskRun.data_access_lineage_complete.is_(True),
            AITaskRun.item_id.is_(None),
            AITaskRun.daily_brief_id.is_(None),
            AITaskRun.report_id.is_(None),
            AITaskRun.parent_run_id.is_(None),
            ~run_has_envelope,
        ),
        and_(
            AITaskRun.data_access_scope == "governed",
            AITaskRun.data_access_lineage_complete.is_(True),
            run_has_envelope,
        ),
    )
    invalid_runs = _count(
        db,
        select(func.count(AITaskRun.id)).where(~valid_run),
    )

    usage_envelope = aliased(DataAccessEnvelope)
    usage_has_envelope = exists(
        select(usage_envelope.id).where(
            usage_envelope.resource_type == DATA_ACCESS_RESOURCE_AI_USAGE_EVENT,
            usage_envelope.resource_id == AIUsageEvent.id,
        )
    )
    linked_run = aliased(AITaskRun)
    linked_run_envelope = aliased(DataAccessEnvelope)
    linked_run_has_envelope = exists(
        select(linked_run_envelope.id).where(
            linked_run_envelope.resource_type == DATA_ACCESS_RESOURCE_AI_TASK_RUN,
            linked_run_envelope.resource_id == linked_run.id,
        )
    )
    linked_exact_system_run = or_(
        AIUsageEvent.task_run_id_snapshot.is_(None),
        exists(
            select(linked_run.id).where(
                linked_run.id == AIUsageEvent.task_run_id_snapshot,
                linked_run.data_access_scope == "system",
                linked_run.task_type == "connection_test",
                linked_run.data_access_lineage_complete.is_(True),
                linked_run.item_id.is_(None),
                linked_run.daily_brief_id.is_(None),
                linked_run.report_id.is_(None),
                linked_run.parent_run_id.is_(None),
                ~linked_run_has_envelope,
            )
        ),
    )
    valid_usage = or_(
        and_(
            AIUsageEvent.data_access_scope == "system",
            AIUsageEvent.feature_type == "connection_test",
            AIUsageEvent.item_id.is_(None),
            AIUsageEvent.daily_brief_id.is_(None),
            AIUsageEvent.report_id.is_(None),
            ~usage_has_envelope,
            linked_exact_system_run,
        ),
        and_(
            AIUsageEvent.data_access_scope == "governed",
            usage_has_envelope,
        ),
    )
    invalid_usage = _count(
        db,
        select(func.count(AIUsageEvent.id)).where(~valid_usage),
    )
    blockers: list[DataPolicyBlockerResponse] = []
    if invalid_runs:
        blockers.append(
            _blocker(
                "ai_task_run_scope_integrity_invalid",
                "One or more retained AI task runs violate their exact system or governed lineage contract.",
                invalid_runs,
            )
        )
    if invalid_usage:
        blockers.append(
            _blocker(
                "ai_usage_event_scope_integrity_invalid",
                "One or more retained AI usage events violate their exact system or governed lineage contract.",
                invalid_usage,
            )
        )
    return blockers


def _audit_lineage_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    inconsistent = int(
        db.scalar(
            text(
                r"""
                WITH base_invalid AS (
                    SELECT audit.id AS audit_log_id
                    FROM audit_logs AS audit
                    WHERE (
                        audit.data_access_governed AND NOT EXISTS (
                            SELECT 1
                            FROM audit_log_data_access_labels AS label
                            WHERE label.audit_log_id = audit.id
                        )
                    ) OR (
                        NOT audit.data_access_governed AND EXISTS (
                            SELECT 1
                            FROM audit_log_data_access_labels AS label
                            WHERE label.audit_log_id = audit.id
                        )
                    )
                ),
                linked_envelopes AS (
                    SELECT audit.id AS audit_log_id, envelope.id AS envelope_id
                    FROM audit_logs AS audit
                    JOIN data_access_envelopes AS envelope
                      ON envelope.resource_type = CASE
                          WHEN audit.resource_type = 'daily_brief'
                          THEN 'ai_daily_brief'
                          ELSE audit.resource_type
                      END
                     AND envelope.resource_id = CASE
                         WHEN COALESCE(audit.resource_id, '') ~
                              '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
                         THEN audit.resource_id::uuid ELSE NULL
                     END
                    UNION
                    SELECT audit.id, envelope.id
                    FROM audit_logs AS audit
                    JOIN data_access_envelopes AS envelope
                      ON envelope.resource_type = 'ai_task_run'
                     AND envelope.resource_id = CASE
                         WHEN audit.resource_type = 'ai_task_run'
                          AND COALESCE(audit.resource_id, '') ~
                              '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
                         THEN audit.resource_id::uuid
                         WHEN COALESCE(audit.metadata_json->>'run_id', '') ~
                              '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
                         THEN (audit.metadata_json->>'run_id')::uuid
                         ELSE NULL
                     END
                    WHERE audit.action LIKE 'ai.%'
                       OR audit.action LIKE 'reports.generate.%'
                ),
                invalid_legacy AS (
                    SELECT DISTINCT audit.id AS audit_log_id
                    FROM audit_logs AS audit
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        audit.data_access_label_ids
                    ) AS value(label_id)
                    LEFT JOIN handling_labels AS handling
                      ON handling.id = CASE
                         WHEN value.label_id ~
                              '^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$'
                         THEN value.label_id::uuid ELSE NULL
                     END
                    WHERE audit.data_access_governed
                      AND (
                          (
                              handling.id IS NOT NULL AND NOT EXISTS (
                                  SELECT 1
                                  FROM audit_log_data_access_labels AS label
                                  WHERE label.audit_log_id = audit.id
                                    AND label.label_id = handling.id
                              )
                          ) OR (
                              handling.id IS NULL AND NOT EXISTS (
                                  SELECT 1
                                  FROM audit_log_data_access_labels AS label
                                  WHERE label.audit_log_id = audit.id
                                    AND label.label_id =
                                        '00000000-0000-4000-8000-000000000202'::uuid
                              )
                          )
                      )
                ),
                invalid_linked AS (
                    SELECT linked.audit_log_id
                    FROM linked_envelopes AS linked
                    JOIN data_access_envelope_labels AS expected
                      ON expected.envelope_id = linked.envelope_id
                    WHERE NOT EXISTS (
                        SELECT 1 FROM audit_log_data_access_labels AS actual
                        WHERE actual.audit_log_id = linked.audit_log_id
                          AND actual.label_id = expected.label_id
                    )
                    UNION
                    SELECT linked.audit_log_id
                    FROM linked_envelopes AS linked
                    JOIN data_access_envelope_sources AS expected
                      ON expected.envelope_id = linked.envelope_id
                     AND expected.source_feed_id IS NOT NULL
                    WHERE NOT EXISTS (
                        SELECT 1 FROM audit_log_data_access_feeds AS actual
                        WHERE actual.audit_log_id = linked.audit_log_id
                          AND actual.source_feed_id_snapshot =
                              expected.source_feed_id
                    )
                )
                SELECT count(*) FROM (
                    SELECT audit_log_id FROM base_invalid
                    UNION SELECT audit_log_id FROM invalid_legacy
                    UNION SELECT audit_log_id FROM invalid_linked
                ) AS invalid
                """
            )
        )
        or 0
    )
    if not inconsistent:
        return []
    return [
        _blocker(
            "normalized_audit_lineage_invalid",
            "Governed audit records and immutable normalized label snapshots do not agree.",
            inconsistent,
        )
    ]


def _inactive_normalized_label_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    references = 0
    reference_models = (
        (DataAccessEnvelopeSource, DataAccessEnvelopeSource.handling_label_id),
        (DataAccessEnvelopeLabel, DataAccessEnvelopeLabel.label_id),
        (AuditLogDataAccessLabel, AuditLogDataAccessLabel.label_id),
        (
            AlertOccurrenceMetricCohortCapturedLabel,
            AlertOccurrenceMetricCohortCapturedLabel.label_id,
        ),
        (
            AlertOccurrenceMetricCohortTaintLabel,
            AlertOccurrenceMetricCohortTaintLabel.label_id,
        ),
        (
            AlertOccurrenceMetricCohortLabel,
            AlertOccurrenceMetricCohortLabel.label_id,
        ),
        (
            IntegrationDeliveryMetricCohortCapturedLabel,
            IntegrationDeliveryMetricCohortCapturedLabel.label_id,
        ),
        (
            IntegrationDeliveryMetricCohortTaintLabel,
            IntegrationDeliveryMetricCohortTaintLabel.label_id,
        ),
        (
            IntegrationDeliveryMetricCohortLabel,
            IntegrationDeliveryMetricCohortLabel.label_id,
        ),
    )
    for model, label_id in reference_models:
        references += _count(
            db,
            select(func.count())
            .select_from(model)
            .where(
                exists(
                    select(HandlingLabel.id).where(
                        HandlingLabel.id == label_id,
                        HandlingLabel.is_active.is_(False),
                    )
                )
            ),
        )
    if not references:
        return []
    return [
        _blocker(
            "inactive_normalized_label_references",
            "Normalized envelope, audit, or metric lineage references archived handling labels.",
            references,
        )
    ]


def _metric_cohort_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    from app.services.alert_metric_data_policy import alert_metric_cohort_integrity
    from app.services.integration_metric_data_policy import (
        integration_metric_cohort_integrity,
    )

    blockers: list[DataPolicyBlockerResponse] = []
    blockers.extend(
        _metric_summary_blockers(
            "alert_metric",
            "alert occurrence",
            alert_metric_cohort_integrity(db),
        )
    )
    blockers.extend(
        _metric_summary_blockers(
            "integration_metric",
            "integration delivery",
            integration_metric_cohort_integrity(db),
        )
    )
    return blockers


def _metric_summary_blockers(
    prefix: str,
    label: str,
    summary: MetricCohortIntegritySummary,
) -> list[DataPolicyBlockerResponse]:
    checks = (
        (
            "invalid_identity",
            summary.invalid_identity_count,
            "capture-time cohort identities",
        ),
        (
            "missing_captured_labels",
            summary.missing_captured_labels_count,
            "captured label sets",
        ),
        (
            "label_parity_mismatch",
            summary.label_parity_mismatch_count,
            "captured, taint, and effective label parity",
        ),
        (
            "aggregate_parity_mismatch",
            summary.metric_parity_mismatch_count,
            "cohort and aggregate metric parity",
        ),
        (
            "incomplete_without_quarantine",
            summary.incomplete_without_quarantine_count,
            "fail-closed quarantine provenance",
        ),
    )
    return [
        _blocker(
            f"{prefix}_{suffix}",
            f"One or more {label} metric cohorts violate {detail}.",
            count,
        )
        for suffix, count, detail in checks
        if count
    ]


def _action_approval_blockers(
    db: Session,
) -> list[DataPolicyBlockerResponse]:
    from app.services.action_approval_data_policy import (
        action_approval_data_policy_blocker_count,
    )

    count = action_approval_data_policy_blocker_count(db)
    if not count:
        return []
    return [
        _blocker(
            "action_approval_data_policy_invalid",
            "One or more retained approvals lack an exact valid target-policy scope and envelope snapshot.",
            count,
        )
    ]


def _response(
    *,
    state: DataPolicyState,
    blockers: list[DataPolicyBlockerResponse],
    route_evidence: DataPolicyRouteManifestEvidenceResponse,
    full: bool,
) -> DataPolicyPreflightResponse:
    blocker_counts: Counter[str] = Counter()
    for blocker in blockers:
        blocker_counts[blocker.code] += blocker.count or 1
    enforcement_only_blocker_codes = {
        "restricted_labels_missing_admin_grant",
        "restricted_labels_without_roles",
    }
    ready_for_audit = full and not any(
        blocker.code not in enforcement_only_blocker_codes for blocker in blockers
    )
    return DataPolicyPreflightResponse(
        ready_for_audit=ready_for_audit,
        ready_for_enforcement=full and not blockers,
        current_coverage_version=min(
            state.coverage_version,
            APPLICATION_DATA_POLICY_COVERAGE_VERSION,
        ),
        required_coverage_version=REQUIRED_ENFORCEMENT_COVERAGE_VERSION,
        blockers=blockers,
        evaluated_policy_revision=state.revision,
        full=full,
        checked_at=datetime.now(timezone.utc),
        route_manifest=route_evidence,
        blocker_counts=dict(sorted(blocker_counts.items())),
    )


def _blocker(
    code: str,
    detail: str,
    count: int | None = None,
) -> DataPolicyBlockerResponse:
    return DataPolicyBlockerResponse(code=code, detail=detail, count=count)


def _count(db: Session, statement) -> int:
    return int(db.scalar(statement) or 0)


__all__ = [
    "APPLICATION_DATA_POLICY_COVERAGE_VERSION",
    "REQUIRED_ENFORCEMENT_COVERAGE_VERSION",
    "database_data_policy_blockers",
    "full_data_policy_preflight",
    "runtime_data_policy_preflight",
]
