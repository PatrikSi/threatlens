"""OAuth discovery and complete least-privilege challenges for MCP responses."""

from app.core.api_errors import ApiHTTPException
from app.services.mcp_oauth import oauth_urls


def bearer_challenge(
    *, required_scopes: tuple[str, ...] = (), insufficient_scope: bool = False
) -> str:
    # Callers supply only the static tool permission catalogue, never client text.
    scopes = sorted({"read:mcp", "read:items", *required_scopes})
    fields = []
    if insufficient_scope:
        fields.append('error="insufficient_scope"')
    try:
        issuer, _ = oauth_urls()
    except ApiHTTPException:
        pass
    else:
        fields.append(
            f'resource_metadata="{issuer}/.well-known/oauth-protected-resource/api/v1/mcp"'
        )
    fields.append(f'scope="{" ".join(scopes)}"')
    return "Bearer " + ", ".join(fields)
