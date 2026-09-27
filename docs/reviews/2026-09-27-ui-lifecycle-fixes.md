# UI lifecycle corrections — 2026-09-27

This implements NR04–NR08 and NR10 from the post-fix review.

- Hunt scheduling guards changes of filter, cursor, team and route with the shared
  discard dialog. Polling keeps an edited row mounted when it leaves the current
  page, with protected actions paused and an explicit reload/discard path. The
  draft retains its schedule and assessment baseline. Access denial hides the row.
- Operations distinguishes explicit 401/403/404 from transient outages across
  overview, worker topology, trends and activity. Explicit denial hides cached
  snapshots and recovery controls, displays the server reason and disables
  diagnostics. A later temporary failure does not revive denied snapshots;
  successful deliberate refresh restores the workspace. In-flight diagnostic
  results are ignored if access has since been denied.
- Compact article cards always expose a named detail action with a usable touch
  target. Mobile inspectors use the shared dialog stack for focus, background
  isolation, nested confirmations and Escape handling. Desktop details remain
  inline. Hunt review notes survive transitions between presentations.
- Operations charts remain inside constrained grid columns; horizontal scrolling
  belongs to the chart/table wrappers. Capacity selection stays inside the mobile
  viewport. Mobile AI, ingestion and audit record collections have labelled group
  semantics instead of unsupported labels on generic elements.

## Verification

- 37 focused DOM/unit cases passed, including real Router/Query hunt transitions,
  preserved baseline versions, poll removal, permission loss and shared dialog
  stacking. The Operations component suite checks denial from each dataset.
- 27 browser cases passed across Chromium, Firefox and WebKit using the actual app
  with intercepted synthetic APIs. They cover guarded hunt filtering, background
  disappearance, navigation, compact article opening, focus return, nested review
  discard, resizing with a draft, mobile chart bounds and keyboard scrolling,
  200 → 503 → 403 → 503 → 200 Operations transitions, report Markdown, and
  whole-page mobile Axe checks for AI statistics, ingestion and audit records.
- TypeScript application/browser checks and targeted ESLint passed.

These are isolated browser fixtures, not live-provider, real-IdP or manual
screen-reader qualification. No live application data or containers were changed.

## Focused follow-up and independent review

The hunt queue now keeps shared-view setup and membership on Team details, with
correct selected-navigation semantics. Moving away from an unfinished shared-view
copy prompts before discarding it. Ingestion day/feed scope lives in the URL and
survives Back navigation, section changes and reload. Missing selected feeds stay
in the explicit scope with a warning instead of silently switching to all feeds.

Independent review identified local indicator-dialog state as an additional
resize concern even though hunt notes are already stored in the session cache.
An open inspector now keeps its initial presentation, and responsive RSS page-size
changes wait until inspectors close. This prevents the enclosing list query from
briefly removing the edited row during a breakpoint change. The browser regression
keeps a locally held nested discard dialog open through resizing. Retained hunt
rows also disable reminder acknowledgement while their actions are paused.

Follow-up validation: 26 team/statistics DOM cases; 42 dashboard/schedule DOM
cases; 15 cross-browser AI configuration/ingestion cases; and the strengthened
nested-resize case in Chromium. The coordinating final suite covers the final
combined source and remaining browser engines. TypeScript and targeted ESLint
passed. No live stack changes were made.
