# Frontend Reference

This page documents app routes, components, constants, UI elements, and every backend API call issued by the web client.

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
  - `/settings` -> `SettingsLayout`
    - index -> `SettingsOverviewPage`
    - `/settings/account` -> `AccountPage`
    - `/settings/tokens` -> `TokensPage`
    - `/settings/users` -> admin-only `UsersPage`
    - `/settings/audit-logs` -> admin-only `AuditLogsPage`

## Shared Client Behavior

### API client (`web/src/api/client.ts`)

- Adds `Content-Type: application/json` for requests.
- Adds `Authorization: Bearer <token>` when `auth=true` and token exists.
- Uses `AbortController` timeout (`REQUEST_TIMEOUT_MS`, default `15000`).
- Throws textual API error body when `response.ok` is false.
- Returns `undefined` for `204` responses.

### Browser storage keys

- Auth token: `threatlens.token`
- Theme mode: `threatlens.theme`
- Dashboard window state: `threatlens.dashboard.windows.v2`

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

Snap modes:

- `free`, `full`, `left`, `right`, `top_left`, `top_right`, `bottom_left`, `bottom_right`

Dashboard constants:

- `DASHBOARD_VIEW_VERSION = 3`
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

RSS filter values:

- Time range: `all|24h|7d|30d|custom`
- Read status: `all|read|unread`
- Star status: `all|starred|unstarred`
- Sort: `published_at_desc|published_at_asc|first_seen_desc|first_seen_asc`
- View mode: `expanded|compact` (compact default)

Alerts filter values (dashboard window):

- Time range: `all|24h|7d|30d|custom`
- Sort: `published_at_desc|published_at_asc|first_seen_desc|first_seen_asc`
- View mode: `expanded|compact`

HTML sanitation values for rich article rendering:

- Allowed tags: `a,b,blockquote,br,code,em,h1,h2,h3,h4,h5,h6,hr,i,li,ol,p,pre,strong,u,ul`
- Allowed `href` protocols: `http`, `https`, `mailto`

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
- `GET /items?...`
- `GET /alerts/matches?...`
- `GET /items/{id}`

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

- Create form: name, category, comma-separated keywords
- Include-disabled toggle
- Grouped cards by category
- Enable/disable and delete actions

API calls:

- `GET /alerts?include_disabled=<bool>`
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

### `SettingsLayout` and `SettingsOverviewPage`

UI elements:

- Role-aware settings nav
- Current role badge
- Role capability cards (Admin, Analyst, Viewer)

API calls:

- `GET /auth/me` (via `useCurrentUser`)

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

- Query key: `['auth', 'me', token]`
- Enabled only when token exists
- API call: `GET /auth/me`
- Stale time: `60000`

### `useDebouncedValue`

- Generic debounce hook
- Default delay: `400`ms

### `ProtectedRoute`

- Redirects to `/login` when auth token is missing.

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
