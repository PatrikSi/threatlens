# Access Governance and Data Policy

ThreatLens combines fixed-role compatibility, code-owned permissions, custom
roles and groups, credential attenuation, object relationships, and handling
labels. Authorization is evaluated by the backend; navigation visibility and
the Settings UI are not security boundaries.

Unless noted otherwise, paths on this page are relative to the published
`/api/v1` base.

## Authorization Layers

An effective access decision applies these layers in order:

1. The human or service-account principal must be active and otherwise eligible.
2. Sealed system roles, custom role assignments, and group assignments produce
   the principal's effective permission set.
3. A personal API token can only attenuate that permission set. It cannot add
   authority the owning user does not have.
4. Handling-label policy restricts governed data to labels granted to the
   principal's durable roles. Temporary elevation does not grant handling-label
   access.
5. Resource-specific relationships, such as investigation membership, still
   apply after the general permission and handling-policy checks.

The legacy `admin`, `analyst`, and `viewer` value remains a compatibility and
break-glass projection. Custom roles are additive; they do not satisfy the
final-built-in-administrator invariant.

Persistent governance mutations require durable authority. Permissions obtained
only through temporary elevation may allow inspection, but cannot change IAM,
workspace role policy, or handling policy. Sensitive human mutations also require
an active, recently authenticated opaque browser session and the applicable local
or OIDC MFA assurance. Personal API tokens and service accounts cannot perform
those browser-only actions.

## Settings Access Workspace

The trusted frontend module `settings.access` is available at
`/settings/access` to principals with `read:iam`. Its Overview summarizes the IAM
catalog and, when separately authorized, service accounts, access reviews,
temporary elevations, action approvals, and data-policy state. Data that the
principal cannot read is not requested and is shown as unavailable rather than
being inferred from legacy role names.

The workspace has four tabs:

- **Overview** shows inventory counts and current handling-policy status.
- **Roles** manages custom roles and their code-owned permissions.
- **Groups** manages local group membership and role assignments.
- **Handling labels** manages label metadata, role grants, activation state, and
  the data-policy mode. This tab requires `read:data_policies`; writes require
  durable `write:data_policies` plus a sensitive browser session.

The Roles, Groups, and Handling labels editors preserve unsaved drafts across
server errors, reject stale revisions, and ask before a tab change discards a
draft. The route remains backend-authorized even if workspace policy hides its
navigation entry.

## Handling Labels and Data Envelopes

`data_policy_state` is a revisioned singleton with `disabled`, `audit`, and
`enforced` modes. Coverage version is separate from mode: a release can declare
complete implementation coverage while enforcement remains disabled until an
administrator activates it.

Two active system labels are invariants:

- `unrestricted` is the default label and is always available to an eligible
  principal.
- `quarantine` represents data whose provenance cannot be proved. It is
  restricted and cannot be archived.

Active restricted labels map to durable IAM roles. Every restricted label must
grant the built-in administrator role before enforcement can be enabled. Feeds
carry a handling label, and derived resources persist normalized data-access
envelopes rather than consulting the feed's current label later.

An envelope identifies the governed resource, the policy revision used to derive
it, each immutable source and parent-source relationship, and aggregate label
counts. This preserves capture-time lineage for reports, daily briefs,
investigations, alert occurrences, integration events and deliveries, AI task
runs and usage, and action approvals. Unknown or incomplete lineage is assigned
to quarantine instead of being treated as unrestricted.

Mode behavior:

- `disabled` evaluates eligibility but does not hide data because of labels.
- `audit` returns the same data as disabled mode and records decisions that
  enforcement would deny.
- `enforced` filters reads and blocks mutations or egress when the principal
  cannot access every required label.

## Canonical Route Attestation

`backend/app/core/data_policy_route_manifest.py` explicitly lists every
canonical `/v1` operation by method, normalized path, route name, endpoint
identity, and governance class. The available classes are `public`,
`control_plane`, `request_context`, `captured_async`, `dynamic_target`, and
`egress_fenced`.

After all routers are mounted, application startup validates the live FastAPI
routes against that manifest. Startup fails if an operation is missing,
unmanifested, duplicated, renamed, bound to a different endpoint, or has an
unexpected `get_data_access_context` dependency. The successful result is
installed once as an immutable per-process attestation.

The manifest is therefore a reviewed security contract, not a discovery cache.
Any route addition, removal, rename, method change, or governance-class change
must update the manifest and its tests in the same change. Compatibility aliases
are outside the canonical `/v1` manifest; production exposes the canonical API
through the `/api/v1` web proxy.

## Activation Preflight and Evidence

`GET /iam/data-policies` and `GET /iam/data-policies/preflight` run the full,
authoritative activation preflight. A transition to `audit` or `enforced` repeats
that full scan while holding the policy-state row lock, so a previously loaded
green result is not sufficient to activate stale state.

The full preflight verifies:

- database/application coverage-version compatibility and both system labels;
- the installed route-manifest version, SHA-256 digest, operation counts,
  request-context count, and governance-class counts;
- active feed-label assignments and restricted-label role grants;
- the approval action registry's target-policy declarations;
- supported envelope types plus source, label, count, and revision parity;
- envelope completeness for retained governed resources;
- exact system-versus-governed lineage for retained AI task and usage history;
- normalized audit labels and feed lineage;
- active-label references and capture-time/effective metric-cohort provenance;
- exact target-policy scope and lineage for retained action approvals.

The response includes `full`, `checked_at`, `evaluated_policy_revision`, coverage
versions, stable blocker codes and counts, and the route-manifest evidence. The
Handling labels UI displays that evidence, including the digest and per-class
operation counts.

Audit mode may tolerate only the two grant-configuration blockers
`restricted_labels_missing_admin_grant` and
`restricted_labels_without_roles`; all lineage, coverage, registry, and route
blockers must already be clear. Enforced mode requires no blockers.

Requests do not repeat the retained-data scan. While audit or enforcement is
active, request authorization runs the bounded runtime invariants: compatible
coverage, valid built-in labels, and the installed route attestation. A failure
raises a data-policy-unavailable error instead of evaluating with incomplete
policy state.

## Action-Approval Target Governance

Every registered approval-backed action declares a versioned target data-policy
contract. Registry validation is part of the full activation preflight. The
current contracts are:

| Action | Target | Data-policy source |
|---|---|---|
| `service_account.disable` | Service account | System control plane |
| `iam.role.delete` | Custom IAM role | System control plane |
| `ai.provider_attempt.confirm_not_sent` | AI provider-attempt receipt | Receipt's exact AI task run |
| `ai.provider_attempt.acknowledge_may_have_sent` | AI provider-attempt receipt | Receipt's exact AI task run |

Creating an approval resolves and authorizes the target before returning its
snapshot. Inaccessible and nonexistent targets use the same not-found behavior.
The stored request binds the action-definition version, target-policy version,
target ID and revision, immutable target snapshot, canonical payload digest,
requester, reason, and expiry.

System control-plane targets have an exact system contract and no envelope. An
AI receipt linked to a valid governed run receives a copied, immutable envelope
with the run's source lineage. An exact connection-test run can remain system
scoped. Missing or unverifiable source lineage becomes an explicit governed
quarantine envelope; it never silently becomes a system target.

List totals, pages, detail reads, and receipt reads all apply the same
handling-policy predicate. Creation replays and decision, cancellation, and
execution mutations reauthorize the stored target under the caller's current
policy context. Execution also rechecks both the original requester and original
approver, their durable action-specific permissions, the approver's security
version, the target snapshot/revision, and both fenced policy snapshots before
the side effect. A changed definition, target, or prerequisite invalidates the
approval instead of executing stale authority.

When coverage is active, a PostgreSQL writer fence rejects approval inserts or
updates whose declared system/governed scope and normalized lineage do not match
the registered version-1 contracts. Audit mode records stable `would_deny`
evidence for approval surfaces that enforcement would hide.

## Upgrade and Rollback Boundary

Migrations `0080_action_approval_policy` and `0081_data_policy_activation` must
run while data policy is disabled. Migration 0080 classifies retained approvals,
copies provable AI-run lineage, quarantines unresolved legacy lineage, and
installs the rolling-writer fence. Migration 0081 locks governed tables, runs a
self-contained database integrity guard, and advances coverage from `0` to `1`
only when the retained state is safe.

Migration 0081 does not change the policy mode. After the matching API and worker
release is deployed, review the full preflight in **Settings > Access > Handling
labels**, enable audit first, inspect would-deny evidence, then enable enforcement
only when the displayed preflight is clear.

Downgrade also requires disabled mode and the expected coverage version. Treat
the action-policy and coverage migrations as a quiesced deployment boundary:
stop API and worker processes, take and verify a backup, apply or reverse the
migrations, deploy the matching code, and only then resume traffic. Do not
manually edit `coverage_version` to bypass a guard.

## Retention and Lineage Pinning

`ACTION_APPROVAL_RETENTION_DAYS` defaults to 730 days, while
`AI_TASK_HISTORY_RETENTION_DAYS` defaults to 180 days. The history-maintenance
order and predicates preserve the cross-retention contract:

- a retained approval pins an AI task run referenced by its captured source or
  by its target receipt;
- a retained approval targeting an AI provider-attempt receipt pins the receipt's
  operation ledger;
- after an eligible terminal or expired approval is deleted, its now-unreferenced
  AI run and receipt can become eligible in the same maintenance pass;
- normalized envelopes are removed only for resources that were actually
  deleted, after the delete predicates are rechecked under row locks.

This prevents the shorter AI-history window from destroying provenance needed by
the longer approval-history window. Retention remains bounded and is not an
immutable audit archive; operators that require longer evidence preservation
must export or back up the database under their own retention policy.

## API and Permission Summary

| Endpoint group | Read permission | Mutation permission and boundary |
|---|---|---|
| `/iam/permissions`, `/iam/roles`, `/iam/groups` | `read:iam` | Durable `write:iam` |
| `/iam/data-policies` | `read:data_policies` | Durable `write:data_policies`; sensitive human browser session |
| `/iam/service-accounts` | `read:service_accounts` | Durable `write:service_accounts` |
| `/iam/elevations` | `read:elevations` | `write:elevations` plus workflow-specific durable authority |
| `/iam/action-approvals` | `read:approvals` | Request/execute require durable `write:approvals` plus requester authority; decisions require durable `approve:approvals` plus approver authority; all mutations require a sensitive browser session |
| `/iam/access-reviews` | `read:access_reviews` | Durable `write:access_reviews`; sensitive browser session |
| `/workspace` | `read:workspace` | Personal `write:workspace_preferences` or durable `write:workspace` |

Governance update, delete, and lifecycle mutations use optimistic revisions.
Endpoints that require an `Idempotency-Key` persist replay receipts. Consult the
generated API reference for the exact request and response schemas.
