# Browser read authority retention

Status: implemented; final test and independent review evidence is tracked in the
[November review](../reviews/2026-11-15-comprehensive-review.md).

## Context

The API remains the authorization boundary. Within one browser session, private
query results and copied operation intents must also stop being presented after
an observed 401, 403 or 404. A later ordinary outage must preserve that withdrawal.
Cached sibling pages, late completions and dashboard list-only actions make query
garbage collection alone an insufficient lifetime for this history.

The original per-QueryClient strong map retained one scope and its closures for
every visited resource until session replacement, even after cache expiration.
An actual Node garbage-collection probe reproduced this retention. Its impact on
long-running browser memory usage was not measured.

## Decision

Use weak references for exact resource scopes, with a per-client conservative
withdrawal summary. The summary allocates at most 64 KiB on the first withdrawal.
It records four bits per resource key and never clears them during that client
lifetime. A possible match requires new verification; it never establishes access.

A live scope's exact state takes precedence. If an exact scope has been collected,
the read-only withdrawal getter consults the summary without creating a scope.
Recreating a possibly withdrawn scope starts denied with a fresh nonzero epoch.
Every withdrawal and such recreation uses the same per-client monotonically
increasing allocator. Thus old envelopes, including manually cloned and query
structurally shared copies, cannot match a recreated epoch. A stale request cannot
authorize it. Counter exhaustion permanently fails closed rather than wrapping.

Successful reads authorize only their captured epoch. They do not clear summary
bits, because that would erase another colliding key's history. Writes preserve
their originating epoch and continue to require current canonical read admission.

Finalization removes only the matching dead weak reference. A lookup additionally
examines at most four table entries for dead references, limiting synchronous
cleanup work. No timer, fixed-size eviction or successful mutation can discard a
known withdrawal. The store does not capture its owning QueryClient; clients stay
in a WeakMap and remain isolated across session replacement.

## Consequences and validation

Live queries, mounted hooks and in-flight reads keep their exact scope alive.
Their memory remains proportional to active application state. Weak table/key
reclamation depends on garbage collection, so the 64 KiB bound applies to the
historical summary, not total browser memory.

Summary collisions, or a collected previously recovered scope, can require an
additional canonical read. A dashboard row's automatic mark-read can remain
paused until opening the detail and successfully reading it. Filter saturation
can extend this conservative behavior to every newly created scope. It cannot
release access, and session replacement discards the summary.

Deterministic collection controls cover a first detail withdrawal without a
successful envelope, stale cloned epochs, read-only lookup, delayed finalizers,
client separation and conservative collision recovery. A separate actual Node
GC probe checks collection with a live session store; it is not a browser or
throughput measurement. Existing real-cache UI controls and the full frontend
suite remain required to verify caller compatibility.

Pure weak ownership was rejected: a dashboard list can outlive the failed detail
query whose withdrawal it consults. Pinning only original envelopes was also
insufficient because structural sharing and manual writers can clone them.
