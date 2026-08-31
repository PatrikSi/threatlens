from __future__ import annotations

from dataclasses import replace

import pytest

from app.services import action_registry
from app.services.action_registry import (
    ACTION_DEFINITIONS,
    RegisteredActionDataPolicy,
)


def test_every_registered_action_has_a_versioned_target_data_policy() -> None:
    declarations = {
        definition.key: definition.target_data_policy
        for definition in ACTION_DEFINITIONS
    }

    assert declarations == {
        "ai.provider_attempt.confirm_not_sent": RegisteredActionDataPolicy(
            version=1,
            target_kind="ai_task_run",
        ),
        "ai.provider_attempt.acknowledge_may_have_sent": (
            RegisteredActionDataPolicy(version=1, target_kind="ai_task_run")
        ),
        "service_account.disable": RegisteredActionDataPolicy(
            version=1,
            target_kind="system_control_plane",
        ),
        "iam.role.delete": RegisteredActionDataPolicy(
            version=1,
            target_kind="system_control_plane",
        ),
    }


def test_registry_validation_rejects_missing_target_data_policy(monkeypatch) -> None:
    malformed = replace(ACTION_DEFINITIONS[0], target_data_policy=None)  # type: ignore[arg-type]
    monkeypatch.setattr(
        action_registry,
        "ACTION_DEFINITIONS",
        (malformed, *ACTION_DEFINITIONS[1:]),
    )

    with pytest.raises(RuntimeError, match="missing its target data-policy"):
        action_registry.validate_action_registry()


def test_registry_validation_rejects_incompatible_target_policy(monkeypatch) -> None:
    malformed = replace(
        ACTION_DEFINITIONS[0],
        target_data_policy=RegisteredActionDataPolicy(
            version=1,
            target_kind="system_control_plane",
        ),
    )
    monkeypatch.setattr(
        action_registry,
        "ACTION_DEFINITIONS",
        (malformed, *ACTION_DEFINITIONS[1:]),
    )

    with pytest.raises(RuntimeError, match="incompatible target data-policy"):
        action_registry.validate_action_registry()
