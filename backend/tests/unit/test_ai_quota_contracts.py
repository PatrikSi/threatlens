import uuid

import pytest
from pydantic import ValidationError

from app.schemas.ai_quota_groups import AIQuotaGroupCreate
from app.schemas.team_hunt_worklist import HuntClaimCommand


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "bad\x00name"},
        {"name": "   "},
        {"provider_keys": ["secret-key"]},
        {"provider_keys": ["legacy", "legacy"]},
        {"provider_keys": ["profile:not-a-uuid"]},
        {"max_concurrent_per_team": -1},
        {"max_concurrent_requests": 1001},
        {"hourly_token_budget": 10**12 + 1},
    ],
)
def test_quota_contract_rejects_unsafe_or_unbounded_values(changes):
    with pytest.raises(ValidationError):
        AIQuotaGroupCreate.model_validate({"name": "Account", **changes})


def test_membership_ids_are_canonical_and_sorted():
    identifier = uuid.uuid4()
    value = AIQuotaGroupCreate(
        name="Account", provider_keys=[f"profile:{str(identifier).upper()}", "legacy"]
    )
    assert value.provider_keys == ["legacy", f"profile:{identifier}"]


def test_hunt_claim_requires_both_concurrency_baselines():
    with pytest.raises(ValidationError):
        HuntClaimCommand(action="claim", expected_version=0)
    assert (
        HuntClaimCommand(
            action="claim", expected_version=0, expected_assessment_version=1
        ).expected_version
        == 0
    )
