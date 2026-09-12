# AI Page

## Purpose

Admin-only control plane for ThreatLens AI configuration, daily briefing, reprocessing, task operations, and audit history. Usage analytics now live under **Statistics → AI statistics** (`/stats?section=ai`); the former overview section links there.

AI statistics preserve the administrator and `read:ai` permission requirements. Ingestion statistics separately require `read:stats`. Moving the interface does not grant access to either dataset.

The AI statistics workspace includes provider/version/model usage, per-feature outcomes, known and missing token usage, successful-call latency percentiles and distribution, typed deadline/timeout/truncation/budget failures, retained retry receipts, evidence coverage and current queue age. Request metrics use the selected time window; backlog, coverage and retained-history panels show the current accessible dataset and do not inherit ingestion feed filters. A deadline is included in timeout totals. Latency percentiles exclude failed calls and missing measurements. Retry reservations do not establish additional billable calls, and receipts whose run was removed are excluded. Missing usage is never treated as zero-cost usage; prices and currency costs are not estimated.

`GET /ai/ops/statistics?days=30` returns bounded database aggregates, under the interactive database deadline and current authorization/data-policy fences. Confirmed access loss hides cached records; transient errors retain the previous snapshot with an explicit error and retry action.

All API paths on this page are relative to the published `/api/v1` base.

## Access and Visibility

- Route: `/ai`
- Nav item is shown only when:
  - the current user is an `admin`
  - `AI_ENABLED=true`

## Set Up Named Providers

Open **Settings → AI → Configuration** to add a named provider. Each provider
stores its own OpenAI-compatible base URL, model, optional API key, completion
limit, temperature, timeout, retry limit, and enabled state.

1. Add a descriptive name, such as `Local analysis` or `Hosted reports`, and enter
   the endpoint and a model identifier supported by that endpoint.
2. Enter a key only when that endpoint requires one. A local Ollama endpoint can
   use an empty credential. An origin such as `http://192.168.0.113:11434` resolves
   to `/v1/chat/completions`; `/v1` and a complete `/chat/completions` path are also
   supported.
3. Save the provider and run its connection test. A successful test checks the
   saved configuration with synthetic content, a 128-token completion cap, no
   automatic retries, and the provider's timeout capped at 30 seconds.
4. Choose the default provider, then select feature overrides where needed. Save
   routing to apply those choices to newly queued work.

For example, set `Local analysis` as the default and select `Hosted reports` for
Reports. Article summaries/relevance and daily briefs can inherit the default.
Changing an override changes that feature's data destination; use an endpoint
appropriate for the information it will receive.

Only the OpenAI-compatible chat-completions protocol is supported. A provider
name is a label, and does not enable a different vendor protocol. Global prompts,
company context, feature switches, and report context-planning limits remain in
AI settings.

A legacy or named provider connection test can reach the endpoint but exhaust its fixed
128-token allowance before returning valid JSON, especially when the model uses
reasoning tokens. The error identifies this diagnostic limit; increasing the
saved completion budget does not change the test. Such a result leaves feature
compatibility unverified. Qualify a small feature request with its saved budget.
Both settings paths use the same bounded diagnostic and recheck the caller's
current authorization immediately before provider I/O. Saved feature budgets
remain unchanged.

### Completion Budgets

**Default completion tokens** sets the initial output allowance for article
enrichment and daily briefs. Both legacy settings and named providers accept
128–131,072 tokens. **Initial report completion tokens**, under **Report context
guardrails**, independently sets the starting allowance for every evidence batch
and report section, from 256–131,072 tokens. Reports reserve that same amount in
their context plan; the provider default no longer caps their initial allowance.
The existing API field remains `report_reserved_output_tokens`.

Defaults and saved values are preserved: a new configuration starts with 5,000
default completion tokens and 1,200 initial report completion tokens. Raising the
supported maximum does not increase an existing request budget. If report JSON
is truncated, increase the report budget and ensure **Model Context Window**
still leaves space for input, protocol overhead, and the safety margin. Choose
values within the selected model's output and context limits.

A report truncation retry can increase the allowance into unused context up to
the greater of the report budget or provider default, with a 131,072-token
ceiling. Retry counts, response-byte limits, and request deadlines still apply;
increasing tokens does not guarantee that a model can produce valid JSON.

### Model Compatibility and Limits

Both the legacy connection and every named provider include **Model compatibility
and limits** controls. Existing configurations keep the compatible chat request
format, temperature 0.2, omitted reasoning, and prompt-only JSON instructions.
Configure the exact model's documented capabilities; ThreatLens does not infer
protocol settings or model limits from a model name.

| Setting | Behavior |
| --- | --- |
| Request format | Compatible chat sends `max_tokens`; modern chat sends `max_completion_tokens`. Both use `/chat/completions`. |
| Temperature | Leave blank to omit the parameter. Zero sends an explicit `0`. |
| Reasoning effort | Leave omitted for the provider default, or send one of the listed values explicitly. `none` is distinct from omission. |
| JSON response mode | Off uses prompt instructions; JSON object mode sends `response_format: {"type":"json_object"}`. This is not strict JSON-schema enforcement. |
| Model context limit | Optional documented input-plus-output limit, 2,048–2,097,152 tokens. Blank means unverified. |
| Model output limit | Optional documented maximum completion allowance, 128–131,072 tokens. Blank means unverified. |

The [OpenAI chat-completions reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
distinguishes the two token parameters and documents model-dependent reasoning
support. Google's [compatible API documentation](https://ai.google.dev/gemini-api/docs/openai)
describes its reasoning-effort mapping. A value appearing in ThreatLens's selector
does not mean every model accepts it. Native Gemini requests and provider-specific
`extra_body` extensions remain outside this adapter.

A configured model context limit checks serialized messages, including framing
and escaped content, before every feature request, using ThreatLens's token estimator, a 15% safety reserve, and
384 protocol tokens. Configured output limits constrain the initial allowance and
retry growth. An oversized request fails before provider I/O with its requested
output, estimated input, and available headroom; this check does not automatically
shorten prompts. Existing article/brief source-character caps and report evidence
truncation still apply while constructing those messages. Token estimates cannot
guarantee agreement with every provider tokenizer.

Report preview and execution use the smaller of the report context window and
selected model context limit, and the larger applicable safety percentage. Their
independent initial report completion budget must also fit the model limits.
Reasoning tokens can consume the completion allowance without producing visible
JSON. These controls do not increase response-byte, timeout, or retry limits.

The additive API fields are `request_dialect`, `reasoning_effort`,
`structured_output_mode`, `model_context_window_tokens`, and
`model_max_output_tokens`. Older clients omitting them on update retain saved
values; explicit null clears an optional model limit or reasoning setting.
Migration 0096 preserves existing requests. Downgrade is blocked while nonlegacy
capabilities or omitted temperatures remain configured; restore legacy settings
and resolve queued AI work before rolling back.

### Provider Workload Limits

Each connection has a **Concurrent request limit** (0–1,000) and a
**Rolling-hour token budget** (0–1,000,000,000,000). Zero means unlimited and
preserves existing installations. Configure these independently for the legacy
connection and each named provider. They apply across API and worker processes,
not separately to each worker. The token budget reserves estimated input plus
requested completion tokens; unknown usage retains a conservative reservation.
These settings limit admission and do not alter provider request parameters.

The API fields are `max_concurrent_requests` and `hourly_token_budget`. Older
clients omitting them retain saved limits; explicitly save zero to remove a
limit. Changes appear in AI configuration audit history. Distinct profiles are
separate budgets even if they use the same external account, so account-wide
provider quotas still need operator coordination.

### Gemini Compatibility

For Gemini, enter `https://generativelanguage.googleapis.com/v1beta/openai/` as the
base URL and enter the provider's model identifier in the separate **Model**
field. This uses Google's [OpenAI-compatible chat-completions interface](https://ai.google.dev/gemini-api/docs/openai).
The bare Gemini origin and its `/v1beta` path also resolve to that compatible API.
Native URLs ending in `:generateContent` or `:streamGenerateContent` use a different
request format and are rejected with a message pointing to the compatible base.

For a named Gemini provider, save its API key on that provider. To use a Gemini
key through the legacy environment configuration, set `AI_API_KEY_BASE_URL` to
`https://generativelanguage.googleapis.com` alongside `AI_API_KEY`, then configure
the compatible base URL and model in legacy AI settings. Apply environment changes
to the API and workers before testing the connection.

### Routing and Legacy Compatibility

| Selection | Result |
| --- | --- |
| Named provider for article enrichment, daily briefs, or reports | That feature uses its selected provider. |
| Feature inherits the default | Use the default selection. |
| Default uses legacy settings | Use the original single endpoint/model configuration. |

Creating a provider does not change active routing. Existing installations retain
the legacy endpoint and server `AI_API_KEY` until an administrator selects named
providers. Resetting the default to legacy and removing feature overrides returns
new work to that configuration.

Named profiles use only their own credentials. They do not use the server
`AI_API_KEY` or another profile's key when their key is absent, cleared, or
unreadable. The legacy environment key is bound to `AI_API_KEY_BASE_URL`, which
defaults to `https://api.openai.com`. The endpoint must match that URL's HTTPS
scheme, host, and effective port; an omitted HTTPS port is equivalent to `443`.
Paths do not participate in this restriction, so different API paths on the same
origin can use the key. Existing legacy endpoints whose origin does not match
continue to operate without the environment key; endpoints requiring a key need
a matching binding or a named provider with its own credential.

Inheritance is not automatic failover. A selected provider that is unavailable,
disabled, deleted, changed after work was queued, or has an unreadable credential
stops that operation with an error. ThreatLens does not silently send the same
content to a different provider.

### Credential Changes and Network Access

Keys are encrypted in PostgreSQL using `APP_DATA_ENCRYPTION_KEY`. Responses report
whether a key is configured; they never return its value. In an edit, leaving the
key field empty preserves the saved key. Entering a replacement changes it;
explicitly clearing it removes it. Entered keys are not saved in browser draft
storage.

When changing the endpoint origin, replace or explicitly clear the saved key so
the old credential is not carried to a new destination. Keep the application's
encryption key available when restoring the database. If a key cannot be
decrypted, restore the required encryption key or replace/clear that provider's
credential before trying again.

Public endpoints require HTTPS. Private-network endpoints require
`ALLOW_PRIVATE_NETWORK_AI=true`; only private endpoints may use plain HTTP under
that opt-in. Embedded URL credentials, query parameters, and fragments are not
accepted. For both legacy and named HTTP providers, runtime connection checks filter the
resolved IP addresses to nonpublic unicast destinations before opening a socket;
a private-looking hostname cannot cause a connection to a public address.

### Queued Work and Conflicts

A queued task records the selected provider's identifier and version. Routing
changes affect future tasks; an existing task keeps its selection. Editing,
disabling, or deleting that provider can therefore prevent previously queued work
from starting. Review the configuration and queue a new operation when the
reported failure occurred before provider I/O.

Tasks created before provider selection was introduced continue to use legacy
settings. Existing enrichment results remain stored; the upgrade does not queue
all articles for regeneration. The next requested enrichment checks the current
configuration and source fingerprint, including newly covered endpoint, prompt,
and model settings, and can regenerate a result whose inputs changed.

Migration `0100` separates the last successful enrichment's source and provider
provenance from the latest attempt. A failed refresh retains the historical result
for inspection, but briefs and newly planned reports use current publisher text
when that result is stale or its source version cannot be verified. This check
includes the article revision, classification, tags, feed, URL and publication
time. Historical results without provenance are not certified during the upgrade,
and the migration does not schedule paid regeneration. A later successful
enrichment records verifiable provenance.

Brief source selection projects at most 900 characters from each selected summary
and loads no summary bodies for audit-only sources. Metadata has independent field
limits, a 2,000-row ceiling and a 40 MiB projected text budget. An overlong URL is
omitted rather than truncated into a different destination. Brief responses expose
`evidence_warnings`; the dashboard shows these notes when current primary evidence
replaces stale enrichment or the source audit budget limits coverage.

Provider edits and routing updates carry optimistic versions. On a conflict,
refresh and review the latest configuration before saving again. Remove routing
references before deleting a provider.

Before downgrading below migration `0095`, stop scheduling and queueing new AI
work, finish or cancel outstanding operations, and wait for workers to settle.
Then remove provider routing references and named providers, and stop the API and
workers before running the downgrade. The migration refuses to remove the
provider tables while named providers or unfinished named-provider tasks remain,
including tasks whose provider was already deleted. Older workers cannot honor
their saved routing. Terminal task history and queued legacy tasks remain intact.

An in-progress outbound request retains the configuration and policy locks until
the call settles, within the request deadline. A configuration edit may wait for
that call. Retries recheck the selected configuration and never move to another
provider. An ambiguous provider outcome means the request may already have been
sent; use the existing provider-attempt reconciliation workflow before retrying.

### Provider API and Errors

Provider reads require an administrator with `read:ai`; changes and connection
tests require an administrator with `write:ai`. All routes remain gated by
`AI_ENABLED`.

| Operation | Request contract |
| --- | --- |
| `GET /ai/providers` | `limit` defaults to 25, maximum 100; `offset` defaults to 0; optional `search` matches names. Returns `items`, `total`, `limit`, and `offset`. |
| `POST /ai/providers` | Required `name`, `base_url`, and `model`; optional credential and request settings. Supply a stable client-generated `id` to retry an uncertain create without creating another profile. |
| `GET /ai/providers/{id}` | Read one saved configuration and its version; no key value is returned. |
| `PUT /ai/providers/{id}` | Full provider fields plus the last-read `version`. Omitted ordinary fields use their defaults. Omitted or `null` `api_key` retains the key; `clear_api_key: true` removes it. |
| `DELETE /ai/providers/{id}?version=N` | Requires the last-read version and no remaining routing references. |
| `POST /ai/providers/{id}/test-connection` | Body contains the last-read `version`; tests saved settings with synthetic content, no retries, and a timeout capped at 30 seconds. |
| `GET, PUT /ai/provider-routing` | PUT contains the last-read `version` and all four routing selections. Omitted selections default to `null`, so send the complete intended routing state. |

Names are unique without regard to case. A successful repeat of an unchanged
create with the same identifier returns the existing profile; that identifier
cannot overwrite a profile edited since its creation. Updates and routing saves
advance their versions. Deleting a provider does not rewrite queued task history.
Deleted provider identifiers are permanently retired, independently of task
history retention. Create a replacement with a new identifier; the deleted
provider's name can be reused. Only the retired identifier and deletion time are
retained, so old queued work cannot silently target a replacement endpoint.

Provider management errors expose a code in the error `detail` object. Background
tasks record configuration failures as their reason. Common codes include:

| Code | Action |
| --- | --- |
| `provider_version_conflict`, `provider_version_changed`, `provider_configuration_conflict` | Reload saved settings and review the concurrent change. |
| `provider_name_conflict`, `provider_id_conflict` | Choose a distinct name or inspect the existing create result. |
| `provider_id_retired` | The provider was deleted. Start a new provider with a new identifier. |
| `provider_credential_destination_changed` | Replace or clear the saved key when changing endpoint origin. |
| `provider_in_use` | Remove routing references before deletion. |
| `provider_disabled`, `provider_not_found`, `provider_missing` | Select an available provider; disabled profiles cannot receive a new assignment. |
| `provider_credential_unreadable`, `provider_credential_unavailable` | Restore the required encryption key or replace/clear the provider credential. |
| `provider_selection_invalid` | The saved task selection cannot be used. Review routing and start a new task. |
| `provider_authorization_changed` | Refresh authorization before attempting another change. |
| `provider_configuration_unavailable`, `provider_credential_storage_unavailable` | Retry after the settings/database or encryption configuration issue is resolved. |

Invalid field values return the standard `422 validation_error`. Saved profiles
remain visible if private-network access is later disabled, so an administrator
can inspect them and replace the endpoint. Reading an inventory entry does not
authorize a network request to it.

## Primary Areas

### Overview

- AI health and readiness summary
- Endpoint/model status
- Daily brief schedule summary
- Usage, latency, token, relevance, freshness, and storage KPIs
- Failure summaries and recent active tasks

Overview totals, model/day summaries, token efficiency, relevance distribution,
and latency percentiles are aggregated in PostgreSQL with the current access
predicates. Daily buckets use UTC. The application receives aggregates rather
than every underlying event; database scan cost still depends on the selected
history volume. Failure history also groups usage and task errors in PostgreSQL,
applies both permission predicates before grouping, and returns only the requested
top groups. Error normalization retains Python whitespace/200-character grouping
semantics; ties have a stable order. Task last-seen time uses the latest available
finish/update time, including unfinished failures.

Usage totals include valid token counts reported on failed responses, including
truncated reasoning-only output. Missing or malformed provider counts remain
unknown, rather than overflowing storage or inventing usage. Older failures are
not retrospectively backfilled. Aggregation is still by model, so profiles using
the same model name are combined.

Enrichment requires nonempty summary text when summaries are enabled and a finite
score when relevance is enabled. Daily briefs require nonempty narrative text
and a title of at most 255 characters when supplied. Before successful settlement,
all feature output must contain database-safe Unicode and finite JSON values,
with at most 32 levels, 100,000 values, and serialized size within
`AI_RESPONSE_MAX_BYTES`. NUL characters and unpaired surrogates are rejected
without changing quoted content. Invalid optional provider model metadata is
omitted in favor of the configured model; diagnostic fields are bounded and
escaped. These metadata faults do not discard known token usage or turn a
received result into an uncertain transmission.
Malformed outputs use the existing bounded retry and durable-attempt workflow;
they are not published as empty successful results. An explicit provider refusal
or content-policy stop produces a terminal diagnostic without automatically
repeating the same request.

### Activity / Operations

- Live queued/running AI task panel
- Task history table across:
  - `item_enrichment`
  - `daily_brief`
  - `connection_test`
  - `reprocess`
  - `report`
- Run detail view with:
  - status, timing, worker, model, token counts
  - parent/child progress for reprocess jobs
  - article-level child runs
  - provider exchange inspection (request / response snapshots when available)
- Daily brief source-item drilldown
- Report run linkage back to the generated report identifier
- Manual action history
- Prompt-change history

Reprocessing child history uses 50-row pages, with visible result scope and
previous/next navigation through the complete accessible history. A transient
page failure keeps the last accepted page visible and offers retry or a return to
the first page. Losing access clears those results. Queue completion clears the
scope form only when it still matches the submitted values; edits made while the
request is pending remain available for the next operation.

### Configuration

- Named OpenAI-compatible providers and default/per-feature routing
- Legacy OpenAI-compatible endpoint base URL and model
  - Ollama origins such as `http://192.168.0.113:11434` are treated as `http://192.168.0.113:11434/v1` for chat completions.
- Timeout, completion-token, and retry settings
- Feature toggles:
  - AI summaries
  - relevance scoring
  - daily brief
  - intelligence report generation
  - auto-enrich new items
- Daily brief controls:
  - run time (UTC)
  - lookback window
  - max articles
  - retained brief history
- Report context guardrails:
  - model context window
  - reserved output tokens
  - per-source token cap
  - maximum sources
  - maximum model calls
  - context safety margin
- Company profile / context:
  - name
  - industry
  - regions
  - stack
  - priority topics
  - keywords
  - exclusions
  - freeform profile text
- Editable system prompt templates and instruction overlays
- Connection test
- Manual daily-brief queue action
- Scoped reprocess action:
  - lookback days
  - explicit time range
  - feed filters
  - last `N` items
  - exact item selection
- Automatic item enrichment is limited to items recently published and recently first seen; older feed backlog is handled through the scoped reprocess action.
- The scheduler evaluates the configured daily-brief time on each UTC minute boundary and creates at most one ready brief row for a UTC date.
- When a normal scheduled or manual brief becomes ready, its integration event is written in the same database transaction. SMTP and webhook delivery is queued immediately after commit and uses an immutable snapshot of the generated brief.
- A five-minute reconciliation task recovers a ready current-day brief if immediate queue publication fails. Integration delivery retries can make a failed destination receive the brief later than its generation time.
- Historical backfill runs populate brief history without sending notifications.
- Report generation uses bounded evidence batches and section-level calls. The builder exposes the conservative preflight estimate before work can be queued.

## Dashboard Integration

- The dashboard can add a `Daily Brief` window when AI daily briefing is enabled.
- RSS item detail can render AI summary + relevance insight blocks when enrichment is available.

## Trust Boundary Notes

- ThreatLens sends selected item/article text, prompt instructions, and company profile context to the configured AI base URL when AI features are enabled.
- Named provider endpoints and models are stored with independent encrypted keys;
  the legacy configuration can use the server-side `AI_API_KEY`. Saved credentials
  are never returned to the browser.
- Provider-exchange inspection stores sanitized request/response metadata and token counts, while generated summaries, relevance results, and daily briefs are stored in the application database.
- Private-network AI egress is disabled by default unless `ALLOW_PRIVATE_NETWORK_AI=true`.
- Content-derived AI task runs, usage events, and downstream artifacts retain
  handling-label envelopes; provider-attempt receipts retain the exact task-run
  link. Connection tests are the only envelope-free system-scoped AI telemetry.
- Resolving an ambiguous provider attempt through an approval preserves the
  receipt's exact task-run scope. Governed lineage is copied to the approval;
  missing or unverifiable lineage is assigned to quarantine.
- Data-policy audit records would-deny evidence for AI operations, while enforced
  mode hides inaccessible task history and prevents restricted provider egress.

See [Access Governance and Data Policy](../reference/access-governance.md) for
the activation preflight and approval target contract.

The [provider and MCP architecture decision](../architecture/0006-ai-provider-profiles-and-mcp-boundary.md)
describes routing invariants and future MCP exposure. This release does not expose
an MCP server or consume external MCP tools.

## API Calls

- `GET /ai/settings`
- `PUT /ai/settings`
- provider catalog and routing routes listed above
- `POST /ai/test-connection`
- `GET /ai/usage`
- `GET /ai/daily-brief/latest`
- `GET /ai/daily-briefs`
- `POST /ai/daily-brief/generate`
- `POST /ai/daily-brief/queue`
- `POST /ai/reprocess`
- `GET /ai/ops/overview`
- `GET /ai/ops/live`
- `GET /ai/ops/runs`
- `GET /ai/ops/runs/{id}`
- `POST /ai/ops/runs/{id}/cancel`
- `GET /ai/ops/manual-actions`
- `GET /ai/ops/prompt-history`
- `GET /ai/daily-briefs/{id}/sources`
