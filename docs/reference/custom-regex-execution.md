# Custom regular-expression execution

User-authored tagging regexes retain Python `re` syntax, including lookaround,
backreferences, and Unicode matching. They are never compiled or searched inside
the API or Celery process. Each item batches eligible regex rules into a disposable
Python interpreter; literal `contains` rules stay in the calling process.

The evaluator has no application imports, inherited application environment, or
open application file descriptors. Python starts with `-I -S`; inputs use JSON,
never executable code. These controls isolate resource use, not operating-system
permissions. The fixed evaluator program remains trusted code.

| Budget | Behavior |
| --- | --- |
| 4,000 pattern characters | Existing authoring limit; compilation is also isolated. |
| 2,000,000 selected text characters | Excess text is rejected, never silently truncated and matched. The bound includes the distinct fields used by the item batch. |
| 200 regex rules per item | Excess rules return `budget_exhausted`; contains rules remain independent. |
| 50 ms per rule | A process-local timer covers compilation and all selected fields together. No partial matches escape a timed-out rule. |
| 400 ms per item batch | Expensive rules cannot each restart the full batch allowance. |
| 1 s parent wait | A stalled child is killed and reaped before the caller continues. |
| 256 MiB address space / 2 s process CPU | OS resource limits protect the parent from compiler/matcher memory and CPU exhaustion. Core dumps are disabled. |
| 200 preview items / 3 s evaluation | Preview reads selected SQL projections incrementally, counts accessible candidates separately, and discloses partial results. |
| 25 returned preview matches | Only compact display metadata survives matching. Titles and feed names display at most 500 and 255 characters respectively, followed by an ellipsis when abbreviated. |
| 25 current tags per preview match / 64 characters per tag | SQL bounds tag rows and text before materialization. The preview discloses omitted or abbreviated tags; these display limits do not change rule matching. |

The supported deployment is Linux. Failure to start or enforce the isolated
evaluator fails closed for custom regex matches. Parent timeout cleanup and OS
process creation can add scheduling overhead; these are operational budgets,
not real-time guarantees. No persistent subprocess pool or background regex
computation remains after evaluation.

Preview warnings distinguish an invalid expression, a rule timeout, a batch
budget, excess input, exhausted resources, and an unavailable/stalled evaluator.
The scan includes up to 200 recent accessible items, including items excluded by
the rule's feed, category, or confidence conditions. Excluded items cannot trigger
an input-size failure; only selected matching fields on eligible items are checked.
At ingestion, an unresolved eligible regex preserves the previous automatic-tag
snapshot and records independent pending tagging work on the item. Classification
and IOC extraction can complete without falsely acknowledging that tag evaluation.
Structured warnings contain only the rule ID and error code, never publisher text
or the pattern. Manual tags remain analyst-owned.

Beat runs `app.tasks.feed_tasks.repair_pending_item_tags` every minute, processing
at most 50 due items. Unavailable/stalled workers and process resource failures
retry after 60, 120, 240 and 480 seconds; five failed evaluations exhaust automatic
retry. Rule timeouts, aggregate/input budgets and invalid patterns immediately
require attention. The item remains explicitly incomplete after exhaustion.
`GET /tagging/settings` reports handling-access-filtered pending/retrying/attention
counts and known error messages, which the tagging settings page displays.

Correct the rule or source and use Reapply over the affected time window and
limit to retry items requiring attention. Manual reapply resets the retry allowance;
a complete evaluation clears the pending state and removes tags for deliberately
disabled, deleted or nonmatching rules. Both paths lock and reload the current item
and article. Manual reapply pages IDs with a stable date/ID cursor and commits each
item separately; automatic repair skips busy items whose durable intent remains.
A worker crash rolls back only its current item, leaving pending work recoverable.
Existing classifications retained after lifecycle content erasure remain retained.

Tests exercise catastrophic backtracking, a healthy rule after a timed-out one,
shared budgets, compiler nesting/overflow, exact Python matching semantics,
environment isolation, and real parent termination with child reaping. The
implementation uses the documented [subprocess timeout behavior](https://docs.python.org/3/library/subprocess.html#subprocess.run),
[Unix resource limits](https://docs.python.org/3/library/resource.html), and
[process timers](https://docs.python.org/3/library/signal.html#signal.setitimer).
