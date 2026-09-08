from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.schema import CreateIndex

from app.models.article import Article
from app.models.lifecycle import (
    LIFECYCLE_TARGET_KEYS,
    LifecyclePolicy,
    LifecyclePreview,
    LifecycleRun,
)
from app.schemas.lifecycle import (
    LifecyclePolicyDraft,
    LifecyclePolicyUpdateRequest,
    LifecyclePreviewRequest,
    LifecycleTargetKey,
)
from app.services.lifecycle_catalog import TARGETS


_BACKEND_DIR = Path(__file__).resolve().parents[2]
_MIGRATION_PATH = (
    _BACKEND_DIR / "alembic/versions/0085_lifecycle_management.py"
)


def _index(table, name: str):
    return next(index for index in table.indexes if index.name == name)


def _valid_draft() -> dict:
    return {
        "enabled": True,
        "retention_days": 365,
        "schedule_cadence": "daily",
        "schedule_hour_utc": 2,
        "schedule_weekday": None,
        "max_records_per_run": 10_000,
        "options": {"protect_starred": True},
    }


def test_lifecycle_models_define_fixed_targets_and_durable_run_fields():
    expected_targets = {target.key for target in TARGETS}
    assert set(LIFECYCLE_TARGET_KEYS) == expected_targets
    assert set(get_args(LifecycleTargetKey)) == expected_targets
    assert len(set(LIFECYCLE_TARGET_KEYS)) == len(LIFECYCLE_TARGET_KEYS)
    assert {
        "revision",
        "next_run_at",
        "schedule_cadence",
        "options_json",
    } <= set(LifecyclePolicy.__table__.columns.keys())
    assert {
        "request_fingerprint",
        "expires_at",
        "eligible_bytes",
    } <= set(LifecyclePreview.__table__.columns.keys())
    assert {
        "scheduled_for",
        "lease_token",
        "lease_expires_at",
        "heartbeat_at",
        "cancel_requested",
        "affected_bytes",
    } <= set(LifecycleRun.__table__.columns.keys())
    assert {"content_purged_at", "content_purge_run_id"} <= set(
        Article.__table__.columns.keys()
    )


@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_lifecycle_partial_indexes_compile_for_supported_dialects(dialect):
    active_sql = str(
        CreateIndex(
            _index(LifecycleRun.__table__, "uq_lifecycle_runs_active_target")
        ).compile(dialect=dialect)
    ).lower()
    article_sql = str(
        CreateIndex(
            _index(Article.__table__, "ix_articles_retention_candidates")
        ).compile(dialect=dialect)
    ).lower()

    assert "unique index" in active_sql
    assert "status in ('queued', 'running')" in active_sql
    assert "content_purged_at is null" in article_sql
    for payload_column in ("text", "title_extracted", "language", "word_count"):
        assert f"{payload_column} is not null" in article_sql


def test_lifecycle_policy_schema_validates_schedule_and_option_allowlist():
    assert LifecyclePolicyDraft.model_validate(_valid_draft()).retention_days == 365

    weekly_without_day = {**_valid_draft(), "schedule_cadence": "weekly"}
    with pytest.raises(ValidationError, match="schedule_weekday is required"):
        LifecyclePolicyDraft.model_validate(weekly_without_day)

    unknown_option = {**_valid_draft(), "options": {"delete_everything": True}}
    with pytest.raises(ValidationError):
        LifecyclePolicyDraft.model_validate(unknown_option)


def test_lifecycle_destructive_requests_are_explicit_and_normalized():
    update = LifecyclePolicyUpdateRequest.model_validate(
        {
            **_valid_draft(),
            "expected_revision": 3,
            "confirmation": "PURGE",
            "reason": "  Reduce retained sensitive data  ",
        }
    )
    assert update.confirmation == "PURGE"
    assert update.reason == "Reduce retained sensitive data"

    preview = LifecyclePreviewRequest.model_validate(
        {
            "target_key": "article_content",
            "expected_revision": 3,
            "draft": _valid_draft(),
        }
    )
    assert preview.draft.options == {"protect_starred": True}


def test_lifecycle_migration_is_self_contained_and_preserves_runtime_defaults():
    source = _MIGRATION_PATH.read_text(encoding="utf-8")

    assert 'down_revision = "0084_ioc_candidate_search"' in source
    assert "from app." not in source
    assert "INSERT INTO lifecycle_policies" not in source

    spec = importlib.util.spec_from_file_location(
        "test_migration_0085_lifecycle_management",
        _MIGRATION_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == "0085_lifecycle_management"
