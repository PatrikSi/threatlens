# Browser workflow tests

The frontend browser suite runs the actual Vite application in Chromium, with deterministic API responses intercepted by Playwright. It does not need a running ThreatLens backend, account, database, or `.env`. Unexpected API calls and external requests fail the suite. Publisher resource requests in the preview consent test are fulfilled locally.

With Node.js 22 and npm installed:

```sh
cd web
npm ci
npx playwright install --with-deps chromium
npm run test:browser
```

The test server binds port 4173 and refuses to reuse an existing process. To select a workflow, pass Playwright arguments after `--`, for example `npm run test:browser -- --grep 'session outage'`. Use `npx playwright show-trace test-results/<failed-test>/trace.zip` to inspect a failure. CI installs Chromium, runs this suite in the frontend quality gate, and keeps failure traces for seven days.

Coverage includes a dirty feed editor through failed session polling and recovery, confirmed expiry, cross-tab identity changes, nested browser Back/discard dialogs, keyboard panel movement and resizing, and per-article consent for external preview resources. These checks complement the real QueryClient regression tests in `SessionQueryProvider.test.tsx` and `SessionVerificationBoundary.test.tsx`. Browser tests use `*.browser.ts` filenames so Vitest does not collect them.

The preview response fixture is generated from the actual backend sanitizer and response headers. Regenerate it with the backend development dependencies available:

```sh
python web/browser/generate_preview_fixture.py
```

The backend quality gate checks that the committed fixture matches current backend behavior. To run inside an existing Playwright container with a different bundled browser revision, set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to its Chromium executable. CI uses the matching browser supplied by the pinned Playwright package.

# Session completion rules

Every auth event synchronously invalidates pending request leases and remounts a fresh QueryClient. API JSON and download transports reject late results, including transports that ignore abort. The mutation cache also rejects old-session success before calling a page's success handler. A transient `/auth/me` failure keeps page and portal drafts mounted beneath a blocking verification dialog; authenticated writes are paused until verification succeeds. A confirmed 401/403 removes protected content and invalidates pending operations.

For a mutation that spans several requests or awaits local work, capture `captureSessionLease()` once at the start of the mutation. Call `lease.assertCurrent()` before each subsequent request and after awaited local work, and pass `lease.signal` to cancellable operations. Capturing a fresh lease for every step would allow an old operation to continue using the next account's cookies. Component-owned success state must also remain scoped to its entity and submitted draft version. A browser cancellation cannot reverse a request already accepted by the server; server authorization and transactional checks remain authoritative.
