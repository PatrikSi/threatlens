from __future__ import annotations

from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.oidc import OIDCProvider
from app.schemas.oidc import OIDCProviderResponse, OIDCRoleMapping
from app.services.url_utils import is_fetchable_url

OIDC_PROVIDER_SYSTEM_KEY = "primary"
DEFAULT_OIDC_SCOPES = ["openid", "profile", "email"]


class OIDCConfigurationError(ValueError):
    pass


def load_primary_oidc_provider(db: Session) -> OIDCProvider | None:
    return db.scalar(select(OIDCProvider).where(OIDCProvider.system_key == OIDC_PROVIDER_SYSTEM_KEY))


def validate_oidc_provider_urls(*, issuer_url: str, public_base_url: str) -> None:
    if issuer_url:
        _validate_outbound_oidc_url(issuer_url, field_name="Issuer URL")
        issuer_parts = urlsplit(issuer_url)
        if issuer_parts.query or issuer_parts.fragment:
            raise OIDCConfigurationError("Issuer URL must not include a query string or fragment")
    if public_base_url:
        _validate_public_base_url(public_base_url)


def validate_oidc_endpoint_url(url: str, *, field_name: str) -> None:
    _validate_outbound_oidc_url(url, field_name=field_name)


def oidc_callback_url(public_base_url: str) -> str:
    if not public_base_url:
        return ""
    callback_path = get_settings().oidc_callback_path
    return f"{public_base_url.rstrip('/')}{callback_path}"


def provider_response(provider: OIDCProvider | None) -> OIDCProviderResponse:
    if provider is None:
        return OIDCProviderResponse(
            configured=False,
            config_revision=0,
            name="Company SSO",
            enabled=False,
            issuer_url="",
            client_id="",
            has_client_secret=False,
            client_auth_method="client_secret_basic",
            public_base_url="",
            callback_url="",
            callback_path=get_settings().oidc_callback_path,
            scopes=list(DEFAULT_OIDC_SCOPES),
            role_claim="groups",
            role_mappings=[],
            default_role="viewer",
            jit_provisioning_enabled=False,
            auto_approve_users=False,
            require_verified_email=True,
            sync_roles_on_login=True,
        )

    return OIDCProviderResponse(
        id=provider.id,
        configured=True,
        config_revision=provider.config_revision,
        name=provider.name,
        enabled=provider.enabled,
        issuer_url=provider.issuer_url,
        client_id=provider.client_id,
        has_client_secret=bool(provider.client_secret_encrypted),
        client_auth_method=provider.client_auth_method,
        public_base_url=provider.public_base_url,
        callback_url=oidc_callback_url(provider.public_base_url),
        callback_path=get_settings().oidc_callback_path,
        scopes=list(provider.scopes or DEFAULT_OIDC_SCOPES),
        role_claim=provider.role_claim,
        role_mappings=[OIDCRoleMapping.model_validate(mapping) for mapping in (provider.role_mappings_json or [])],
        default_role=provider.default_role,
        jit_provisioning_enabled=provider.jit_provisioning_enabled,
        auto_approve_users=provider.auto_approve_users,
        require_verified_email=provider.require_verified_email,
        sync_roles_on_login=provider.sync_roles_on_login,
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


def _validate_outbound_oidc_url(url: str, *, field_name: str) -> None:
    settings = get_settings()
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise OIDCConfigurationError(f"{field_name} is not a valid URL") from exc

    if not parts.hostname:
        raise OIDCConfigurationError(f"{field_name} is not a valid URL")
    if parts.username or parts.password:
        raise OIDCConfigurationError(f"{field_name} must not contain embedded credentials")
    _ = port
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise OIDCConfigurationError(f"{field_name} must use HTTP or HTTPS")
    if scheme == "http" and not settings.allow_insecure_http_oidc and not settings.allow_private_network_oidc:
        raise OIDCConfigurationError(
            f"{field_name} must use HTTPS unless ALLOW_INSECURE_HTTP_OIDC is enabled"
        )

    publicly_fetchable = is_fetchable_url(url, allow_private_network=False)
    legacy_private_http_allowed = settings.allow_private_network_oidc and not publicly_fetchable
    if scheme == "http" and not settings.allow_insecure_http_oidc and not legacy_private_http_allowed:
        raise OIDCConfigurationError(
            f"{field_name} must use HTTPS unless ALLOW_INSECURE_HTTP_OIDC is enabled"
        )
    if not is_fetchable_url(url, allow_private_network=settings.allow_private_network_oidc):
        if not publicly_fetchable:
            raise OIDCConfigurationError(
                f"{field_name} targets a private or internal host; enable ALLOW_PRIVATE_NETWORK_OIDC only if it is trusted"
            )
        raise OIDCConfigurationError(f"{field_name} is not allowed for outbound OIDC requests")


def _validate_public_base_url(url: str) -> None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise OIDCConfigurationError("Public base URL is not a valid URL") from exc
    if not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise OIDCConfigurationError("Public base URL must be an origin without credentials, query, or fragment")
    if parts.path not in {"", "/"}:
        raise OIDCConfigurationError("Public base URL must not include a path")
    _ = port
    settings = get_settings()
    if parts.scheme.lower() == "https":
        return
    if parts.scheme.lower() == "http" and settings.allow_insecure_http_oidc:
        return
    if settings.allow_private_network_oidc and parts.scheme.lower() == "http" and not is_fetchable_url(
        url,
        allow_private_network=False,
    ):
        return
    raise OIDCConfigurationError(
        "Public base URL must use HTTPS unless ALLOW_INSECURE_HTTP_OIDC is enabled"
    )
