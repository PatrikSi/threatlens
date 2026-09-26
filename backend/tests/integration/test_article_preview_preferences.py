"""Preview privacy is personal, revisioned, and independent of navigation policy."""

from sqlalchemy import select

from app.models.workspace import WorkspaceRolePolicy, WorkspaceUserPreference
from tests.integration.test_workspace_policy_api import (
    workspace_client as workspace_client,
)


def test_preview_consent_defaults_off_and_is_personal(
    workspace_client, seed_users, auth_headers
):
    path = "/v1/workspace/preferences"
    for role in ("viewer", "analyst"):
        response = workspace_client.get(path, headers=auth_headers[role])
        assert response.status_code == 200
        assert response.json()["article_preview_external_resources"] is False

    enabled = workspace_client.put(
        path,
        headers=auth_headers["viewer"],
        json={
            "expected_revision": 0,
            "article_preview_external_resources": True,
        },
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["article_preview_external_resources"] is True
    reloaded = workspace_client.get(path, headers=auth_headers["viewer"])
    assert reloaded.json()["article_preview_external_resources"] is True
    assert (
        workspace_client.get(path, headers=auth_headers["analyst"]).json()[
            "article_preview_external_resources"
        ]
        is False
    )
    disabled = workspace_client.put(
        path,
        headers=auth_headers["viewer"],
        json={
            "expected_revision": enabled.json()["revision"],
            "article_preview_external_resources": False,
        },
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["article_preview_external_resources"] is False


def test_privacy_only_write_preserves_navigation_after_layout_policy_changes(
    workspace_client,
    db_session,
    seed_users,
    auth_headers,
):
    row = WorkspaceUserPreference(
        user_id=seed_users["viewer"].id,
        modules_json={
            "primary.iocs": {"visible": False},
            "future.module": {"visible": True},
        },
        landing_module_id="primary.dashboard",
        dashboard_panel_ids_json=["rss", "future-panel"],
        revision=3,
    )
    db_session.add(row)
    policy = db_session.scalar(
        select(WorkspaceRolePolicy).where(WorkspaceRolePolicy.role == "viewer")
    )
    policy.modules_json = {
        key: {**value, "optional": False} for key, value in policy.modules_json.items()
    }
    db_session.commit()
    result = workspace_client.put(
        "/v1/workspace/preferences",
        headers=auth_headers["viewer"],
        json={
            "expected_revision": 3,
            "article_preview_external_resources": True,
        },
    )
    assert result.status_code == 200, result.text
    db_session.refresh(row)
    assert row.modules_json == {
        "primary.iocs": {"visible": False},
        "future.module": {"visible": True},
    }
    assert row.landing_module_id == "primary.dashboard"
    assert row.dashboard_panel_ids_json == ["rss", "future-panel"]
    assert row.article_preview_external_resources is True


def test_navigation_writes_and_resets_preserve_consent_and_stale_writes_conflict(
    workspace_client,
    seed_users,
    auth_headers,
):
    path = "/v1/workspace/preferences"
    headers = auth_headers["analyst"]
    assert (
        workspace_client.put(
            path,
            headers=headers,
            json={
                "expected_revision": 0,
                "article_preview_external_resources": True,
            },
        ).status_code
        == 200
    )
    stale = workspace_client.put(
        path,
        headers=headers,
        json={
            "expected_revision": 0,
            "article_preview_external_resources": False,
        },
    )
    assert stale.status_code == 409
    navigation = workspace_client.put(
        path,
        headers=headers,
        json={
            "expected_revision": 1,
            "modules": [],
            "dashboard_panel_ids": ["rss"],
        },
    )
    assert navigation.status_code == 200, navigation.text
    assert navigation.json()["article_preview_external_resources"] is True
    reset = workspace_client.post(
        path + "/reset", headers=headers, json={"expected_revision": 2}
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["article_preview_external_resources"] is True
    assert reset.json()["dashboard_panel_ids"] is None
    assert reset.json()["revision"] == 3
