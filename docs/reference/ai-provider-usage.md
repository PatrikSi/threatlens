# Provider usage and failure attribution

The AI settings Overview tab includes a Provider usage panel. It uses the same selected time window as the existing overview, with its own refresh, loading/error state and 25-record pagination. Changing the time window returns to the first provider page. A failed refresh leaves previously loaded records visible with an explicit warning.

A record is a provider ID, saved provider version, saved provider name and model combination. Two named profiles using the same model remain separate. Historical snapshots survive profile renames and deletion; the panel does not infer that a provider still exists or is currently enabled. Provider UUIDs distinguish profiles with identical names. Newly recorded legacy calls use **Legacy settings**. Older events without a captured identity use **Unknown historical provider** and are not retroactively attributed to whichever provider is configured now.

## Read API

`GET /api/v1/ai/ops/providers?days=30&limit=25&offset=0` (or `/v1/ai/ops/providers` at the backend) requires AI to be enabled, administrator access and the `read:ai` token scope. The endpoint applies the same AI telemetry data-policy predicates as the overview. It checks authorization and policy revision again before returning, and records would-deny evidence in audit mode.

The response contains `items`, `total`, `days`, `limit` and `offset`. `total` counts visible provider/model groups, including when the requested page is empty. Days are limited to 1–365, page size to 1–100 and offset must be nonnegative. Groups sort by recorded call count descending, then provider name, UUID, version and model. This is a live aggregate; new calls or retention cleanup can move a group between pages. Refresh or return to the first page to reconcile a changed inventory.

Each row includes:

- Provider identity/version/name, model and last call time.
- Recorded call, success and failure counts, and success percentage.
- Recorded input, output and total tokens; a separate count of calls with missing total-token usage.
- Average and interpolated 95th-percentile latency among successful calls with a recorded latency. Both are `null` when no such measurements exist.
- Counts for requests not sent, uncertain I/O outcomes, and total/DNS deadline failures.
- Nonzero counts for typed failure categories. Unknown or historical untyped failures use `unclassified`.

Not-sent and uncertain outcomes describe the HTTP exchange boundary; they do not imply that every failure consumed provider tokens. Admission denials can appear in not-sent totals with categories such as `provider_concurrency_budget`, `provider_hourly_token_budget` or `budget_request_too_large`. Missing token usage is not an estimate of zero provider cost. The panel reports retained usage events, not billing statements or remaining admission-budget capacity.

Failure classification uses stable categories, including DNS and total deadlines, provider authentication/rate limits, transport timeouts, refusal, truncation and invalid output. Endpoint-health timeout/authentication totals also use these categories. They do not infer a timeout or authentication failure by searching English error text; historical events without typed categories remain unclassified.

Aggregation and pagination occur in PostgreSQL. The application materializes only the requested group page and total count; it does not load event history or join against the current provider catalog. Failure-category output has a fixed finite set, with unknown values grouped together. Interactive database operation/statement limits apply, and unavailable or timed-out aggregation returns HTTP 503 with a retry hint.

## Verification

`backend/tests/integration/test_ai_provider_usage.py` exercises actual PostgreSQL grouping, totals, pagination, percentiles/null usage, immutable attribution, lineage filtering, authorization scopes, policy refencing and failure handling. `web/src/pages/AiProviderUsagePanel.dom.test.tsx` uses a real QueryClient for pagination, refresh errors and delayed responses across time-window changes. The browser suite adds keyboard pagination, table semantics, automated accessibility rules and narrow-viewport checks in Chromium, Firefox and WebKit. These automated checks do not establish full screen-reader compatibility.
