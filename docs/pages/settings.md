# Settings Section

## Purpose

Centralized account, token, integration, and operational controls.

## Navigation Items

Default authenticated-user items:

- Account
- API Tokens
- Workspace
- Integrations
  - Webhooks

Additional items visible by default to the administrator role (canonical
permissions remain authoritative):

- AI (`/settings/ai`) when enabled
- Integrations
  - SMTP
- Tagging
- Access
- Identity
- Users
- Operations
- Audit Logs

SMTP and Operations do not require the built-in administrator base role. A
user with the corresponding canonical read permission can access either surface
when workspace policy exposes it. AI and Identity remain sealed to the built-in
administrator role in addition to their canonical permission checks.

The effective workspace policy may reorder or hide modules marked optional. The
frontend resolves server policy only against its static trusted module registry;
server-supplied labels, routes, and unknown module IDs never become navigation
links. Account and Workspace remain fixed local controls when their required
permissions are available.

Legacy route behavior:

- `/ai` redirects to `/settings/ai`
- `/settings` redirects to the first visible trusted Settings child, with
  `/settings/account` as the fallback
- `/settings/notifications` redirects to `/settings/integrations/webhooks`
- `/settings/integrations` redirects to the first visible trusted integration
  child, with `/settings` as the fallback

## Account Page

- User profile summary (`email`, `role`, `status`, `created`)
- Change password form
- OIDC identity status with link and password-confirmed unlink controls when a provider is enabled
- API calls:
  - `GET /auth/me`
  - `POST /auth/change-password`
  - `GET /auth/oidc/account`
  - `POST /auth/oidc/link`
  - `DELETE /auth/oidc/account`

## Identity Provider (Admin)

- One configurable OIDC provider with Authorization Code plus PKCE sign-in
- Discovery, JWKS connection test, client authentication, scopes, and exact callback URL
- Optional verified-email JIT provisioning and automatic approval
- Configurable default role and exact claim-to-role mappings, including dotted claim paths
- Optional role synchronization on each sign-in
- Client secrets are write-only in the UI and encrypted at rest
- HTTPS is required by default. Plain HTTP displays an operator warning and requires the corresponding deployment opt-in; private IdPs remain separately gated.
- API calls:
  - `GET /auth/oidc/provider`
  - `PUT /auth/oidc/provider`
  - `POST /auth/oidc/provider/test`
  - `GET /auth/oidc/settings`

## Integrations: Webhooks

- Personal outbound webhook notifications for:
  - `rss_item_new`
  - `alert_match`
  - `feed_failing`
  - `webhook_failed`
  - `daily_digest` (backward-compatible API identifier for the AI Daily Brief)
  - `report_ready`
- The AI Daily Brief event is only offered when AI is enabled, configured, and daily briefing is enabled.
- The report event is only offered when AI reporting is enabled and configured; stored inactive selections remain visible.
- AI Daily Brief delivery uses the persisted system-wide brief; a hook's RSS feed scope does not rebuild or filter the generated brief.
- Saved webhook list with create/edit/delete
- Create, update, test, retry, and delete actions are available to `admin` and `analyst` users with write notification access.
- Viewers can still see their own notification analytics and delivery history when scopes permit.
- Viewer-role reads and read-only API tokens receive redacted URL, header, query, and body configuration values.
- Webhook targets are validated before create/update/test/retry and again before delivery. Public targets must use `https`; private-network or internal-only targets require `ALLOW_PRIVATE_NETWORK_WEBHOOKS=true`.
- Cross-origin redirects are blocked during delivery, and redirect depth is capped by `OUTBOUND_MAX_REDIRECTS`.
- Webhook configuration fields:
  - name
  - enabled flag
  - HTTP method
  - webhook URL
  - query parameters
  - headers
  - content type
  - body mode (`json`, `form`, `raw`, `none`)
  - timeout
  - any feed or selected feeds
- URL query strings entered directly in the URL field are automatically moved into query parameter fields
- Template variables use `{{ item.title }}` style placeholders
- Delivery tooling:
  - test send against a selected sample feed
  - rendered request/response preview
  - recent delivery history per webhook
  - retry button for a stored delivery snapshot
  - analytics cards for success rate, recent failures, event mix, and most-failing webhook
- API calls:
  - `GET /notifications/template-variables`
  - `GET /notifications/analytics`
  - `GET /notifications/webhooks`
  - `POST /notifications/webhooks`
  - `PATCH /notifications/webhooks/{id}`
  - `DELETE /notifications/webhooks/{id}`
  - `POST /notifications/webhooks/test`
  - `GET /notifications/webhooks/{id}/deliveries`
  - `POST /notifications/webhooks/{id}/deliveries/{delivery_id}/retry`

## Integrations: SMTP

Viewing SMTP integrations requires `read:integrations`; creating, changing,
testing, deleting, or replaying them requires `write:integrations`. Neither
permission requires the built-in administrator base role.

- Multiple configurable outbound SMTP hooks with per-hook delivery statistics and history.
- Supports enabling/disabling, host, port, security mode, credentials, sender identity, recipient emails, timeout, event types, feed scope, subject template, and HTML template.
- New hooks can store their own authentication or reuse credentials from an existing SMTP hook.
- Changing the `Send for` event loads an event-specific default subject and body template.
- `AI Daily Brief` sends the persisted generated brief immediately after it is ready. Its stable API event identifier remains `daily_digest` for compatibility with existing hooks and delivery history.
- `Intelligence Report` sends reports whose manual or scheduled run requested delivery. Its API event identifier is `report_ready`.
- The AI Daily Brief choice, including its membership in `All notification events`, is omitted unless AI daily briefing is available. Historical hooks retain their stored selection without being silently rewritten.
- The report choice is omitted unless AI reporting is available. Historical `report_ready` selections remain visible as inactive when reporting is later disabled.
- Historical daily-brief backfills do not send notification emails. Normal scheduled or manual generation for the current brief emits one idempotent event per brief.
- Test tooling can run a connection/authentication check or send a rendered test email to a chosen recipient.
- Per-hook history separates event deliveries from SMTP tests. Test history retains result, action, recipient, saved-versus-draft settings, duration, normalized error details, SMTP server response, timestamps, and run ID.
- Tests against a saved hook are retained even when they use unsaved draft values. A brand-new unsaved hook has no persistent integration ID, so its result is available only in the immediate test response until the hook is saved.
- Test runs do not affect delivery analytics, retries, circuit breaking, or dead-letter counts.
- SMTP test audit entries include the retained run ID when available, duration, normalized error details, and a bounded server response without storing SMTP credentials.
- Dead-letter deliveries can be replayed without rewriting their historical attempt records.
- API calls:
  - `GET /integrations/connectors`
  - `GET /integrations`
  - `GET /integrations/smtp/hooks`
  - `POST /integrations/smtp/hooks`
  - `PATCH /integrations/smtp/hooks/{hook_id}`
  - `DELETE /integrations/smtp/hooks/{hook_id}`
  - `GET /integrations/smtp/template-defaults`
  - `GET /integrations/smtp/analytics`
  - `GET /integrations/smtp/hooks/{hook_id}/deliveries`
  - `GET /integrations/smtp/hooks/{hook_id}/test-runs`
  - `POST /integrations/smtp/hooks/{hook_id}/deliveries/{delivery_id}/replay`
  - `POST /integrations/smtp/hooks/test`
- Legacy-compatible SMTP settings endpoints remain available: `GET /integrations/smtp/settings`, `PUT /integrations/smtp/settings`, and `POST /integrations/smtp/test`.

## Tagging Page (Admin)

- Global auto-tagging controls for built-in classification categories
- Tunable defaults:
  - enabled built-in category tags
  - minimum auto-tag confidence
  - secondary tag limit
- Custom rule management:
  - create/edit/delete rules
  - `contains` or `regex` match type
  - choose fields to inspect (`title`, `summary`, `article text`, `feed name`)
  - optional category requirements
  - all feeds or selected feeds
  - optional minimum classification confidence
- Rule preview shows current corpus matches before save
- Reapply tagging queues a background pass for recent items
- API calls:
  - `GET /tagging/settings`
  - `PUT /tagging/settings`
  - `POST /tagging/rules`
  - `PATCH /tagging/rules/{id}`
  - `DELETE /tagging/rules/{id}`
  - `POST /tagging/rules/preview`
  - `POST /tagging/reapply`

## API Tokens Page

- `read:tokens` opens the token inventory; without `write:tokens`, the page is
  explicitly read-only.
- `write:tokens` enables the create form and revoke actions.
- Create token form: name, expiry days, scopes CSV
- One-time display of created token secret
- Admin optional filter by `user_id`
- API calls:
  - `GET /tokens`
  - `POST /tokens`
  - `DELETE /tokens/{id}`

## Workspace Page

- Personal workspace controls separate the global top navigation from the
  contextual Settings sidebar. The top-navigation editor and preview list only
  trusted primary modules that can appear in the application header; users can
  reorder or hide optional entries. `Settings` remains structurally fixed;
  `Dashboard` is fixed by default but can be made optional by organization
  policy.
- Optional Settings destinations remain available in a separately labeled,
  compact Settings-navigation surface. Existing Settings preferences stay in
  the personal draft and write payload when a user changes only the top
  navigation, so an unrelated save does not silently discard them.
- Personal top-navigation order applies to the desktop header. Mobile
  top-navigation order follows the organization role policy, while the Settings
  sidebar uses one order at both breakpoints.
- Start-page selection is independent of top-navigation membership. A user can
  choose any available trusted landing destination, including a Settings page,
  or inherit the organization default.
- Personal controls cannot expose a module hidden by role policy, unavailable to
  the account, disabled by a feature dependency, or blocked by permissions.
- Principals with durable `write:workspace` authority can edit role policies for
  `admin`, `analyst`, and `viewer`. Top-navigation defaults and Settings-sidebar
  defaults are presented separately; both support visibility, optionality, and
  ordering, while mobile priority applies only to the top navigation. Role
  policies also define the default landing page and first-use dashboard panels.
- The role top-navigation preview is inert and contains only primary header
  modules. It does not impersonate a role, issue requests as another user, or
  turn preview entries into links.
- Revision conflicts keep the current draft visible and prompt the editor to
  reload before retrying, preventing an older browser from overwriting newer
  policy.
- Unknown module and dashboard-panel IDs are shown as version-skew warnings and
  preserved in write payloads when the server reports them. They never create
  routes or arbitrary links.
- Workspace mutations use the backend workspace audit events; the frontend does
  not maintain a second audit trail.
- API calls:
  - `GET /workspace/modules`
  - `GET /workspace/effective`
  - `GET /workspace/preferences`
  - `PUT /workspace/preferences`
  - `POST /workspace/preferences/reset`
  - `GET /workspace/role-policies`
  - `PUT /workspace/role-policies/{role}`
  - `POST /workspace/role-policies/{role}/reset`

## Access Governance Page

- Route: `/settings/access`
- Requires `read:iam`; individual overview inventories and the Handling labels
  tab are additionally permission-gated.
- Overview cards show system/custom roles, local/federated groups, service
  accounts, access-review campaigns, temporary elevations, action approvals,
  current data-policy mode, coverage, and blocker status. A missing permission or
  failed optional request is displayed explicitly and does not hide a failure by
  rendering a zero count.
- Roles and Groups tabs manage custom roles, code-owned permission selections,
  group membership, and group role assignments with optimistic revision checks.
- The Handling labels tab manages label metadata, durable role grants, archive
  status, and `disabled`, `audit`, or `enforced` policy mode.
- Persistent IAM and handling-policy writes reject authority obtained only by
  temporary elevation. Handling-policy writes additionally require a recently
  authenticated browser session with the applicable local or OIDC MFA assurance.
- The activation preflight displays whether it was a full scan, its timestamp and
  evaluated policy revision, coverage versions, stable blocker codes/counts, and
  the installed canonical route-manifest version, digest, operation counts, and
  governance-class counts.
- The mode-change confirmation repeats the target mode and reason. Audit cannot
  be selected while a non-grant preflight blocker remains; enforcement requires
  the preflight to have no blockers.
- API calls:
  - `GET /iam/permissions`
  - `GET|POST /iam/roles`
  - `PATCH|DELETE /iam/roles/{role_id}`
  - `GET|POST /iam/groups`
  - `PATCH|DELETE /iam/groups/{group_id}`
  - group membership and role-assignment endpoints under `/iam/groups/{group_id}`
  - `GET /iam/data-policies`
  - label, role-grant, status, and mode mutations under `/iam/data-policies`
  - permission-gated summary reads for service accounts, access reviews,
    elevations, and action approvals

For the activation and target-lineage contracts, see [Access Governance and Data
Policy](../reference/access-governance.md).

## Users Page (Admin)

- Create user form
- Search and edit user directory
- Expandable role definitions for `admin`, `analyst`, and `viewer`
- Editable fields per user:
  - role
  - active flag
  - optional password reset
- API calls:
  - `GET /users`
  - `POST /users`
  - `PATCH /users/{id}`

## Audit Logs Page (Admin)

- Filter by `action`
- Filter by `actor_user_id`
- Paginated log table
- Export filtered logs to JSON (`Export JSON`)
- API call:
  - `GET /audit-logs`
  - `GET /audit-logs/export`

## Access Rules

- Protected by authenticated route guard.
- Navigation visibility is presentation policy, not an authorization boundary;
  direct routes and API requests remain protected by route and backend IAM.
- The Workspace page is available with `read:workspace`; organization policy
  editing additionally requires durable `write:workspace` authority.
- Access Governance is available with `read:iam`; the Handling labels tab
  additionally requires `read:data_policies`. Persistent policy writes require
  durable authority, and handling-policy writes also require a sensitive browser
  session.
- Webhook analytics/list/history are available to authenticated users for their own webhooks.
- Webhook create/update/test/retry/delete additionally require operator access (`admin` or `analyst`) and write notification access.
- Restricted settings pages use the same canonical read permissions enforced by
  their backend APIs. SMTP and Operations have no sealed base-role requirement.
- AI is nested at `/settings/ai`, requires the built-in administrator base role
  plus `read:ai`, and remains available at `/ai` through a backward-compatible
  redirect. Identity likewise requires the administrator base role plus
  `read:users`; mutations require the corresponding write permission.
