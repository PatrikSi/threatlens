# Browser workflow tests

Two suites run the real Vite application in Chromium, Firefox, and WebKit using
the browsers matched to the pinned Playwright package. The interaction suite
intercepts API responses for deterministic asynchronous races. The server suite
runs real FastAPI, PostgreSQL, Redis, and a controlled OIDC provider. Neither
suite needs an existing deployment, real account, or deployment `.env`.

With Node.js 22 and npm installed:

```sh
cd web
npm ci
npx playwright install --with-deps chromium firefox webkit
npm run test:browser
```

The interaction server binds port 4173 and refuses to reuse an existing process.
Select a browser or workflow with Playwright arguments after `--`, for example
`npm run test:browser -- --project=firefox --grep 'session outage'`. Unexpected API
calls and external requests fail this suite; publisher resources in the preview
consent test are fulfilled locally. Each article uses distinct resource URLs so
browser caching cannot hide an unexpected request after consent resets.

The interaction suite also serves the real application at the isolated
`http://threatlens-browser.invalid` origin. Only static assets are forwarded to
the suite's Vite server; API responses stay intercepted and no DNS or external
service is needed. Chromium, Firefox and WebKit verify their native nonsecure
context, where `crypto.randomUUID` is absent, then complete background export
and selected processing recovery workflows using `crypto.getRandomValues`.
The workflows cover ambiguous acceptance, reuse of the unchanged request key,
reopening recovery confirmation, and new keys after confirmed acceptance.
Schedule editing separately verifies both editorial-review policy choices
through versioned writes, editor reopening and page reload.

CI has an independent job for each browser. It runs both suites and retains
traces from failures, axe reports, and the disposable server log for seven days.
Each browser also runs the real provider-settings and AI statistics scenarios
with `--ai-providers`, including independent statistics permissions and
accessibility. These scenarios start no AI worker and send no external model requests. Their
traces use `test-results/real-server-ai-providers` and their server log uses
`browser-server-ai-providers.log`, preserving the authentication suite's evidence.
From `web/`, use `npx playwright show-trace test-results/<suite>/<failed-test>/trace.zip`
to inspect a failure. Evidence contains synthetic test identities and cookies;
never substitute real credentials in the fixtures.

Coverage includes a dirty feed editor through failed session polling and recovery, confirmed expiry, cross-tab identity changes, nested browser Back/discard dialogs, edits made during a pending feed save, keyboard panel movement and resizing, and per-article consent for external preview resources. These checks complement the real QueryClient regression tests in `SessionQueryProvider.test.tsx` and `SessionVerificationBoundary.test.tsx`. Browser tests use `*.browser.ts` filenames so Vitest does not collect them.

The preview response fixture is generated from the actual backend sanitizer and response headers. From the repository root (return there if you ran `cd web` above), regenerate it with the backend development dependencies available:

```sh
python web/browser/generate_preview_fixture.py
```

The backend quality gate checks that the committed fixture matches current backend behavior. `PLAYWRIGHT_CHROMIUM_EXECUTABLE` is an optional local override for interaction tests only; CI and the real-server Docker runner use matching packaged browsers.

## Real-server authentication and accessibility

From the repository root, with Python 3.12, backend development dependencies,
the frontend dependencies above, and Docker available:

```sh
python web/browser/server/run.py
# Select one browser or scenario:
python web/browser/server/run.py --project firefox --grep 'real OIDC'
# Enable AI only in the disposable server and exercise provider settings:
python web/browser/server/run.py --ai-providers --grep 'real AI provider settings|real statistics combines'
```

Use the backend virtual-environment interpreter when dependencies are installed
there. On Linux, `--docker-browser` runs the pinned Playwright image, including
all three matched browser binaries and system libraries; host Node is then
unnecessary. The frontend `node_modules` must still be installed. For example:

```sh
docker run --rm --user "$(id -u):$(id -g)" \
  --mount "type=bind,src=$PWD,dst=/work" -w /work/web node:22 npm ci
backend/.venv/bin/python web/browser/server/run.py --docker-browser
```

The launcher creates uniquely named PostgreSQL and Redis containers, maps them
to dynamically allocated loopback ports, migrates the database, and seeds a
disabled synthetic feed. Each test gets synthetic accounts. A fresh temporary
working directory and allowlisted environment prevent loading deployment
settings; Vite also uses the empty temporary environment directory. Inherited
`THREATLENS_TEST_DATABASE_URL`, `THREATLENS_TEST_REDIS_URL`, or
`THREATLENS_BROWSER_BASE_URL` are rejected. Application database URLs and secrets
are replaced, never reused. The launcher owns process groups and removes its
containers on normal exit, failure, timeout, or interrupt. A host crash or SIGKILL
can prevent cleanup; identify orphaned containers by the
`threatlens.browser.run` label before removing only that run's resources.

The controlled IdP and fixture controls exist only under `web/browser/server/`.
They are not imported by the production application. Controls require a random
per-run token; browser network guards allow only that run's app and IdP origins.
The harness uses loopback HTTP and test-only private-network OIDC allowances.
It does not start a Celery consumer or fetch the synthetic feed. Background export
tests publish to the disposable Redis broker, then a token-protected fixture
control invokes the real export worker entrypoint at a chosen time. Generation,
authorization checks, Redis locking, encrypted PostgreSQL artifacts, status, and
download use production code; these cases do not establish broker delivery or
worker restart recovery. Export scratch files use the run's temporary directory.
Processing recovery cases likewise advance one accepted selection through the
real promotion, publication, and worker helpers under a fixture control; the
remaining selection stays waiting so cancellation can be exercised reliably.

The server suite verifies:

- Named team creation through IAM groups; a member's shared alert claim,
  URL-based reopening and assignment filters; current access rejection after
  group membership is withdrawn. Alert evaluation uses a protected fixture
  control invoking production evaluation helpers, not a live Celery consumer.
- Organization-enforced start pages, navbar policy and dashboard layouts through
  real administrator and analyst sessions; policy restoration and editable
  personal layouts after enforcement is released.
- Report draft edits and discard, review notes, approval invalidation on return
  to draft, exact-revision publication, immutable published content and one
  publication event. Synthetic report evidence is seeded only in the disposable
  server; provider generation is not part of this browser assertion.
- HttpOnly session cookies, SameSite=Lax, persistent CSRF-authorized writes,
  rejected missing/incorrect CSRF, logout, and subsequent API rejection.
- A real API verification outage, draft preservation, keyboard recovery, and
  expiry applied to the actual PostgreSQL session record.
- A real second-tab sign-in that rotates the shared cookie and retires the
  previous account's editor.
- Browser redirects through authorization, one-use code exchange with verified
  PKCE, signed RS256 ID tokens, nonce validation, JWKS, UserInfo, and JIT sign-in.
  Wrong nonce, wrong signing key, and a token-endpoint outage must leave the
  browser unauthenticated.
- Axe checks of login/error, feed list/editor, account settings, and a dark
  editor, plus explicit labels, error announcements, keyboard activation,
  dialog focus containment, Escape, and focus restoration.
- Background CSV job acceptance, navigation away while queued, real generation,
  return and browser download with verified article contents; cancellation;
  rejection after accepting-session expiry, and isolation from another owner.
  The rendered export workspace with a ready job also runs the same axe rules.
- Processing worklist selection by keyboard, real acceptance, a saved run URL
  reopened after navigation, committed classification progress, cancellation of
  the remaining selection, reload persistence, and isolation from another owner.
  The resulting recovery workspace also runs the same axe rules.
- Named provider creation, reload and versioned update; write-only credential
  retention; rejected endpoint changes with retained credentials; default and
  report routing; protection against deleting an assigned provider; and deletion
  after clearing assignments. The interaction suite additionally checks delayed
  responses, draft preservation, conflicts, and keyboard/accessibility behavior.

Axe runs its WCAG 2 A/AA, 2.1 A/AA, and 2.2 AA tagged rules without blanket rule
exclusions. Reports retain violations and results requiring manual review.
Automated checks cover these rendered states, not every route or workflow.
See [Playwright's accessibility guidance](https://playwright.dev/docs/accessibility-testing).

## Manual assistive-technology protocol

No screen-reader assessment has been performed by the automated suite. Before
claiming assistive compatibility, use a disposable staging installation with
synthetic accounts and record OS, browser, assistive software/version, zoom,
theme, tested commit, observed announcement, and pass/fail for every step.
Use NVDA with Firefox on Windows and VoiceOver with Safari on macOS; the Linux
WebKit project does not establish Safari/VoiceOver compatibility.

1. Navigate by landmarks/headings, then by Tab and Shift+Tab. Identify the current
   page, account, and primary navigation; check visible focus at 200% zoom and in
   both themes without clipping the controls needed to continue.
2. Sign in with invalid credentials, then correct them. Verify labels, announced
   errors, preserved email, and predictable focus. Complete SSO and verify the
   return from the provider makes the authenticated context clear.
3. Open the feed editor from the keyboard, change a field, and open the discard
   confirmation. Verify each dialog's name/description, isolation from background
   content, Tab containment, topmost Escape handling, and focus restoration.
4. Trigger a controlled session-verification outage and recovery. Verify the
   recovery message is announced, protected controls cannot be activated, and
   the draft/focus return. Expire the session and verify private content is gone.
5. Read account/session tables by row and column, identify each action's target,
   and inspect a confirmation without completing any unintended revocation.
   Move and resize a dashboard panel by keyboard and verify its position and
   controls remain understandable without relying solely on visual location.

Record failures as workflow defects with reproduction steps. Passing axe and
keyboard tests alone does not prove WCAG conformance or screen-reader usability.
The automated harness also does not test production HTTPS/Secure cookies,
external IdP interoperability, MFA/step-up permutations, browser extensions,
mobile assistive software, or production proxy behavior.

# Session completion rules

## Shared editor lifecycle contract

`web/src/testing/editorLifecycleContract.ts` defines reusable behavioral tests for
editor controllers. The initial adapters in
`EditorLifecycleContracts.dom.test.tsx` exercise the real feed and tagging
controllers with `AuthProvider`, `SessionQueryProvider`, and a real memory router.
Only API responses and the current-user fixture are substituted; the mutation
cache, navigation blocker, discard dialog, and editor state run unchanged.

The shared cases cover delayed save completion with later edits, changing away
and reselecting, a refreshed inventory, a rejected save followed by refresh,
cancel/discard navigation, and a session change while a save is outstanding.
Assertions check retained draft values, visible discard dialogs, actual route
transitions, and isolation from late cache writes. New editors can supply the same
small test driver without adopting a generic production form framework.

Keep domain-specific tests alongside the editor. For example, schedule and report
tests must still verify that the original resource version remains attached to its
draft; feed/tagging tests verify server normalization of unchanged fields. A
generic rejected-write test does not establish an endpoint's concurrency contract.
The existing browser workflows continue to cover real focus, input, and session
verification behavior; controller contracts do not replace them.

## Session fences

Every auth event synchronously invalidates pending request leases and remounts a fresh QueryClient. API JSON and download transports reject late results, including transports that ignore abort. The mutation cache also rejects old-session success before calling a page's success handler. A transient `/auth/me` failure keeps page and portal drafts mounted beneath a blocking verification dialog; authenticated writes are paused until verification succeeds. A confirmed 401/403 removes protected content and invalidates pending operations.

For a mutation that spans several requests or awaits local work, capture `captureSessionLease()` once at the start of the mutation. Call `lease.assertCurrent()` before each subsequent request and after awaited local work, and pass `lease.signal` to cancellable operations. Capturing a fresh lease for every step would allow an old operation to continue using the next account's cookies. Component-owned success state must also remain scoped to its entity and submitted draft version. A browser cancellation cannot reverse a request already accepted by the server; server authorization and transactional checks remain authoritative.

Modal focus order uses the pinned `tabbable` utility from the
[focus-trap project](https://github.com/focus-trap/tabbable). Stacking and Escape
remain coordinated by the application. Empty saved-view dialogs are exercised
in Chromium, Firefox, and WebKit, including forward/reverse cycling and actions
that become hidden while the dialog remains open. DOM tests provide explicit
layout rectangles because JSDOM does not calculate layout.
