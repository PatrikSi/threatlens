# Processing recovery

Open **Settings → System health → Processing** to inspect incomplete article
retrieval, classification, indicator extraction, and tagging. Reading the worklist
requires both `read:operations` and `read:items`; recovery also requires
`write:operations`. Source handling restrictions apply to the worklist and recovery.

The list shows up to 50 accessible work records per page, one per article and
stage. Filter by stage or state, or select a source name to filter by that source.
Each row includes status, age, attempts, next retry, and a safe explanation; stable
identifiers and reason codes are available under **Technical details**. Completed
work is absent from this list. Filters, cursors, and the selected recovery run are
stored in the URL. Browser Back restores earlier scopes.

Select individual eligible records or **Select eligible on this page**, then
**Review selected recovery**. Selection is limited to the current page and clears
when the scope changes. If a refresh changes a selected record's revision or
eligibility, review and select it again. The server rechecks the exact reviewed
revisions and access when accepting the request.

Each review has an idempotency key. If acceptance cannot be confirmed, retrying
inside that review reuses the same key. If you close the review or reload the page,
inspect **Your recovery runs** before creating another request. A conflict requires
a new review of the current worklist. Capacity and permission errors remain visible
with their recovery guidance.

Accepted runs continue after navigation. Open a run from **Your recovery runs** or
return to its URL to inspect progress and individual outcomes. This collection is
owned by the requesting principal; URLs do not grant access to another operator's
runs. If source access or the accepting credential becomes unavailable, the UI
labels restricted run details instead of displaying redacted zero counts as actual
results. Cancellation is separately checked against current permissions.

**Cancel remaining work** stops remaining selected work and preserves processing
results committed before cancellation was accepted. A changed run version requires
reviewing the refreshed run before cancellation is retried. Recovery targets
pipeline stages, not direct retries of AI jobs or notification deliveries; normal
processing can still produce downstream alerts and notifications.

Work and run lists refresh every 15 seconds while visible; a selected active run
refreshes every five seconds. Transient worklist failures retain the last known
rows while pausing recovery actions. Permission failures hide cached affected
details. Session verification preserves selection and review dialogs under the
existing blocking verification surface; account changes retire them and reject
late request completions.
