"""Credential-fenced asynchronous provider contract qualification."""
import copy
import uuid
from dataclasses import replace
from typing import Literal
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models.ai_qualification import AIQualification
from app.models.ai_provider_attempt_receipt import AIProviderAttemptReceipt
from app.models.ai_task_run import AITaskRun
from app.services.ai_config import load_active_ai_settings
from app.services.ai_egress_data_policy import AIEgressPolicyError, lock_ai_egress_policy_fence
from app.services.ai_execution_ownership import require_ai_execution
from app.services.ai_qualification_request import request_ai_qualification_json as request_ai_json_with_usage
from app.services.ai_ops import ai_task_run_stop_reason, finish_ai_task_run
from app.services.ai_provider_client import AIIntegrationError
from app.services.ai_provider_protocol import provider_output_ceiling
from app.services.ai_qualification_cases import qualification_messages, validate_qualification, CASE_VERSION, qualification_plan_fingerprint
from app.services.export_job_access import authorize_export_job, ExportJobAccessDenied
from app.services.report_prompt_budget import estimate_message_tokens
from app.services.ai_request_identity import ai_request_fingerprint
from app.services.ai_workflow_dispatch import AIWorkflowDeferred


def qualification_fence(db: Session, run_id: uuid.UUID) -> AIQualification:
    lock_ai_egress_policy_fence(db)
    row = db.get(AIQualification, run_id)
    if row is None:
        raise AIEgressPolicyError("Provider qualification no longer exists.", retryable=False)
    try:
        authorize_export_job(db, row, lock=True, required_permissions=("write:ai",))
    except ExportJobAccessDenied as exc:
        raise AIEgressPolicyError("The credential accepting this qualification expired or lost access. Queue a new qualification.", retryable=False) from exc
    run = db.scalar(select(AITaskRun).where(AITaskRun.id == run_id).with_for_update().execution_options(populate_existing=True))
    if run is None or run.status != "running" or ai_task_run_stop_reason(run):
        raise AIEgressPolicyError("Provider qualification was canceled or replaced.", retryable=False)
    require_ai_execution(run)
    return db.scalar(select(AIQualification).where(AIQualification.run_id == run_id).with_for_update().execution_options(populate_existing=True))


def generate_qualification(db: Session, *, run_id: uuid.UUID) -> Literal["ready", "error"]:
    """Checkpoint contract probes and return their committed completion status."""
    row = qualification_fence(db, run_id)
    run = db.get(AITaskRun, run_id)
    if (run.metadata_json or {}).get("qualification_plan_sha256") != qualification_plan_fingerprint(row.features_json):
        raise AIIntegrationError("The qualification prompts changed after acceptance. Authorize a new qualification for the current probes.",
            retryable=False, provider_io_outcome="not_sent", failure_category="qualification_plan_changed")
    active = load_active_ai_settings(db, task_run_id=run_id)
    if not active.ai_enabled or not active.ai_configured:
        raise AIIntegrationError(active.configuration_error or "Provider is unavailable.", retryable=False, provider_io_outcome="not_sent")
    active = replace(active, request_max_retries=0, request_timeout_seconds=min(active.request_timeout_seconds, 60))
    features = [probe for feature in row.features_json for probe in (["report", "report_section"] if feature == "report" else [feature])]
    for feature in features:
        row = qualification_fence(db, run_id)
        results = copy.deepcopy(row.results_json)
        existing = next((entry for entry in results if entry["feature"] == feature), None)
        if existing and existing["state"] == "completed":
            continue
        messages = qualification_messages(feature)
        output = min(active.max_completion_tokens, 4096, provider_output_ceiling(active, messages))
        reservation = estimate_message_tokens(messages) + max(0, output)
        fingerprint = ai_request_fingerprint(active=active, feature_type="connection_test", messages=messages,
            item_id=None, daily_brief_id=None, report_id=None, requested_max_tokens=output)
        if existing:
            # A lost worker can stop after reserving local tokens but before the
            # receipt runtime begins. Reuse that exact reservation only with its
            # persisted request identity and proof that no provider I/O occurred.
            # Legacy starts without these fields remain unresolved.
            receipts = list(db.scalars(select(AIProviderAttemptReceipt).where(
                AIProviderAttemptReceipt.task_run_id_snapshot == run_id,
                AIProviderAttemptReceipt.request_fingerprint == fingerprint,
            )))
            if (existing.get("state") != "started"
                    or existing.get("request_fingerprint") != fingerprint
                    or existing.get("output_tokens") != output
                    or existing.get("reserved_tokens") != reservation
                    or any(receipt.state != "voided" or receipt.io_outcome != "not_sent"
                           or receipt.reconciliation_action is not None for receipt in receipts)):
                raise AIIntegrationError("Qualification delivery was interrupted. Review its receipt before authorizing a new run; automatic replay is blocked.",
                    retryable=False, provider_io_outcome="not_sent", failure_category="qualification_delivery_unresolved")
        else:
            if output < 128 or row.reserved_tokens + reservation > row.token_budget:
                raise AIIntegrationError("The qualification token budget cannot fit the next contract probe. Completed results remain available.",
                    retryable=False, provider_io_outcome="not_sent", failure_category="qualification_budget_exhausted")
            results.append({"feature": feature, "state": "started", "case_version": CASE_VERSION,
                            "request_fingerprint": fingerprint, "output_tokens": output,
                            "reserved_tokens": reservation})
            row.results_json = results
            row.reserved_tokens += reservation
            db.commit()
        def checkpoint() -> None:
            qualification_fence(db, run_id)
        try:
            completion = request_ai_json_with_usage(db, active, feature_type="connection_test", task_run_id=run_id,
                provider_operation_scope=f"qualification:{CASE_VERSION}:{feature}", messages=messages,
                execution_checkpoint=checkpoint, request_authorization_check=checkpoint,
                max_completion_tokens=output, max_retry_completion_tokens=output, max_provider_attempts=1)
        except AIWorkflowDeferred:
            # Admission deferral proves no provider call began. Releasing only
            # this unsent reservation preserves both fairness and token limits.
            db.rollback()
            row = qualification_fence(db, run_id)
            row.results_json = [entry for entry in row.results_json if entry["feature"] != feature]
            row.reserved_tokens -= reservation
            db.commit()
            raise
        passed, error = True, None
        try:
            validate_qualification(feature, completion.payload, messages)
        except ValueError as exc:
            passed, error = False, str(exc)[:1000]
        row = qualification_fence(db, run_id)
        results = copy.deepcopy(row.results_json)
        result = next(entry for entry in results if entry["feature"] == feature)
        result.update(request_fingerprint=fingerprint, state="completed", contract_passed=passed, error=error, model=completion.model,
            latency_ms=completion.latency_ms, prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens, total_tokens=completion.total_tokens)
        row.results_json = results
        db.commit()
    row = qualification_fence(db, run_id)
    passed = all(entry.get("contract_passed") for entry in row.results_json)
    status: Literal["ready", "error"] = "ready" if passed else "error"
    finish_ai_task_run(db, run_id=run_id, status=status,
        reason="qualification_contracts_passed" if passed else "qualification_contracts_failed",
        error=None if passed else "One or more feature contracts failed. Review qualification results before using this provider.",
        metadata_updates={"qualification_contract_passed": passed, "semantic_quality_approved": False})
    db.commit()
    return status


def qualification_checkpointed_fingerprints(db: Session, run_id: uuid.UUID) -> set[str] | None:
    row = db.get(AIQualification, run_id)
    if row is None:
        return None
    fingerprints = set()
    for result in row.results_json:
        value = result.get("request_fingerprint")
        if (result.get("state") == "completed" and type(result.get("contract_passed")) is bool
                and (result.get("feature") in row.features_json or result.get("feature") == "report_section" and "report" in row.features_json) and isinstance(value, str) and len(value) == 64):
            fingerprints.add(value)
    return fingerprints
