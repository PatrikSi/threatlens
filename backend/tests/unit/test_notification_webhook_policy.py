from types import SimpleNamespace

import pytest

from app.services.notification_webhook_policy import (
    notification_host_matches_allowlist,
    notification_target_host,
    validate_notification_target_for_actor,
)


def test_notification_target_host_normalizes_case_and_trailing_dot():
    assert notification_target_host("https://Hooks.Example.com./notify") == "hooks.example.com"


def test_notification_host_matches_allowlist_supports_exact_and_wildcard_hosts():
    assert notification_host_matches_allowlist("hooks.example.com", "hooks.example.com") is True
    assert notification_host_matches_allowlist("hooks.example.com", "*.example.com") is True
    assert notification_host_matches_allowlist("deep.ops.example.com", "*.example.com") is True
    assert notification_host_matches_allowlist("example.net", "*.example.com") is False


def test_validate_notification_target_for_actor_blocks_analysts_without_allowlist():
    analyst = SimpleNamespace(role="analyst")

    with pytest.raises(ValueError, match="disabled until NOTIFICATION_WEBHOOK_ALLOWED_HOSTS is configured"):
        validate_notification_target_for_actor(
            "https://hooks.example.com/notify",
            actor_user=analyst,
            allowed_hosts=(),
        )


def test_validate_notification_target_for_actor_allows_admin_and_approved_analyst_hosts():
    validate_notification_target_for_actor(
        "https://hooks.example.com/notify",
        actor_user=SimpleNamespace(role="admin"),
        allowed_hosts=(),
    )

    validate_notification_target_for_actor(
        "https://hooks.example.com/notify",
        actor_user=SimpleNamespace(role="analyst"),
        allowed_hosts=("*.example.com",),
    )


def test_validate_notification_target_for_actor_fails_closed_for_missing_or_inactive_owner():
    with pytest.raises(ValueError, match="owner is no longer active and approved"):
        validate_notification_target_for_actor(
            "https://hooks.example.com/notify",
            actor_user=None,
            allowed_hosts=("*.example.com",),
        )

    with pytest.raises(ValueError, match="owner is no longer active and approved"):
        validate_notification_target_for_actor(
            "https://hooks.example.com/notify",
            actor_user=SimpleNamespace(role="analyst", is_active=False, is_approved=True),
            allowed_hosts=("*.example.com",),
        )
