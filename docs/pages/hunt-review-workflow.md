# Team hunt review workflow

Open **Teams → select a team → Team hunt queue**. The queue now defaults to oldest
source evidence first, so new ingestion does not continually bury older reviews.
The API retains its historical newest-first default for existing clients; explicit
`order=oldest`, `order=newest` or `order=due` selects a stable keyset order.

Use priority and **Overdue reviews only** with the existing status and ownership
filters. Due ordering puts unset deadlines last. Overdue means the deadline has
passed and the suggestion remains pending or stale; accepted/rejected suggestions
are excluded. Filters are retained in the URL, and a page cursor is valid only for
its original filters and order. Changed filters return to the first page. Access
filtering can produce an empty page with more results; use Next page to continue.

## Coordinate review without duplicating investigation assignment

A pending suggestion can have a low, normal, high or urgent review priority and an
optional review deadline. These fields have their own version and do not change
claims, approval, execution status or the evidence revision. The editor presents
local time; the API requires a timezone and stores an absolute timestamp.

The existing owner or a team manager can change a claimed suggestion's schedule.
An unclaimed suggestion or a claim belonging to a former member can be scheduled
by an eligible team editor. Every mutation rechecks current team membership,
credential scopes, captured/current evidence access and assessment version under
the existing assessment lock order. Conflicting edits return an actionable `409`.
A background refresh does not silently advance the draft's version.

Scheduling stale evidence is allowed so it can be prioritized for regeneration;
reviewing/approving stale evidence remains blocked. Accepted/rejected suggestions
and promoted hunts do not accept new review deadlines. Investigations remain the
place for execution ownership, assignees, outcomes and evidence.

## Durable in-app reminders

A worker sweep runs every minute, locks at most 100 due eligible schedules with
`SKIP LOCKED`, and records one reminder per explicitly saved schedule revision.
Multiple workers, redelivery and repeated sweeps cannot create duplicate reminders.
Deleted/replaced hunt IDs and already-reviewed suggestions are excluded in SQL,
so obsolete deadlines cannot monopolize the scan.

The queue displays an overdue reminder until the current owner or a manager
acknowledges it. Acknowledgements are idempotent and audited; they do not complete
the review, clear the deadline, accept a hunt or launch an investigation. Changing
the schedule starts a new reminder revision. Reminders are local application
state: this release does not send email, webhook or SIEM actions from a deadline.
Evidence permissions are rechecked when displaying a reminder, and losing access
hides cached queue content.

## Shared saved filters

Team managers can save up to 30 named team hunt views. Each stores status,
ownership, order, priority and overdue filters, never evidence or a list of source
IDs. The `mine` ownership filter means the user currently opening the view.
Members with team read access can apply a view; managers with team write access
can create, update or remove it. Writes retain optimistic versions and stable
creation IDs for safe retries. Deleting a preset preserves hunts and reviews.

## API and migration

- `GET /teams/{team_id}/hunts`: optional `order`, `priority`, `overdue`; existing
  `status`, `ownership`, bounded `limit` and cursor remain supported.
- `PATCH /teams/{team_id}/hunts/{assessment_id}/{hunt_id}/schedule`: expected
  schedule and assessment versions, priority and nullable `due_at`.
- `POST /teams/{team_id}/hunts/{assessment_id}/{hunt_id}/reminder-acknowledgement`:
  expected schedule and assessment versions.
- `GET /teams/{team_id}/hunts/views`, `PUT`/`DELETE .../views/{view_id}`: shared
  filter presets with optimistic versions and a 30-view team bound.

Migration `0120_hunt_review_workflow` preserves all existing claims and starts them
without deadlines or reminders. Downgrades refuse to silently discard retained
review schedules or shared presets; archive and explicitly remove them first.
