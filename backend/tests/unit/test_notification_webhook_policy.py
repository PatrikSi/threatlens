from types import SimpleNamespace

import pytest

from app.core.config import Settings
from app.services.notification_webhook_policy import (
    notification_host_matches_allowlist,
    notification_origin_matches_allowlist,
    notification_target_origin,
    notification_target_host,
    notification_target_matches_allowlist,
    validate_notification_target_for_actor,
)


def test_notification_target_host_normalizes_case_and_trailing_dot():
    assert notification_target_host("https://Hooks.Example.com./notify") == "hooks.example.com"


def test_notification_target_origin_normalizes_case_trailing_dot_and_default_port():
    assert notification_target_origin("https://Hooks.Example.com./notify") == "https://hooks.example.com"
    assert notification_target_origin("https://hooks.example.com:8443/notify") == "https://hooks.example.com:8443"


def test_notification_host_matches_allowlist_supports_exact_and_subdomain_only_wildcard_hosts():
    assert notification_host_matches_allowlist("hooks.example.com", "hooks.example.com") is True
    assert notification_host_matches_allowlist("hooks.example.com", "*.example.com") is True
    assert notification_host_matches_allowlist("deep.ops.example.com", "*.example.com") is True
    assert notification_host_matches_allowlist("example.com", "*.example.com") is False
    assert notification_host_matches_allowlist("example.net", "*.example.com") is False


def test_notification_origin_matches_allowlist_requires_matching_scheme_and_port():
    assert notification_origin_matches_allowlist("https://hooks.example.com/notify", "hooks.example.com") is True
    assert notification_origin_matches_allowlist("https://hooks.example.com:443/notify", "hooks.example.com") is True
    assert notification_origin_matches_allowlist("https://hooks.example.com:8443/notify", "hooks.example.com") is False
    assert notification_origin_matches_allowlist("https://hooks.example.com:8443/notify", "https://hooks.example.com:8443") is True
    assert notification_origin_matches_allowlist("http://hooks.example.com/notify", "hooks.example.com") is False


def test_notification_target_matches_allowlist_supports_path_prefixes_and_segment_boundaries():
    allow_entry = "https://hooks.example.com/services/tenant-a"

    assert notification_target_matches_allowlist(
        "https://hooks.example.com/services/tenant-a/notify",
        allow_entry,
    ) is True
    assert notification_target_matches_allowlist(
        "https://hooks.example.com/services/tenant-a",
        allow_entry,
    ) is True
    assert notification_target_matches_allowlist(
        "https://hooks.example.com/services/tenant-a/../tenant-b/notify",
        allow_entry,
    ) is False
    assert notification_target_matches_allowlist(
        "https://hooks.example.com/services/tenant-ab/notify",
        allow_entry,
    ) is False


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

    validate_notification_target_for_actor(
        "https://hooks.example.com/services/tenant-a/notify",
        actor_user=SimpleNamespace(role="analyst"),
        allowed_hosts=("https://hooks.example.com/services/tenant-a",),
    )


def test_validate_notification_target_for_actor_rejects_apex_and_non_default_ports():
    analyst = SimpleNamespace(role="analyst")

    with pytest.raises(
        ValueError,
        match="Webhook destination 'https://example.com/notify' is not approved for analyst-managed webhook deliveries",
    ):
        validate_notification_target_for_actor(
            "https://example.com/notify",
            actor_user=analyst,
            allowed_hosts=("*.example.com",),
        )

    with pytest.raises(
        ValueError,
        match="Webhook destination 'https://hooks.example.com:8443/notify' is not approved for analyst-managed webhook deliveries",
    ):
        validate_notification_target_for_actor(
            "https://hooks.example.com:8443/notify",
            actor_user=analyst,
            allowed_hosts=("hooks.example.com",),
        )


def test_validate_notification_target_for_actor_rejects_urls_outside_approved_prefix():
    analyst = SimpleNamespace(role="analyst")

    with pytest.raises(
        ValueError,
        match="Webhook destination 'https://hooks.example.com/services/tenant-b/notify' is not approved for analyst-managed webhook deliveries",
    ):
        validate_notification_target_for_actor(
            "https://hooks.example.com/services/tenant-b/notify",
            actor_user=analyst,
            allowed_hosts=("https://hooks.example.com/services/tenant-a",),
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


def test_settings_notification_webhook_allowlist_accepts_legacy_hosts_and_url_prefixes():
    settings = Settings(
        _env_file=None,
        notification_webhook_allowed_hosts=[
            "Hooks.Example.com.",
            "https://Hooks.Example.com/Services/Tenant-A/../Tenant-A/",
        ],
    )

    assert settings.notification_webhook_allowed_hosts == [
        "hooks.example.com",
        "https://hooks.example.com/Services/Tenant-A",
    ]
