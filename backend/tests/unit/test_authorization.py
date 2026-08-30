from app.models.iam import IAMPolicyState
from app.services import authorization
import pytest


def test_authorization_retries_when_policy_revision_changes_during_read(
    db_session, seed_users, monkeypatch
):
    original = authorization._authorization_snapshot_for_user
    calls = 0

    def changing_snapshot(*args, **kwargs):
        nonlocal calls
        calls += 1
        snapshot = original(*args, **kwargs)
        if calls == 1:
            state = db_session.get(IAMPolicyState, 1)
            assert state is not None
            state.revision += 1
            db_session.add(state)
            db_session.flush()
        return snapshot

    monkeypatch.setattr(
        authorization, "_authorization_snapshot_for_user", changing_snapshot
    )

    context = authorization.authorization_context_for_user(
        db_session, seed_users["viewer"]
    )

    assert calls == 2
    assert context.policy_revision == db_session.get(IAMPolicyState, 1).revision


def test_authorization_fails_closed_when_policy_state_is_missing(
    db_session, seed_users
):
    state = db_session.get(IAMPolicyState, 1)
    assert state is not None
    db_session.delete(state)
    db_session.flush()

    try:
        authorization.authorization_context_for_user(db_session, seed_users["viewer"])
    except authorization.AuthorizationStateUnavailable as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("missing IAM policy state must fail closed")


def test_authorization_fence_rejects_a_stale_snapshot(db_session, seed_users):
    context = authorization.authorization_context_for_user(
        db_session,
        seed_users["viewer"],
    )
    state = db_session.get(IAMPolicyState, 1)
    assert state is not None
    state.revision += 1
    db_session.flush()

    with pytest.raises(authorization.AuthorizationStateUnavailable, match="changed"):
        authorization.fence_authorization_context(db_session, context)
