# Outbound request budgets

AI, feed, and article HTTP requests use one monotonic budget across DNS, TCP
connection attempts, TLS, sending, response headers, redirects, and the response
body. Feed requests default to 60 seconds (`FEED_TOTAL_TIMEOUT_SECONDS`), article
requests to 90 seconds (`ARTICLE_TOTAL_TIMEOUT_SECONDS`). Both settings accept a
positive number up to 300 seconds. Article fallback URLs share the same budget.
The configured AI provider `request_timeout_seconds` is also its total budget.
Existing connect/read settings remain shorter limits for individual operations.

The synchronous pinned transport recomputes the remaining timeout before every
socket read, including partial response headers. Socket writes use `sendall` so
partial sends cannot restart the timeout. The original hostname remains the TLS
server name; the socket connects only to a vetted IP. Each redirect is validated
again. By default, only global unicast IPs are allowed, including when obtained
from DNS; shared address space such as `100.64.0.0/10` requires the corresponding
explicit private-network opt-in.

Domain-slot acquisition checks the same budget while waiting and releases any
acquired slot when it expires. Redis coordination operations and cleanup retain
their separately configured socket timeouts; a blocking Redis operation can add
that bounded interval to elapsed wall time. Local scheduling and cleanup are not
real-time operations, so the HTTP deadline is not a promise of millisecond-exact
worker completion.

The operating-system DNS resolver cannot be canceled safely. Only DNS resolution
may finish after its caller times out; a maximum of eight daemon resolver threads
per process can remain in flight, with no unbounded submission queue. Saturated
resolution capacity fails within the caller's remaining budget. No HTTP request
runs in a detached thread. Provider policy and task locks remain held until the
synchronous provider call has stopped, then normal error settlement releases them.

AI responses have a decoded and encoded byte cap of 2,000,000 bytes by default
(`AI_RESPONSE_MAX_BYTES`, allowed range 1,024–16,000,000). Feed and article requests
use their existing byte caps for both encoded and decoded bodies. This applies
before JSON parsing and includes HTTP error responses. The client advertises
identity, gzip and zlib-wrapped deflate; unsupported or stacked encodings,
truncated compressed streams and trailing compressed data are rejected. Decoding
uses bounded output allocations, so a small gzip body cannot expand into an
unbounded temporary HTTPX buffer. Socket reads still have the transport's bounded
chunk allocation in addition to the configured body budget.

A provider timeout after entering the HTTP path is ambiguous and non-retryable.
Its durable attempt receipt prevents replay after worker redelivery. An oversized
provider response is a received, non-retryable failure with its HTTP status and no
retained oversized diagnostics. Normal bounded provider error responses retain
the existing status-based retry policy. Raising a byte or time limit does not make
an ambiguous provider attempt safe to repeat.

# Classification recovery after source persistence

Each item retains a required and completed classification revision. Feed title or
summary changes and stored article-text changes advance the requirement in the
same transaction as the source write. SQL performs the increment, so a writer
that loaded an older item cannot overwrite a newer revision. The classifier
acknowledges the revision while holding the existing Item processing lock, in the
same commit as classification and durable alert-evaluation intent. Skipped work
and failed broker publication never acknowledge a revision.

The periodic unclassified-item dispatcher repairs both missing classification
rows and pending revisions. Each candidate branch and publication pass use
`DISPATCH_UNCLASSIFIED_ITEMS_BATCH_SIZE`; duplicate candidates are removed.
Successful duplicate classifier deliveries remain idempotent by source hash and
rules version. Repeated broker failures leave the durable requirement available
for the next repair pass.

Migration `0086_classification_versions` compares existing classification hashes
with the current title, summary and article text in PostgreSQL, without returning
article bodies to Python. Already-current results are acknowledged; stale or
missing results remain pending. Classifications retained after an explicit
lifecycle content purge are preserved. The one-time reconciliation scans retained
source content and should be included in the maintenance window for a large
catalog. Stop ingestion and classification workers for the migration and replace
them together: older worker code cannot advance the new source revision fields.
