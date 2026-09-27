# Read-only MCP access

ThreatLens can expose saved intelligence to an external Model Context Protocol
(MCP) client. The optional endpoint supports nine bounded read tools. Reading
through MCP does not run a model, generate a report, refresh an assessment, or
require an AI provider key. Existing processing determines which saved evidence
is available.

Existing **personal API tokens and service-account bearer credentials** remain
supported. Optional delegated OAuth access adds pre-registered public clients,
explicit browser consent and short-lived MCP-only tokens. See
[delegated authorization](#delegated-authorization) below. Browser OIDC login
can provide the consenting user's identity; provider API keys are never MCP credentials.

## Enable and connect

1. Use a deployment built from a revision containing the MCP endpoint. In the
   deployment's environment, set `MCP_ENABLED=true`. The default is disabled.
   Use the existing HTTPS ingress for remote clients. Set `PUBLIC_APP_URL` to
   the public ThreatLens URL if clients need absolute canonical record links;
   otherwise links are relative. For a browser client that sends `Origin`, set
   `MCP_ALLOWED_ORIGINS` to its exact HTTP(S) origin, for example
   `https://assistant.example`. Origins contain no path. This list is separate
   from `CORS_ORIGINS`; leaving it empty rejects requests carrying any Origin
   header while allowing clients that omit it.
2. Recreate the API service after saving all environment changes. Preserve the
   original Compose project, `--env-file`, and `-f` override options. For the
   default published-image deployment, the command is `docker compose up -d api`.
   Source builds must retain `docker-compose.build.yml`; follow the
   [source-build instructions](../../docker/README.md#run-the-development-stack)
   and use the same override files when recreating the service. Recreate the API
   again after any later MCP environment change, including the Origin allowlist.
3. In **Settings → API Tokens**, create a short-lived, dedicated token. Enter
   `read:mcp,read:items` in **Permissions (API scopes)** for article search and
   evidence. The token's owner must currently hold these permissions. Store the
   one-time secret in the client's secret store.
4. Configure the client with the endpoint URL and bearer header below.
5. List tools and call `search_articles` with `{"limit":5}`. A successful response
   contains `data.articles`, provenance, freshness, and truncation information.
   An empty article list is a valid result when no visible stored items match.

| Client setting | Value |
| --- | --- |
| URL through the bundled web proxy | `https://threatlens.example/api/v1/mcp` |
| URL when connecting directly to the API service | `https://api.example/v1/mcp` |
| Transport | Streamable HTTP with JSON responses |
| Authorization | `Bearer <token-from-your-secret-store>` |

Credentials must go in `Authorization`, never in a URL query parameter. Browser
session cookies and browser JWTs are not accepted as MCP credentials. Do not use
a model provider key here.

`read:mcp` must appear **literally in the stored credential scopes**. Default
tokens, empty-scope legacy tokens, and a wildcard such as `read:*` alone do not
opt a credential into MCP. Other scopes limit the tools and records the caller
can read; `read:mcp` grants no access to otherwise hidden records.

### Python client example

The following example uses the official Python SDK version exercised by the
protocol tests. Install it in a separate client environment; the ThreatLens
backend does not need an MCP SDK runtime dependency.

```bash
python3 -m venv /tmp/threatlens-mcp-client
/tmp/threatlens-mcp-client/bin/python -m pip install 'mcp==2.2.0'
export THREATLENS_MCP_URL='https://threatlens.example/api/v1/mcp'
export THREATLENS_MCP_TOKEN='<load-from-your-secret-store>'
```

Save this as `mcp_read.py` and run it with
`/tmp/threatlens-mcp-client/bin/python mcp_read.py`. Replace the token placeholder
through your secret manager or shell's secret-input mechanism before running it.

```python
import asyncio
import json
import os

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client


async def main():
    async with httpx2.AsyncClient(
        headers={"Authorization": "Bearer " + os.environ["THREATLENS_MCP_TOKEN"]}
    ) as http:
        transport = streamable_http_client(
            os.environ["THREATLENS_MCP_URL"], http_client=http
        )
        async with Client(transport, mode="auto") as client:
            tools = await client.list_tools()
            print([tool.name for tool in tools.tools])
            result = await client.call_tool("search_articles", {"limit": 5})
            if result.is_error:
                detail = "\n".join(
                    block.text for block in result.content if block.type == "text"
                )
                raise RuntimeError(detail or "ThreatLens MCP read failed")
            print(json.dumps(result.structured_content, indent=2))


asyncio.run(main())
```

## Tools and permissions

All tools require `read:mcp` in addition to the feature permissions below. The
catalogue is filtered for the authenticated principal. Discovery does not imply
that every record is accessible.

| Tool | Additional permissions | Arguments and behavior |
| --- | --- | --- |
| `search_articles` | `read:items` | Optional `q` (up to 200 characters), `feed_id`, `since`, `until`, `limit`, `cursor`. Matches stored titles/summaries. Dates use `first_seen_at` and require timezone-qualified ISO 8601 timestamps. |
| `get_article_evidence` | `read:items` | Required `item_id`; optional `text_limit` defaults to 12,000 characters and cannot exceed 16,000. Accepts a continuation `cursor`; returns stored article text, source revision, character offset, bounded extraction and freshness metadata. A changed source requires restarting pagination. |
| `get_team_assessment` | `read:items`, `read:teams` | Required `item_id` and `team_id`. Reads an existing assessment for a current member of the team; no generation or refresh. Human users only. |
| `get_investigation` | `read:investigations` | Required `investigation_id`; optional `limit`. Existing ownership, collaborator/team membership, and source-access checks apply. Human users only. |
| `get_report` | `read:reports` | Required `report_id`; optional `limit`. Reads an accessible saved report and bounded source metadata under the shared report export checks. |
| `get_indicator_assessments` | `read:items`; `read:teams` when `team_id` is supplied | Human credentials only. Article `item_id`, optional `team_id`, bounded `page`/`limit`. Current verdict, expiry, suppression, evidence and stale-result flags use the same permissions as the UI. |
| `get_hunt_queue` | `read:items`, `read:teams` | Human team membership; `team_id`, status/ownership, oldest/newest/due ordering, priority/overdue filters and bounded cursor pagination. No review or launch side effects. |
| `get_reviewed_publications` | `read:items`, `read:teams` | Human team membership; current accessible publication revisions and withdrawal status. This does not grant approval or subscribe a consumer. |
| `lookup_attack_technique` | `read:items` | Exact ATT&CK identifier, such as `T1059`; returns the installed catalog's official technique and detection-strategy references. |


IDs are UUID strings. Unknown argument fields are rejected. Collection limits
default to 20 and range from 1 to 50. Service-account credentials can use only
`search_articles`, `get_article_evidence` and `lookup_attack_technique` when their current roles and
credential scopes permit them. The service-account permission allowlist excludes
`read:reports`, so report retrieval requires a personal token. Team-assessment and
investigation tools also require a human user.

Search results use stable descending `first_seen_at,item_id` order. Pass
`next_cursor` back with the same search arguments, including `limit`. Cursors
expire after 30 minutes and bind the search filters, principal, credential, and
authorization/data-policy revisions. A changed policy or credential requires a
new search. Investigation/report collections are bounded excerpts, without a
continuation cursor; follow their canonical record links when more detail is
needed.

Successful tool results include `data`, `provenance`, `freshness`,
`canonical_link`, `truncation`, and `next_cursor`. MCP returns the same structured
result as JSON text for clients that do not use structured tool content. Treat
`truncation.truncated=true` as an incomplete excerpt. A freshness timestamp does
not guarantee that a source is current; retained article text may be absent and
saved assessments may be stale.

Report results include `data.coverage`, preserving the saved grounding status,
coverage warnings, and available source-coverage information. `degraded`,
`insufficient_evidence`, and `human_edited` reports require those qualifications
to accompany their narrative. Oversized legacy metadata keeps its grounding
status with an explicit incomplete-details warning and truncation marker. MCP
clients must also honor response-byte truncation and follow the canonical report
link to inspect retained evidence before acting on a claim.

## Protocol and operating limits

The current protocol is **2026-07-28**. Requests carry protocol metadata in
`params._meta`; HTTP protocol, method, and tool-name headers must match the body.
The endpoint supports `server/discover`, `tools/list`, and `tools/call`. It also
supports the **2025-11-25** `initialize`, `notifications/initialized`, `ping`, and
tools flow for compatible older clients. No session ID is issued or required.
See the official [Streamable HTTP specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http)
and [version compatibility rules](https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning).

Each message is a separate POST. Responses use `application/json`; clients should
advertise `Accept: application/json, text/event-stream` as required by MCP. The
server does not offer SSE streams, subscriptions, prompts, resources, JSON-RPC
batches, asynchronous tasks, or tool mutations. GET and DELETE return 405;
OPTIONS supports explicit-origin browser preflight.

| Environment variable | Default | Limit or purpose |
| --- | ---: | --- |
| `MCP_ENABLED` | `false` | Enable the endpoint explicitly. |
| `MCP_ALLOWED_ORIGINS` | empty | Comma-separated exact HTTP(S) browser origins; no wildcard. |
| `MCP_REQUEST_MAX_BYTES` | `16384` | Maximum request body bytes; configurable from 1,024 to 65,536. |
| `MCP_RESPONSE_MAX_BYTES` | `65536` | Maximum complete JSON response, including duplicate text/structured content; configurable from 16,384 to 65,536. |
| `MCP_REQUEST_TIMEOUT_SECONDS` | `15` | Operation/transfer deadline; configurable from 1 to 30 seconds. Initial database checkout and cleanup have the limitations described below. |
| `MCP_RATE_LIMIT_PER_MINUTE` | `60` | Redis-backed allowance per authenticated principal; source-IP allowance is five times this value. |
| `MCP_MAX_CONCURRENT_REQUESTS` | `4` | Configured concurrent MCP requests per API process; configurable from 1 to 64 and further limited by database pool capacity. |

Each admitted database-backed read acquires and pins two connections: one for
the authorized read and one for its audit record. Both are acquired before any
authorization locks, and remain reserved until response transfer and cleanup
finish. Effective admission per API process is
`min(MCP_MAX_CONCURRENT_REQUESTS, floor((DATABASE_POOL_SIZE + DATABASE_MAX_OVERFLOW) / 2))`.
Enabling MCP requires a pool capacity of at least two connections. For example,
the code-default pool of two permits one concurrent MCP request; the bundled
Compose API pool of eight plus two overflow connections permits the configured
default of four. Other API traffic shares this pool, so this cap does not reserve
connections exclusively for MCP.

Effective permissions are rebuilt after authentication under retained IAM,
handling-policy, principal, and credential locks. Later checks in that same
request reuse the locked permission snapshot while refreshing owner/credential
state and policy revisions in one query. This reuse ends with the exact original
transaction; it never crosses requests or survives commit, rollback, or a changed
savepoint. Credential, OIDC-assertion, and temporary-elevation expiry still limit
publication and the remaining transfer allowance.

The MCP deadline bounds SQL work, new response output, and response transfer.
Initial shared-pool checkout, connection establishment, and cleanup cannot always
be cancelled immediately and may overrun that deadline. The ordinary
`DATABASE_POOL_TIMEOUT_SECONDS` and `DATABASE_CONNECT_TIMEOUT_SECONDS` settings
still govern connection acquisition, and cleanup can add elapsed time. The MCP
setting is therefore not a hard wall-clock upper bound for the entire request.
No authorization locks are held while the initial connections are being
acquired; after acquisition, an expired deadline prevents the read from proceeding.

JSON nesting is limited to 16 and node count to 2,048. Request IDs are strings of
at most 128 characters or JavaScript-safe integers. The parser rejects duplicate
JSON object members, nonfinite numbers, NULs, and unpaired Unicode surrogates.
Pagination and text limits can reduce the returned data further than the overall
response byte cap.

## Data disclosure and troubleshooting

MCP is a data disclosure path to the client you configure. Current account
eligibility, credential expiry/revocation, permissions, handling labels, and
derived-record lineage are checked through the shared read/export boundaries.
Ordinary article reads use the same label policy as article export; composed
reports and investigations additionally use their source-envelope checks. Tool
results contain source material that clients must treat as untrusted data, not
as instructions or authorization to execute another tool.

ThreatLens cannot control how an external client or its model provider stores,
logs, shares, or retains returned information. Source deletion, retention cleanup,
label changes, and token revocation stop later authorized access; they cannot
recall copies already delivered. Approve the client destination and its retention
policy before issuing the dedicated credential. Retrieval itself does not call a
model, but the external client may send the retrieved data to its own provider.

| Symptom | Action |
| --- | --- |
| 404 before discovery | Confirm `MCP_ENABLED=true`, the API was recreated, and the endpoint path is correct. Unknown RPC methods also return 404 with JSON-RPC code `-32601`. |
| 401 | Supply a current ThreatLens personal or service-account bearer credential. Cookies, JWTs, provider keys, and revoked/expired credentials are insufficient. |
| 403 | Check the literal `read:mcp` credential scope, current principal permissions, and exact Origin allowlist. |
| Tool missing or record unavailable | Check feature scopes, current membership, ownership, handling labels, and saved-source access. Service-account credentials expose only the two article tools. |
| HTTP 400 with `-32020` | Correct missing or mismatched MCP headers. With `-32022`, select one of the error's supported protocol versions. |
| HTTP 413 or a truncation marker | Reduce request size, `limit`, or `text_limit`; open the canonical record for omitted detail. |
| HTTP 429 | Respect `Retry-After` and reduce request rate/concurrency. |
| HTTP 503 with `mcp_database_busy` | Database lock contention, a deadlock, or a serialization retry interrupted this read. Respect `Retry-After`; repeated failures warrant investigating database wait activity. |
| HTTP 504 or a connection interrupted during transfer | Retry the read with a smaller result. No tool mutation needs reconciliation. |

PostgreSQL statement cancellations use the same `mcp_deadline` error as the
application deadline. Errors remain JSON-RPC envelopes even when rollback or
connection cleanup also fails. Explicitly allowed browser origins can read all
transport errors, their `Retry-After` guidance, and `X-Request-ID` correlation.

Use the `threatlens.mcp` logs and response `X-Request-ID` to correlate admission,
`mcp_request_prepared`, and `mcp_request_complete`. The final event records the
wire status, transfer completion, deadline/disconnect outcome, elapsed time, and
cleanup outcome. A status of 200 with `transfer_complete=False` is an interrupted
response, not a successfully delivered read. `mcp_rollback_failed`,
`mcp_invalidation_failed`, and `mcp_cleanup_failed` expose the exception type
without logging database queries, credentials, or returned article content.

Domain failures and invalid tool arguments use tool results with `isError=true`;
malformed protocol envelopes or RPC parameters use JSON-RPC errors. Disabling MCP
or revoking its credential does not alter saved evidence, reports, or the existing
AI processing configuration.

## Development checks

Protocol tests run without a database. A separate test dependency set pins the
official SDK used by the wire checks:

```bash
python3 -m venv /tmp/threatlens-mcp-tests
/tmp/threatlens-mcp-tests/bin/python -m pip install -r backend/requirements-mcp-test.txt
cd backend
/tmp/threatlens-mcp-tests/bin/python -m pytest --confcutdir=tests/unit \
  tests/unit/test_mcp_protocol.py tests/unit/test_mcp_sdk_protocol.py
```

These checks exercise the SDK's HTTP transport against a mocked HTTP endpoint
around the protocol adapter. A second test exercises both protocol modes against
the real FastAPI endpoint, a scoped token, stored article evidence, and disposable
PostgreSQL/Redis. With Docker available, run it from `backend`:

```bash
/tmp/threatlens-mcp-tests/bin/python -m pip install \
  -r requirements-dev.txt -r requirements-mcp-test.txt
env -u THREATLENS_TEST_DATABASE_URL -u THREATLENS_TEST_REDIS_URL \
  /tmp/threatlens-mcp-tests/bin/python -m pytest tests/integration/test_mcp_sdk_http.py
```

The dedicated SDK CI job runs both sets. The route tests use an in-process HTTP
transport. To exercise the bundled nginx configuration and Uvicorn over actual
HTTP, run the disposable proxy smoke from the repository root:

```bash
python3 scripts/verify_mcp_proxy.py \
  --sdk-python /tmp/threatlens-mcp-tests/bin/python
```

The script requires existing local backend, web, PostgreSQL, and Redis images;
use its `--help` options to select their names. It mounts current backend source
and nginx configuration over those images, creates synthetic data, and publishes
only a random loopback port. It does not read the deployment `.env` or connect
to the running stack. This verifies the current source and proxy configuration,
rather than rebuilding or qualifying a release image. It does not test a
particular hosted assistant's configuration/OAuth flow or production capacity.

Authorization and retrieval tests cover additional application boundaries
separately. See the [qualification record](../reviews/2026-09-17-mcp.md) and
[ADR 0006](../architecture/0006-ai-provider-profiles-and-mcp-boundary.md) for the
implementation boundary and deferred features.


## Delegated authorization

Set `MCP_ENABLED=true`, `MCP_OAUTH_ENABLED=true`, and `PUBLIC_APP_URL` to the
public HTTPS application origin, for example `https://threatlens.example`.
Loopback HTTP is allowed for development. This OAuth configuration targets the
bundled web ingress and its `/api/v1/mcp` resource URI. Existing direct-API bearer
clients remain supported; a separate custom OAuth resource path is not configurable.

Administrators pre-register a public client through
`POST /api/v1/mcp/oauth/clients` with `name` and `redirect_uris` (one to five exact
HTTPS URLs, or loopback HTTP callbacks with explicit ports). The returned
`client_id` is public. Configure it in the MCP client; registration does not grant
any user access. Redirect queries/fragments and wildcard callbacks are rejected.
Client registration requires administrator access and `write:tokens`. The
corresponding GET lists registrations; DELETE by client ID revokes all its
outstanding delegated credentials. At most 100 registrations are retained.
Client creation and revocation recheck the accepting account and exact browser
session or API credential after waiting for the IAM policy lock. Legacy session
bearers cannot administer clients; use a current browser session or scoped token.

Discovery is served at `/.well-known/oauth-authorization-server` and
`/.well-known/oauth-protected-resource/api/v1/mcp`. The bundled nginx proxy forwards
these routes. Custom ingress must forward them to the API as well as `/api/v1/`.
Metadata and `WWW-Authenticate` use the configured public origin, never a supplied
Host header. Browser-based external clients also need their exact origin in
`CORS_ORIGINS` for discovery/token requests; `MCP_ALLOWED_ORIGINS` only controls
the MCP transport and does not grant OAuth CORS access. Keep both origin lists
explicit. Follow the [MCP authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).

The authorization endpoint is `/mcp/authorize`. The client supplies `response_type=code`,
its exact `client_id`, `redirect_uri` and `resource`, `scope`, a random `state` of at
least 16 characters, and `code_challenge_method=S256` with its PKCE challenge.
The signed-in user reviews the named client, callback and requested scopes.
Approval requires the same local password/MFA or recent OIDC assurance used for
API-token creation. Denial returns `access_denied` to the validated callback.
The response includes `iss`; clients must verify issuer and state before exchange.

Authorization codes expire after five minutes and can be used once. The token
endpoint accepts bounded form-encoded authorization-code exchanges with the
original client, callback, resource and verifier. Access tokens expire after
15 minutes. Only `read:mcp`, `read:items`, `read:teams`, `read:reports` and
`read:investigations` can be delegated, capped by durable user permissions and
captured handling access. Current membership, policy and token revocation remain
checked on every read. Delegated `tlmcp_` tokens cannot authenticate ordinary APIs.
Revoke individual grants in **Settings → API Tokens**.

The `maintenance` queue sweeps OAuth retention every minute. Each transaction
removes at most 100 delegated tokens terminal for at least 24 hours, 100 expired
authorization codes and 20 revoked client registrations with no remaining codes
or grants. Ordinary API tokens and audit records are retained. Active MCP response
fences complete before cleanup can remove a credential boundary. A busy database
leaves cleanup for a later sweep; expiry/revocation still denies authorization.
Registrations with retained grants continue to occupy their slot until that
history clears. Each user can retain at most 1,000 delegated tokens plus live
unexchanged codes; reaching that bound returns `delegation_capacity`. Revoke unused
grants and let the retention sweep clear terminal history before authorizing again.
Upgrade API, beat and maintenance workers together after migration `0122_mcp_delegation`;
missing beat/maintenance workers prevent cleanup but cannot extend token lifetimes.

Refresh tokens, dynamic client registration and remote client metadata documents
are not implemented. Clients must support pre-registration and reauthorization.
This does not assert compatibility with every hosted assistant: qualify the exact
client, ingress and identity-provider configuration before rollout.

## Evidence continuation and exact lookup

`search_articles` accepts `indicator_type` and `indicator_value` together for exact
normalized matching. Defanged input is normalized; it does not turn substring
matches into indicator matches. The normal title/summary query remains available.

For `get_article_evidence`, send the returned `next_cursor` with the same `item_id`
and `text_limit`. Cursors are signed, expire after 30 minutes and bind the caller's
credential and policy revision. `data.text_offset`, `data.text_length` and
`data.source_revision` describe each page. Byte-limit truncation advances only
over characters actually returned. `source_revision_changed` means discard the
partial assembled text and start from the first page. Stored extracted evidence
retains its original quote offsets; partial article pages never imply whole-source coverage.
