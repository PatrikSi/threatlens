from app.core.permissions import (
    ALL_PERMISSION_IDS,
    RESERVED_CUSTOM_ROLE_PERMISSION_IDS,
    expand_permission_grants,
    is_known_permission,
)
from app.core.rbac import ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER
from app.core.token_scopes import (
    ALLOWED_API_TOKEN_SCOPES,
    get_role_api_token_scope_grants,
)


def test_permission_catalog_is_available_to_scoped_api_tokens():
    assert ALL_PERMISSION_IDS <= ALLOWED_API_TOKEN_SCOPES


def test_permission_expansion_preserves_legacy_role_envelopes():
    for role in (ROLE_ADMIN, ROLE_ANALYST, ROLE_VIEWER):
        grants = get_role_api_token_scope_grants(role)
        expanded = expand_permission_grants(grants)
        assert expanded
        assert expanded <= ALL_PERMISSION_IDS

    assert "write:feeds" in expand_permission_grants(
        get_role_api_token_scope_grants(ROLE_ANALYST)
    )
    assert "write:feeds" not in expand_permission_grants(
        get_role_api_token_scope_grants(ROLE_VIEWER)
    )


def test_sealed_administrator_wildcards_are_known_but_reserved():
    assert is_known_permission("admin:*")
    assert is_known_permission("*:* ".strip())
    assert RESERVED_CUSTOM_ROLE_PERMISSION_IDS == {
        "read:*",
        "write:*",
        "admin:*",
        "*:*",
    }
