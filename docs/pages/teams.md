# Named team workspaces

**Teams** groups shared dashboard views, investigations and alert queues under
current IAM group membership. Teams are resource ownership boundaries within one
ThreatLens installation; they do not create separate tenants or grant access to
otherwise restricted article evidence.

## Set up and delegate

1. In **Settings → Access control**, create a non-system member group and,
   optionally, a separate manager group. Add the intended users or configure
   existing identity-provider group mappings.
2. Open **Teams → Team administration → Create team**. Choose a name, a stable
   lowercase key, and the groups. Creating teams and changing their access
   bindings requires the existing IAM administration permissions.
3. Users in either group can open the team when they also have `read:teams`.
   Managers with `write:teams` can maintain team metadata. Shared content still
   requires its own feature permissions and current evidence access.

An administrator can inspect team configuration without receiving automatic
access to team content. Membership must come from the configured groups. Local
memberships remain valid until changed; OIDC memberships require a current,
unexpired assertion. Disabled or unapproved accounts cannot use team resources.
Disabling a team removes access while retaining its records.

## Shared work

- Save dashboard views with a team owner. Eligible team members can use the view;
  editing requires the team and saved-view write permissions. Personal copies
  remain available where sharing or editing is unavailable.
- Create team investigations for shared evidence and notes. Group membership
  supplies investigator access; the manager group supplies owner-level authority.
  Individual investigation membership overrides are not supported for these
  group-owned investigations.
- Follow the team's triage link to its occurrence queue. Create team watchlists,
  claim work and filter assignments. Managers can assign eligible members and
  set deadlines. See [Shared team triage](alerts.md#shared-team-triage).

Team ownership is fixed when a resource is created. Shared resources survive
deletion of their creator and are never silently transferred to another user.
Lists and assignment rosters are paginated. Shared links preserve context but
confer no permissions.

## Concurrent changes and recovery

Editors keep the revision associated with their draft. A conflicting save returns
`409` and preserves the draft for deliberate reload or reconciliation. Pending
saves disable their controls. A temporary access-check failure preserves the
workspace while disabling protected actions; confirmed withdrawal hides the
protected data. Mutations lock authorization, the actor and team before the
resource, and recheck time-limited membership after resource lock waits.

Group-backed access is additional to handling-label policies, caller token scopes
and account eligibility. Assignment validates the recipient's current feature
permissions and access to the occurrence's evidence; team membership alone does
not make someone an eligible assignee.

## Deployment

Migrations `0102_named_team_workspaces` and `0103_shared_triage_queues` add team
ownership and preserve personal resources. Deploy the matching API, web and all
classification/alert workers together before creating team watchlists. Old alert
workers assume personal ownership and must not consume team work. Stop producers
and affected workers, apply migrations, replace the workers, then resume work.
Use the same coordinated window for the
[report editorial migration](reporting.md#failure-recovery).

Downgrades must preserve the migration's explicit protection checks. Export or
remove newly owned resources before removing their schema; do not bypass a guard
by changing ownership directly in SQL. Take a backup before a downgrade that
removes team metadata, editorial history or organization policy fields.
