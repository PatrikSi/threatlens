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
Summary and organizational relevance continue to describe the first section.
Shared extraction is merged across all completed sections. Equivalent entity
identities are deduplicated; conflicting roles/assertions remain distinct.
Combined results are bounded to 96 entities, 96 relationships, three passages per
entry and eight information gaps. Quotes prove traceability, not factual truth.

Checkpoints live in the item's existing enrichment row, inherit its access and
retention boundary, and contain at most eight section results. Public progress
omits provider payloads and task IDs. A redelivery of the same logical task and
source revision reuses completed sections. Worker recovery accepts a successful
receipt only when its exact request fingerprint has a validated durable section
checkpoint. A crash between provider success and checkpoint publication, a
reserved receipt or ambiguous transport still requires the existing receipt
reconciliation workflow. Automatic recovery never repeats those requests. An
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
explicit approval. This gate deliberately remains unsatisfied for the seed set.

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

Compare multiple model/prompt combinations in one predictions JSONL file:

```bash
backend/.venv/bin/python backend/scripts/evaluate_ai_quality.py \
  --predictions /tmp/ai-quality-results.jsonl \
  --output /tmp/ai-quality-comparison.json
# After analysts approve every corpus case, add --require-reviewed.
```

The result reports fixture entity precision/recall, exact-evidence validation
rate, analyst-supported/unsupported/uncertain claim rates, hunt usefulness,
latency p50/p95, known cost and each metric's sample count. It lists missing cases
per model/prompt combination and rejects duplicate cases, mismatched dataset
hashes, non-finite/negative telemetry and oversized artifacts. Missing human
judgments remain null. Entity fixture agreement is a regression signal;
interpretation and claim accuracy depend on the separately recorded analyst
review. Compare complete, equivalent coverage and retain dataset, prediction and
comparison artifacts with the release evidence.
