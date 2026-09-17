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

## Team AI context and hunt review

Open a team and select **AI context** to maintain its technology stack, priorities,
available telemetry and relevance criteria. Current members can read the context;
team managers with `write:teams` can edit it. Each save creates a new context
version. Do not include credentials: generation sends this context to the
configured article AI provider alongside the selected article excerpt.

Expand an article, select a team under **Team assessment**, and generate an
assessment. It is visible only to current team members who can also read that
article. Team membership does not override handling labels or token scopes.
Shared article extraction remains separate from each team's interpretation.

When **Suggested hunt cards** is enabled in AI settings, an assessment may include
hypotheses with supporting passages, telemetry requirements, benign explanations,
uncertainties and official ATT&CK references. Accept or reject each suggestion.
An accepted, current suggestion can create a team investigation with the source
article and a snapshot of the reviewed hypothesis. This requires
`write:investigations` in addition to assessment access. Nothing executes a hunt
or contacts an external security system automatically.

Investigation handoffs retain the supporting passages and analyst review before
the other hunt fields. Long snapshots become numbered notes, each within the
investigation note limit, created together in the same transaction. No hunt field
is silently cut off. Every part records the assessment and team-context revision;
normal investigation note permissions and history apply afterward.

Article or context changes mark old results stale and require regeneration before
review. Review notes stay attached to the revision being edited. Regeneration
preserves prior result revisions; it does not rewrite investigations already
created from them. See [AI enrichment](ai.md) for configuration and limits.

Unsaved hunt notes remain available for the signed-in session when you collapse
an article, switch teams or navigate to another page. Use **Resume hunt reviews**
above the workspace to reopen them; access is checked again before retained notes
are displayed. The browser warns before refresh or tab closure while any hunt
notes remain unsaved. Notes are held in memory, not durable storage: save them
before refreshing, closing the tab or signing out. You can deliberately discard
one assessment's drafts with **Reload saved reviews**, or all drafts from the
recovery dialog. Confirmed loss of article/team access removes affected drafts.

Saving a different hunt does not invalidate an unchanged note draft. A draft
advances to the new aggregate revision only when its hunt, saved review and
generated evidence baseline are unchanged. Changed evidence or another analyst's
review keeps submission blocked until you inspect the current result and reload.
If regeneration replaces a hunt entirely, its unsaved note stays available as a
read-only field for copying; it cannot be submitted against different evidence.

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

Migration `0106_article_team_intelligence` adds team context, assessment revisions
and the shared extraction column. Both new feature switches default to off.
Replace the API, web and AI workers together before enabling them or queuing team
assessments. Pending work uses the durable AI outbox and current provider budgets.
Restore quarantine interrupts queued assessments and disables AI automation.

Downgrades must preserve the migration's explicit protection checks. Export or
remove newly owned resources before removing their schema; do not bypass a guard
by changing ownership directly in SQL. Take a backup before a downgrade that
removes team metadata, editorial history or organization policy fields.
