# ADR 0004: Access Governance and Workspace Policy

- Status: Accepted
- Date: 2026-08-30

## Context

ThreatLens 1.8 has three complementary authorization mechanisms: the fixed
`admin`, `analyst`, and `viewer` account role; attenuating personal API-token
scopes; and object membership on investigations. UI routes and navigation also
repeat portions of those decisions. This is understandable at the current size,
but adding custom operators, identity-provider groups, service accounts, and
administrative workspace defaults would create conflicting authorization
vocabularies if each feature extended a different mechanism.

The upgrade must preserve every existing role, URL, API payload, token scope, and
object-access result. In particular, a custom role must never become a second way
to satisfy the final built-in administrator or local break-glass invariants.

## Decision

ThreatLens will add a modular access-governance subsystem while retaining the
existing `users.role` field as a permanent compatibility and break-glass
projection.

### Authorization model

- A code-owned permission catalog is the only source of valid permission IDs.
  Database roles may compose those permissions but may not invent new policy
  expressions.
- Sealed system roles project the current `admin`, `analyst`, and `viewer`
  behavior. The built-in administrator remains the only role that satisfies
  final-administrator and local break-glass checks.
- Custom role assignments are additive. They may be assigned directly or through
  groups; explicit deny rules and per-user permission exceptions are not added.
- Effective access is evaluated as account eligibility, effective permissions,
  credential-scope attenuation, data handling policy, and object relationship.
- Policy state is revisioned and initially evaluated from PostgreSQL on every
  request. A later cache must include policy and principal authorization revisions
  in its key and invalidation protocol.
- Existing API-token scope names, wildcards, delegation limits, and
  write-implies-read behavior remain published compatibility contracts.

### Identity and groups

- Local groups and identity-provider groups retain separate assignment sources so
  an OIDC synchronization cannot delete locally managed access.
- The system `all-users` group represents every active, approved human user. It
  preserves the old meaning of team-visible investigations without silently
  reinterpreting those records as membership in a newly created team.
- OIDC custom access mappings are additive to the existing fixed-role mapping
  payload. Missing or stale provider claims fail according to an explicit mapping
  policy; locally assigned roles are not overwritten.
- Non-human service accounts use separate principals and credentials. They cannot
  obtain browser sessions, passwords, OIDC identities, MFA recovery, temporary
  elevation, action approval authority, or sealed administrator permissions.

### Governance workflows

- Temporary elevation grants a complete custom role for a bounded interval. It is
  enforced using database time, cannot be self-approved, and does not require a
  cleanup job to expire.
- Action approvals bind the requester, action type, target, target revision,
  canonical payload digest, expiry, and decision. Execution reauthorizes the
  requester and approver and consumes an idempotent receipt.
- Access reviews snapshot assignments and provenance. Review expiry does not
  silently remove access; applying a revoke decision is an explicit, audited
  mutation.
- Handling labels are distinct from ThreatLens topical article classification.
  Feed restrictions are activated only after coverage checks confirm that feeds,
  items, search, statistics, exports, reports, AI sources, alerts, investigations,
  and outbound delivery cannot bypass them.

### Workspace policy

- The frontend owns a static registry of trusted module IDs, routes, components,
  required permissions, feature dependencies, and mobile behavior. Server policy
  can reference registered IDs but can never supply executable UI, routes, or
  arbitrary links.
- Feature availability, module availability, authorization, administrative
  navigation visibility, and personal navigation preference remain distinct
  states.
- Administrators may set role-based module order, visibility, landing page, mobile
  priorities, and dashboard defaults. Users may customize only entries declared
  optional by the effective policy.
- Role preview is an inert simulation using the same resolver as normal
  navigation. It is not impersonation and cannot make requests as another user.
- Unknown module IDs survive round trips and appear as version-skew warnings so an
  older frontend cannot silently erase newer policy.

### Audit and operations

- Existing audit columns and action names remain unchanged. Additive principal,
  credential, request, and source fields identify human, service, session, token,
  elevation, and approval context.
- Successful privileged mutations and their audit entries commit atomically.
  Rejected sensitive actions record stable reason codes without secrets, raw OIDC
  claims, token values, or full approval payloads.
- Restore quarantine revokes human and service credentials, temporary grants, and
  pending approvals. Data-policy and approval enforcement are quiesced-deployment
  boundaries because an older process would otherwise fail open.

## Compatibility and rollout

The rollout is additive and ordered:

1. Canonical permissions, sealed/custom roles, groups, effective-access responses,
   policy revisions, and additive audit context.
2. Trusted module registry, organization workspace policy, and user preferences.
3. Service accounts and separately prefixed machine credentials.
4. Temporary elevation, action approval, and access-review workflows.
5. Data handling labels and an explicitly activated feed-access policy.

Existing clients continue receiving the fixed `role` field and may ignore all
additive access and workspace objects. Existing OIDC provider updates do not gain
new fields whose omission could erase custom mappings. Populated governance state
blocks destructive downgrade with recovery instructions. Activating enforcement
requires API and worker processes from the same release.

## Consequences

- ThreatLens gains explainable least-privilege roles and enterprise identity
  workflows without adopting a general policy language.
- The fixed role remains visible alongside custom roles, which is less conceptually
  pure but makes upgrades and break-glass behavior predictable.
- Per-request policy queries favor immediate revocation and correctness over early
  caching. Query count and latency must be measured before introducing a cache.
- Data restrictions require broad query-surface coverage and therefore ship last,
  disabled until an administrator explicitly activates them.

## Deferred work

- arbitrary ABAC expressions and explicit deny policies;
- SCIM provisioning and provider event-driven deprovisioning;
- external or public investigation sharing;
- custom executable navigation modules or arbitrary administrator-supplied links;
- service extraction of the IAM control plane.
