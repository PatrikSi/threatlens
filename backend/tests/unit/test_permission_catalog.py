from app.core.permissions import PERMISSION_BY_ID
from app.core.token_scopes import (
    SCOPE_READ_USERS,
    SCOPE_WRITE_FEEDS,
    SCOPE_WRITE_OPERATIONS,
)


def test_permission_catalog_describes_partial_and_reserved_access_boundaries():
    expected_copy = {
        SCOPE_WRITE_FEEDS: (
            "Manage feeds",
            "Create, update, import, and refresh feeds.",
        ),
        SCOPE_READ_USERS: (
            "View users",
            "View the user directory. Identity-provider configuration also requires "
            "the Administrator base role.",
        ),
        SCOPE_WRITE_OPERATIONS: (
            "Manage operations",
            "Manage data lifecycle policies and initiate or cancel maintenance runs.",
        ),
    }

    for permission_id, (label, description) in expected_copy.items():
        permission = PERMISSION_BY_ID[permission_id]
        assert permission.label == label
        assert permission.description == description
        assert permission.delegable is True
