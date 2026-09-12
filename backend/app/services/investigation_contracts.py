"""Stable domain errors and object-role constants for investigation services."""

WRITE_MEMBER_ROLES = frozenset({"owner", "editor"})
OWNER_MEMBER_ROLE = "owner"


class InvestigationNotFoundError(LookupError):
    code = "investigation_not_found"

    def __init__(
        self, detail: str = "Investigation not found.", *, code: str | None = None
    ) -> None:
        super().__init__(detail)
        self.code = code or self.code


class InvestigationPermissionError(PermissionError):
    pass


class InvestigationActorNotEligibleError(InvestigationPermissionError):
    code = "investigation_actor_not_eligible"


class InvestigationReadAuthorizationChangedError(InvestigationPermissionError):
    code = "investigation_read_authorization_changed"


class InvestigationConflictError(RuntimeError):
    code = "investigation_conflict"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.code = code or self.code


class InvestigationValidationError(ValueError):
    pass
