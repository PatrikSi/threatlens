# Feed worker boundaries

`app.tasks.feed_tasks` registers the public Celery tasks and constructs each
runner's orchestration dependencies. Existing task names, positional task arguments,
late acknowledgements and worker-loss rejection remain compatible.

| Runner | Injected capabilities | Concrete service ownership |
| --- | --- | --- |
| `feed_fetch_tasks` | Session factory, immutable fetch options, article enqueue callback | RSS parsing, fetch transport and leases, feed ownership, metadata and integration-event persistence |
| `article_fetch_tasks` | Session factory, immutable fetch options, classification enqueue callback | URL policy, transport and leases, extraction, article storage |
| `item_processing_tasks` | Session factory, IOC/AI enqueue callbacks, skipped-AI recorder, AI recency predicate | Classification, bounded regex/tagging, IOC extraction/storage, alert acceptance |
| `item_ai_tasks` | Session factory, AI enqueue callback, durable run-stop lookup | AI settings, provider execution, run lifecycle and claim checks |

`feed_task_dependencies.py` declares frozen typed records and callback protocols.
Fetch options copy only the relevant limits and URL-policy settings at task entry;
they carry no credentials or database configuration. Runners import their concrete
service owners directly. They receive neither `feed_tasks` nor another mutable
module as a runtime object. Intentional import aliases remain in the public task
module for existing internal callers; tests patch the concrete service owner and
use the task dependency factories when invoking runners directly.

The boundary tests reject module injection, undeclared dependency fields and
imports back into the public task facade. They also verify stable task registration,
callback capture, immutable fetch-option snapshots and positional forwarding.

Item writers and tag reapplication share the Item row lock. Classification flushes
its tagging state before alert acceptance reloads the locked item, then acknowledges
its source revision with the classification and alert intent. Tagging repair has an
independent durable pending state; see [custom regex execution](custom-regex-execution.md).

IOC storage aggregates normalized keys, sections, occurrence counts and maximum
confidence before writing. A locked item's association snapshot is replaced in one
transaction. Each 500-key batch uses one PostgreSQL IOC upsert and one link insert,
plus one deletion per item. Global keys are sorted consistently across workers;
uniqueness races preserve the first raw spelling and first-seen timestamp, and
last-seen never moves backwards. Tests assert query counts, cross-item uniqueness,
metadata changes, rollback and dense-document CPU behavior.
