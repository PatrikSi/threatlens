from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER
from app.core.token_scopes import (
    has_required_scope,
    missing_delegable_scopes,
    missing_role_token_scopes,
    normalize_token_scopes,
)


def test_normalize_token_scopes_deduplicates_and_sorts():
    scopes = normalize_token_scopes([" read:feeds ", "READ:FEEDS", "write:items", ""])
    assert scopes == ["read:feeds", "write:items"]


def test_has_required_scope_supports_wildcards():
    granted = {"read:*"}
    assert has_required_scope(granted, "read:feeds")
    assert not has_required_scope(granted, "write:feeds")


def test_has_required_scope_write_implies_read():
    granted = {"write:feeds"}
    assert has_required_scope(granted, "read:feeds")
    assert has_required_scope(granted, "write:feeds")


def test_has_required_scope_write_all_implies_read_all():
    granted = {"write:*"}
    assert has_required_scope(granted, "read:feeds")
    assert has_required_scope(granted, "read:*")


def test_has_required_scope_admin_matches_all():
    granted = {"admin:*"}
    assert has_required_scope(granted, "read:feeds")
    assert has_required_scope(granted, "write:users")


def test_has_required_scope_empty_grants_nothing():
    assert not has_required_scope(set(), "read:feeds")


def test_missing_delegable_scopes_filters_out_scopes_not_granted_to_parent():
    granted = ["write:tokens", "read:*"]
    requested = ["read:feeds", "write:notifications", "write:tokens"]
    assert missing_delegable_scopes(granted, requested) == ["write:notifications"]


def test_missing_role_token_scopes_allows_viewer_scopes_granted_via_role_envelope():
    requested = ["read:alerts", "write:views", "read:tokens"]
    assert missing_role_token_scopes(ROLE_VIEWER, requested) == []


def test_missing_role_token_scopes_rejects_scopes_outside_role_envelope():
    assert missing_role_token_scopes(ROLE_VIEWER, ["write:feeds"]) == ["write:feeds"]
    assert missing_role_token_scopes(ROLE_ANALYST, ["read:users"]) == ["read:users"]


def test_missing_role_token_scopes_allows_admin_wildcard_scopes():
    requested = ["admin:*", "*:*", "write:users"]
    assert missing_role_token_scopes(ROLE_ADMIN, requested) == []
