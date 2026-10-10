# AI extraction coverage and quality evaluation

Evidence-backed extraction uses the existing item-enrichment provider selection,
credential and data-policy fences, usage accounting, durable provider receipts and
cancellation controls. Enable **Evidence-backed extraction** in AI settings.
Short articles retain the existing single-call response contract.

## Long articles

For articles exceeding 8,000 whitespace-normalized characters, extraction plans
up to eight contiguous sections of at most 8,000 characters. It prefers sentence
boundaries. Each section is validated against only its exact supplied title,
summary and article passage. Published evidence offsets refer to the complete
whitespace-normalized article, with the exact article ID, retrieval time, source
processing version and source hash retained.

The request budget reserves estimated input tokens plus maximum output tokens
before each call, to a total of 64,000. Individual output is limited to the lower
of the feature setting, the configured model's available context/output limit,
and 8,192 tokens. Each section permits one provider attempt. Reservations are
conservative and are not refunded based on optimistic provider usage metadata;
provider-side accounting can differ from input estimates. Every actual request
still passes the ordinary provider admission and policy checks. No model limits
are inferred from a provider name.

The article evidence panel shows completed and planned sections, processed and
uncovered character counts, and output-limit disclosures. The planner stops when
its section or token budget is exhausted; it never calls uncovered text processed.
When summaries are enabled, a final bounded synthesis combines every completed
section and cites section numbers. It uses at most 2,048 output tokens and fits
inside the same cumulative token budget. If it cannot fit, labeled per-section
summaries remain available, with their coverage disclosed. Stored section summaries
are capped at 900 characters so every processed section fits the aggregate;
truncation is disclosed, and verified primary quotations remain available. Organizational relevance
retains its initial assessment; team assessments independently select relevant
primary evidence across completed sections.
Shared extraction is merged across all completed sections. Equivalent entity
identities are deduplicated; conflicting roles/assertions remain distinct.
Combined results are bounded to 96 entities, 96 relationships, three passages per
entry and eight information gaps. Quotes prove traceability, not factual truth.

Checkpoints live in the item's existing enrichment row, inherit its access and
retention boundary, and contain at most 32 explicitly authorized section results. Public progress
omits provider payloads and task IDs. A redelivery of the same logical task and
source revision reuses completed sections. Worker recovery accepts a successful
receipt only when its exact request fingerprint has a validated durable section
checkpoint. A crash between provider success and checkpoint publication, a
reserved receipt or ambiguous transport still requires the existing receipt
reconciliation workflow. Automatic recovery never repeats those requests. Repair
sweeps also preserve the existing logical task, provider selection,
continuation authorization, completed sections and token allocation. They resume
only receipt-safe work, with at most three automatic recovery deliveries. Settled
nonretryable failures, missing legacy proof and cancellation require operator
review; a sweep never authorizes a fresh plan. Short-article work follows the same
receipt safety checks even though it has no section checkpoints. An
explicitly accepted new task has a new bounded plan; prior ambiguous receipts
still block unsafe I/O through the common runtime.

Cancellation and replacement delivery ownership are checked before requests and
checkpoint writes. Changing article evidence stops the owned attempt and keeps
previously published extraction as historical. A source/model/prompt change does
not relabel old checkpoints as current. A fingerprint covers the complete rendered
section plan, model settings, source revision and budgets. If that plan changes
within the same logical task (including after a deployment), extraction stops
before sending another request and retains its checkpoints and reservations.
Review the task outcome before explicitly starting a new reprocessing task.
Legacy checkpoints without that plan fingerprint also require a new task;
recovery never silently resets their budget or repeats paid work.
Migration `0111_extraction_sections` adds
the nullable checkpoint column without rewriting existing results.

If optional combined-summary synthesis fails, verified section extraction and
labeled section summaries are still published, with `synthesis_status` and an
information-gap disclosure. Authorization loss, cancellation, changed evidence
and superseded execution still block publication. A provider-success receipt
without valid synthesis, or an ambiguous receipt, never grants another call.

When all sections are covered, **Retry combined summary** uses the existing
continuation endpoint to authorize only synthesis. It reuses every completed
section and grants no additional section or token allocation. Acceptance requires
settled receipts proving the prior synthesis was not sent and sufficient remaining
budget for the complete request. If delivery is uncertain, first reconcile the
receipt; if the budget is exhausted or settings changed, review the retained
results before deliberately starting a new reprocessing task. Older interrupted
synthesis checkpoints are matched through their durable operation identity.

## Explicit continuation and hunt evidence

An administrator with `write:ai` and `read:items` can select **Authorize additional
article sections** in the article evidence panel. Each acceptance adds at most
eight sections and 64,000 estimated tokens, up to a cumulative ceiling of 32
sections and 256,000 tokens. The button discloses that budget before sending.
Completed calls are reused; a new normal reprocessing task deliberately starts a
new plan. The original selected provider is retained, even if global routing was
changed while the task waited. Model, prompt or evidence revision changes stop
continuation instead of quietly combining incompatible outputs.

`POST /ai/articles/{item_id}/continue` accepts a UUID `request_id` and the public
`progress_revision`. Retries with identical input return the original run. A
stale revision, unresolved section delivery, existing queued/running enrichment,
or exhausted ceiling returns an actionable conflict. The accepted credential is
stored encrypted and rechecked before each provider call and checkpoint. Revoked
credentials cannot authorize later sections. Successful section and synthesis
receipts need their exact durable checkpoints before recovery can resume.
After two-person reconciliation confirms that an interrupted section was **not
sent**, authorize continuation using the unchanged current progress revision.
The new task retries only that unsent section and remaining work; it keeps completed
checkpoints, receipt history, the accepted provider and prior conservative token
reservations. Its calls reserve budget again within the displayed cumulative
ceiling. Missing receipts, successful calls without checkpoints, or an acknowledgement
that a call may have been sent do not authorize section replay.
At the cumulative ceiling, a proven-unsent section can still be retried within
the unused token budget. This grants no extra sections or tokens; exhausted
reservations or an uncovered tail without a recoverable section remain blocked.

Hunt assessment prompts reserve space for exact verified quotations from across
current extraction sections. Deterministic keyword ranking uses the team's context;
AI entity descriptions are never substituted for primary quotations. The prompt
and published result disclose selected source coordinates, source version and
extraction coverage. Missing/stale extraction falls back to current bounded primary
text. Selection is bounded to 16,000 characters and does not claim exhaustive
article coverage. Analysts can inspect the selection beneath the team assessment.
If the model context needs a shorter input, fitting removes whole quotations and
updates both the prompt and stored selection to the passages actually sent.
Published source ranges also include their offsets in the assessment prompt's
article text. Extraction coverage continues to describe the prior extraction;
it does not claim that every extracted passage was sent to the assessment model.

## Versioned evaluation corpus

`backend/evaluations/ai-quality/v1.json` contains twelve synthetic adversarial
cases covering reference domains, malicious and benign infrastructure, disputed
attribution, incomplete telemetry, affected versions, prompt injection, examples
and unsupported relationships. **These seed cases were authored by AI and remain
pending analyst review. They are not an analyst-reviewed accuracy benchmark.**

An analyst should review the source, expected entities and case rubric, amend
incorrect/incomplete annotations, and record `review_status: analyst_approved`,
`reviewed_by` and `reviewed_at`. Changes create a new content digest and should use
a new dataset version. Review the corpus as code; avoid adding sensitive incident
evidence or real API credentials. `--require-reviewed` fails until every case has
explicit approval of its exact current content, a named reviewer and a valid
timezone-aware review timestamp. The comparison's `dataset_analyst_approved`
flag uses these same checks. This gate deliberately remains unsatisfied for the seed set.

Prepare inputs without contacting any provider:

```bash
backend/.venv/bin/python backend/scripts/evaluate_ai_quality.py \
  --prepare --output /tmp/ai-quality-inputs.jsonl
```

Capture results from authorized runs using the same application request runtime
and selected provider capabilities. Complete the JSONL templates with the exact
`model`, `prompt_version`, `structured_extraction`, available token counts and
`latency_ms`. Preserve `dataset_sha256` and `case_id`. Record the applicable
`input_usd_per_million` and `output_usd_per_million` explicitly; missing prices or
usage remain unknown instead of becoming zero cost. The evaluator itself performs
no network requests, makes no paid calls and provides no alternative egress path.

An optional per-prediction `review` records a named analyst's judgments:

```json
{
  "reviewer": "analyst identifier",
  "reviewed_at": "2026-09-26T12:00:00Z",
  "claim_verdicts": ["supported", "unsupported", "uncertain"],
  "hunt_usefulness": 2
}
```

Evaluate each substantive claim, including descriptions, attribution, numerical
versions and relationships. Use the same claim segmentation across comparisons;
the metric denominator is the supplied reviewed claims, not all model output.
Hunt usefulness uses 0–4: 0 unusable/unsafe, 1 generic, 2 plausible but missing
important evidence, 3 actionable with explicit prerequisites, 4 useful and
well-grounded with benign explanations and gaps. Omit a score when no hunt was
reviewed. The tool records review provenance; it cannot independently authenticate
that a human performed the review. Production qualification requires normal
review and approval of these artifacts.
Malformed, future or timezone-free review timestamps and blank reviewer names are
rejected before their judgments can contribute to a comparison.

Compare multiple model/prompt combinations in one predictions JSONL file:

```bash
backend/.venv/bin/python backend/scripts/evaluate_ai_quality.py \
  --predictions /tmp/ai-quality-results.jsonl \
  --output /tmp/ai-quality-comparison.json
# After analysts approve every corpus case, add --require-reviewed.
```

Preparation and scoring are separate CLI modes: `--prepare` cannot be combined
with `--gate` or `--predictions`; custom `--thresholds` require `--gate`. An invalid
combination exits 2 without writing an output artifact.

The result reports fixture entity precision/recall, exact-evidence validation
rate, analyst-supported/unsupported/uncertain claim rates, hunt usefulness,
latency p50/p95, known cost and each metric's sample count. It lists missing cases
per model/prompt combination and rejects duplicate cases, mismatched dataset
hashes, non-finite/negative telemetry and oversized artifacts. Missing human
judgments remain null. Entity fixture agreement is a regression signal;
interpretation and claim accuracy depend on the separately recorded analyst
review. Compare complete, equivalent coverage and retain dataset, prediction and
comparison artifacts with the release evidence.

## Database rollback

The expansion migrations preserve existing summaries, provider selections and
article identities. Downgrading deliberately refuses to discard retained section
checkpoints, receiver receipts, shared-account reservation attribution, owned hunt
claims, or reviewed publication history. Drain active work and archive the relevant
records before explicitly clearing them or releasing claims. An upgrade does not
perform that removal automatically, and a failed downgrade leaves these records
intact. Shared-account reservation attribution remains protected after a profile
leaves its group, including settled reservations retained for accounting.

## Strict promotion gates and analyst workflow

The checked-in [review package](../../backend/evaluations/ai-quality/review-package/README.md)
contains all twelve seed sources, expected annotations, case rubrics and exact
content digests, plus blank capture and review forms. Every seed remains pending.
Generate a reproducible package from a selected dataset into a fresh directory:

```bash
backend/.venv/bin/python backend/scripts/prepare_ai_quality_review.py \
  --dataset backend/evaluations/ai-quality/v1.json --output-dir /tmp/ai-case-review
```

After personal case review, regenerate from the final approved dataset before
capturing model outputs: approval metadata changes its byte digest. Add
`--predictions PATH` to package captured JSONL unchanged and generate blank
judgments keyed to each actual output claim. The manifest discloses missing cases
per model/prompt and stores the capture digest. Existing directories are refused
so regeneration cannot overwrite human work. The tool makes no provider calls
and records no approvals; a named human still reviews both corpus and output.

The existing `claim_verdicts` list remains accepted for historical comparisons,
but cannot satisfy a promotion gate. Comparison reports now include stable claim
IDs derived from each exact entity/relationship object. An analyst records
`review.claims: [{claim_id, verdict, rationale}]`, with reviewer, timezone-aware
reviewed time and hunt usefulness. Every claim must have exactly one judgment;
changing output invalidates those IDs. Do not have an AI mark itself approved.

Approve one personally reviewed corpus case using
`backend/scripts/review_ai_quality.py --dataset PATH --case CASE --reviewer NAME
--expected-sha256 DIGEST --approve --output NEW_PATH`. The tool reports the current
case digest when approval is missing/mismatched. Review the source, expected
entities and rubric first. The stored approval includes `reviewed_sha256`, so later
source or annotation edits invalidate it. Keep review artifacts in normal reviewed
version control; this CLI records operator attestations, not independent identity
verification. Seed cases remain unapproved in the repository.

Use `evaluate_ai_quality.py --predictions PATH --output REPORT --gate` to enforce
promotion requirements. It exits **3** for an unmet gate and writes all failures;
invalid input exits 2. Default thresholds require 100% structural validation,
95% entity precision, 90% recall, no unsupported reviewed claims, mean hunt
usefulness at least 3/4, p95 latency at most 60 seconds, and known total cost at
most USD 1. Supply a reviewed JSON policy via `--thresholds PATH` to change these
thresholds. Missing dataset cases, exact approvals, claim judgments, cost or
latency samples fail closed. The gate does not automatically change production
routing. Promote a saved provider/prompt revision only after retaining this report
and independently reviewing the evidence and deployment policy.

## Asynchronous provider feature qualification

Open a saved provider under **AI settings → Provider connections → Feature
qualification**. Select extraction, report findings/sections and/or hunt output, review the
estimated token ceiling, and explicitly authorize calls. Only synthetic fixture
text is sent. Results are pinned to the saved provider revision; changed provider
settings stop queued work. This exercises current request dialect, output limits,
structured evidence contracts and citations through the normal authorization,
quota, deadline, usage and provider-receipt runtime.

The report feature runs both evidence-finding and cited-section probes, including a numeric table.
The section fixture supplies an explicit numerical fact; qualification requires a
parsed table data cell with a number and a valid citation on every data row.
A paragraph-only response, numbers only in headers, or digits in citation IDs
cannot satisfy the probe. Each selected probe makes at most one provider attempt, uses at most 4,096 output
tokens (or a lower configured model/default ceiling), and has at most a 60-second
request deadline. A job has at most 32,000 estimated input/output tokens. Two
pending jobs per provider are admitted; larger submissions receive a retryable
capacity response. Budget reservations are durable before I/O. Proven admission
deferral releases only its unsent reservation. Completed probes are checkpointed;
ambiguous delivery requires receipt review and is never repeated automatically.
Worker recovery checks active, reserved and scheduled deliveries before treating
a qualification as lost. Once the normal stale-worker grace expires, it resumes
only unsent probes, retaining the accepted provider, completed results and token
reservations. A replacement delivery fences the old worker. An uncheckpointed
successful call or unresolved receipt instead finishes the task with an error for
operator review, releasing its pending-job slot without repeating the call.

`POST /ai/providers/{provider_id}/qualifications` requires `request_id`, saved
`provider_version`, `features`, `token_budget` and `authorize_provider_calls: true`.
The GET returns the newest 20 jobs, states, per-feature outcomes, latency and usage;
this scope is disclosed in the UI. Cancel through existing AI Operations. The
receipts and accepted credential follow task retention and encrypted inventory.

These small probes establish **contract compatibility only**. They do not prove
long-report performance, semantic accuracy, target-hardware capacity or human
approval. Their result always reports `semantic_quality_approved: false`. Real
analyst-reviewed evaluation and production workload qualification remain required.
Migrations 0117/0118 refuse downgrade while retained authorizations or qualification
records exist; archive and explicitly clear them before a planned rollback.
