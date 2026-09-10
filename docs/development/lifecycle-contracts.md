# Architecture and asynchronous lifecycle contracts

Selected dependency boundaries are enforced by
`backend/tests/unit/test_dependency_boundaries.py`. Services and worker modules
must not import HTTP routes or the application composition module. The guard
resolves relative imports and literal dynamic imports as well as ordinary imports.
Feed-runner dependency declarations may depend on models and configuration, but
cannot import service orchestration, other task modules, HTTP composition or
Celery. Shared behavior belongs below routes; runner callbacks and immutable
options are declared in `feed_task_dependencies.py`.

A processing attempt owns one source generation and lease token. Its domain
writes and progress acknowledgement share one transaction. Cancellation, credential
revocation, changed source versions and expired leases must reject publication
from the old attempt. Recovery acceptance, publication and cleanup share a short
admission fence; domain workers continue independently. PostgreSQL integration
tests exercise committed transitions, rollback, replacement claims, concurrent
cleanup/retry and generated crash sequences. These tests assert visible state and
durable effects, rather than only mocked call order.

Frontend editor lifecycle tests use the actual authentication provider, session
query cache and router. The shared contract covers changes during submission,
selection changes, conflicts, discard and retired sessions. Use it incrementally
for other editors while retaining domain-specific validation tests. Browser tests
cover the processing worklist and keyboard-accessible operations trends in
Chromium, Firefox and WebKit. Automated accessibility checks supplement manual
assistive-technology review; they cannot establish usability with a screen reader.

When adding a new asynchronous workflow, document its source revision, owner,
lease, retry admission, side-effect ambiguity and cancellation boundary. A stale
completion must neither publish data nor overwrite a newer editor baseline.
