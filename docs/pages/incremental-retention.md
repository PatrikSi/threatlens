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
skips locked provider and approval receipt rows rather than waiting in an order
that could deadlock a concurrent writer.

Cancelling a lifecycle run stops subsequent batches. It does not recreate child
history already removed or release a partially drained parent for reuse. A later
run under an enabled retention policy can finish that parent. Permission
provenance and unresolved provider-side-effect receipts are not removed by the
append-only child cleanup helper. Oversized bundles outside the supported
incremental families remain protected by the dependency budget.

Validation covers a 40,004-event workload, committed progress, rollback before
commit, an event writer already waiting when a claim is published, a concurrent
report reference that prevents pruning, and rejection of late reports and parent
reactivation. All concurrency cases use disposable PostgreSQL transactions.
