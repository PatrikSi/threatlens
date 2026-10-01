# Intelligence automation and SIEM webhooks

ThreatLens can send revisioned article AI, extraction and approved-hunt events to an HTTPS receiver. The receiver can enqueue a SIEM hunt and use the included evidence to explain its purpose. Delivery history records the HTTP outcome; separate [execution receipts and authenticated callbacks](automation-execution.md) track the receiver's asynchronous work and findings.

## Configure a receiver

1. Run migrations and upgrade API and notification workers together. Existing personal webhook IDs, templates, feed scopes and delivery history continue to work.
2. In **Settings → Integrations → Webhooks**, create a reusable credential profile. Choose a bearer token, a custom authentication header, signing only, or authentication plus signing. Generate a random signing secret with at least 32 characters and provision it on the receiver before enabling delivery.
3. Create a webhook with **Structured automation JSON v1** payloads, `POST`, the receiver's HTTPS URL, and an event below. Select feeds and conditions. New hooks are personal; managers can explicitly transfer a destination to a team and administer its custodian and scoped receiver credentials through [team integrations](team-integrations.md).
4. Select an authorized stored event and run the non-sending preview. Verify matched conditions, unavailable fields, source freshness and the typed JSON payload. Automation hooks deliberately disable synthetic test sends so testing does not launch an invented hunt.
5. Enable the hook. For a first controlled trial, approve a reviewed hunt, then check its delivery history and the receiver's accepted job. Have the receiver acknowledge promptly after durable acceptance; the webhook timeout is 1–60 seconds, not the hunt's runtime budget.

Public webhook destinations require HTTPS. Internal/private destinations require the existing `ALLOW_PRIVATE_NETWORK_WEBHOOKS=true` deployment setting. Cross-origin redirects remain blocked. Source evidence can contain untrusted article text: the receiver must treat it as data, validate its own action schema, and enforce its own hunt permissions and resource budget.

Opted-in plaintext HTTP connections remain restricted to the pinned private address even if a hostname changes its DNS answers. Template connection tests revalidate the accepting browser session or API token before each request, including after DNS and credential preparation. An expired, revoked or newly restricted credential returns a credential-changed diagnostic; a request rejected before I/O is recorded as `not_sent`.

## Events and revisions

| Event | Produced when | Action boundary |
| --- | --- | --- |
| `article.ai.ready` | A successful shared article AI result is published, including when its indicator inventory did not change. | Exact article source and successful AI result provenance. |
| `intel.extraction.ready` | Deterministic extraction records a new current source/processor/evidence revision. | Article source and extraction revisions. |
| `intel.indicators.changed` | The indicator inventory, AI evidence role, or a team's effective indicator assessment changes. | Global inventory revision, or the team-scoped assessment/policy revision. |
| `hunt.approved` | An analyst accepts a specific suggested hunt. | Stable approval identity and the approved content/evidence/context. |

Unrelated note changes, investigation links or edits to another hunt do not create another approval action. Rejecting and subsequently accepting a hunt creates a new approval identity. AI inference remains labelled as inference; acceptance does not prove that the hypothesis is true.

Editing a team exclusion immediately affects reads and invalidates queued actions. It does not fan out change events for every historical article; subsequent processing or review events carry the current policy.

The six legacy event names (`rss_item_new`, `alert_match`, `feed_failing`, `webhook_failed`, `daily_digest`, `report_ready`) remain available. `rss_item_new` describes initial ingestion and does not promise article retrieval, extraction or AI completion. Legacy templates remain the default. SMTP retains its existing event catalogue; the four dotted event names above are webhook destinations.

New events and their deliveries use the existing PostgreSQL outbox. Automation checks current source/extraction revision before routing and again before sending. An approved hunt also checks current approval, team context, membership and team indicator policy. Superseded actions are not sent. A source concurrently being updated produces a retriable deferral. An older stored event remains available for authorized preview, which explains why it would no longer deliver.

Events retain source revisions, handling labels, supporting evidence, exclusions and provenance. Indicator snapshots are limited to 250 indicators and 256 KiB of event data. Over-budget inventories are marked incomplete instead of pretending that a prefix is the complete set, and are not automatically delivered. The automation envelope has an additional bounded allowance. Inspect `indicators_complete`, `indicator_count`, `incomplete_reason` and any per-field completeness markers.

## Subscription conditions

All hooks retain their existing **all feeds / selected feeds** selection. Optional conditions add bounded `all`, `any` and `not` groups. A tree allows at most four levels and 32 total nodes; each set condition allows up to 50 values, each at most 200 characters. A `not` group has exactly one child.

| Field | Operators | Meaning |
| --- | --- | --- |
| `feed_id`, `tag_id`, `tag`, `alert_rule_id`, `team_id` | `in`, `not_in` | Match captured source/rule/team identifiers or tag names. |
| `ioc_type`, `ioc_role` | `in`, `not_in` | Match the non-excluded indicator inventory. Roles include `malicious_infrastructure`, `benign`, `reference`, `unknown`. |
| `attack_technique`, `hunt_review_status` | `in`, `not_in` | Match captured ATT&CK IDs or the hunt review state (`accepted`). Global intelligence events use IDs explicitly present in verified current source passages; hunt events use their reviewed technique IDs. |
| `extraction_confidence`, `maliciousness_confidence` | `gte`, `lte` | Separate scores in `[0, 1]`. Extraction confidence measures extraction, not maliciousness. |
| `ai_relevance_score` | `gte`, `lte` | Shared article AI relevance in `[0, 1]`, captured only from a successful result with verified current source provenance. |
| `ai_relevance_label` | `in`, `not_in` | Captured shared article relevance: `low`, `medium` or `high`. Labels use the AI relevance thresholds applied when the result was generated. |
| `freshness_seconds` | `gte`, `lte` | For AI-ready, intelligence and hunt events, age of the retained article retrieval; unavailable if that timestamp is missing, invalid or in the future. Legacy events use event age. Configuration is capped at one year. This is not the article's publication age. |

Set matching is case-insensitive. Conditions select events; they do not trim the event payload to a matching indicator. In particular, `ioc_type` and `ioc_role` are set-level conditions and can match different indicators. A receiver must inspect each indicator's own role, confidence and exclusions before using it.

Numeric indicator confidence uses the minimum across the non-excluded inventory, and remains unavailable if any relevant score is absent. Thus `gte: 0.9` requires every score to reach 0.9, while `lte: 0.5` finds a set containing at least one score at or below 0.5; it does not require every score to be low. Current automated extraction does not invent a maliciousness probability. A missing field does not become a match through `not` or `not_in`; unknown branches remain unknown. An `any` group may still match a different known-true branch. Historical events without new tag/rule metadata explain that absence in preview. Incomplete tag/rule lists cannot satisfy exclusion conditions. Approving a hunt or emitting a new event does not make an old article retrieval fresh.

Malformed retained values also remain unknown, including under negation. Condition text containing characters that PostgreSQL cannot store is rejected during request validation.

### Route relevant articles to a SOC queue

Choose **AI article analysis ready** when the decision depends on AI relevance.
For example, use `ai_relevance_score` **at least** `0.8`, optionally combined with
selected feeds and a freshness condition. A new RSS item usually has no AI result
yet; adding an AI condition to that early event does not delay it until AI finishes.
AI processing must be enabled and complete successfully to produce the AI-ready
event. Creating a subscription does not analyze historical articles or invoke a
provider.
Use this signal for triage or enrichment. A high relevance score does not approve
a hunt; `hunt.approved` remains the trigger for analyst-approved execution.

Relevance is the shared assessment against the installation's AI context. It is
separate from a team's assessment, indicator maliciousness and analyst approval.
Missing, malformed or unverified relevance cannot satisfy a positive or negative
condition. Existing historical events are not retroactively enriched. A replaced
AI result or changed source prevents its old AI-ready action from being sent.
AI-ready payloads can also include the captured shared AI summary and relevance
reasons through `{{ ai.summary }}` and `{{ ai.relevance_reasons }}`. Their truncation
fields (`ai.summary_truncated` and `ai.relevance_reasons_truncated`) disclose the
bounded representation: 8,000 summary characters and four reasons of up to 500
characters each. `{{ item.summary }}` remains the RSS
summary. Rich AI narrative belongs to the AI-ready event; existing intelligence
events carry the smaller relevance metadata without duplicating that narrative.

The editor offers named, searchable choices for source identifiers and dropdowns
for enumerated values. Existing selections remain in the draft when their lookup
is unavailable. Preview a recent event before enabling the destination: the event
picker includes its captured article title and feed, and the result explains each
condition and unavailable field. Preview sends no request to the receiver.

### Choose payload fields and article text

For a template, use **Search payload fields**, select a **Payload field**, choose
an **Output key**, and select **Add payload field**. JSON body fields escape text
correctly, including quotes, line breaks and Unicode. Dotted output keys create
nested JSON objects; the picker prevents overwriting an existing field or a parent
object. Raw-body placeholders remain available for custom formats, with escaping
under the template author's control.

`{{ item.full_text }}` includes the retained extracted plain text. Include
`{{ item.full_text_status }}` alongside it to distinguish `available`, `truncated`,
`unavailable` and `source_changed`. Source revision, retrieval time, original
character count and included byte count are also selectable. Full text belongs in
request body values; it is rejected in URLs, query parameters, headers or JSON keys.

For structured automation, enable **Include extracted article text**. The optional
`data.article_text` object contains the text, availability status and source metadata.
This option defaults off and does not change the shared event's default payload.
The text is fetched only for the captured article revision; a later article body is
never substituted into an older event. An early ingestion event without fetched
text remains unavailable even if retrieval finishes later.

Text is bounded to **128 KiB of UTF-8**, and may be shortened further to fit the
existing structured-envelope limit. The status and included-byte count disclose
truncation. Large template bodies also have a serialized request budget; an
over-budget payload fails with an actionable error. Full text means the extracted
article prose, not raw HTML or linked attachments. Selecting it does not authorize
otherwise prohibited source disclosure: existing source access, destination policy,
credential and revision checks still apply.
Synthetic template tests that include article text additionally require
`read:items`, which is checked again immediately before sending. For AI-ready
events use a stored-event preview; synthetic AI-ready test sends are disabled.

Example: send a current accepted hunt with non-excluded malicious infrastructure:

```json
{
  "op": "all",
  "conditions": [
    {"field": "hunt_review_status", "operator": "in", "value": ["accepted"]},
    {"field": "ioc_role", "operator": "in", "value": ["malicious_infrastructure"]},
    {"field": "freshness_seconds", "operator": "lte", "value": 3600}
  ]
}
```

Conditions and freshness are checked again immediately before outbound I/O. Editing a subscription can therefore stop an already queued delivery from sending.

Global ATT&CK metadata is attached to extraction/indicator events already being emitted. A technique-only AI change does not manufacture an indicator-set change or a separate notification. Shared extraction has no dedicated inferred ATT&CK mapping field; arbitrary model descriptions cannot supply routing IDs. Format-valid IDs from source passages are source claims, not validation against the current ATT&CK catalogue.

## API and typed payload

API paths below are relative to `/api/v1` through the web proxy, or `/v1` on the API:

- `GET/POST /notifications/credential-profiles`
- `PATCH /notifications/credential-profiles/{id}`
- Existing webhook writes accept `payload_mode`, `conditions`, `credential_profile_id` and the opt-in `include_article_text` flag.
- `GET /notifications/webhooks/events?event_type=hunt.approved&limit=20` lists bounded event metadata available to the caller.
- `POST /notifications/webhooks/preview` accepts `{"event_id":"…","webhook":{…}}` and returns `matches`, `checks`, `missing_fields` and optional `automation_payload`. Template mode returns `template_body` or a `template_body_error`; it does not expose destination headers or credentials. It never sends an HTTP request.

Credential profiles expose metadata and configured-secret flags, never plaintext secrets. Creation and updates require write-notification access. Profile updates require the current `expected_revision`; an omitted secret keeps its current value, while `clear_auth_secret` and `clear_signing_secret` explicitly clear it. Setting and clearing the same secret in one request is rejected. Disabled profiles stop pending deliveries. A user may retain up to 100 profiles and reuse them across their own hooks.

Payload mode `automation_v1` sends actual JSON arrays, numbers and booleans:

```json
{
  "schema_version": "threatlens.automation.v1",
  "event_id": "8ca9c3eb-03ce-46cc-a390-599c3fc90cf7",
  "event_type": "intel.extraction.ready",
  "occurred_at": "2026-09-26T12:00:00+00:00",
  "action_id": "<stable action identity>",
  "source": {"type": "item", "id": "<item UUID>"},
  "data": {
    "source_revision": 4,
    "extraction_revision": 7,
    "indicators_complete": true,
    "indicator_count": 1,
    "indicators": [
      {
        "type": "domain",
        "value": "suspicious-host.net",
        "extraction_confidence": 0.95,
        "maliciousness_confidence": null,
        "role": "unknown",
        "excluded": false,
        "evidence": []
      }
    ]
  }
}
```

This abbreviated example omits evidence and other provenance fields. Extraction-ready with role `unknown` is not a recommendation to block or hunt that domain. Approved-hunt payloads include the stored hunt, approval identity and article indicator scope. Team-scoped events require current human team membership and feature permissions to preview or deliver.

Legacy clients may continue sending their existing webhook write shape. Omitting
`payload_mode`, `conditions`, `credential_profile_id` or `include_article_text`
preserves the corresponding saved value during updates; explicit `conditions: null`
or `credential_profile_id: null` clears that setting. The effective configuration
is validated, so an old client cannot silently switch a retained automation hook
away from `POST`. Run migration `0125_webhook_article_text` and upgrade the API and
all workers together. AI-ready subscriptions, relevance criteria and new snapshot
template fields use webhook configuration schema v3, so older workers defer instead
of ignoring their semantics. Earlier conditions/credentials retain their existing
schema-v2 compatibility boundary.

Automation retries and replays retain the accepted body and destination snapshot,
including when a legacy event uses Automation v1. Templates using the new article
text or AI snapshot variables also retain their accepted request across retries.
Subsequent URL, template or payload-mode edits do not rewrite those saved requests.
Current authorization, conditions and credentials still apply before sending.
Ordinary template-to-template retries retain their existing context-refresh behavior;
switching to Automation v1 does not convert an already queued template request.

A rendering failure has no accepted request to replay. Frozen article-text/AI
requests and generic replays return HTTP 409 instead of sending unexpanded
placeholders or an empty body. Correct or reduce the template, verify it with a
stored-event preview, and use a future matching event. Ordinary legacy template
retries can still render corrected configuration from their current context.
The rendering outcome is retained separately from later policy diagnostics, so
another failure cannot accidentally make an unrendered request sendable.

## Verify signatures and deduplicate actions

Signing resolves the live profile immediately before each outbound request, after authorization/lease renewal. Authentication secrets and signatures are added only to the actual request; they are not copied into rendered delivery snapshots. Profiles participate in encrypted-data health inventory and support the application's previous encryption keys.

Signed requests carry:

- `X-ThreatLens-Signature: v1=<HMAC-SHA256 hex>`
- `X-ThreatLens-Timestamp`: Unix seconds
- `X-ThreatLens-Event-ID`: stable across retries/replays of the event
- `X-ThreatLens-Attempt-ID`: delivery ID and attempt number
- `X-ThreatLens-Key-Revision`: credential-profile revision

The signing input is the exact byte concatenation `v1\nTIMESTAMP\nEVENT_ID\nATTEMPT_ID\nBODY`. Verify the raw body before JSON parsing or normalization. For legacy test sends without an event, the logical ID represents that test receipt; it is not an automation action. Existing `X-ThreatLens-Delivery-ID` and retry-source headers remain available.

This Python example performs signature and envelope checks. Integrate it with the receiver's own request-body limit, duplicate-header handling, authentication and durable transaction:

```python
import hashlib
import hmac
import json
import time


def verify_automation(headers, raw_body: bytes, secret: str, *, now=None):
    headers = {key.lower(): value for key, value in headers.items()}
    if len(raw_body) > 270_336:
        raise ValueError("Payload too large")
    stamp = headers["x-threatlens-timestamp"]
    event_id = headers["x-threatlens-event-id"]
    attempt_id = headers["x-threatlens-attempt-id"]
    current_time = time.time() if now is None else now
    if abs(current_time - int(stamp)) > 300:
        raise ValueError("Expired or future request timestamp")
    prefix = f"v1\n{stamp}\n{event_id}\n{attempt_id}\n".encode("ascii")
    digest = hmac.new(secret.encode("utf-8"), prefix + raw_body, hashlib.sha256)
    if not hmac.compare_digest(
        headers["x-threatlens-signature"], "v1=" + digest.hexdigest()
    ):
        raise ValueError("Invalid signature")
    event = json.loads(raw_body)
    if event.get("schema_version") != "threatlens.automation.v1":
        raise ValueError("Unsupported envelope")
    if event.get("event_id") != event_id or not event.get("action_id"):
        raise ValueError("Invalid event identity")
    return event
```

After verification, validate the event type and complete evidence, then atomically insert an action-id deduplication record and enqueue the hunt. Return success only after acceptance is durable. Repeated attempts should return the prior accepted job rather than start another hunt. Keep deduplication history long enough to cover the operator's replay horizon; signature age validation alone does not provide idempotency.

For rotation, provision the next secret on the receiver, update the profile using its current revision, and then retire the previous receiver key after outstanding attempts settle. The key-revision header identifies the profile revision, including non-secret edits; it is not itself authorization. Pending requests use the current profile. Use separate profiles when destinations must not share authentication or signing trust.

## Recovery and limits

The delivery engine has durable attempts, bounded retries, circuit/concurrency/rate controls and replay history. It cannot guarantee exactly-once external execution. An interrupted request may have been accepted by the receiver; existing ambiguous-outcome paths suppress automatic retries and require investigation before deliberate replay. The receiver's action-id deduplication remains necessary even when the HTTP response looked like a failure.

HTTP success, including an accepted response, records successful delivery only.
Receivers can separately report job state and findings, poll policy updates, and
acknowledge withdrawals through the [execution protocol](automation-execution.md).
The [OpenSearch connector](opensearch-connector.md) implements a supported vendor
flow; an ambiguous external acceptance still requires reconciliation before any
new launch. Team-owned destinations and scoped receiver credentials are described
in [team integrations](team-integrations.md). Outbound OAuth token acquisition,
mutual-TLS client profiles and writable MCP actions remain outside this webhook
contract. The read-only MCP interface can supply additional authorized evidence.

Use delivery history and the receiver's job log together. A superseded-event diagnostic requires a fresh extraction or renewed review, not blind replay of old approval. A disabled/unreadable credential profile requires correction or key recovery before retrying. Preview is the first check for a condition mismatch or unavailable legacy metadata.

## Execution receipts and subsequent policy changes

Typed destinations can now report external job state and findings, and consume a
durable acknowledged withdrawal/replacement stream. HTTP delivery remains distinct
from hunt execution. See [receiver protocol, reference implementation, and retention
behavior](automation-execution.md). More precise subscription conditions are described
in [same-indicator matching](indicator-conditions.md).
