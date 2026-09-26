# September 26 comprehensive review

This review covers the current application and the recent indicator intelligence
and webhook expansion. Four reviewers divided authorization and evidence
boundaries, integrations and failure handling, frontend lifecycle and
accessibility, and exports, dependencies and system validation. Confirmed defects
were fixed with regression coverage; the review does not establish that every
possible failure or deployment configuration has been exercised.

## Findings and fixes

| Area | Confirmed issue | Implemented correction |
| --- | --- | --- |
| Evidence authorization | Repeated feed relabeling could expose indicator review notes written under a more restrictive label. | Assessments and history accumulate every captured handling label. Reads, writes and derived automation require the complete boundary. Missing lineage withholds automation. |
| Upgrade safety | Older edited assessments do not contain enough information to reconstruct all historical labels. | Migration `0110_indicator_review_lineage` conservatively retains every existing label for edited legacy assessments. Unedited assessments retain their original label. Downgrade refuses to discard a wider boundary. |
| Webhook authorization | Synthetic connection tests could send after their accepting token was revoked or expired. | Recheck the accepting credential, current permission and label scope through the final outbound request boundary; denied sends receive a correlated 403 and durable `not_sent` outcome. |
| Outbound transport | Opted-in plaintext HTTP did not enforce private-only addresses in the pinned transport. | Require the private-only transport restriction for HTTP, including DNS changes. HTTPS retains the configured private-network policy. |
| Webhook encoding | HTTPX treated form field pairs as a raw byte stream, causing form delivery to fail. Raw byte bodies also used its deprecated `data` path. | Encode form pairs explicitly and use `content` for form/raw bytes, preserving duplicate fields, Unicode, empty values, caller headers and signatures over the actual request body. |
| Input/error handling | Nested condition text could fail database persistence; malformed retained event fields could satisfy negated conditions. | Validate storage-safe text before persistence and treat malformed values as unknown, including under negation. |
| Event resilience | Historical hunt/extraction labels were also treated as a feed's current label, causing conflicts after legitimate relabeling. | Preserve historical snapshots independently while retaining a separate live source association. |
| Evidence freshness | An outdated source fingerprint could leave an analyst verdict displayed as current. | Mark the verdict historical when extraction is stale. |
| Export completeness | STIX and MISP omitted newly supported IPv6 addresses, URLs and email addresses. | Add all three mappings and test real serializers, STIX parsing and quoted values. |
| Automation safety | Raw extracted values were exported as detection candidates; AI relevance was treated as MISP threat severity. | Export MISP attributes with `to_ids=false` and unknown severity. Label STIX patterns unreviewed and distinguish match confidence from maliciousness. Disclose that raw exports do not apply team reviews or suppressions. |
| Preview reliability | Changing external-resource mode did not restart the iframe loading/recovery state. | Give each preview URL its own loading state, timer and iframe lifecycle. |
| Preference concurrency | Navigation reset deleted the preference revision, allowing an old revision-zero draft to become valid again. | Keep revisions increasing across navigation resets while preserving personal privacy consent. |
| Preference cache | An in-flight preference read could overwrite a successful privacy change with an older value. | Cancel the account's pending preference reads before publishing the saved response; retain an asynchronous regression for disabling consent. |
| Report UI lifecycle | Duplicate sibling React keys caused review controls to accumulate on report refresh. | Use distinct component identities; a parent-level regression preserves unsaved edits during polling and resets editor/evidence state on report selection. |
| Preview accessibility | Opening and closing the nonmodal original preview left keyboard focus unpredictable. | Focus its close control on opening and restore its connected opener on closing, while respecting modal isolation and preserving focus during resource-mode changes. |
| Browser test reliability | Session-outage tests could race automatic recovery, and the full three-browser real-server run shared a single-browser supervisor allowance. | Observe failed automatic retries before testing manual recovery, and retain a bounded ten-minute allowance per selected browser. |
| Dependencies | The Python audit reported two SoupSieve selector-parser advisories. | Upgrade the runtime lock and dependency/legal inventory to SoupSieve 2.9. No exploitable application selector path was demonstrated. |

SoupSieve's upstream advisories describe
[recursive selector parsing](https://github.com/facelessuser/soupsieve/security/advisories/GHSA-j934-xhv5-fg8f)
and [selector parsing resource consumption](https://github.com/facelessuser/soupsieve/security/advisories/GHSA-gjv8-xp57-g29c).
The repository's fixed selectors are not evidence of attacker-controlled selector
input; the dependency update addresses the audit without claiming such a route.

## Personal preview preference

**Settings → My account → Article previews → Always load external resources in
original article previews** persists a personal default. It starts off, explains
that browser requests disclose the user's IP address and viewing activity, and
retains a temporary override in each preview. Scripts, forms and nested frames
remain blocked; the iframe retains its sandbox and no-referrer policy.

The API change is additive. Privacy-only writes preserve navigation, older
clients omitting the field preserve consent, and organization layout policies
cannot opt users in. Account-scoped queries, revision conflicts, disabled editing
during save and error recovery protect asynchronous transitions. Navigation
resets preserve consent and the latest revision.

## Review scope

The review inspected AI dispatch and worker ownership, report approval/content
hashes, processing and retention boundaries, MCP credential and transport
lifetimes, bounded provider responses, export query/materialization limits,
logging redaction and correlation, deployment/recovery scripts, dependency
contracts, and UI permission, session, draft and dialog lifecycles. These areas
also run through the repository's regression suites. No speculative large
refactor or weakened authorization check was introduced to make a test pass.

## Validation

- The complete backend run finished with **3,867 passed, five expected opt-in
  skips, and one outdated webhook-test-double failure**. That fixture was
  corrected and its entire 31-case API/authorization group passed. The SDK and
  capacity opt-ins were exercised separately. Combined line/branch coverage was
  **86.61% overall** and **86.74% for reporting**, with every existing critical
  floor passing. New floors protect indicator lineage (95%), webhook conditions
  (90%) and webhook request authority (90%); their measured coverage was 100%,
  95.24% and 94.59%, respectively.
- The final form/raw transport correction passed a **370-case webhook batch**,
  including 24 new serialization/signature cases, current credentials and
  policy checks, retries, legacy deliveries, deadlines and private-address
  restrictions. This batch also contains the corrected strict route fixture.
  Combining the full run with this targeted follow-up gives **3,892 distinct
  passing backend cases and no unresolved failures**. This is an aggregate of
  those runs, not a claim that the initial full run had no failure. The transport
  follow-up emitted no HTTPX deprecation warnings.

- The full frontend suite passed **1,232 tests in 147 files**. TypeScript,
  ESLint, the production build and the production-bundle smoke passed. A final
  fixture-only typing correction also passed its focused regression.
- The complete real-server browser matrix passed **51 cases across Chromium,
  Firefox and WebKit**, including authentication, OIDC, session outages,
  exports/cancellation, processing recovery, teams, organization layouts,
  report review/publication and persisted preview consent with keyboard focus.
  All **six AI-enabled provider/settings and statistics cases** also passed
  against a separate explicitly enabled server, bringing real-server coverage
  to 57 passing cases across the three engines.
- The complete fixture-backed interaction matrix passed **81 cases across the
  same engines**. After the final report/preview changes, all 12 affected
  dashboard, preview privacy, dialog focus and Markdown cases passed again in
  a sequential browser run. These include real preview CSP/resource requests
  against local response fixtures and automated accessibility assertions.

- Both runtime dependency audits pass. The Python lock reproduces exactly, and
  `pip check` passes. The built backend image matches all 94 Python packages,
  all 107 OS packages and the checked-in package metadata inventory. The final
  image was rebuilt after the transport correction; its packaged form delivery
  passed in a read-only, network-isolated container, and its inventories match
  the checked-in files byte for byte.
- The isolated official MCP SDK suite passed 119 protocol/interoperability
  cases and two authenticated HTTP cases.
- Non-destructive recovery, bootstrap and deployment checks passed: 125 tests
  run, with four disposable Docker recovery cases deliberately deferred to the
  separately enabled drill.
- All four Docker recovery cases then passed against the freshly built backend
  image: backup/restore, failed-restore recovery, offline role upgrades, sequence
  preservation and limited-runtime-role migration round trips. All resources
  owned by the drill were removed; the running deployment was not targeted.
- The mixed-workload capacity smoke passed every budget. Consumer recovery
  took 19.46 seconds, peak sampled lock-wait query age was 25.26 ms, and
  application RSS increased by 40.72 MiB. It ran alongside other validation on
  the development host, so these are not comparative release-performance claims.
- The separately enabled capacity recovery profile also passed every budget.
  Accepted messages survived a forced Redis termination; the broker restarted
  in 0.48 seconds. After terminating an active prefork task child, durable
  article repair and task redelivery restored processing in 6.88 seconds without
  restarting the producer. Its disposable services were cleaned up.
- Whole backend application/test Ruff, Python compilation, source-size gates
  covering 887 production files, shell syntax, and generated API/preview-fixture
  consistency passed. All 26 coverage-gate tests passed after adding the new
  security-boundary floors.

The broader runs exposed the duplicate report-key defect and an outdated strict
webhook test double. The latter now explicitly asserts the captured credential,
permissions and handling-label scope; all 31 notification API/authorization
cases passed after correction. Initial browser runs also recorded Chromium
`ERR_NETWORK_CHANGED` during concurrent Docker network recreation and a
WebKit manual-retry race. The clean matrix ran after recovery operations ended,
with the test race corrected. No application authorization or error assertion was
relaxed to accommodate these failures.

## Operating considerations

- Apply migrations through `0110_indicator_review_lineage`. Previously edited
  reviews may become inaccessible to principals with narrower label access;
  the conservative boundary includes archived labels. History is preserved and
  its boundary is never automatically cleared. Queued team actions with changed
  policy fingerprints are withheld; hunts require deliberate review and
  reapproval against current evidence. See
  [team indicator review handling](../pages/teams.md).
- MISP detection flags and unknown threat severity are intentional behavior
  changes. Consumers should review raw extraction before enabling detections.
  See [export semantics](../pages/export.md).
- Local workload tests are development-host measurements. Sustained capacity on
  deployment hardware, actual third-party provider behavior, receiver-side SIEM
  actions, and testing with assistive-technology users require separate
  qualification. Automated accessibility checks do not replace that work.
- Existing credential-fence reuse spans export and webhook services. A future
  refactor could give that shared contract a domain-neutral module, with lock
  ordering and authorization regression coverage preserved.
