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

Team settings show the current group names and copyable group IDs. Missing group
names are identified as unavailable rather than replaced with a generic current
group label. When several editors are open, leaving the workspace asks once
before discarding any unsaved changes; a clean editor cannot suppress another
editor's warning. Canceling the dialog preserves the drafts and keyboard access.

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
`write:investigations` in addition to assessment access. Accepting a hunt also
records a durable `hunt.approved` event. An explicitly enabled webhook subscription
can deliver that approved revision to an external system; ThreatLens does not
itself execute the suggested hunt or treat webhook acceptance as hunt completion.

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


## Team indicator review and exclusions

Expand **Indicators** on an article to inspect normalized values alongside their
original spelling, normalization steps and source passages. Select a team to
review an indicator. A review records a verdict, reason and optional expiry;
current members with `read:items`, `read:teams` and `write:teams` can save it.
History retains each saved revision, including retraction. Concurrent saves return
a conflict and require refreshing the baseline rather than overwriting another
analyst's decision.

Extraction confidence describes the deterministic match. AI indicator roles,
reported-versus-inferred assertions and supporting passages are separate fields.
ThreatLens only attaches AI evidence when the entity's complete canonical value
matches and the successful result still matches the current source provenance.
The AI contract does not provide a calibrated maliciousness probability, so
`maliciousness_confidence` remains null. A pattern confidence of 0.95 does not mean
an indicator is 95% likely to be malicious.

Reserved documentation/example addresses and current AI reference or benign
assessments are excluded from action selection by default. A current analyst
malicious verdict can override an AI reference/benign classification; it does not
override reserved-example exclusions or a team suppression. Benign, reference,
example and retracted analyst verdicts exclude the indicator for that team.
Expiry or a changed source/extraction revision makes a verdict historical rather
than a current decision. The API exposes both `expired` and `current` explicitly.

Team managers can maintain exact-value **Indicator suppressions** with a reason,
optional expiry, activation state and version history. Values use the same
canonicalization as extraction, including defanged addresses. These rules apply
only to that team; membership never grants access to a source handling label.
Assessment notes and history retain the union of handling labels captured by
every review, including reviews after a source is relabelled. Reading or
overwriting the assessment requires access to all those labels. Team automation
events retain the same boundary; an inaccessible or missing review boundary
withholds delivery rather than dropping a restrictive verdict from the payload.
Migration `0110_indicator_review_lineage` backfills the original label for reviews
that have never been edited. For previously edited reviews, earlier releases did
not retain every source label, so the migration conservatively captures every
existing handling label, including archived labels. These histories remain
preserved but may become inaccessible to principals with narrower label access.
The boundary is never cleared automatically. Existing queued team actions whose
policy fingerprint changed are withheld; a hunt needs deliberate review and
reapproval using the current evidence. Downgrade is blocked while an
assessment retains labels beyond its original label, to avoid weakening history
access. Deleting the parent item removes its assessment labels and history.

A changed effective analyst verdict emits a team-scoped
`intel.indicators.changed` event. Editing only its explanation does not resend an
unchanged indicator set. Approved hunt payloads apply the team's current verdicts
and suppression rules. Changing a suppression does not fan out across the entire
article archive; it affects subsequent snapshots and invalidates queued actions
whose effective indicator policy changed.

Hunt approvals have a distinct action identity for each deliberate transition to
accepted. Saving annotations on an already accepted hunt does not create another
action. A rejection followed by a deliberate approval creates a new action.
Queued delivery rechecks the accepted hunt's content/evidence fingerprint,
article/extraction and team-context revisions, current team access and effective
indicator policy. Annotation edits and investigation links do not change the hunt
content fingerprint. Nonblocking source locks protect the final delivery check;
busy sources produce a retryable result.

If delivery history or the match preview reports that an accepted hunt's action
was superseded, review the latest indicator evidence before sending again. The
hunt can still display **Accepted** because its narrative/context is unchanged;
that status does not mean the old automation action remains eligible. To request
a new action deliberately, reject the suggestion and then accept it after review.
This creates a new approval/action ID. Saving a note or retrying the superseded
delivery cannot silently launch a replacement action.

## Indicator storage and upgrades

Migration `0107_intel_assessments` adds occurrence evidence, extraction revision
state, team verdict/history and suppression/history tables. Existing normalized
IoCs remain compatible. Their old rows have no invented evidence passages; run
selected IOC extraction through the processing worklist to populate provenance
before reviewing them. Deploy the API and processing/AI workers together before
enabling the new automation subscriptions.

Evidence retains at most three occurrence passages for each article/indicator.
The API exposes `occurrences` and `evidence_truncated` so a selected excerpt list
is not mistaken for every mention. Indicator pages are bounded to 100 rows.
Automation snapshots contain at most 250 indicators and 256 KiB of event data.
Larger sets are recorded with their count, `indicators_complete: false` and an
explicit reason; automatic webhook delivery is withheld rather than sending an
apparently complete partial indicator list.

Occurrence passages and team review history are derived evidence. Like retained
report and AI evidence, they are not erased by the article-content-only retention
policy. Article or team deletion cascades the corresponding review records;
outbox/delivery snapshots follow their existing integration-history retention
policies. Raw-content purging invalidates the old extraction revision; a later
extraction can use the remaining title/summary with fresh provenance.


IOC and suppression uniqueness uses a stored UTF-8 SHA-256 key, preserving full
URLs up to the supported 4,096 characters without exceeding PostgreSQL's btree
entry-size limit. Exact canonical values remain available for comparison and
export. The upgrade rebuilds the affected IOC indexes and may take longer on a
large inventory. Downgrade refuses long retained values before removing schema;
export and remove incompatible values first if rolling back to the older indexes.
The digest is computed by PostgreSQL, so existing worker inserts continue to work
without supplying the new column.

## Team hunt queue

Open **Teams → select a team → Team hunt queue** to review generated hunt cards
across articles. Filter pending, stale, accepted or rejected suggestions and all,
owned or unclaimed work. Filters and encrypted continuation cursors stay in the
URL. Pages contain up to 25 accessible suggestions; a bounded authorization
scan can return an empty page with a next-page action. The queue does not expose
an unfiltered total. Refresh or return to the first page to include newly
generated work ahead of the current cursor.

The queue shows the article, source evidence age when a retrieved-article time
is known, reviewer, review time, owner and visible linked-investigation outcome.
Older reviews without recorded reviewer metadata remain unknown. Current team
membership and both current and captured source handling labels govern reads.
Investigation outcomes additionally require investigation read permission and
the investigation's own evidence boundary.

Claim/release commands require the current claim version and assessment version.
An owner or team manager may release a claim; an eligible member can take over
when the old owner loses team eligibility. Claims survive navigation and browser
refresh. A queued regeneration blocks new claims; publishing its new suggestions
removes obsolete claims atomically while audit history is retained. Stale evidence
must be regenerated before claiming or reviewing. Claims also protect the
existing per-article review and promotion actions against another active owner.

Accept or reject the evidence-backed suggestion, then promote an accepted card
into a team investigation. Further assignment, status, disposition and outcomes
use the investigation workspace. Review drafts retain their original assessment
version through background refresh; edits are disabled during submission and
conflicts require deliberate reload. Approval still describes the exact reviewed
evidence and does not itself establish that a hypothesis is true.

### Review priorities and approved AI destinations

The [hunt review workflow](hunt-review-workflow.md) adds oldest-first and overdue
views, versioned priorities/deadlines, durable in-app reminders and shared team
filters. [Team AI governance](team-ai-governance.md) lets administrators approve
destinations and handling restrictions before managers select an assessment route.
