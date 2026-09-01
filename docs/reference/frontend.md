# Frontend Reference

This page documents app routes, components, constants, UI elements, and every backend API call issued by the web client.

Unless noted otherwise, endpoint paths on this page are relative to the published API base `/api/v1`. The schema endpoint remains a separate path at `/api/openapi.json`.

## App Composition (`web/src/App.tsx`)

Providers (outer to inner):

1. `ThemeProvider`
2. `AuthProvider`
3. `QueryClientProvider`

The authenticated route branch additionally wraps `AppShell` in
`WorkspaceProvider`, which resolves navigation and first-use dashboard defaults
from the workspace APIs against a static trusted frontend registry.

React Query defaults:

- `staleTime: 30000`
- `refetchOnWindowFocus: false`
- `retry: 1`

Route tree:

- `/login` -> `LoginPage`
- `/` -> `ProtectedRoute` + `AppShell`
  - index -> `DashboardPage`
  - `/start` -> effective trusted landing-page redirect
  - `/alerts` -> `AlertsPage`
  - `/investigations` -> `InvestigationsPage` list workspace
  - `/investigations/:investigationId` -> `InvestigationsPage` detail workspace
  - `/feeds` -> `FeedsPage`
  - `/stats` -> `StatsPage`
  - `/export` -> `ExportPage`
  - `/reporting` -> `ReportingPage`
  - `/reporting/:reportId` -> `ReportingPage` report detail
  - `/ai` -> redirect to `/settings/ai`
  - `/settings` -> `SettingsLayout`
    - index -> first visible trusted Settings child, falling back to `/settings/account`
    - `/settings/account` -> `AccountPage`
    - `/settings/workspace` -> `WorkspaceSettingsPage`
    - `/settings/notifications` -> redirect to `/settings/integrations/webhooks`
    - `/settings/integrations` -> first visible trusted integration child
    - `/settings/integrations/webhooks` -> `NotificationWebhooksSettings`
    - `/settings/integrations/smtp` -> `read:integrations`-gated `SMTPIntegrationSettingsPage` with no sealed base-role requirement
    - `/settings/ai` -> administrator-base-role and `read:ai`-gated `AiSettingsPage` (shown only when `features.ai_enabled`)
    - `/settings/identity` -> administrator-base-role and `read:users`-gated `IdentitySettingsPage`
    - `/settings/tagging` -> `read:tagging`-gated `TaggingSettingsPage`
    - `/settings/tokens` -> `read:tokens`-gated `TokensPage`
    - `/settings/operations` -> `read:operations`-gated `OperationsPage` with no sealed base-role requirement
    - `/settings/users` -> `read:users`-gated `UsersPage`
    - `/settings/audit-logs` -> `read:audit`-gated `AuditLogsPage`

## Shared Client Behavior

### API client (`web/src/api/client.ts`)

- Production fallback base URL is `/api/v1`; development fallback base URL is `http(s)://<host>:8000/v1`.
- `VITE_API_BASE_URL` overrides the fallback, and the shipped compose stack passes it from `WEB_VITE_API_BASE_URL` (default `/api/v1`).
- Adds `Content-Type: application/json` for requests.
- Supports binary downloads with structured API errors and sanitized `Content-Disposition` filenames.
- Sends browser credentials (`credentials: include`) for cookie-based session auth.
- Adds CSRF header (`x-csrf-token` by default) on mutating requests when `auth=true`.
- Uses an `AbortController` timeout (`REQUEST_TIMEOUT_MS`, default `15000`) and distinguishes timeouts from network failures.
- Parses the compatible top-level `detail` field and the structured API error envelope, including stable code, retry hint, request ID, and `Retry-After` timing.
- Rejects malformed successful responses and summarizes non-JSON proxy failures without rendering HTML response bodies in the UI.
- Returns `undefined` for `204` responses.
- `LoginPage` and self-registration calls pass `auth=false`; after login the app relies on the server-set session cookies rather than persisting any bearer token in browser storage.

### Error presentation (`web/src/api/errors.ts`)

- Operation-specific context and safe backend detail are shown together instead of replacing one another.
- Retryable, rate-limited, authorization, CSRF, timeout, and network failures receive distinct recovery guidance.
- The API request reference is shown when available so an operator can correlate the UI failure with server logs.
- Login credential failures intentionally suppress server detail that could make account enumeration easier.

### Browser storage keys

- Theme mode: `threatlens.theme`
- Dashboard window state: `threatlens.dashboard.windows.v2:<userId>`
- Dashboard alert seen timestamps: `threatlens.dashboard.window-seen.v1:<userId>`
- User RSS last-open timestamps: `threatlens.user-last-open.v1:<userId>`
- Legacy unscoped dashboard storage keys are migrated into the user-scoped keys on first load
- No auth token is stored in local storage or session storage by the shipped frontend

## Theme System (`ThemeContext.tsx`)

Supported modes:

- `light`
- `dark`

Root class behavior:

- Adds the `dark` class only for dark mode.
- Adds `theme-light` or `theme-dark` to `<html>`.
- Normalizes legacy stored values beginning with `dark-` or `theme-dark` to `dark`.
- `/theme-init.js` applies the stored mode before React mounts to avoid a light-mode flash.

## App Shell (`AppShell.tsx`)

Default top navigation links:

- `Dashboard`
- `Alerts`
- `Investigations`
- `Feeds`
- `Stats`
- `Export`
- `Reporting`
- `Settings`

The effective workspace policy controls visibility and order while retaining the
existing desktop and mobile shell styling. For these top-navigation modules,
desktop order uses module order and mobile order uses mobile priority. Only
module IDs compiled into `workspace/moduleRegistry.ts` can provide labels, icon
components, routes, or mobile behavior. Unknown server IDs are retained for
compatibility diagnostics but are never rendered as links.

The top-navigation editors and previews use the same trusted primary-module
boundary as the application header. Contextual Settings destinations are managed
in a separately labeled, compact Settings-navigation surface; their module order
is shared by the desktop and mobile Settings sidebars rather than using mobile
priority.

Top-right controls:

- Current user badge (`email (role)`)
- Light/dark mode toggle
- `Logout` button

### Workspace client

- `workspace/moduleRegistry.ts` is the frontend trust boundary for module IDs,
  routes, Lucide icon components, permissions, feature dependencies, role
  defaults, and mobile behavior.
- `WorkspaceProvider` queries the registry, effective policy, and current-user
  preferences, then applies local permission and feature checks as defense in
  depth.
- A workspace API failure falls back to trusted role defaults and surfaces a
  degraded state instead of trusting partial server policy. A first-use
  dashboard initializes once from safe local defaults and preserves that layout
  if the workspace service later recovers.
- Local password login enters through `/start`, and successful OIDC callbacks
  return there as well. The resolver selects the configured available trusted
  route while explicit safe deep links are still honored. `/` always remains the
  literal Dashboard route. A configured start page may be an available Settings
  destination even though Settings leaves are not top-navigation modules.
- Restricted Settings routes use canonical permissions from the current-user
  access envelope. The backend remains authoritative and enforces the same
  permissions on every API call.
- Personal preference and role-policy writes include expected revisions and
  invalidate the effective-workspace query after success. Personal drafts keep
  Settings-navigation preferences when the user edits only top navigation, and
  the complete trusted draft preserves existing Settings values instead of
  dropping unrelated preferences.
- Workspace API calls:
  - `GET /workspace/modules`
  - `GET /workspace/effective`
  - `GET /workspace/preferences`
  - `PUT /workspace/preferences`
  - `POST /workspace/preferences/reset`
  - `GET /workspace/role-policies`
  - `PUT /workspace/role-policies/{role}`
  - `POST /workspace/role-policies/{role}/reset`

## Page-Level Reference

### `LoginPage`

UI elements:

- Email input
- Password input
- Error message on failed login
- Submit button with pending label

API calls:

- `POST /auth/login`

### `DashboardPage`

Window types:

- `rss`
- `alerts`
- `notes`
- `daily_brief`

Snap modes:

- `free`, `full`, `left`, `right`, `top_left`, `top_right`, `bottom_left`, `bottom_right`

Dashboard constants:

- `DASHBOARD_VIEW_VERSION = 6`
- `WINDOW_MIN_WIDTH = 460`
- `WINDOW_MIN_HEIGHT = 320`
- `DRAG_EDGE_SNAP_THRESHOLD = 12`
- `DRAG_MIDLINE_SNAP_THRESHOLD = 8`
- `HIDDEN_TAGS = {content_fetched, priority}`
- `PAGE_SIZE_OPTIONS = [10, 25, 50, 100]`
- `MAX_VIEWS_IMPORT_FILE_BYTES = 2000000`
- `MAX_IMPORTED_VIEWS = 250`

Window behaviors:

- Drag and resize (wide layout)
- Snap presets
- Soft magnetic edge/midline snapping while dragging free windows
- Per-window controls collapse/expand
- Per-window rename
- Scratch note persistence for notes windows
- RSS and alerts filter state is isolated per window and preserved in saved views
- The toolbar search reflects a shared cross-panel search only when searchable panels are aligned; otherwise it shows a mixed-state placeholder
- RSS expanded item detail stays panel-scoped, while note drafts follow the item across panels until saved
- RSS expanded item detail can open a right-side original article preview drawer with a sandboxed iframe and new-tab fallback
- Daily Brief window selection is isolated per window and preserved in saved views

RSS filter values:

- Time range: `all|24h|7d|30d|days|custom`
- Read status: `all|read|unread`
- Star status: `all|starred|unstarred`
- Sort: `published_at_desc|published_at_asc|first_seen_desc|first_seen_asc`
- View mode: `expanded|compact` (compact default)

Alerts filter values (dashboard window):

- Time range: `all|24h|7d|30d|days|custom`
- Sort: `published_at_desc|published_at_asc|first_seen_desc|first_seen_asc`
- View mode: `expanded|compact`

HTML sanitation values for rich article rendering:

- Allowed tags: `a,b,blockquote,br,code,em,h1,h2,h3,h4,h5,h6,hr,i,li,ol,p,pre,strong,u,ul`
- Allowed `href` protocols: `http`, `https`

API calls:

- `GET /feeds`
- `GET /views`
- `GET /tags`
- `GET /alerts?include_disabled=false`
- `POST /views`
- `DELETE /views/{id}`
- `POST /items/{id}/read`
- `POST /items/{id}/star`
- `POST /items/{id}/note`
- `POST /items/{id}/retry-article-fetch`
- `GET /items?...`
- `GET /alerts/matches?...`
- `GET /items/{id}`
- `GET /ai/daily-briefs`

AI-enhanced dashboard behavior:

- RSS item detail can render:
  - AI summary text
  - AI relevance label and score
  - AI reasons or error state
- RSS item detail exposes an inline article retry action when extraction is missing or errored
- Item expansion state is isolated per dashboard window.
- Unsaved note drafts stay aligned per item across dashboard panels.
- Daily Brief windows show retained brief history and per-window brief selection

### `AiSettingsPage`

Tabs:

- `Overview`
- `Activity`
- `Configuration`

Key UI areas:

- endpoint/model and feature toggles
- company profile context
- editable prompt templates and instruction overlays
- daily brief scheduling and retention
- scoped AI reprocess controls
- live running/queued task panel
- run history and drilldown
- selected run detail automatically re-anchors to the visible filtered list
- provider exchange inspection
- prompt history and manual action history

API calls:

- `GET /ai/settings`
- `PUT /ai/settings`
- `POST /ai/test-connection`
- `GET /ai/usage`
- `GET /ai/daily-brief/latest`
- `GET /ai/daily-briefs`
- `POST /ai/daily-brief/generate`
- `POST /ai/daily-brief/queue`
- `POST /ai/reprocess`
- `GET /ai/ops/overview`
- `GET /ai/ops/live`
- `GET /ai/ops/runs`
- `GET /ai/ops/runs/{id}`
- `POST /ai/ops/runs/{id}/cancel`
- `GET /ai/ops/manual-actions`
- `GET /ai/ops/prompt-history`
- `GET /ai/daily-briefs/{id}/sources`

### `AlertsPage`

Alert category values:

- `software`
- `vendor`
- `apt_group`
- `vulnerability`
- `malware`
- `technique`
- `campaign`
- `infrastructure`
- `other`

UI elements:

- Keyboard-accessible Rules, Occurrences, and administrator-only Operations tabs
- Rule create/edit form with name, category, keywords, severity, and suppression
- Current computed-match preview, include-disabled toggle, and grouped rule cards
- Durable occurrence metrics, filters, desktop table, mobile list, detail, activity,
  lifecycle, snooze, and bulk actions
- Administrator backfill preview/apply flow with keyset continuation
- Administrator evaluation queue, attention filters, retained metrics, detail,
  activity, and dead-letter replay

API calls:

- `GET /alerts?include_disabled=<bool>`
- `POST /alerts/preview`
- `POST /alerts`
- `PATCH /alerts/{id}`
- `DELETE /alerts/{id}`
- occurrence list, detail, activity, lifecycle, snooze, and bulk endpoints
- `POST /alerts/occurrences/reconciliation/preview`
- `POST /alerts/occurrences/reconciliation/apply`
- `GET /alerts/occurrences/metrics`
- administrator evaluation list, detail, activity, and replay endpoints

### `InvestigationsPage`

UI elements:

- Search, status, severity, assignment, archive, and pagination controls
- Separate desktop table and compact mobile collection cards
- Create dialog with severity, visibility, description, and optional assignee
- Versioned detail workspace with Overview, Members, Evidence, Notes, and Activity
  tabs
- Owner/editor/viewer object-role controls, final-owner protection, and explicit
  confirmations for destructive changes
- Item, IOC, report, and alert-occurrence evidence with immutable source snapshots
- Refresh guidance for optimistic conflicts and indistinguishable not-found/private
  access failures

API calls:

- `GET`, `POST /investigations`
- `GET /investigations/member-candidates`
- `GET`, `PATCH /investigations/{id}`
- member add, update, and remove endpoints
- paginated evidence reads plus evidence add and remove endpoints
- paginated note reads plus note add, update, and soft-delete endpoints
- `GET /investigations/{id}/activity`

### `FeedsPage`

Sort values:

- `name_asc`
- `name_desc`
- `last_fetch_desc`
- `last_fetch_asc`
- `created_desc`

Fetch mode values:

- `interval`
- `schedule`

UI elements:

- Add feed form
  - URL with metadata detect button
  - Name/description/site URL/language
  - Fetch mode selector
  - Interval seconds or cron expression input
- Feed inventory
  - Search
  - Sort select
  - Export JSON
  - Import JSON file picker + run import
  - Overwrite-existing toggle
  - Per feed refresh, enable/disable, mode update, schedule/interval update

API calls:

- `GET /feeds`
- `POST /feeds/metadata`
- `POST /feeds`
- `PATCH /feeds/{id}`
- `POST /feeds/{id}/refresh`
- `POST /feeds/import`
- `GET /feeds/export`

### `StatsPage`

Chart color palette:

- `#0891b2`, `#06b6d4`, `#0ea5e9`, `#14b8a6`, `#10b981`, `#22c55e`, `#eab308`, `#f97316`

UI elements:

- Day range selector (`7/30/90/180`)
- Multi-select feed filter
- KPI cards
- Interactive multi-series time-series chart
  - Hover guide line
  - Hover legend sorted by count desc
  - Per-series visibility chips
- Activity heatmap:
  - single day/hour matrix bound to selected stats time range
  - intensity legend
  - hover legend for selected bucket (count + intensity)
- Signal radar:
  - category polygon with interactive axis points
  - per-category side list with relative intensity bars
- Derived metrics
- Status bars
- Daily volume bars
- Top domain bars
- Feed share bars
- Feed contribution table

API calls:

- `GET /feeds`
- `GET /stats/overview?...`
- `GET /stats/feed-timeseries?...`
- `GET /stats/activity-heatmap?...`
- `GET /stats/signal-radar?...`

### `ExportPage`

UI elements:

- Debounced full-text, feed, tag, classification, AI relevance, user-state, article-text, and date filters
- Live match, article-text, IOC, and preview-row counters
- CSV, JSONL, ThreatLens ZIP, STIX 2.1, MISP, and PDF ZIP format selector
- Format-aware content options, including requester state and private notes
- STIX TLP marking and MISP distribution selectors
- Responsive preview table on desktop and compact preview rows on mobile
- Preflight validation for empty, stale, invalid, and over-limit result sets
- Binary download action with a five-minute client timeout

API calls:

- `GET /exports/capabilities`
- `POST /exports/preview`
- `POST /exports`

### `ReportingPage`

UI elements:

- Reports, templates, and admin-only schedules views
- Filtered seven-day builder with source inclusion controls
- Audience, objective, tone, detail, company-context, focus, exclusion, and custom-instruction controls
- Ordered/enabled report sections
- Live context, source, batch, model-call, and coverage estimates
- Private/shared template save, immutable built-in clone, and template delete controls
- Report library with queued, running, ready, skipped, and error states
- Report detail with stage progress, coverage warnings, source evidence, owner/admin retry/delete, and Markdown/HTML/PDF downloads
- Weekly/monthly IANA-time-zone schedule create/edit/pause/run/delete controls
- Optional `report_ready` SMTP/webhook delivery using link, summary, or bounded full content

API calls:

- `GET /reports/capabilities`
- `POST /reports/preview`
- `GET|POST /reports/templates`
- `PUT|DELETE /reports/templates/{template_id}`
- `POST /reports/templates/{template_id}/clone`
- `GET|POST /reports`
- `GET|DELETE /reports/{report_id}`
- `POST /reports/{report_id}/retry`
- `GET /reports/{report_id}/download`
- `GET|POST /reports/schedules`
- `PUT|DELETE /reports/schedules/{schedule_id}`
- `POST /reports/schedules/{schedule_id}/run`

### `SettingsLayout`

UI elements:

- Workspace-policy and permission-aware settings nav
- Current role badge
- Settings nav entries:
  - `Account`
  - `API Tokens`
  - `Workspace`
  - `Integrations` with `Webhooks`
  - permission-gated `Integrations` -> `SMTP`
  - permission-gated `AI`, `Tagging`, `Access`, `Identity`, `Users`,
    `Operations`, and `Audit Logs`

API calls:

- `GET /auth/me` (via `useCurrentUser`)

### `AccessGovernancePage`

UI elements:

- Permission-aware Overview, Roles, Groups, and Handling labels tabs.
- Current-posture cards for IAM, machine identities, access reviews, temporary
  elevations, action approvals, and data-policy state. Optional inventory
  failures remain visible instead of being rendered as zero counts.
- Custom-role editor with grouped permission selection and revision-aware saves.
- Local/federated group inventory, paginated member management, and group-role
  assignments.
- Handling-label metadata and durable-role grant editor, archive controls, and
  revision-aware mode changes.
- Activation-preflight evidence showing full-versus-runtime evaluation, checked
  time, policy revision, coverage, blocker details, and the route-manifest
  version, digest, operation counts, request-context count, and governance-class
  counts.
- Write gates distinguish missing authority, temporary-only authority, and a
  browser session that needs recent local or OIDC MFA-backed authentication.
- Unsaved-draft confirmation before switching governance tabs or refreshing.

API calls:

- `GET /iam/permissions`
- `GET|POST /iam/roles`
- `PATCH|DELETE /iam/roles/{role_id}`
- `GET|POST /iam/groups`
- `PATCH|DELETE /iam/groups/{group_id}`
- group membership and role-assignment calls under `/iam/groups/{group_id}`
- `GET /iam/data-policies`
- handling-label, role-grant, status, and mode mutations under
  `/iam/data-policies`
- permission-gated `GET` inventory calls for `/iam/service-accounts`,
  `/iam/access-reviews`, `/iam/elevations`, and `/iam/action-approvals`

The Overview is an operational summary, not a workflow editor for service
accounts, reviews, elevations, or approvals. See [Access Governance and Data
Policy](./access-governance.md) for the backend activation and target-lineage
contracts.

### `NotificationWebhooksSettings`

UI elements:

- Saved webhooks list
- Webhook create/edit form
- Feed scope selection (`all` or selected feeds)
- Query params, headers, content type, and body configuration
- Template variable reference list
- Test webhook action with rendered request/response preview
- Delivery history list with retry action

API calls:

- `GET /feeds`
- `GET /notifications/template-variables`
- `GET /notifications/webhooks`
- `POST /notifications/webhooks`
- `PATCH /notifications/webhooks/{id}`
- `DELETE /notifications/webhooks/{id}`
- `POST /notifications/webhooks/test`
- `GET /notifications/webhooks/{id}/deliveries?page=1&page_size=10`
- `POST /notifications/webhooks/{id}/deliveries/{delivery_id}/retry`

### `SMTPIntegrationSettingsPage`

Reading the SMTP workspace requires `read:integrations`; its mutation, test,
delete, and replay controls require `write:integrations`. Neither permission is
restricted to the built-in administrator base role.

UI elements:

- Multi-hook SMTP list with per-hook health and credential-source status
- SMTP connection, authentication, sender, recipient, event, and feed-scope editor
- Optional credential reuse from another direct-credential SMTP hook
- Event-specific default subject and HTML templates
- Connection test and rendered test-email action using saved or current draft settings
- Paginated `Deliveries` and `Tests` history views with attempts, errors, server responses, timing, recipients, configuration source, and run IDs
- Dead-letter delivery replay
- Aggregate delivery health and event statistics
- Template variable reference

API calls:

- `GET /integrations/connectors`
- `GET /integrations/smtp/hooks`
- `POST /integrations/smtp/hooks`
- `PATCH /integrations/smtp/hooks/{hook_id}`
- `DELETE /integrations/smtp/hooks/{hook_id}`
- `GET /integrations/smtp/template-defaults`
- `GET /integrations/smtp/analytics`
- `GET /integrations/smtp/hooks/{hook_id}/deliveries?page={page}&page_size=10`
- `GET /integrations/smtp/hooks/{hook_id}/test-runs?page={page}&page_size=10`
- `POST /integrations/smtp/hooks/{hook_id}/deliveries/{delivery_id}/replay`
- `POST /integrations/smtp/hooks/test`
- `GET /feeds`
- `GET /notifications/template-variables`

### `TaggingSettingsPage`

UI elements:

- Auto-tag defaults editor
  - enabled built-in categories
  - minimum confidence
  - secondary tag limit
- Retagging queue form
  - days back
  - item limit
- Custom rule list and editor
- Rule preview with recent matches, matched sections, and current tags

API calls:

- `GET /tagging/settings`
- `PUT /tagging/settings`
- `POST /tagging/rules`
- `PATCH /tagging/rules/{id}`
- `DELETE /tagging/rules/{id}`
- `POST /tagging/rules/preview`
- `POST /tagging/reapply`

### `AccountPage`

UI elements:

- Account summary fields: email, role, status, created timestamp
- Local-account password management; SSO-managed accounts show the external authority instead of impossible password controls
- Local TOTP enrollment, confirmation, recovery-code regeneration, and disable controls
- Browser-session inventory with client, authentication method, activity, and expiry details
- Exact-session and revoke-other-sessions confirmations with local or SSO recent-auth continuation; restored actions always require confirmation again
- OIDC identity link/unlink controls where the account ownership model permits them

API calls:

- `GET /auth/me`
- `POST /auth/change-password`
- `GET /auth/security/mfa`
- MFA enrollment, confirmation, recovery-code, disable, and local reauthentication endpoints
- `GET /auth/security/sessions`
- exact and bulk session revocation endpoints
- OIDC link, unlink, and reauthentication endpoints

### `TokensPage`

UI elements:

- Token inventory for users with `read:tokens`; an explicit read-only state hides
  mutation controls when `write:tokens` is absent
- Create token form for users with `write:tokens`: name, expiry days, scopes CSV, and an authentication-method-aware step-up
- Leave scopes blank to get the default read-only scopes; an explicit empty list is rejected by the API
- Local browser sessions provide the current password and enabled local MFA. OIDC browser sessions require recent provider authentication with the configured external MFA assurance; the draft is restored after redirect and is never auto-submitted.
- One-time token reveal panel that receives keyboard focus, announces creation without reading the secret aloud, and clears the bearer value after copy or acknowledgement
- Admin-only `user_id` filter with explicit Apply/Clear actions, UUID validation, and a visible draft-versus-applied state
- Revoke button per token for users with `write:tokens`

API calls:

- `GET /tokens` (optionally with `?user_id=` for admin)
- `POST /tokens`
- `DELETE /tokens/{id}`

### `UsersPage`

UI elements:

- Create user form: email, password, role, active
- Search, authentication-source, role, status, approval, and password-state filters with paginated results
- Local and SSO source badges plus the managing OIDC provider and last SSO sign-in
- Per-user row editor:
  - role
  - active flag
  - approval state
  - optional password reset only for locally managed passwords
  - administrator MFA reset with reason and recent-auth verification
- SSO-managed fields are read-only with an explanation of where they must be changed
- MFA-reset continuation reloads its exact target by ID after SSO verification, so it remains reliable when the target is outside the current directory page

API calls:

- `GET /users/directory`
- `GET /users/{id}`
- `POST /users`
- `PATCH /users/{id}`
- `POST /users/{id}/mfa/reset`

### `OperationsPage`

Permission-gated workspace for deployment health and recovery readiness. Access
requires `read:operations` and does not require the built-in administrator base
role.

UI elements:

- Overall readiness and prioritized issue summary
- API, PostgreSQL, Redis, Celery Beat, worker-queue, migration, and encryption checks
- Queue/backlog age and depth projections with explicit unavailable states
- Storage growth and retained-history estimates
- Recovery archive, verification, drill, quarantine, and last-operation status
- On-demand bounded diagnostics with a correlation ID and safe failure details

API calls:

- `GET /operations/overview`
- `GET /operations/runs`
- `GET /operations/diagnostics`

### `AuditLogsPage`

UI elements:

- Action filter input
- Actor user ID filter input
- Log table columns:
  - time
  - action
  - resource
  - actor
  - status
- Pagination controls (`Prev/Next`)
- Export button:
  - downloads filtered logs as JSON
  - surfaces truncation message when backend export limit is hit

API calls:

- `GET /audit-logs?page=<n>&page_size=50&action=<...>&actor_user_id=<...>`
- `GET /audit-logs/export?action=<...>&actor_user_id=<...>&limit=10000`

## Hook and Guard Components

### `useCurrentUser`

- Query key: `['auth', 'me', sessionVersion]`
- API call: `GET /auth/me`
- Stale time: `60000`

### `useDebouncedValue`

- Generic debounce hook
- Default delay: `400`ms

### `ProtectedRoute`

- Resolves `/auth/me`, redirects to `/login` on `401`, and renders the backend's
  account-access error on `403`.

### `PermissionRoute`

- Waits for `/auth/me` resolution.
- Checks the current access envelope with the same wildcard and read-from-write
  implication rules as the backend IAM model.
- Fails closed with an actionable permission state when required grants are
  absent; it never treats a legacy role label as authorization.

## Complete Frontend -> Backend Call Matrix

| File | Method | Endpoint |
|---|---|---|
| `hooks/useCurrentUser.ts` | `GET` | `/auth/me` |
| `pages/LoginPage.tsx` | `POST` | `/auth/login` |
| `pages/UsersPage.tsx` | `GET` | `/users/directory` and `/users/{id}` |
| `pages/UsersPage.tsx` | `POST` | `/users` |
| `pages/UsersPage.tsx` | `PATCH` | `/users/{id}` |
| `pages/UsersPage.tsx` | `POST` | `/users/{id}/mfa/reset` |
| `pages/OperationsPage.tsx` | `GET` | `/operations/overview` |
| `pages/OperationsPage.tsx` | `GET` | `/operations/runs` |
| `pages/OperationsPage.tsx` | `GET` | `/operations/diagnostics` |
| `pages/accessGovernanceApi.ts` | `GET` | `/iam/permissions`, `/iam/roles`, and `/iam/groups` |
| `pages/accessGovernanceApi.ts` | `POST`, `PATCH`, `DELETE` | custom IAM roles and groups, group members, and group-role assignments |
| `pages/accessGovernanceApi.ts` | `GET` | `/iam/data-policies`, `/iam/service-accounts`, `/iam/access-reviews`, `/iam/elevations`, and `/iam/action-approvals` |
| `pages/accessGovernanceApi.ts` | `POST`, `PATCH`, `PUT` | handling-label and data-policy mode mutations |
| `pages/AccountPage.tsx` | `POST` | `/auth/change-password` |
| `pages/ExportPage.tsx` | `GET` | `/exports/capabilities` |
| `pages/ExportPage.tsx` | `POST` | `/exports/preview` |
| `pages/ExportPage.tsx` | `POST` | `/exports` |
| `pages/useReportingController.ts` | `GET` | `/reports/capabilities` |
| `pages/useReportingController.ts` | `POST` | `/reports/preview` |
| `pages/useReportingController.ts` | `GET`, `POST` | `/reports` |
| `pages/useReportingController.ts` | `GET`, `DELETE` | `/reports/{report_id}` |
| `pages/useReportingController.ts` | `POST` | `/reports/{report_id}/retry` |
| `pages/useReportingController.ts` | `GET` | `/reports/{report_id}/download` |
| `pages/useReportingController.ts` | `GET`, `POST` | `/reports/templates` |
| `pages/useReportingController.ts` | `PUT`, `DELETE` | `/reports/templates/{template_id}` |
| `pages/useReportingController.ts` | `POST` | `/reports/templates/{template_id}/clone` |
| `pages/useReportingController.ts` | `GET`, `POST` | `/reports/schedules` |
| `pages/useReportingController.ts` | `PUT`, `DELETE` | `/reports/schedules/{schedule_id}` |
| `pages/useReportingController.ts` | `POST` | `/reports/schedules/{schedule_id}/run` |
| `pages/NotificationsPage.tsx` | `GET` | `/feeds` |
| `pages/NotificationsPage.tsx` | `GET` | `/notifications/template-variables` |
| `pages/NotificationsPage.tsx` | `GET` | `/notifications/webhooks` |
| `pages/NotificationsPage.tsx` | `POST` | `/notifications/webhooks` |
| `pages/NotificationsPage.tsx` | `PATCH` | `/notifications/webhooks/{id}` |
| `pages/NotificationsPage.tsx` | `DELETE` | `/notifications/webhooks/{id}` |
| `pages/NotificationsPage.tsx` | `POST` | `/notifications/webhooks/test` |
| `pages/NotificationsPage.tsx` | `GET` | `/notifications/webhooks/{id}/deliveries` |
| `pages/NotificationsPage.tsx` | `POST` | `/notifications/webhooks/{id}/deliveries/{delivery_id}/retry` |
| `pages/IntegrationsSettingsPage.tsx` | `GET` | `/integrations/connectors` |
| `pages/IntegrationsSettingsPage.tsx` | `GET` | `/integrations/smtp/hooks` |
| `pages/IntegrationsSettingsPage.tsx` | `POST` | `/integrations/smtp/hooks` |
| `pages/IntegrationsSettingsPage.tsx` | `PATCH` | `/integrations/smtp/hooks/{hook_id}` |
| `pages/IntegrationsSettingsPage.tsx` | `DELETE` | `/integrations/smtp/hooks/{hook_id}` |
| `pages/IntegrationsSettingsPage.tsx` | `GET` | `/integrations/smtp/template-defaults` |
| `pages/IntegrationsSettingsPage.tsx` | `GET` | `/integrations/smtp/analytics` |
| `pages/IntegrationsSettingsPage.tsx` | `GET` | `/integrations/smtp/hooks/{hook_id}/deliveries` |
| `pages/IntegrationsSettingsPage.tsx` | `GET` | `/integrations/smtp/hooks/{hook_id}/test-runs` |
| `pages/IntegrationsSettingsPage.tsx` | `POST` | `/integrations/smtp/hooks/{hook_id}/deliveries/{delivery_id}/replay` |
| `pages/IntegrationsSettingsPage.tsx` | `POST` | `/integrations/smtp/hooks/test` |
| `pages/IntegrationsSettingsPage.tsx` | `GET` | `/feeds` |
| `pages/IntegrationsSettingsPage.tsx` | `GET` | `/notifications/template-variables` |
| `pages/TaggingSettingsPage.tsx` | `GET` | `/feeds` |
| `pages/TaggingSettingsPage.tsx` | `GET` | `/tagging/settings` |
| `pages/TaggingSettingsPage.tsx` | `PUT` | `/tagging/settings` |
| `pages/TaggingSettingsPage.tsx` | `POST` | `/tagging/rules` |
| `pages/TaggingSettingsPage.tsx` | `PATCH` | `/tagging/rules/{id}` |
| `pages/TaggingSettingsPage.tsx` | `DELETE` | `/tagging/rules/{id}` |
| `pages/TaggingSettingsPage.tsx` | `POST` | `/tagging/rules/preview` |
| `pages/TaggingSettingsPage.tsx` | `POST` | `/tagging/reapply` |
| `pages/TokensPage.tsx` | `GET` | `/tokens` |
| `pages/TokensPage.tsx` | `POST` | `/tokens` |
| `pages/TokensPage.tsx` | `DELETE` | `/tokens/{id}` |
| `pages/AuditLogsPage.tsx` | `GET` | `/audit-logs` |
| `pages/AuditLogsPage.tsx` | `GET` | `/audit-logs/export` |
| `pages/FeedsPage.tsx` | `GET` | `/feeds` |
| `pages/FeedsPage.tsx` | `POST` | `/feeds/metadata` |
| `pages/FeedsPage.tsx` | `POST` | `/feeds` |
| `pages/FeedsPage.tsx` | `PATCH` | `/feeds/{id}` |
| `pages/FeedsPage.tsx` | `POST` | `/feeds/{id}/refresh` |
| `pages/FeedsPage.tsx` | `POST` | `/feeds/import` |
| `pages/FeedsPage.tsx` | `GET` | `/feeds/export` |
| `pages/AlertsPage.tsx` | `GET` | `/alerts` |
| `pages/AlertsPage.tsx` | `POST` | `/alerts/preview` |
| `pages/AlertsPage.tsx` | `POST` | `/alerts` |
| `pages/AlertsPage.tsx` | `PATCH` | `/alerts/{id}` |
| `pages/AlertsPage.tsx` | `DELETE` | `/alerts/{id}` |
| `pages/useAlertOccurrencesController.ts` | `GET` | `/alerts/occurrences` |
| `pages/useAlertOccurrencesController.ts` | `GET` | `/alerts/occurrences/{id}` and activity |
| `pages/useAlertOccurrencesController.ts` | `POST` | occurrence lifecycle, snooze, and bulk actions |
| `pages/useAlertOccurrencesController.ts` | `POST` | `/alerts/occurrences/reconciliation/preview` and `/apply` |
| `pages/useAlertOperationsController.ts` | `GET` | `/alerts/occurrences/metrics` |
| `pages/useAlertOperationsController.ts` | `GET` | evaluation list, detail, and activity |
| `pages/useAlertOperationsController.ts` | `POST` | evaluation replay |
| `pages/useInvestigationsPage.ts` | `GET`, `POST` | `/investigations` |
| `pages/useInvestigationDetail.ts` | `GET`, `PATCH` | `/investigations/{id}` |
| `pages/useInvestigationDetail.ts` | `GET` | member candidates, paginated evidence and notes, and investigation activity |
| `pages/useInvestigationDetail.ts` | `POST`, `PATCH`, `DELETE` | members, evidence, and notes |
| `pages/DashboardPage.tsx` | `GET` | `/feeds` |
| `pages/DashboardPage.tsx` | `GET` | `/views` |
| `pages/DashboardPage.tsx` | `GET` | `/tags` |
| `pages/DashboardPage.tsx` | `GET` | `/alerts?include_disabled=false` |
| `pages/DashboardPage.tsx` | `POST` | `/views` |
| `pages/DashboardPage.tsx` | `DELETE` | `/views/{id}` |
| `pages/DashboardPage.tsx` | `POST` | `/items/{id}/read` |
| `pages/DashboardPage.tsx` | `POST` | `/items/{id}/star` |
| `pages/DashboardPage.tsx` | `POST` | `/items/{id}/note` |
| `pages/DashboardPage.tsx` | `GET` | `/items` |
| `pages/DashboardPage.tsx` | `GET` | `/alerts/matches` |
| `pages/DashboardPage.tsx` | `GET` | `/items/{id}` |
| `pages/StatsPage.tsx` | `GET` | `/feeds` |
| `pages/StatsPage.tsx` | `GET` | `/stats/overview` |
| `pages/StatsPage.tsx` | `GET` | `/stats/feed-timeseries` |
| `pages/StatsPage.tsx` | `GET` | `/stats/activity-heatmap` |
| `pages/StatsPage.tsx` | `GET` | `/stats/signal-radar` |
