# AI Page

## Purpose

Admin-only control plane for ThreatLens AI configuration, daily briefing, reprocessing, task operations, usage analytics, and audit history.

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
unreadable. The legacy environment key remains limited to
`https://api.openai.com` on its default HTTPS port.

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
accepted. For named HTTP providers, runtime connection checks also filter the
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
