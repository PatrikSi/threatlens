# Incremental history cleanup

Lifecycle retention can drain expired history whose dependent rows exceed the
10,000-row transaction budget. AI task events, integration attempts, alert
evaluation activity and matches, and action-approval operation receipts use this
path. Existing report references, unresolved provider attempts, active delivery
retries, and other retention protections still prevent cleanup from starting.

Each parent has a durable `lifecycle_pruning_records` entry containing its dataset,
retention cutoff, start/update times, and number of children removed. Parent and
child changes commit together. A crash before commit rolls back that batch;
after commit, the next eligible scan resumes from the remaining child records.
The parent is deleted by the normal retention path once its remaining dependent
rows fit the budget. Deleting it also removes the temporary progress entry.
Lifecycle run details retain `children_pruned` and `pruning_parents_started` totals.

A pruning claim is a one-way decision to remove expired history. Database guards
reject late references and reactivation while that claim exists. An insert that
started before the claim committed rechecks it after waiting for the parent
lock. Cleanup also rechecks retained references after locking the source. It
skips busy child and receipt rows before final parent deletion, avoiding lock
waits that could deadlock a concurrent writer.

Cancelling a lifecycle run stops subsequent batches. It does not recreate child
history already removed or release a partially drained parent for reuse. A later
run under an enabled retention policy can finish that parent. Extending retention
pauses cleanup if that parent is no longer expired; its durable claim remains
until the parent becomes eligible again. Permission
provenance and unresolved provider-side-effect receipts are not removed by the
append-only child cleanup helper. Oversized bundles outside the supported
incremental families remain protected by the dependency budget. Bundles whose
retained references alone exceed that budget do not start incremental pruning.
Preview counts include oversized parents with actual drainable child history.

Validation covers a 40,004-event workload, committed progress, rollback before
commit, an event writer already waiting when a claim is published, a concurrent
report reference that prevents pruning, and rejection of late reports and parent
reactivation. Delivery attempts and approval receipts are checked across repeated
bounded batches, and final deletion skips an event writer waiting on another
locked source. All concurrency cases use disposable PostgreSQL transactions.

## Permission-bearing history

Expired audit logs, alert occurrence metrics, and integration delivery metrics
use the same transaction budget to drain normalized source and label records.
Before removing any permission provenance, cleanup marks the parent with
`retention_pruning_started_at` and creates its durable claim in one transaction.
That parent immediately leaves audit list/export responses and metric analytics,
including when handling policy is disabled. Audit projection refreshes and locks
the parent before reading its labels, so an object loaded before the claim cannot
reappear after its labels have been removed.

Ordinary retained audit and metric provenance remains immutable. Database guards
permit incremental label deletion only for a hidden parent with a matching claim.
Feed relabels skip claimed history, and late cohort updates or references are
rejected. Delayed metric rollups skip a bucket already claimed for cleanup; they
do not recreate visible counts for that expired bucket. Empty cohorts count
against the same budget and are deleted only after all of their children are gone.
Final parent deletion skips dependent rows held by concurrent writers.

Partially pruned permission history cannot be made readable again. Migration
0094 rejects downgrade while any such parent remains; finish the eligible
retention cleanup before running an older binary. Cancelling a run or extending
retention does not restore already deleted labels. If a later policy no longer
selects the parent, it remains hidden until cleanup becomes eligible again.

Regression coverage includes 20,002 audit provenance rows drained across commits,
cohort and child budgets, every handling-policy mode, stale audit objects,
rollback, a waiting feed-taint writer, and a cohort writer holding its child lock
while waiting for the parent.
