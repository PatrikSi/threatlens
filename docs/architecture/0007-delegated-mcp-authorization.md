# ADR 0007: Delegated MCP Authorization

- Status: Accepted
- Date: 2026-09-27
- Supersedes: the initial MCP catalogue and local-token-only scope in
  [ADR 0006](0006-ai-provider-profiles-and-mcp-boundary.md). Its provider routing,
  secret isolation, read-only boundary and current-access requirements remain.

## Context

The original MCP adapter supported five tools and manually supplied local bearer
credentials. The current catalogue contains nine read-only tools, including team
hunt queues, reviewed publications and indicator lookup. External clients can
also obtain explicit, short-lived delegated access through browser consent.
ThreatLens therefore acts as an authorization server for its own MCP resource;
its existing OIDC login integration remains a separate client relationship.

## Decision

Keep ordinary scoped bearer access compatible. Delegated access is a separate,
opt-in mode requiring `MCP_ENABLED`, `MCP_OAUTH_ENABLED`, a configured public
origin and an administrator-registered public client. Redirects must match a
registered URI. Discovery identifies the bundled `/api/v1/mcp` resource.

Consent uses the signed-in browser, CSRF protection and existing credential
step-up requirements. Authorization codes bind the client, exact redirect,
resource, requested read scopes and an S256 PKCE challenge. Codes are short-lived
and single-use. Exchange issues a 15-minute opaque token restricted to MCP;
ordinary REST endpoints reject that credential family. Dynamic client
registration and refresh tokens are outside the implemented contract.

The delegation stores the consented handling-label ceiling. Each read intersects
that ceiling and the delegated scopes with the user's current permissions,
account state and current evidence access. Consent never grants permanent access
to a team or source. Disabling clients, revoking credentials, offboarding users,
changing handling access and expiry all constrain subsequent reads. Credential
and policy fences remain subject to bounded lock and transfer deadlines.

Provider API keys, automation receiver credentials and publication feed tokens
are distinct credential families. None can substitute for an MCP grant. MCP
reads do not invoke providers, execute arbitrary tools, edit records or launch
hunts. Existing team ownership does not override source clearance.

## Lifecycle and recovery

Persist clients, consent codes and delegations explicitly. Capacity limits and
bounded cleanup prevent retained terminal grants from growing indefinitely.
Cleanup preserves required audit records. Backup restore quarantine invalidates
restored machine credentials; deployment recovery must re-establish authority
instead of trusting credentials issued against an older database state.

Logs record request identity, outcome and authorized operation without bearer
secrets, raw prompts or returned article text. Evidence pagination is bounded
and pinned to a source revision. Clients must restart retrieval after a revision
conflict instead of assembling evidence from different source versions.

## Consequences and verification

The authorization boundary now includes consent UI state, authorization-server
metadata, client registration and durable grant lifecycle. These require their
own negative and transition tests in addition to individual read-tool tests:

- PKCE and exact redirect/resource binding, code replay and unsupported scopes;
- ordinary API rejection of delegated tokens and non-MCP machine credentials;
- revocation, expiry, offboarding and handling changes after consent;
- changed consent requests and delayed UI responses;
- official-client discovery and reads through the deployed proxy path.

Local contract tests do not certify every hosted assistant or deployment. Qualify
the intended client, public origin, TLS and ingress configuration using the
[MCP operations guide](../pages/mcp.md). Retain pre-registration and short-lived
grants until additional client lifecycle features have explicit contracts and
corresponding security tests.
