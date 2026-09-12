# AI workflow recovery

ThreatLens records accepted article enrichment, article reprocessing, and daily briefing work in PostgreSQL before publishing it to Celery. Keep the maintenance worker running: `app.tasks.ai_workflow_tasks.dispatch_pending_ai_workflows` runs every 30 seconds on the maintenance queue, independently of the AI worker backlog.

A queued task remains accepted when the broker is unavailable. The API returns its durable run identity, and publication resumes after the broker recovers. Celery inspection alone does not establish that a queued task was lost: messages waiting in Redis may not appear in worker inspection.

## Publication and execution

Each accepted task has an `ai_workflow_dispatches` record containing its task name, immutable arguments, delivery identity, publication claim, and next attempt time. Publication claims expire after 60 seconds. Failed publications use a bounded delay of 15–900 seconds. Published work that has not started becomes eligible for broker reconciliation after one hour.

Recovery inspects both the AI queue lists and Kombu's Redis `unacked` hash, which includes prefetched deliveries. It examines at most 2,000 entries. If that state is unavailable, malformed, or above the limit, ThreatLens waits instead of duplicating an uncertain delivery. A known delivery retains its identity; positively absent deliveries can be published again. These checks target the application's standard Redis transport and priority queues.

Each pass considers at most 50 tasks. At most 100 queued tasks with attempted publications occupy admission slots, including uncertain sends whose broker call failed. Further accepted work remains in PostgreSQL until slots become available. A short database claim serializes admission; no database publication lock is held across the broker call. The worker then atomically claims the logical run, so duplicate messages cannot run its body concurrently.

## Article reprocessing

The accepted selection is fixed in `ai_reprocess_members`. Each parent and article pair has one position, one child identity, and one terminal outcome. Replaying fanout finds the existing child rather than creating another attempt. Progress counts distinct selected articles, so a delivery order such as `A, A, B` cannot complete a two-article parent after only article A finishes.

Cancellation is recorded on the parent before child cancellation proceeds. New fanout observes that intent. Child identities and outcomes survive article deletion and remain until their parent history is removed. If a referenced child history row has disappeared before its outcome was captured, the member becomes `child_history_unavailable`; recovery does not manufacture a new paid operation.

For unfinished legacy work, recovery uses retained source lineage and existing children where available. When duplicate legacy children exist, an already ready child takes precedence. Selection without a retained snapshot is anchored to the original parent creation time. Existing provider selections are retained; changing routing does not silently retarget accepted work.

## Provider capacity and report stages

A provider concurrency or token-budget denial occurs before provider I/O. The worker returns the same logical task to its durable queue with a bounded delay. Article and daily briefing tasks check current settings, cancellation, permissions, source access, and the selected provider again when they resume.

Budget accounting starts with requests admitted while at least one provider workload limit is enabled. When both limits are zero, requests do not create budget reservations. Enabling a limit cannot retroactively reserve earlier requests or account for calls already running without a reservation.

Report completion payloads are stored in `ai_report_stage_artifacts` in the same transaction as successful provider receipts. After a capacity deferral, the report worker retains completed evidence batches and sections and commits the report state, task deferral, and generation lease release together. After a worker crash, an expired report lease can be replaced and the same logical task resumed only when every retained successful operation has its matching saved stage and no uncertain provider receipts remain. Receiptless legacy reports do not gain automatic replay permission. Replayed stages must match the original request fingerprint, report, active task, and current authorization. Changed inputs stop automatic replay rather than issuing another paid request for that stage. Replaying a saved completion does not create a second provider usage event.

The synchronous `POST /ai/daily-brief/generate` endpoint still returns a briefing with HTTP 200 when complete. When provider capacity is unavailable after acceptance, it returns HTTP 202 with `status: "queued"`, `reason`, and `run_id`. Clients should poll the run instead of treating that response as a briefing. The UI uses the existing queue endpoint.

## Interrupted provider calls

Recovery distinguishes queued work from a worker that disappeared during execution. A running task without unsafe provider receipts can receive a replacement delivery under the same logical run; its former delivery is fenced out. Reserved, ambiguous, or successful provider receipts without a replayable saved result do not authorize another provider call. Legacy pending provider work without a receipt also lacks safe replay evidence.

An ambiguous provider outcome can therefore leave a task in an error state requiring the existing provider receipt reconciliation procedure. This is intentional: the application cannot infer whether an external provider processed a request after a connection or worker failure. Normal history retention removes workflow records through their parent foreign keys. Migration downgrade refuses to remove the recovery tables while accepted dispatches or unfinished report artifacts remain.
