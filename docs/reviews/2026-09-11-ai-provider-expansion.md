# Named AI providers and MCP groundwork — 2026-09-11

ThreatLens now supports a paginated catalog of named AI providers and independent
assignments for article enrichment, daily briefs and reports. Changes are on
`dev`, committed incrementally as `Patrik <patrik@local>`. Existing single-provider
settings remain the default until an administrator deliberately changes routing.

The supported adapter remains OpenAI-compatible chat completions. Multiple
endpoints and models are supported; a provider name does not enable a different
vendor's native protocol. MCP is an architectural proposal in this change, with
no exposed endpoint, external tool connection or additional SDK dependency.

## Implementation and review

Backend/API, runtime/security and frontend/accessibility reviewers assessed the
change independently, including follow-up review of each other's boundaries.

| Area | Implemented behavior and review outcome |
| --- | --- |
| Architecture | Provider configuration, feature routing and runtime selection have separate modules. Existing feature prompts, company context, planning limits and durable execution remain shared. The catalog has no fixed provider-slot count; list requests are paginated and bounded. |
| Backward compatibility | Migration `0095` adds tables without rewriting legacy settings. Creating a profile does not change routing. Older tasks without a selection snapshot, and children of those tasks, retain legacy selection. Legacy request-receipt fingerprints retain their previous shape. |
| Settings and authoring | Administrators can create, search, edit, disable, test and delete providers, then assign default and per-feature routes. Save versions stay with the draft baseline. Pending saves, refetches, tab changes and retired sessions do not replace subsequent edits. Unsaved provider and routing changes participate in navigation confirmation. Failed detail loads have an explicit retry, key validation errors are linked to the input, and successful deletion restores keyboard focus. |
| Credentials | Each profile has an independent encrypted, write-only key. Retain, replace and clear are distinct operations. An endpoint-origin change requires an explicit credential decision. Profile keys never inherit the legacy environment key. Encryption inventory and key-rotation checks include the new credentials. |
| Authorization and egress | Management requires administrator access plus the relevant AI scope. Mutations recheck current authorization. Connection tests use synthetic content through the existing provider runtime and current caller fences. Named HTTP endpoints require the deployment's private-network opt-in and nonpublic DNS-pinned connection addresses. |
| Queued work | New tasks capture provider ID/version, including inherited selections for child work. Missing, disabled, changed or unreadable selections fail before sending instead of choosing another destination. Deleted IDs are permanently retired without retaining keys or configuration. |
| Concurrency | Provider writes use optimistic versions and the established IAM-first lock order. A final shared provider lock verifies the saved selection and credential before I/O and remains held through settlement. A conflicting edit produces a controlled failure; it cannot redirect a prepared request. |
| Resilience | Existing total deadlines, decoded response limits, cancellation fences, retry budgets and ambiguous-outcome receipts remain in use. Connection tests cap the saved timeout at 30 seconds, completion at 128 tokens and retries at zero. Worker configuration faults have actionable errors instead of being recorded as ordinary skips. |
| Diagnostics | API errors distinguish conflicts, retired identifiers, credential-origin changes, unreadable credentials, unavailable configuration and authorization changes. Retained provider response strings scrub the active key, including structured error fields. Raw keys are absent from API responses, audit metadata and persisted browser drafts. |
| Upgrade and downgrade | Upgrade preserves legacy configuration. Downgrade refuses to remove provider state while profiles or unfinished named-provider tasks remain, including work whose profile was deleted. This prevents an older worker from silently executing that work through legacy routing. |
| MCP | [ADR 0006](../architecture/0006-ai-provider-profiles-and-mcp-boundary.md) proposes a thin, initially read-only adapter over authorized application services. It separates model credentials, inbound MCP authentication and any future outbound MCP connections. Protocol and authorization claims link to the 2026-07-28 primary specification. |

## Validation

Validation uses synthetic data and disposable PostgreSQL, Redis and browser
services. Existing deployment configuration and application data were not used.

| Check | Result |
| --- | --- |
| Backend | Frozen `810a1a3`: 2,557 passed, two opt-in capacity cases skipped, zero failures. Combined line/branch coverage was 85.03%, reporting 78.19%; all 41 critical-module floors passed. Provider API/configuration/selection coverage was 98.28% / 94.44% / 95.95%. |
| Frontend | At `28ced2d`, 969 unit/DOM tests across 113 files passed, with lint, TypeScript and production build checks. |
| Mocked browser workflows | Six provider workflows passed at `28ced2d`: two scenarios each in Chromium, Firefox and WebKit, including draft lifecycle, focus after deletion and automated accessibility checks. |
| Real-server browser workflows | Provider creation, routing, secret retention, validation and deletion passed against real authentication, CSRF and PostgreSQL in all three browsers. This workflow is now included in the browser CI matrix. No paid or external model endpoint was contacted. |
| Migration | At `810a1a3`, populated `0042 → 0095` preserved identity and saved-view JSON. Single-head, downgrade to base, re-upgrade and both Alembic schema-drift checks passed on disposable PostgreSQL 16. The retired-ID table was verified. |
| Static and generated contracts | Backend Ruff and the source-size gate passed for 695 production files. API/OpenAPI generation is current. Route authorization and data-policy inventories include the new operations. |

The first full regression run exposed older test stubs that rejected the new
feature-selection argument and an explicit route inventory that needed the new
administrative operations. The corrected report stubs also assert that scheduling
selects the report route. Focused regressions passed before the final full run.
CI now also requires minimum combined line/branch coverage for provider API routes,
configuration services and selection, alongside the existing runtime gates.

Backend source and tests remained unchanged after the frozen run began. The
real-server browser checks preceded the final retired-ID guard and UI polish;
PostgreSQL API/migration tests cover the guard, and the final frontend suite and
three-browser workflows cover the UI refinements. The provider identity tests
include an older queued task after deletion, attempted identifier reuse and
rollback of a failed deletion.

These are local checks. No remote GitHub CI result, deployment, or push is claimed.

## Remaining work

| Priority | Improvement | Completion criterion |
| --- | --- | --- |
| Next | Native provider adapters and capability declarations | Each protocol has typed request/response translation, supported-feature checks and a shared contract suite. Unsupported parameters produce actionable validation before work is sent. |
| Next | Provider-specific budgets and operations | Usage is attributable to profile ID/version, with per-provider concurrency, rate and cost limits. Queue age and provider failures can be compared without confusing profiles that use the same model name. |
| Next | Endpoint qualification | Validate representative hosted and local servers, including protocol deviations, rate limits and slow/failing responses. Current network tests use controlled transports; a passing synthetic connection test does not qualify every model capability. |
| Next | MCP implementation | Implement the ADR's read-only surface with an explicit OAuth protected-resource integration, authorization and data-policy parity, bounded results, protocol conformance and revocation tests before exposing it. Existing OIDC browser login alone is insufficient. |
| Later | Additional AI enrichment | Add separately versioned, source-grounded artifacts and evaluations before introducing entity extraction, ATT&CK mapping, cross-article synthesis or a retrieval assistant. Article summary/relevance currently share one enrichment assignment. |
| Qualification | Capacity and accessibility | Measure the chosen local/hosted endpoints and concurrent feature workloads on intended hardware. Perform manual screen-reader and deployment-specific identity/proxy testing; automated axe and Linux WebKit do not establish those results. |

Automatic cross-provider failover is deliberately absent: a retry can change the
data destination, duplicate an uncertain external action or exceed a provider
budget. Any future failover policy must specify permitted destinations and reuse
the existing attempt-reconciliation rules.

See the [operator guide](../pages/ai.md) for setup, routing, credential changes,
errors and downgrade requirements, and the [browser testing guide](../development/browser-testing.md)
for the isolated real-server scenario.
