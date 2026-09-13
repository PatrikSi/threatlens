# CodeQL merge-candidate triage — 2026-09-13

This record reviews the results for `refs/heads/dev` at
`f81f21f1e9fc0e78d19f209a494029d48b8d40f8` from
[quality run 34746224373](https://github.com/PatrikSi/threatlens/actions/runs/34746224373).
Both CodeQL jobs completed successfully. Successful analysis execution does not
mean that the analysis reported no findings.

| Language | Analysis ID | Completed analysis timestamp (UTC) | Results |
| --- | --- | --- | --- |
| Python | `1768067807` | `2026-09-13T07:53:07Z` | 22 |
| JavaScript/TypeScript | `1768066655` | `2026-09-13T07:52:21Z` | 2 |

The alert query explicitly included `ref=refs/heads/dev&state=open`; all 24
returned instances identified the commit above. Fourteen findings persisted
from earlier analyses, and ten export-path findings were new. The earlier
custom-rule regex alert, #3, was absent from this candidate's results. GitHub's
default-branch alert list alone is insufficient to qualify this commit.

## Fourteen persisted findings

Links below pin the source to the analyzed commit. The conclusions apply to
the identified data flows, not every possible caller or future implementation.

| Alerts / rule | Disposition | Evidence |
| --- | --- | --- |
| #1 · `py/weak-sensitive-data-hashing` | Intentional high-entropy token digest | [API token generation and hashing](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/core/security.py#L163) hashes a bearer token containing `secrets.token_urlsafe(32)`. SHA-256 stores a verifier for a random 256-bit secret, rather than a human password. |
| #9, #10 · `py/weak-sensitive-data-hashing` | Intentional legacy verification compatibility | [Legacy bcrypt verification](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/core/security.py#L99) feeds the SHA-256/HMAC prehash into salted bcrypt verification. The intermediate digest is not the stored password hash. [New passwords and successful legacy upgrades](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/core/security.py#L73) use Argon2. |
| #16 · `py/weak-sensitive-data-hashing` | Intentional high-entropy challenge digest | [MFA challenge creation](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/services/local_mfa.py#L325) generates a UUID plus a random 256-bit secret. [The flagged hash](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/services/local_mfa.py#L684) verifies this challenge token, not the user's password or six-digit TOTP. |
| #4, #5, #14 · `py/clear-text-logging-sensitive-data` | Constant namespace misclassified as a credential | SARIF traces the literal [namespace strings](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/services/auth_rate_limit.py#L32) `password_verify` and `mfa_action` to the `namespace` logger argument at lines 106, 181 and 245. Neither string contains an account password. |
| #19 · `py/clear-text-logging-sensitive-data` | Credential kind misclassified as a credential | SARIF traces the literal `AUTH_API_TOKEN = "api_token"` to [the credential-kind field](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/api/deps.py#L745). The logged value identifies the authentication mechanism; it is not the bearer token. |
| #15 · `py/clear-text-logging-sensitive-data` | Hostname misclassified as a secret | SARIF traces `settings.trusted_proxy_hosts` to [the logged normalized DNS hostname](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/api/deps.py#L960). This field configures trusted proxy names, not credentials. |
| #17 · `py/insecure-protocol` | Not reproduced in the qualified backend image | [SMTP TLS](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/services/smtp_transport.py#L241) receives `ssl.create_default_context()`. The shipped runtime probe reports minimum TLS 1.2, required certificate verification and hostname checking; details and limits follow below. |
| #2, #13, #18 · URL checking / plaintext storage | Test fixtures, outside production request handling | [The OpenAI URL check](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/tests/integration/test_security_surface_api.py#L111) asserts an error message. [The recovery test](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/tests/recovery/test_recovery_manifest.py#L235) writes a synthetic secret to its temporary fixture and checks that fingerprint output omits it. [The navigation test](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/web/src/workspace/workspaceModel.test.ts#L284) checks rejection of an injected route; production navigation resolves routes from the local trusted registry. |
| #12 · `js/double-escaping` | Confirmed P3 display defect; corrected in `277cf04` | [Sequential entity replacements](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/web/src/pages/alertPageModel.ts#L97) turn `&amp;lt;tag&amp;gt;` into `<tag>` instead of the intended literal `&lt;tag&gt;`. Direct execution of the source decoder reproduced this. The result is [rendered as React text](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/web/src/pages/AlertOccurrenceDetail.tsx#L245), so the observed defect does not execute HTML. [The correction](https://github.com/PatrikSi/threatlens/commit/277cf047f942fd1d9824c54632925763a6208d04) decodes in one pass; independent review, eight focused tests and frontend lint passed. Final CI remains required. |

## Runtime checks and limits

The SMTP probe ran without network access in the qualified backend image
`sha256:5cbb96aff9ae5dbe72a8af266e4e38ab4aef8d3e3e61f8a2de2aa9ad4a55d491`.
Python `3.12.13` with OpenSSL `3.0.20` returned `minimum_version=TLSv1_2`,
`verify_mode=CERT_REQUIRED` and `check_hostname=True`. That image's runtime
source is `09485b4`; the SMTP implementation is unchanged in the analyzed
commit. This checks context policy, not negotiation against an external SMTP
server. Alternate Python/OpenSSL builds can have different defaults; an
explicit minimum version would make that policy more portable.

The logging alerts do not trace exception messages. Three throttle paths also
log Redis errors, and the proxy resolver logs an OS error. Normal Redis
connection/authentication error construction and the application's
[credential-redacting formatters](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/core/logging_config.py#L98)
were inspected; no actual credential leak was demonstrated. This is not a
guarantee that arbitrary future exception strings or alternate log handlers
cannot expose data. Logging error types instead of raw third-party messages
remains a separate defensive improvement.

No alert was dismissed or suppressed, and no severity gate was weakened.
Final merge qualification still requires CI on the final corrected commit.

## Ten new export-path findings

Independent source and runtime review found no reachable path injection for
#20–29. The SARIF source is the browser harness's
[typed UUID route parameter](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/web/browser/server/fixture_server.py#L154).
The production [Celery entry point](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/tasks/export_tasks.py#L58)
also parses `uuid.UUID` before invoking the worker. The other scratch-path
component is a [server-generated UUID claim token](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/services/export_job_worker.py#L97).

| Boundary / alerts | Evidence |
| --- | --- |
| Input to worker | Actual harness HTTP probes rejected malformed UUIDs and backslashes with 422, and traversal/absolute-path attempts with 404, without invoking the worker. A valid compact UUID reached the worker only as a canonical UUID object. |
| Scratch creation / cleanup · #28, #29 | [Claim-specific scratch directories](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/services/export_job_scratch.py#L13) use mode `0700`. Creation rejects an existing root symlink, stale cleanup skips root symlinks, and recursive deletion preserves nested symlink targets. Disposable filesystem probes verified these behaviors. |
| Artifact operations · #20–27 | [Artifact paths](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/services/export_artifacts.py#L321) come from `tempfile.mkstemp` inside that scratch directory. The user filename prefix affects only the [sanitized download name](https://github.com/PatrikSi/threatlens/blob/f81f21f1e9fc0e78d19f209a494029d48b8d40f8/backend/app/services/export_artifacts.py#L313). All six artifact formats stayed inside the temporary root with hostile prefixes; ZIP member names stayed relative and contained no traversal segments. |

These checks cover the present HTTP and task entry points. They rely on the
worker's private temporary directory and the deployment's filesystem isolation;
they do not make an unvalidated arbitrary future Python caller safe. No source
change or alert suppression was needed for these ten results.
