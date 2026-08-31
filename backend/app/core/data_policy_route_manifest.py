from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeAlias


CANONICAL_API_PREFIX: Final = "/v1"
ROUTE_GOVERNANCE_MANIFEST_VERSION: Final = 1


class RouteGovernanceClass(StrEnum):
    PUBLIC = "public"
    CONTROL_PLANE = "control_plane"
    REQUEST_CONTEXT = "request_context"
    CAPTURED_ASYNC = "captured_async"
    DYNAMIC_TARGET = "dynamic_target"
    EGRESS_FENCED = "egress_fenced"


@dataclass(frozen=True, order=True, slots=True)
class RouteOperation:
    method: str
    path_format: str
    route_name: str
    endpoint_identity: str
    raw_path: str | None = None

    def display(self) -> str:
        return (
            f"{self.method} {self.path_format} "
            f"({self.route_name}; {self.endpoint_identity})"
        )


@dataclass(frozen=True, slots=True)
class RouteGovernanceEntry:
    operation: RouteOperation
    governance_class: RouteGovernanceClass


@dataclass(frozen=True, slots=True)
class RouteGovernanceManifest:
    version: int
    canonical_prefix: str
    entries: tuple[RouteGovernanceEntry, ...]
    request_context_classes: frozenset[RouteGovernanceClass]


OperationLiteral: TypeAlias = tuple[str, str, str] | tuple[str, str, str, str]


# Every canonical application operation is intentionally listed. Keep these tuples
# literal and review route additions, removals, renames, and policy-class changes as
# security-sensitive contract changes.
# fmt: off
_CONTROL_PLANE_OPERATIONS: tuple[OperationLiteral, ...] = (
    ('GET', '/v1/ai/settings', 'get_ai_settings_route'),
    ('PUT', '/v1/ai/settings', 'update_ai_settings_route'),
    ('GET', '/v1/alerts', 'list_alert_interests'),
    ('POST', '/v1/alerts', 'create_alert_interest'),
    ('DELETE', '/v1/alerts/{alert_id}', 'delete_alert_interest'),
    ('PATCH', '/v1/alerts/{alert_id}', 'update_alert_interest'),
    ('POST', '/v1/auth/change-password', 'change_password'),
    ('GET', '/v1/auth/me', 'me'),
    ('DELETE', '/v1/auth/oidc/access-policy', 'remove_oidc_access_policy'),
    ('GET', '/v1/auth/oidc/access-policy', 'get_oidc_access_policy'),
    ('POST', '/v1/auth/oidc/access-policy', 'post_oidc_access_policy'),
    ('PUT', '/v1/auth/oidc/access-policy', 'put_oidc_access_policy'),
    ('POST', '/v1/auth/oidc/access-policy/mapping-sets', 'post_oidc_mapping_set'),
    ('DELETE', '/v1/auth/oidc/access-policy/mapping-sets/{mapping_set_id}', 'remove_oidc_mapping_set'),
    ('PUT', '/v1/auth/oidc/access-policy/mapping-sets/{mapping_set_id}', 'put_oidc_mapping_set'),
    ('DELETE', '/v1/auth/oidc/account', 'unlink_oidc_account'),
    ('GET', '/v1/auth/oidc/account', 'oidc_account_status'),
    ('POST', '/v1/auth/oidc/link', 'start_oidc_link'),
    ('GET', '/v1/auth/oidc/provider', 'get_oidc_provider'),
    ('PUT', '/v1/auth/oidc/provider', 'update_oidc_provider'),
    ('POST', '/v1/auth/oidc/provider/test', 'test_configured_oidc_provider'),
    ('POST', '/v1/auth/oidc/reauth', 'start_oidc_reauthentication'),
    ('DELETE', '/v1/auth/security/mfa', 'remove_totp'),
    ('GET', '/v1/auth/security/mfa', 'get_mfa_status'),
    ('POST', '/v1/auth/security/mfa/confirm', 'confirm_totp'),
    ('POST', '/v1/auth/security/mfa/enroll', 'enroll_totp'),
    ('DELETE', '/v1/auth/security/mfa/enrollment', 'cancel_totp_enrollment'),
    ('POST', '/v1/auth/security/mfa/recovery-codes', 'replace_recovery_codes'),
    ('POST', '/v1/auth/security/reauthenticate', 'reauthenticate_local_session'),
    ('GET', '/v1/auth/security/sessions', 'list_sessions'),
    ('POST', '/v1/auth/security/sessions/revoke-others', 'revoke_other_sessions'),
    ('DELETE', '/v1/auth/security/sessions/{session_id}', 'revoke_session'),
    ('POST', '/v1/feeds/metadata', 'get_feed_metadata'),
    ('GET', '/v1/health/beat', 'beat'),
    ('GET', '/v1/health/encrypted-data', 'encrypted_data'),
    ('GET', '/v1/health/notifications', 'notifications'),
    ('GET', '/v1/health/worker', 'worker'),
    ('GET', '/v1/iam/access-reviews', 'get_access_review_campaigns'),
    ('POST', '/v1/iam/access-reviews', 'post_access_review_campaign'),
    ('GET', '/v1/iam/access-reviews/{campaign_id}', 'get_access_review_campaign_route'),
    ('POST', '/v1/iam/access-reviews/{campaign_id}/apply/complete', 'post_access_review_apply_complete'),
    ('POST', '/v1/iam/access-reviews/{campaign_id}/apply/items/{item_id}', 'post_access_review_apply_item'),
    ('POST', '/v1/iam/access-reviews/{campaign_id}/apply/items/{item_id}/resolve', 'post_access_review_resolve_item'),
    ('POST', '/v1/iam/access-reviews/{campaign_id}/apply/start', 'post_access_review_apply_start'),
    ('POST', '/v1/iam/access-reviews/{campaign_id}/cancel', 'post_access_review_cancel'),
    ('POST', '/v1/iam/access-reviews/{campaign_id}/close', 'post_access_review_close'),
    ('POST', '/v1/iam/access-reviews/{campaign_id}/decisions', 'post_access_review_decisions'),
    ('GET', '/v1/iam/access-reviews/{campaign_id}/items', 'get_access_review_items'),
    ('GET', '/v1/iam/action-approvals/actions', 'get_action_catalog'),
    ('GET', '/v1/iam/data-policies', 'get_data_policy_overview'),
    ('PUT', '/v1/iam/data-policies/feeds/{feed_id}', 'put_feed_handling_label'),
    ('POST', '/v1/iam/data-policies/labels', 'post_handling_label'),
    ('PATCH', '/v1/iam/data-policies/labels/{label_id}', 'patch_handling_label'),
    ('PUT', '/v1/iam/data-policies/labels/{label_id}/role-grants', 'put_handling_label_role_grants'),
    ('PUT', '/v1/iam/data-policies/labels/{label_id}/status', 'put_handling_label_status'),
    ('PUT', '/v1/iam/data-policies/mode', 'put_data_policy_mode'),
    ('GET', '/v1/iam/data-policies/preflight', 'get_data_policy_preflight'),
    ('GET', '/v1/iam/effective', 'get_my_effective_access'),
    ('GET', '/v1/iam/effective/explain', 'explain_my_access'),
    ('GET', '/v1/iam/elevations', 'get_elevations'),
    ('POST', '/v1/iam/elevations', 'post_elevation'),
    ('GET', '/v1/iam/elevations/{elevation_id}', 'get_elevation'),
    ('POST', '/v1/iam/elevations/{elevation_id}/close', 'post_elevation_close'),
    ('POST', '/v1/iam/elevations/{elevation_id}/decision', 'post_elevation_decision'),
    ('GET', '/v1/iam/groups', 'get_groups'),
    ('POST', '/v1/iam/groups', 'post_group'),
    ('DELETE', '/v1/iam/groups/{group_id}', 'remove_group'),
    ('PATCH', '/v1/iam/groups/{group_id}', 'patch_group'),
    ('GET', '/v1/iam/groups/{group_id}/members', 'get_group_members'),
    ('POST', '/v1/iam/groups/{group_id}/members', 'post_group_member'),
    ('DELETE', '/v1/iam/groups/{group_id}/members/{membership_id}', 'delete_group_member'),
    ('GET', '/v1/iam/groups/{group_id}/role-assignments', 'get_group_roles'),
    ('POST', '/v1/iam/groups/{group_id}/role-assignments', 'post_group_role'),
    ('DELETE', '/v1/iam/groups/{group_id}/role-assignments/{assignment_id}', 'delete_group_role'),
    ('GET', '/v1/iam/permissions', 'get_permissions'),
    ('GET', '/v1/iam/roles', 'get_roles'),
    ('POST', '/v1/iam/roles', 'post_role'),
    ('DELETE', '/v1/iam/roles/{role_id}', 'remove_role'),
    ('GET', '/v1/iam/roles/{role_id}', 'get_role'),
    ('PATCH', '/v1/iam/roles/{role_id}', 'patch_role'),
    ('GET', '/v1/iam/service-accounts', 'get_service_accounts'),
    ('POST', '/v1/iam/service-accounts', 'post_service_account'),
    ('DELETE', '/v1/iam/service-accounts/{service_account_id}', 'delete_disabled_service_account'),
    ('GET', '/v1/iam/service-accounts/{service_account_id}', 'get_service_account'),
    ('PATCH', '/v1/iam/service-accounts/{service_account_id}', 'patch_service_account'),
    ('GET', '/v1/iam/service-accounts/{service_account_id}/credentials', 'get_credentials'),
    ('POST', '/v1/iam/service-accounts/{service_account_id}/credentials', 'post_credential'),
    ('POST', '/v1/iam/service-accounts/{service_account_id}/credentials/{credential_id}/revoke', 'post_revoke_credential'),
    ('POST', '/v1/iam/service-accounts/{service_account_id}/credentials/{credential_id}/rotate', 'post_rotate_credential'),
    ('POST', '/v1/iam/service-accounts/{service_account_id}/disable', 'post_disable_service_account'),
    ('GET', '/v1/iam/service-accounts/{service_account_id}/role-assignments', 'get_role_assignments'),
    ('POST', '/v1/iam/service-accounts/{service_account_id}/role-assignments', 'post_role_assignment'),
    ('DELETE', '/v1/iam/service-accounts/{service_account_id}/role-assignments/{assignment_id}', 'delete_role_assignment'),
    ('GET', '/v1/iam/users/{user_id}/effective', 'get_user_effective_access'),
    ('GET', '/v1/iam/users/{user_id}/role-assignments', 'get_user_roles'),
    ('POST', '/v1/iam/users/{user_id}/role-assignments', 'post_user_role'),
    ('DELETE', '/v1/iam/users/{user_id}/role-assignments/{assignment_id}', 'delete_user_role'),
    ('GET', '/v1/integrations', 'list_integrations'),
    ('GET', '/v1/integrations/connectors', 'get_integration_connectors'),
    ('DELETE', '/v1/integrations/smtp/hooks/{hook_id}', 'delete_smtp_hook_route'),
    ('GET', '/v1/integrations/smtp/hooks/{hook_id}/test-runs', 'get_smtp_hook_test_runs'),
    ('GET', '/v1/integrations/smtp/template-defaults', 'get_smtp_template_defaults'),
    ('GET', '/v1/investigations/member-candidates', 'get_investigation_member_candidates'),
    ('GET', '/v1/notifications/template-variables', 'get_notification_template_variables'),
    ('DELETE', '/v1/notifications/webhooks/{webhook_id}', 'delete_notification_webhook'),
    ('GET', '/v1/operations/diagnostics', 'diagnostics'),
    ('GET', '/v1/operations/overview', 'overview'),
    ('GET', '/v1/operations/runs', 'runs'),
    ('GET', '/v1/reports/schedules', 'list_schedules'),
    ('POST', '/v1/reports/schedules', 'create_schedule'),
    ('DELETE', '/v1/reports/schedules/{schedule_id}', 'remove_schedule'),
    ('PUT', '/v1/reports/schedules/{schedule_id}', 'update_schedule'),
    ('GET', '/v1/reports/templates', 'list_report_templates'),
    ('POST', '/v1/reports/templates', 'create_template'),
    ('DELETE', '/v1/reports/templates/{template_id}', 'remove_template'),
    ('PUT', '/v1/reports/templates/{template_id}', 'update_template'),
    ('POST', '/v1/reports/templates/{template_id}/clone', 'clone_template'),
    ('POST', '/v1/tagging/reapply', 'queue_tagging_reapply'),
    ('PUT', '/v1/tagging/settings', 'update_tagging_settings'),
    ('POST', '/v1/tags', 'create_tag'),
    ('GET', '/v1/tokens', 'list_tokens'),
    ('POST', '/v1/tokens', 'create_token'),
    ('GET', '/v1/tokens/inventory', 'list_token_inventory'),
    ('DELETE', '/v1/tokens/{token_id}', 'revoke_token'),
    ('GET', '/v1/users', 'list_users'),
    ('POST', '/v1/users', 'create_user'),
    ('GET', '/v1/users/directory', 'list_user_directory'),
    ('GET', '/v1/users/{user_id}', 'get_user'),
    ('PATCH', '/v1/users/{user_id}', 'update_user'),
    ('POST', '/v1/users/{user_id}/mfa/reset', 'reset_user_mfa'),
    ('GET', '/v1/views', 'list_views'),
    ('POST', '/v1/views', 'create_view'),
    ('DELETE', '/v1/views/{view_id}', 'delete_view'),
    ('PATCH', '/v1/views/{view_id}', 'update_view'),
    ('GET', '/v1/workspace/effective', 'get_my_effective_workspace'),
    ('GET', '/v1/workspace/modules', 'get_workspace_modules'),
    ('GET', '/v1/workspace/preferences', 'get_my_workspace_preferences'),
    ('PUT', '/v1/workspace/preferences', 'put_my_workspace_preferences'),
    ('POST', '/v1/workspace/preferences/reset', 'reset_my_workspace_preferences'),
    ('GET', '/v1/workspace/role-policies', 'get_workspace_role_policies'),
    ('GET', '/v1/workspace/role-policies/{role}', 'get_workspace_role_policy'),
    ('PUT', '/v1/workspace/role-policies/{role}', 'put_workspace_role_policy'),
    ('POST', '/v1/workspace/role-policies/{role}/reset', 'reset_workspace_role_policy'),
)

_PUBLIC_OPERATIONS: tuple[OperationLiteral, ...] = (
    ('POST', '/v1/auth/login', 'login'),
    ('POST', '/v1/auth/logout', 'logout'),
    ('POST', '/v1/auth/mfa/verify', 'verify_mfa_login'),
    ('GET', '/v1/auth/oidc/callback', 'oidc_callback'),
    ('GET', '/v1/auth/oidc/login', 'start_oidc_login'),
    ('GET', '/v1/auth/oidc/settings', 'public_oidc_settings'),
    ('POST', '/v1/auth/register', 'register'),
    ('GET', '/v1/auth/registration-settings', 'registration_settings'),
    ('GET', '/v1/health', 'health'),
    ('GET', '/v1/health/live', 'live'),
    ('GET', '/v1/health/ready', 'ready'),
)

_REQUEST_CONTEXT_OPERATIONS: tuple[OperationLiteral, ...] = (
    ('GET', '/v1/ai/daily-brief/latest', 'get_latest_daily_brief_route'),
    ('GET', '/v1/ai/daily-briefs', 'list_daily_briefs_route'),
    ('GET', '/v1/ai/daily-briefs/{brief_id}/sources', 'list_daily_brief_sources_route'),
    ('GET', '/v1/ai/ops/live', 'get_ai_ops_live_route'),
    ('GET', '/v1/ai/ops/manual-actions', 'list_ai_ops_manual_actions_route'),
    ('GET', '/v1/ai/ops/overview', 'get_ai_ops_overview_route'),
    ('GET', '/v1/ai/ops/prompt-history', 'list_ai_ops_prompt_history_route'),
    ('GET', '/v1/ai/ops/runs', 'list_ai_ops_runs_route'),
    ('GET', '/v1/ai/ops/runs/{run_id}', 'get_ai_ops_run_detail_route'),
    ('POST', '/v1/ai/ops/runs/{run_id}/cancel', 'cancel_ai_ops_run_route'),
    ('GET', '/v1/ai/usage', 'get_ai_usage_route'),
    ('GET', '/v1/alerts/matches', 'list_alert_matches'),
    ('GET', '/v1/alerts/occurrences', 'get_alert_occurrences'),
    ('POST', '/v1/alerts/occurrences/bulk/acknowledge', 'bulk_acknowledge_alert_occurrences'),
    ('POST', '/v1/alerts/occurrences/bulk/close', 'bulk_close_alert_occurrences'),
    ('GET', '/v1/alerts/occurrences/evaluations', 'get_alert_evaluations'),
    ('GET', '/v1/alerts/occurrences/evaluations/{request_id}', 'get_alert_evaluation_detail'),
    ('GET', '/v1/alerts/occurrences/evaluations/{request_id}/activity', 'get_alert_evaluation_activity'),
    ('POST', '/v1/alerts/occurrences/evaluations/{request_id}/replay', 'replay_alert_evaluation'),
    ('GET', '/v1/alerts/occurrences/metrics', 'get_alert_occurrence_metrics'),
    ('POST', '/v1/alerts/occurrences/reconciliation/apply', 'apply_alert_occurrence_backfill'),
    ('POST', '/v1/alerts/occurrences/reconciliation/preview', 'preview_alert_occurrence_backfill'),
    ('GET', '/v1/alerts/occurrences/{occurrence_id}', 'get_alert_occurrence_detail'),
    ('GET', '/v1/alerts/occurrences/{occurrence_id}/activity', 'get_alert_occurrence_activity'),
    ('PATCH', '/v1/alerts/occurrences/{occurrence_id}/lifecycle', 'patch_alert_occurrence_lifecycle'),
    ('PATCH', '/v1/alerts/occurrences/{occurrence_id}/snooze', 'patch_alert_occurrence_snooze'),
    ('POST', '/v1/alerts/preview', 'preview_alert_interest'),
    ('GET', '/v1/audit-logs', 'list_audit_logs'),
    ('GET', '/v1/audit-logs/export', 'export_audit_logs'),
    ('POST', '/v1/exports', 'download_export'),
    ('GET', '/v1/exports/capabilities', 'get_export_capabilities'),
    ('POST', '/v1/exports/preview', 'preview_export'),
    ('GET', '/v1/feeds', 'list_feeds'),
    ('POST', '/v1/feeds', 'create_feed'),
    ('GET', '/v1/feeds/export', 'export_feeds_sanitized'),
    ('GET', '/v1/feeds/export/backup', 'export_feeds_backup'),
    ('POST', '/v1/feeds/import', 'import_feeds'),
    ('DELETE', '/v1/feeds/{feed_id}', 'delete_feed'),
    ('PATCH', '/v1/feeds/{feed_id}', 'update_feed'),
    ('POST', '/v1/feeds/{feed_id}/refresh', 'refresh_feed'),
    ('POST', '/v1/integrations/deliveries/{delivery_id}/replay', 'replay_integration_delivery'),
    ('GET', '/v1/integrations/smtp/analytics', 'get_smtp_analytics_route'),
    ('GET', '/v1/integrations/smtp/hooks', 'get_smtp_hooks'),
    ('POST', '/v1/integrations/smtp/hooks', 'create_smtp_hook_route'),
    ('POST', '/v1/integrations/smtp/hooks/test', 'test_smtp_hook'),
    ('PATCH', '/v1/integrations/smtp/hooks/{hook_id}', 'update_smtp_hook_route'),
    ('GET', '/v1/integrations/smtp/hooks/{hook_id}/deliveries', 'get_smtp_hook_deliveries'),
    ('POST', '/v1/integrations/smtp/hooks/{hook_id}/deliveries/{delivery_id}/replay', 'replay_smtp_hook_delivery'),
    ('GET', '/v1/integrations/smtp/settings', 'get_smtp_settings'),
    ('PUT', '/v1/integrations/smtp/settings', 'update_smtp_settings'),
    ('POST', '/v1/integrations/smtp/test', 'test_smtp_settings'),
    ('GET', '/v1/investigations', 'get_investigations'),
    ('POST', '/v1/investigations', 'post_investigation'),
    ('GET', '/v1/investigations/{investigation_id}', 'get_investigation'),
    ('PATCH', '/v1/investigations/{investigation_id}', 'patch_investigation'),
    ('GET', '/v1/investigations/{investigation_id}/activity', 'get_investigation_activity'),
    ('GET', '/v1/investigations/{investigation_id}/evidence', 'get_investigation_evidence'),
    ('POST', '/v1/investigations/{investigation_id}/evidence', 'post_investigation_evidence'),
    ('DELETE', '/v1/investigations/{investigation_id}/evidence/{evidence_id}', 'delete_investigation_evidence'),
    ('POST', '/v1/investigations/{investigation_id}/members', 'post_investigation_member'),
    ('DELETE', '/v1/investigations/{investigation_id}/members/{member_user_id}', 'delete_investigation_member'),
    ('PATCH', '/v1/investigations/{investigation_id}/members/{member_user_id}', 'patch_investigation_member'),
    ('GET', '/v1/investigations/{investigation_id}/notes', 'get_investigation_notes'),
    ('POST', '/v1/investigations/{investigation_id}/notes', 'post_investigation_note'),
    ('DELETE', '/v1/investigations/{investigation_id}/notes/{note_id}', 'delete_investigation_note'),
    ('PATCH', '/v1/investigations/{investigation_id}/notes/{note_id}', 'patch_investigation_note'),
    ('GET', '/v1/items', 'list_items'),
    ('GET', '/v1/items/{item_id}', 'get_item'),
    ('GET', '/v1/items/{item_id}/article-preview', 'get_item_article_preview'),
    ('GET', '/v1/items/{item_id}/graph', 'get_item_graph'),
    ('POST', '/v1/items/{item_id}/note', 'set_item_note'),
    ('POST', '/v1/items/{item_id}/read', 'set_item_read'),
    ('POST', '/v1/items/{item_id}/retry-article-fetch', 'retry_item_article_fetch'),
    ('POST', '/v1/items/{item_id}/star', 'set_item_star'),
    ('GET', '/v1/items/{item_id}/tag-suggestions', 'get_item_tag_suggestions'),
    ('POST', '/v1/items/{item_id}/tags', 'set_item_tags'),
    ('GET', '/v1/notifications/analytics', 'get_notifications_analytics'),
    ('GET', '/v1/notifications/webhooks', 'list_notification_webhooks'),
    ('POST', '/v1/notifications/webhooks', 'create_notification_webhook'),
    ('POST', '/v1/notifications/webhooks/test', 'test_notification_webhook_route'),
    ('PATCH', '/v1/notifications/webhooks/{webhook_id}', 'update_notification_webhook'),
    ('GET', '/v1/notifications/webhooks/{webhook_id}/deliveries', 'list_notification_webhook_deliveries'),
    ('POST', '/v1/notifications/webhooks/{webhook_id}/deliveries/{delivery_id}/retry', 'retry_notification_webhook_delivery_route'),
    ('GET', '/v1/reports', 'list_reports'),
    ('POST', '/v1/reports', 'create_report'),
    ('GET', '/v1/reports/capabilities', 'get_report_capabilities'),
    ('POST', '/v1/reports/preview', 'preview_report'),
    ('DELETE', '/v1/reports/{report_id}', 'remove_report', '/v1/reports/{report_id:uuid}'),
    ('GET', '/v1/reports/{report_id}', 'get_report', '/v1/reports/{report_id:uuid}'),
    ('GET', '/v1/reports/{report_id}/download', 'download_report', '/v1/reports/{report_id:uuid}/download'),
    ('POST', '/v1/reports/{report_id}/retry', 'retry_report', '/v1/reports/{report_id:uuid}/retry'),
    ('GET', '/v1/stats/activity-heatmap', 'get_activity_heatmap'),
    ('GET', '/v1/stats/feed-timeseries', 'get_feed_timeseries'),
    ('GET', '/v1/stats/overview', 'get_stats_overview'),
    ('GET', '/v1/stats/signal-radar', 'get_signal_radar'),
    ('POST', '/v1/tagging/rules', 'create_tagging_rule'),
    ('POST', '/v1/tagging/rules/preview', 'preview_tagging_rule'),
    ('DELETE', '/v1/tagging/rules/{rule_id}', 'delete_tagging_rule'),
    ('PATCH', '/v1/tagging/rules/{rule_id}', 'update_tagging_rule'),
    ('GET', '/v1/tagging/settings', 'get_tagging_settings_bundle'),
    ('GET', '/v1/tags', 'list_tags'),
)

_CAPTURED_ASYNC_OPERATIONS: tuple[OperationLiteral, ...] = (
    ('POST', '/v1/ai/daily-brief/backfill', 'queue_daily_brief_backfill_route'),
    ('POST', '/v1/ai/daily-brief/generate', 'generate_daily_brief_route'),
    ('POST', '/v1/ai/daily-brief/queue', 'queue_daily_brief_route'),
    ('POST', '/v1/ai/reprocess', 'reprocess_ai_for_recent_items_route'),
    ('POST', '/v1/reports/schedules/{schedule_id}/run', 'run_schedule'),
)

_DYNAMIC_TARGET_OPERATIONS: tuple[OperationLiteral, ...] = (
    ('GET', '/v1/iam/action-approvals', 'get_action_approvals'),
    ('POST', '/v1/iam/action-approvals', 'post_action_approval'),
    ('GET', '/v1/iam/action-approvals/{approval_id}', 'get_action_approval'),
    ('POST', '/v1/iam/action-approvals/{approval_id}/cancel', 'post_action_approval_cancel'),
    ('POST', '/v1/iam/action-approvals/{approval_id}/decision', 'post_action_approval_decision'),
    ('POST', '/v1/iam/action-approvals/{approval_id}/execute', 'post_action_approval_execute'),
    ('GET', '/v1/iam/action-approvals/{approval_id}/receipt', 'get_action_receipt'),
)

_EGRESS_FENCED_OPERATIONS: tuple[OperationLiteral, ...] = (
    ('POST', '/v1/ai/test-connection', 'test_ai_connection_route'),
)
# fmt: on


# Endpoint identity is part of the immutable operation contract. Route names are
# retained as useful operator evidence, but cannot by themselves detect a handler
# replacement that reuses the same display name.
_ENDPOINT_NAMES_BY_MODULE: Final[dict[str, tuple[str, ...]]] = {
    "app.api.routes.access_reviews": (
        "get_access_review_campaign_route",
        "get_access_review_campaigns",
        "get_access_review_items",
        "post_access_review_apply_complete",
        "post_access_review_apply_item",
        "post_access_review_apply_start",
        "post_access_review_campaign",
        "post_access_review_cancel",
        "post_access_review_close",
        "post_access_review_decisions",
        "post_access_review_resolve_item",
    ),
    "app.api.routes.action_approvals": (
        "get_action_approval",
        "get_action_approvals",
        "get_action_catalog",
        "get_action_receipt",
        "post_action_approval",
        "post_action_approval_cancel",
        "post_action_approval_decision",
        "post_action_approval_execute",
    ),
    "app.api.routes.ai": (
        "cancel_ai_ops_run_route",
        "generate_daily_brief_route",
        "get_ai_ops_live_route",
        "get_ai_ops_overview_route",
        "get_ai_ops_run_detail_route",
        "get_ai_settings_route",
        "get_ai_usage_route",
        "get_latest_daily_brief_route",
        "list_ai_ops_manual_actions_route",
        "list_ai_ops_prompt_history_route",
        "list_ai_ops_runs_route",
        "list_daily_brief_sources_route",
        "list_daily_briefs_route",
        "queue_daily_brief_backfill_route",
        "queue_daily_brief_route",
        "reprocess_ai_for_recent_items_route",
        "test_ai_connection_route",
        "update_ai_settings_route",
    ),
    "app.api.routes.alert_operations": (
        "get_alert_evaluation_activity",
        "get_alert_evaluation_detail",
        "get_alert_evaluations",
        "get_alert_occurrence_metrics",
        "replay_alert_evaluation",
    ),
    "app.api.routes.alerts": (
        "apply_alert_occurrence_backfill",
        "bulk_acknowledge_alert_occurrences",
        "bulk_close_alert_occurrences",
        "create_alert_interest",
        "delete_alert_interest",
        "get_alert_occurrence_activity",
        "get_alert_occurrence_detail",
        "get_alert_occurrences",
        "list_alert_interests",
        "list_alert_matches",
        "patch_alert_occurrence_lifecycle",
        "patch_alert_occurrence_snooze",
        "preview_alert_interest",
        "preview_alert_occurrence_backfill",
        "update_alert_interest",
    ),
    "app.api.routes.audit": (
        "export_audit_logs",
        "list_audit_logs",
    ),
    "app.api.routes.auth": (
        "change_password",
        "login",
        "logout",
        "me",
        "register",
        "registration_settings",
        "verify_mfa_login",
    ),
    "app.api.routes.auth_security": (
        "cancel_totp_enrollment",
        "confirm_totp",
        "enroll_totp",
        "get_mfa_status",
        "list_sessions",
        "reauthenticate_local_session",
        "remove_totp",
        "replace_recovery_codes",
        "revoke_other_sessions",
        "revoke_session",
    ),
    "app.api.routes.data_policies": (
        "get_data_policy_overview",
        "get_data_policy_preflight",
        "patch_handling_label",
        "post_handling_label",
        "put_data_policy_mode",
        "put_feed_handling_label",
        "put_handling_label_role_grants",
        "put_handling_label_status",
    ),
    "app.api.routes.exports": (
        "download_export",
        "get_export_capabilities",
        "preview_export",
    ),
    "app.api.routes.feeds": (
        "create_feed",
        "delete_feed",
        "export_feeds_backup",
        "export_feeds_sanitized",
        "get_feed_metadata",
        "import_feeds",
        "list_feeds",
        "refresh_feed",
        "update_feed",
    ),
    "app.api.routes.health": (
        "beat",
        "encrypted_data",
        "health",
        "live",
        "notifications",
        "ready",
        "worker",
    ),
    "app.api.routes.iam": (
        "delete_group_member",
        "delete_group_role",
        "delete_user_role",
        "explain_my_access",
        "get_group_members",
        "get_group_roles",
        "get_groups",
        "get_my_effective_access",
        "get_permissions",
        "get_role",
        "get_roles",
        "get_user_effective_access",
        "get_user_roles",
        "patch_group",
        "patch_role",
        "post_group",
        "post_group_member",
        "post_group_role",
        "post_role",
        "post_user_role",
        "remove_group",
        "remove_role",
    ),
    "app.api.routes.integrations": (
        "create_smtp_hook_route",
        "delete_smtp_hook_route",
        "get_integration_connectors",
        "get_smtp_analytics_route",
        "get_smtp_hook_deliveries",
        "get_smtp_hook_test_runs",
        "get_smtp_hooks",
        "get_smtp_settings",
        "get_smtp_template_defaults",
        "list_integrations",
        "replay_integration_delivery",
        "replay_smtp_hook_delivery",
        "test_smtp_hook",
        "test_smtp_settings",
        "update_smtp_hook_route",
        "update_smtp_settings",
    ),
    "app.api.routes.investigations": (
        "delete_investigation_evidence",
        "delete_investigation_member",
        "delete_investigation_note",
        "get_investigation",
        "get_investigation_activity",
        "get_investigation_evidence",
        "get_investigation_member_candidates",
        "get_investigation_notes",
        "get_investigations",
        "patch_investigation",
        "patch_investigation_member",
        "patch_investigation_note",
        "post_investigation",
        "post_investigation_evidence",
        "post_investigation_member",
        "post_investigation_note",
    ),
    "app.api.routes.items": (
        "get_item",
        "get_item_article_preview",
        "get_item_graph",
        "get_item_tag_suggestions",
        "list_items",
        "retry_item_article_fetch",
        "set_item_note",
        "set_item_read",
        "set_item_star",
        "set_item_tags",
    ),
    "app.api.routes.notifications": (
        "create_notification_webhook",
        "delete_notification_webhook",
        "get_notification_template_variables",
        "get_notifications_analytics",
        "list_notification_webhook_deliveries",
        "list_notification_webhooks",
        "retry_notification_webhook_delivery_route",
        "test_notification_webhook_route",
        "update_notification_webhook",
    ),
    "app.api.routes.oidc": (
        "oidc_callback",
        "start_oidc_link",
        "start_oidc_login",
        "start_oidc_reauthentication",
    ),
    "app.api.routes.oidc_access_policy": (
        "get_oidc_access_policy",
        "post_oidc_access_policy",
        "post_oidc_mapping_set",
        "put_oidc_access_policy",
        "put_oidc_mapping_set",
        "remove_oidc_access_policy",
        "remove_oidc_mapping_set",
    ),
    "app.api.routes.oidc_account": (
        "oidc_account_status",
        "unlink_oidc_account",
    ),
    "app.api.routes.oidc_provider": (
        "get_oidc_provider",
        "public_oidc_settings",
        "test_configured_oidc_provider",
        "update_oidc_provider",
    ),
    "app.api.routes.operations": (
        "diagnostics",
        "overview",
        "runs",
    ),
    "app.api.routes.reports": (
        "clone_template",
        "create_report",
        "create_schedule",
        "create_template",
        "download_report",
        "get_report",
        "get_report_capabilities",
        "list_report_templates",
        "list_reports",
        "list_schedules",
        "preview_report",
        "remove_report",
        "remove_schedule",
        "remove_template",
        "retry_report",
        "run_schedule",
        "update_schedule",
        "update_template",
    ),
    "app.api.routes.service_accounts": (
        "delete_disabled_service_account",
        "delete_role_assignment",
        "get_credentials",
        "get_role_assignments",
        "get_service_account",
        "get_service_accounts",
        "patch_service_account",
        "post_credential",
        "post_disable_service_account",
        "post_revoke_credential",
        "post_role_assignment",
        "post_rotate_credential",
        "post_service_account",
    ),
    "app.api.routes.stats": (
        "get_activity_heatmap",
        "get_feed_timeseries",
        "get_signal_radar",
        "get_stats_overview",
    ),
    "app.api.routes.tagging": (
        "create_tagging_rule",
        "delete_tagging_rule",
        "get_tagging_settings_bundle",
        "preview_tagging_rule",
        "queue_tagging_reapply",
        "update_tagging_rule",
        "update_tagging_settings",
    ),
    "app.api.routes.tags": (
        "create_tag",
        "list_tags",
    ),
    "app.api.routes.temporary_elevations": (
        "get_elevation",
        "get_elevations",
        "post_elevation",
        "post_elevation_close",
        "post_elevation_decision",
    ),
    "app.api.routes.tokens": (
        "create_token",
        "list_token_inventory",
        "list_tokens",
        "revoke_token",
    ),
    "app.api.routes.users": (
        "create_user",
        "get_user",
        "list_user_directory",
        "list_users",
        "reset_user_mfa",
        "update_user",
    ),
    "app.api.routes.views": (
        "create_view",
        "delete_view",
        "list_views",
        "update_view",
    ),
    "app.api.routes.workspace": (
        "get_my_effective_workspace",
        "get_my_workspace_preferences",
        "get_workspace_modules",
        "get_workspace_role_policies",
        "get_workspace_role_policy",
        "put_my_workspace_preferences",
        "put_workspace_role_policy",
        "reset_my_workspace_preferences",
        "reset_workspace_role_policy",
    ),
}


def _build_endpoint_identity_catalog() -> dict[str, str]:
    endpoint_identities = {
        endpoint_name: f"{module_name}.{endpoint_name}"
        for module_name, endpoint_names in _ENDPOINT_NAMES_BY_MODULE.items()
        for endpoint_name in endpoint_names
    }
    declared_count = sum(
        len(endpoint_names) for endpoint_names in _ENDPOINT_NAMES_BY_MODULE.values()
    )
    if len(endpoint_identities) != declared_count:
        raise RuntimeError("endpoint identity catalog contains duplicate qualnames")
    return endpoint_identities


_ENDPOINT_IDENTITY_BY_ROUTE_NAME: Final = _build_endpoint_identity_catalog()

_OPERATION_GROUPS: Final = (
    _PUBLIC_OPERATIONS,
    _CONTROL_PLANE_OPERATIONS,
    _REQUEST_CONTEXT_OPERATIONS,
    _CAPTURED_ASYNC_OPERATIONS,
    _DYNAMIC_TARGET_OPERATIONS,
    _EGRESS_FENCED_OPERATIONS,
)
_OPERATION_ROUTE_NAMES = {
    operation[2] for operations in _OPERATION_GROUPS for operation in operations
}
if set(_ENDPOINT_IDENTITY_BY_ROUTE_NAME) != _OPERATION_ROUTE_NAMES:
    missing_identities = sorted(
        _OPERATION_ROUTE_NAMES - set(_ENDPOINT_IDENTITY_BY_ROUTE_NAME)
    )
    unused_identities = sorted(
        set(_ENDPOINT_IDENTITY_BY_ROUTE_NAME) - _OPERATION_ROUTE_NAMES
    )
    raise RuntimeError(
        "endpoint identity catalog does not match operation catalog: "
        f"missing={missing_identities!r}, unused={unused_identities!r}"
    )


def _entries(
    governance_class: RouteGovernanceClass,
    operations: tuple[OperationLiteral, ...],
) -> tuple[RouteGovernanceEntry, ...]:
    return tuple(
        RouteGovernanceEntry(
            operation=RouteOperation(
                method=operation[0],
                path_format=operation[1],
                route_name=operation[2],
                endpoint_identity=_ENDPOINT_IDENTITY_BY_ROUTE_NAME[operation[2]],
                raw_path=operation[3] if len(operation) == 4 else None,
            ),
            governance_class=governance_class,
        )
        for operation in operations
    )


ROUTE_GOVERNANCE_MANIFEST: Final = RouteGovernanceManifest(
    version=ROUTE_GOVERNANCE_MANIFEST_VERSION,
    canonical_prefix=CANONICAL_API_PREFIX,
    entries=(
        *_entries(
            RouteGovernanceClass.PUBLIC,
            _PUBLIC_OPERATIONS,
        ),
        *_entries(
            RouteGovernanceClass.CONTROL_PLANE,
            _CONTROL_PLANE_OPERATIONS,
        ),
        *_entries(
            RouteGovernanceClass.REQUEST_CONTEXT,
            _REQUEST_CONTEXT_OPERATIONS,
        ),
        *_entries(
            RouteGovernanceClass.CAPTURED_ASYNC,
            _CAPTURED_ASYNC_OPERATIONS,
        ),
        *_entries(
            RouteGovernanceClass.DYNAMIC_TARGET,
            _DYNAMIC_TARGET_OPERATIONS,
        ),
        *_entries(
            RouteGovernanceClass.EGRESS_FENCED,
            _EGRESS_FENCED_OPERATIONS,
        ),
    ),
    request_context_classes=frozenset(
        {
            RouteGovernanceClass.REQUEST_CONTEXT,
            RouteGovernanceClass.DYNAMIC_TARGET,
        }
    ),
)


def route_governance_manifest_digest(manifest: RouteGovernanceManifest) -> str:
    operations = sorted(
        (
            {
                "governance_class": entry.governance_class.value,
                "endpoint_identity": entry.operation.endpoint_identity,
                "method": entry.operation.method,
                "path_format": entry.operation.path_format,
                "raw_path": entry.operation.raw_path,
                "route_name": entry.operation.route_name,
            }
            for entry in manifest.entries
        ),
        key=lambda value: (
            value["path_format"],
            value["method"],
            value["route_name"],
            value["endpoint_identity"],
            value["raw_path"] or "",
        ),
    )
    payload = {
        "canonical_prefix": manifest.canonical_prefix,
        "operations": operations,
        "request_context_classes": sorted(
            item.value for item in manifest.request_context_classes
        ),
        "version": manifest.version,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


ROUTE_GOVERNANCE_MANIFEST_SHA256: Final = route_governance_manifest_digest(
    ROUTE_GOVERNANCE_MANIFEST
)


__all__ = [
    "CANONICAL_API_PREFIX",
    "ROUTE_GOVERNANCE_MANIFEST",
    "ROUTE_GOVERNANCE_MANIFEST_SHA256",
    "ROUTE_GOVERNANCE_MANIFEST_VERSION",
    "RouteGovernanceClass",
    "RouteGovernanceEntry",
    "RouteGovernanceManifest",
    "RouteOperation",
    "route_governance_manifest_digest",
]
