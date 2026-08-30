from app.schemas.iam import EffectiveAccessResponse, EffectiveRoleResponse
from app.services.authorization import AuthorizationContext


def effective_access_response(
    context: AuthorizationContext,
) -> EffectiveAccessResponse:
    return EffectiveAccessResponse(
        principal_type=context.principal_type,
        principal_id=context.principal_id,
        legacy_role=context.legacy_role,
        account_eligible=context.account_eligible,
        credential_limited=context.credential_limited,
        roles=[
            EffectiveRoleResponse(
                id=role.id,
                key=role.key,
                name=role.name,
                source=role.source,
            )
            for role in context.roles
        ],
        groups=list(context.groups),
        permissions=sorted(context.permissions),
        policy_revision=context.policy_revision,
    )


__all__ = ["effective_access_response"]
