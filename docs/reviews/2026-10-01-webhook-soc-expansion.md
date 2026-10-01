# SOC webhook improvements — 2026-10-01

This change adds relevance-based article routing, searchable configuration and
explicit article-text delivery. Three implementation agents covered AI event
semantics, editor workflows and payload delivery; cross-review covered provenance,
authorization, retry behavior and compatibility. The coordinating pass exercised
the real API and browser together and reviewed desktop and mobile layouts.

## Delivered behavior

- **AI completion routing:** `article.ai.ready` emits for a verified successful
  shared AI result even when the indicator set did not change. Subscriptions can
  combine its relevance score or label with existing conditions. An RSS arrival
  is still an ingestion event and does not wait for AI processing.
- **Editor usability:** searchable payload variables, named source choices and
  enumerated dropdowns reduce the need to enter IDs or template syntax. JSON
  output keys support nesting without silently replacing configured fields.
  Personal and team destinations retain the same configuration capabilities.
- **Article evidence:** templates can include extracted article text and source
  metadata; structured payloads have an explicit text opt-in. Text is limited to
  128 KiB of UTF-8, may be shortened to fit the envelope and reports its coverage.
  AI-ready events additionally provide a bounded summary and relevance reasons.
- **Non-sending previews:** stored events display captured article/feed labels,
  condition explanations and rendered template or structured bodies. Previews
  disclose unavailable or superseded source text without substituting a new body.

## Boundaries and review corrections

AI relevance is the shared installation assessment, separate from team-specific
judgment or approval to execute a hunt. Missing or malformed relevance remains
unknown under negation. Source and accepted-result identity are checked before
delivery; a concurrent replacement cannot rewrite the result hashed into an
existing action ID. SQL projects bounded text and a digest of the full provenance.

Article text is captured from the retained revision, never fetched from a newer
article on behalf of an old event. Requests using snapshot variables preserve
accepted bytes across retries. Template tests and stored-event previews recheck
the caller's current source-read authority; revocation during DNS preparation
prevents the test send. Large expanded bodies have a serialized size budget.

Cross-review reproduced a retry defect: a request that failed the render budget
could be cloned without its error and send raw placeholders. A durable rendering
outcome now survives event attachment and overwritten diagnostics. Frozen failed
requests and generic replays return actionable HTTP 409 responses; a worker guard
also prevents sending them. Ordinary legacy templates retain their existing
correction-and-rerender recovery path. Accepted request snapshots remain replayable.

The editor keeps selections during catalogue failures and exposes recoverable
lookup errors. Mobile fieldsets are constrained so long choices do not push
controls outside the viewport. Rendering handles quotes, line breaks and Unicode.
Retry errors are announced after the confirmation dialog closes, retaining the
server's recovery guidance and request reference.

## Validation

- Full frontend suite: **1,343 tests in 161 files passed**.
- Following the final retry-error accessibility change, **8 focused frontend
  tests passed**, including a new asynchronous HTTP 409 lifecycle regression.
- Combined webhook backend regression suite: **261 passed**.
- Adjacent integration, SMTP and AI output/continuation regressions: **210 passed**.
- HTTP encoding, signing and transport security regressions: **27 passed**.
- After the retry/replay correction, **191 affected backend tests passed**,
  including **10 new regressions** for rendering state, legacy recovery and
  replay safety. This run overlaps the backend suites above.
- Mock-backed editor browser workflows: **9 passed**, including accessibility
  checks and mobile layout coverage.
- Real-server webhook workflows: **6 passed** across Chromium, Firefox and WebKit.
  These covered saved filters, structured text, escaped template previews and
  rejection of superseded evidence against migrated disposable PostgreSQL.
- Frontend lint, application/browser TypeScript checks, production build and
  distribution checks passed. Backend Ruff, source-size checks, generated API
  artifact consistency and whitespace checks passed.

Browser and backend fixtures used synthetic stored AI results and transport
mocks. Validation did not call a paid model or send a hunt to a real receiver.

## Rollout

Run migration `0125_webhook_article_text` and upgrade the API and workers together.
New snapshot variables, AI routing and article-text configurations use schema v3;
older workers defer these subscriptions. Existing templates remain compatible,
and omitted optional fields preserve saved values when older clients update a
webhook. No live stack deployment is part of this change.

Configuration and operating examples are in the
[intelligence automation guide](../pages/intelligence-automation.md).
