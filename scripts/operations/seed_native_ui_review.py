#!/usr/bin/env python3
"""Seed only a fresh disposable native UI review database; emit private JSON.

Run after migrations/admin bootstrap and before starting Beat or workers. The
caller must capture stdout into a private 0600 file; no credentials are emitted.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit


SOURCE_REVISION = "c591c3a8e30a6ef1fc38956317f0b0b41a12285b"
BASELINE_COUNTS = {
    "users": (1, 1), "audit_logs": (1, 1), "iam_policy_state": (1, 1),
    "iam_roles": (3, 3), "iam_role_permissions": (1, 1000), "iam_groups": (1, 1),
    "data_policy_state": (1, 1), "handling_labels": (2, 2),
    "data_policy_role_grants": (1, 1), "workspace_role_policies": (3, 3),
    "lifecycle_catalog_state": (1, 1), "report_templates": (7, 7),
    "processing_dispatch_state": (1, 1), "ai_provider_routing": (1, 1),
    "ai_provider_budget_states": (1, 1),
}
TEMPLATE_KEYS = {
    "weekly_threat_landscape", "executive_security_summary",
    "vulnerability_exploitation_review", "malware_campaign_review",
    "technology_stack_exposure", "ioc_infrastructure_summary",
    "custom_intelligence_report",
}
TITLE = "Synthetic defensive intelligence"
PARAGRAPH = (
    "This synthetic review article describes defensive intelligence triage, "
    "analyst collaboration, evidence review, and accessible reading workflows. "
    "It contains no operational intelligence or external provider requests."
)
SUMMARY = "\n\n".join(f"RSS section {number}. {PARAGRAPH}" for number in range(1, 61))
ARTICLE_TEXT = "\n\n".join(
    f"Article section {number}. {PARAGRAPH}" for number in range(1, 91)
)


def require(condition: bool) -> None:
    if not condition:
        raise ValueError("native_ui_seed_guard_failed")


def validate_environment(environment: dict[str, str]) -> dict[str, str]:
    require(environment.get("REVIEW_DISPOSABLE_DATABASE") == "1")
    require(environment.get("REVIEW_SOURCE_SHA") == SOURCE_REVISION)
    require(environment.get("AI_ENABLED", "").lower() == "true")
    require(environment.get("ALLOW_PRIVATE_NETWORK_FETCH", "").lower() == "true")
    email = environment.get("ADMIN_EMAIL", "").strip().lower()
    require(3 <= len(email) <= 320 and email.count("@") == 1)
    require(all(ord(char) > 32 and ord(char) != 127 for char in email))
    origin = environment.get("REVIEW_PUBLISHER_ORIGIN", "")
    require(bool(origin) and all(ord(char) > 32 and ord(char) != 127 for char in origin))
    parsed = urlsplit(origin)
    require(parsed.scheme == "http" and parsed.hostname == "review-source")
    require(parsed.port == 8765 and parsed.netloc == "review-source:8765")
    require(not parsed.username and not parsed.password)
    require(parsed.path in {"", "/"} and not parsed.query and not parsed.fragment)
    return {"admin_email": email, "publisher_origin": origin.rstrip("/")}


def validate_counts(counts: dict[str, int]) -> None:
    require(BASELINE_COUNTS.keys() <= counts.keys())
    for name, count in counts.items():
        require(type(count) is int and count >= 0)
        lower, upper = BASELINE_COUNTS.get(name, (0, 0))
        require(lower <= count <= upper)


def validate_login_credentials(email: str, password: str, *, request_model=None) -> None:
    if request_model is None:
        from app.schemas.auth import LoginRequest

        request_model = LoginRequest
    request = request_model(email=email, password=password)
    require(str(request.email) == email)


def run_transaction(session_factory, operation):
    db = session_factory()
    try:
        result = operation(db)
        # Serialization must succeed before any fixture becomes committed.
        payload = json.dumps(result, sort_keys=True, separators=(",", ":"))
        db.commit()
        return payload
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def assert_pristine_database(db, inputs):
    from sqlalchemy import func, select

    from app.core.permissions import ALL_USERS_GROUP_ID, SYSTEM_ROLE_IDS
    from app.db.base import Base
    from app.models import (
        AIProviderBudgetState, AIProviderRouting, AuditLog, DataPolicyRoleGrant,
        DataPolicyState, HandlingLabel, IAMGroup,
        IAMPolicyState, IAMRole, IAMRolePermission, LifecycleCatalogState,
        ProcessingDispatchState, ReportTemplate, User, WorkspaceRolePolicy,
    )
    from app.models.data_policy import (
        QUARANTINE_HANDLING_LABEL_ID, UNRESTRICTED_HANDLING_LABEL_ID,
    )

    require(db.get_bind().dialect.name == "postgresql")
    counts = {
        table.name: db.scalar(select(func.count()).select_from(table))
        for table in Base.metadata.sorted_tables
    }
    validate_counts(counts)
    admin = db.scalar(select(User))
    require(admin.email == inputs["admin_email"] and admin.role == "admin")
    require(admin.is_active and admin.is_approved and admin.password_login_enabled)
    require(admin.provisioning_source == "local" and admin.auth_token_version == 0)
    audit = db.scalar(select(AuditLog))
    require(audit.action == "system.seed_admin.create" and audit.resource_type == "user")
    require(audit.resource_id == str(admin.id) and audit.actor_user_id is None)
    policy = db.get(DataPolicyState, 1)
    require(policy.mode == "disabled" and policy.revision == 1)
    require(policy.coverage_version == 1 and policy.updated_by_user_id is None)
    require(db.get(IAMPolicyState, 1).revision == 1)
    roles = list(db.scalars(select(IAMRole)))
    require({role.id for role in roles} == set(SYSTEM_ROLE_IDS.values()))
    require(all(role.is_system and role.revision == 1 for role in roles))
    require(all(row.role_id in SYSTEM_ROLE_IDS.values()
                for row in db.scalars(select(IAMRolePermission))))
    group = db.scalar(select(IAMGroup))
    require(group.id == ALL_USERS_GROUP_ID and group.key == "all-users")
    require(group.is_system and group.source == "local" and group.revision == 1)
    labels = list(db.scalars(select(HandlingLabel)))
    require({label.id for label in labels} == {
        UNRESTRICTED_HANDLING_LABEL_ID, QUARANTINE_HANDLING_LABEL_ID,
    })
    require(all(label.is_system and label.is_active and label.revision == 1
                for label in labels))
    grant = db.scalar(select(DataPolicyRoleGrant))
    require(grant.label_id == QUARANTINE_HANDLING_LABEL_ID)
    require(grant.role_id == SYSTEM_ROLE_IDS["admin"])
    workspace = list(db.scalars(select(WorkspaceRolePolicy)))
    require({row.role for row in workspace} == {"admin", "analyst", "viewer"})
    require(all(row.revision == 1 and row.updated_by_user_id is None for row in workspace))
    lifecycle = db.get(LifecycleCatalogState, 1)
    require(lifecycle.catalog_version == 1 and lifecycle.bootstrapped_at is None)
    require(lifecycle.bootstrap_snapshot_json == {})
    templates = list(db.scalars(select(ReportTemplate)))
    require({row.builtin_key for row in templates} == TEMPLATE_KEYS)
    require(all(row.owner_user_id is None and row.visibility == "shared" for row in templates))
    require(db.get(ProcessingDispatchState, 1).last_feed_id is None)
    routing = db.get(AIProviderRouting, 1)
    require(routing.version == 1 and all(getattr(routing, name) is None for name in (
        "default_provider_id", "item_enrichment_provider_id", "team_assessment_provider_id",
        "daily_brief_provider_id", "report_provider_id",
    )))
    require(db.scalar(select(AIProviderBudgetState)).provider_key == "!quota-configuration")
    return admin


def build_fixture(db, inputs):
    from app.core.config import get_settings
    from app.models import Article, Feed, Item, ItemClassification, Team
    from app.models.data_policy import UNRESTRICTED_HANDLING_LABEL_ID
    from app.schemas.iam import GroupWriteRequest
    from app.services.iam_groups import add_group_member, create_group

    settings = get_settings()
    require(settings.ai_enabled and settings.allow_private_network_fetch)
    require(settings.admin_email.strip().lower() == inputs["admin_email"])
    admin = assert_pristine_database(db, inputs)
    now = datetime.now(timezone.utc)
    group = create_group(
        db, payload=GroupWriteRequest(key="native-ui-review", name="Native UI review"),
        actor_user_id=admin.id,
    )
    membership = add_group_member(
        db, group_id=group.id, user_id=admin.id, actor_user_id=admin.id,
        expected_group_revision=group.revision,
    )
    require(membership.created)
    team = Team(
        key="native-ui-review", name="Native UI review", membership_group_id=group.id,
        manager_group_id=group.id, created_by_user_id=admin.id,
    )
    origin = inputs["publisher_origin"]
    feed = Feed(
        name="Synthetic native UI review", url=f"{origin}/feed.xml", site_url=origin,
        description="Synthetic disposable review feed", language="en", enabled=False,
        handling_label_id=UNRESTRICTED_HANDLING_LABEL_ID,
        last_fetch_at=now, last_success_at=now,
    )
    db.add_all([team, feed])
    db.flush()
    source_hash = hashlib.sha256((SUMMARY + ARTICLE_TEXT).encode()).hexdigest()
    item = Item(
        feed_id=feed.id, source_guid="synthetic-native-ui-review", title=TITLE,
        url=f"{origin}/article.html", canonical_url=f"{origin}/article.html",
        url_domain="review-source", summary=SUMMARY, published_at=now, first_seen_at=now,
        dedupe_key="synthetic-native-ui-review", content_hash=source_hash,
        status="fetched", ioc_extraction_state="completed_empty",
        classification_required_version=1, classification_completed_version=1,
    )
    db.add(item)
    db.flush()
    db.add_all([
        Article(
            item_id=item.id, final_url=item.url, retrieved_at=now, http_status=200,
            content_type="text/html; charset=utf-8", title_extracted=TITLE,
            text=ARTICLE_TEXT, extraction_method="native_review_fixture", language="en",
            word_count=len(ARTICLE_TEXT.split()), fetch_ms=0,
        ),
        ItemClassification(
            item_id=item.id, primary_category="vulnerability", secondary_categories=[],
            confidence=0.8, scores_json={"vulnerability": 0.8}, matched_terms_json={},
            source_hash=source_hash, rules_version="v1", classified_at=now,
        ),
    ])
    db.flush()
    return {
        "schema": "threatlens-native-ui-seed-v1", "source_revision": SOURCE_REVISION,
        "synthetic": True, "team_id": str(team.id), "feed_id": str(feed.id),
        "item_id": str(item.id), "counts": {
            "teams": 1, "groups": 1, "memberships": 1, "feeds": 1,
            "items": 1, "articles": 1, "classifications": 1,
        }, "attention_queue_rows": 0,
    }


def seed_database(inputs):
    from app.core.config import get_settings

    validate_login_credentials(inputs["admin_email"], get_settings().admin_password)
    from app.db.session import SessionLocal

    return run_transaction(SessionLocal, lambda db: build_fixture(db, inputs))


def main(environment=None, *, seed=None, stdout=None, stderr=None) -> int:
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    try:
        inputs = validate_environment(dict(os.environ) if environment is None else environment)
        payload = (seed_database if seed is None else seed)(inputs)
        stdout.write(payload + "\n")
        return 0
    except Exception as error:
        # No exception text, tracebacks, IDs, SQL, credentials, or environment.
        stderr.write(f"native_ui_seed_failed error_type={type(error).__name__}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
