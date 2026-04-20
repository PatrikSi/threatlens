# Frontend Reference

This page documents app routes, components, constants, UI elements, and every backend API call issued by the web client.

Unless noted otherwise, endpoint paths on this page are relative to the published API base `/api/v1`. The schema endpoint remains a separate path at `/api/openapi.json`.

## App Composition (`web/src/App.tsx`)

Providers (outer to inner):

1. `ThemeProvider`
2. `AuthProvider`
3. `QueryClientProvider`

React Query defaults:

- `staleTime: 30000`
- `refetchOnWindowFocus: false`
- `retry: 1`

Route tree:

- `/login` -> `LoginPage`
- `/` -> `ProtectedRoute` + `AppShell`
  - index -> `DashboardPage`
  - `/alerts` -> `AlertsPage`
  - `/feeds` -> `FeedsPage`
  - `/stats` -> `StatsPage`
  - `/ai` -> redirect to `/settings/ai`
  - `/settings` -> `SettingsLayout`
    - index -> redirect to `/settings/account`
    - `/settings/account` -> `AccountPage`
    - `/settings/notifications` -> `NotificationsPage`
    - `/settings/ai` -> admin-only `AiSettingsPage` (shown only when `features.ai_enabled`)
    - `/settings/tagging` -> admin-only `TaggingSettingsPage`
    - `/settings/tokens` -> `TokensPage`
    - `/settings/users` -> admin-only `UsersPage`
    - `/settings/audit-logs` -> admin-only `AuditLogsPage`

## Shared Client Behavior

### API client (`web/src/api/client.ts`)

- Production fallback base URL is `/api/v1`; development fallback base URL is `http(s)://<host>:8000/v1`.
- `VITE_API_BASE_URL` overrides the fallback, and the shipped compose stack passes it from `WEB_VITE_API_BASE_URL` (default `/api/v1`).
- Adds `Content-Type: application/json` for requests.
- Sends browser credentials (`credentials: include`) for cookie-based session auth.
- Adds CSRF header (`x-csrf-token` by default) on mutating requests when `auth=true`.
- Uses `AbortController` timeout (`REQUEST_TIMEOUT_MS`, default `15000`).
- Throws textual API error body when `response.ok` is false.
- Returns `undefined` for `204` responses.
- `LoginPage` and self-registration calls pass `auth=false`; after login the app relies on the server-set session cookies rather than persisting the returned JWT.

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
- `dark-emerald`
- `dark-cobalt`
- `dark-slate`
- `dark-carbon`
- `dark-amber`
- `dark-crimson`
- `dark-violet`
- `dark-ice`
- `dark-forest`
- `dark-solarized`

Root class behavior:

- Adds `dark` class for all non-light modes.
- Adds `theme-light` or `theme-<mode>` class to `<html>`.

## App Shell (`AppShell.tsx`)

Top navigation links:

- `Dashboard`
- `Alerts`
- `Feeds`
- `Stats`
- `Settings`

Top-right controls:

- Current user badge (`email (role)`)
- Theme dropdown
- `Logout` button

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

- Create/edit form: name, category, comma-separated keywords
- Current match preview while typing
- Include-disabled toggle
- Grouped cards by category
- Edit, enable/disable, and delete actions

API calls:

- `GET /alerts?include_disabled=<bool>`
- `POST /alerts/preview`
- `POST /alerts`
- `PATCH /alerts/{id}`
- `DELETE /alerts/{id}`

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

### `SettingsLayout`

UI elements:

- Role-aware settings nav
- Current role badge
- Settings nav entries:
  - `Account`
  - `API Tokens`
  - `Notifications`
  - admin-only `AI`, `Tagging`, `Users`, `Audit Logs`

API calls:

- `GET /auth/me` (via `useCurrentUser`)

### `NotificationsPage`

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
- Change password form

API calls:

- `GET /auth/me`
- `POST /auth/change-password`

### `TokensPage`

UI elements:

- Create token form: name, expiry days, scopes CSV
- Leave scopes blank to get the default read-only scopes; an explicit empty list is rejected by the API
- One-time token reveal panel
- Token inventory
- Admin-only `user_id` filter input
- Revoke button per token

API calls:

- `GET /tokens` (optionally with `?user_id=` for admin)
- `POST /tokens`
- `DELETE /tokens/{id}`

### `UsersPage`

UI elements:

- Create user form: email, password, role, active
- Search users input
- Per-user row editor:
  - role
  - active flag
  - optional password reset

API calls:

- `GET /users`
- `POST /users`
- `PATCH /users/{id}`

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

- Resolves `/auth/me` and redirects to `/login` on `401/403`.

### `RoleRoute`

- Waits for `/auth/me` resolution.
- Redirects to `/` when user role is not in allowed role list.

## Complete Frontend -> Backend Call Matrix

| File | Method | Endpoint |
|---|---|---|
| `hooks/useCurrentUser.ts` | `GET` | `/auth/me` |
| `pages/LoginPage.tsx` | `POST` | `/auth/login` |
| `pages/UsersPage.tsx` | `GET` | `/users` |
| `pages/UsersPage.tsx` | `POST` | `/users` |
| `pages/UsersPage.tsx` | `PATCH` | `/users/{id}` |
| `pages/AccountPage.tsx` | `POST` | `/auth/change-password` |
| `pages/NotificationsPage.tsx` | `GET` | `/feeds` |
| `pages/NotificationsPage.tsx` | `GET` | `/notifications/template-variables` |
| `pages/NotificationsPage.tsx` | `GET` | `/notifications/webhooks` |
| `pages/NotificationsPage.tsx` | `POST` | `/notifications/webhooks` |
| `pages/NotificationsPage.tsx` | `PATCH` | `/notifications/webhooks/{id}` |
| `pages/NotificationsPage.tsx` | `DELETE` | `/notifications/webhooks/{id}` |
| `pages/NotificationsPage.tsx` | `POST` | `/notifications/webhooks/test` |
| `pages/NotificationsPage.tsx` | `GET` | `/notifications/webhooks/{id}/deliveries` |
| `pages/NotificationsPage.tsx` | `POST` | `/notifications/webhooks/{id}/deliveries/{delivery_id}/retry` |
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
