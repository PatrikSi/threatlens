# Team AI destinations and capacity

Team assessment context is sensitive: an approved model for one team may be an
unsuitable destination for another. ThreatLens now separates installation routing,
team destination approval and local account admission.

## Approve destinations, then select a route

An AI administrator can open **Teams → Team administration → select a team →
Team AI destinations**. Approvals require an administrator account and `write:ai`;
reading the administrative policy requires `read:ai`. A team manager with
`write:teams` can select a previously approved route in **Teams → AI destinations**.
Membership grants neither AI administration nor article access.

- An unconfigured team retains the existing installation route: explicit team
  assessment routing, enrichment routing, default provider, then legacy settings.
- Once configured, the approved provider-key list is an allowlist. An empty list
  blocks team AI requests. `legacy` explicitly approves the legacy connection;
  `profile:<UUID>` approves one named provider identity.
- An empty selected destination inherits installation routing, subject to the
  allowlist. Changing a team selection affects new assessments only.
- Handling-label restrictions narrow the allowlist. A label mapped to an empty
  array denies all destinations; omitted labels use the approved list. Both the
  article's captured label and current label apply, including the unrestricted
  label. Handling approval never replaces the existing data-policy egress check.
- Provider secrets stay in AI settings. Team readers see approved names/IDs and
  availability, not credentials or provider configuration.

Use **Restrict a handling label** to choose a named label, then select its allowed
destinations. Clearing every destination explicitly blocks AI for that label;
**Use all approved destinations** removes the additional restriction. Label
choices require `read:iam`. If choices cannot be loaded, existing restrictions
remain visible and are preserved. The editor rejects malformed provider keys,
unapproved selected routes, and label restrictions containing removed providers
before saving. The API continues accepting the existing UUID-keyed policy format.

Policy updates use their own optimistic version. Drafts retain the version they
were opened against, disable editing while submitting, and expose conflicts
without overwriting unsaved changes. Current permissions are checked again after
waiting for locks. Administrative approval and delegated selection are audited.

The selected provider identity and profile version are captured when a task is
queued. Workers do not silently fall back or move a queued prompt after a route
change. Every paid request checks the actual selected destination against current
team policy while holding the same shared team fence used by assessments. Policy
writers take the exclusive team lock. Removing approval blocks an unsent queued
request with an explanation; changing approval does not recall a request already
accepted by a provider. Review policy and explicitly queue new work when needed.

## Short-window account limits and team allocations

**Settings → AI → Shared provider account quotas** supports these cumulative
limits, in addition to each provider profile's limits:

| Limit | Scope and accounting |
|---|---|
| Concurrent requests | All configured profiles sharing the upstream account |
| Tokens per rolling hour | Account-wide input plus requested output reservations |
| Requests per rolling minute | Provider attempts, including ambiguous attempts; confirmed unsent attempts do not consume it |
| Tokens per rolling minute | Estimated input/output before sending; reported totals replace estimates when available |
| Concurrent requests per team | Team assessments compete fairly with one `shared` allocation for work without a team |
| Team tokens per rolling hour | Explicit `team:<UUID>` or `shared` allocation; also constrained by account and profile budgets |

Zero means no additional local limit. An omitted team allocation inherits account
and profile budgets without an extra team token cap. Local safeguards cannot
measure requests issued outside ThreatLens or guarantee an upstream provider's
billing/rate-limit semantics.

Admission uses short transactions and stable, sorted account/profile locks. A
request larger than its entire token allowance fails before I/O with a configuration
explanation. Saturated work defers safely; it does not hold provider worker slots
while sleeping. Locally blocked teams retain diagnostic wait entries but cannot
reserve the next account turn ahead of eligible teams. Unretried wait entries
expire after three minutes. A provider failure with an ambiguous usage result
retains the conservative token estimate; a confirmed unsent request charges zero.

Changing profile membership does not reset recent charges. Old groups retain
reservation attribution while new groups inherit their current members' charges.
Older clients omitting the new short-window/allocation fields preserve those
fields during unrelated quota updates.

Selecting a saved quota shows database-aggregated active requests, minute request
counts, reserved/reported/accounted rolling-hour tokens, oldest wait and team
reasons for deferral. At most 200 team rows are materialized; totals include all
allocations and truncation is disclosed. These are local admission measurements,
not provider invoices. Individual AI task history retains provider admission
messages for operators investigating a specific delay.

## Upgrade and rollback

Migration `0119_team_ai_governance` adds optional team policies and defaults all
new quota limits to disabled. It requires no changes to existing credentials.
Rollback refuses to silently remove configured destination or capacity policies;
export and deliberately remove those policies before downgrading. Keep an
independent backup before any schema rollback.
