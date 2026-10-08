# AI quality review package

This package contains 12 corpus cases from `2026-09-v1`.
Its exact dataset SHA256 is `4dce59ec16c4b1d54c92d0c40f003465c96c9f8e27cc06a854ca351984c603e1`. No model outputs have been captured.
The package records no new analyst approvals, model-quality judgments or paid calls.
`manifest.json` lists exact case digests and any captured model/prompt coverage gaps.

| Case | Review source, annotations and rubric | Content SHA256 |
| --- | --- | --- |
| `reference-domain` | [cases/01-reference-domain.md](cases/01-reference-domain.md) | `7f9f5b18c397837fe086fc40d951f0e0f8e04b34070cd314ba65848e48d3e385` |
| `explicit-infrastructure` | [cases/02-explicit-infrastructure.md](cases/02-explicit-infrastructure.md) | `cc99cc0a56a44e32b64f5aed32170943a8fa28af6defa459f47d38727b1c3703` |
| `conflicting-attribution` | [cases/03-conflicting-attribution.md](cases/03-conflicting-attribution.md) | `2f27ecd27256adb56561f3f342cc82e1d95e06c2b5d465245747f8edf220bae1` |
| `uncertain-attribution` | [cases/04-uncertain-attribution.md](cases/04-uncertain-attribution.md) | `862891a91e7e41b84d66645b145b519e3a0a2b877e3b50cda13f90a6307e293e` |
| `product-versions` | [cases/05-product-versions.md](cases/05-product-versions.md) | `25b735bc04d8d0296e1555a4b7b739654f56de33ae9b24435c7728777130467f` |
| `incomplete-telemetry` | [cases/06-incomplete-telemetry.md](cases/06-incomplete-telemetry.md) | `1f8fefc8ec7579f55ead93b4b66697d3b769f7b5a3dd087ffa0f132980c523e4` |
| `prompt-injection` | [cases/07-prompt-injection.md](cases/07-prompt-injection.md) | `9e3572499f8ba8e97b801f5e29d1d74b17d8eb02718d67d77a15bccf90f03d63` |
| `example-address` | [cases/08-example-address.md](cases/08-example-address.md) | `cca8e2a60d9624807c65e623ce1170d250ba90ba1cd26d36c1d21a5389980e9b` |
| `inferred-relationship` | [cases/09-inferred-relationship.md](cases/09-inferred-relationship.md) | `328b1d4d32009942176fb936835998a775fc789284a3bf292650b844011e91e8` |
| `benign-provider` | [cases/10-benign-provider.md](cases/10-benign-provider.md) | `0f56ee1ee552a86fe6000be2b857e3a202af7a3ba88bfdde076bc2d3095f350a` |
| `quoted-hostile-claim` | [cases/11-quoted-hostile-claim.md](cases/11-quoted-hostile-claim.md) | `5509a7bb3bf84dc48b1cffa50ef389caf07d16a81c66cfe2888f749b2ef2c2a9` |
| `evidence-scarcity` | [cases/12-evidence-scarcity.md](cases/12-evidence-scarcity.md) | `8235a458a854d652fccb0f4f74c18a54e5f0be7106d0f6d8aabc1cced9961139` |

1. A named human reviews every source, expected entity and rubric. Correct annotations
   in a new dataset version when needed, then regenerate into a fresh directory.
2. Approve each personally reviewed case with the existing CLI. Substitute the
   selected case ID, its current digest and your actual name. Successive approvals
   must read the last reviewed dataset output so earlier approvals are retained:

   ```bash
   backend/.venv/bin/python backend/scripts/review_ai_quality.py \
     --dataset PATH/TO/dataset.json --case CASE_ID --reviewer YOUR_NAME \
     --expected-sha256 CASE_CONTENT_SHA256 --approve --output /tmp/reviewed-dataset.json
   ```

3. Once all cases are reviewed, regenerate the package from that final dataset.
   Approvals change its byte digest; old prediction templates no longer bind to it.
   Use `predictions.template.jsonl` to capture exact model and prompt revisions,
   structured outputs, latency, usage and explicit prices from separately authorized
   application runs. Unknown telemetry stays null. Preparation performs no provider I/O.
4. Prepare a fresh package with `--predictions PATH/TO/captured.jsonl`. It retains
   the original bytes in `captured-predictions.jsonl` and supplies exact claim IDs in
   `reviews.template.jsonl`. Fill reviewer, truthful timezone-aware review time,
   supported/unsupported/uncertain verdict and rationale for every claim, plus
   hunt usefulness (0–4). Empty fields remain pending; blank input templates cannot
   qualify. Preserve the original captured file, and review actual model output
   rather than treating the corpus's expected entities as predictions.
5. Run the promotion gate on the human-completed file. The default thresholds are
   included in `thresholds.json`; any policy changes require independent review:

   ```bash
   backend/.venv/bin/python backend/scripts/evaluate_ai_quality.py \
     --dataset PATH/TO/dataset.json --predictions PATH/TO/completed-reviews.jsonl \
     --require-reviewed --gate --thresholds PATH/TO/thresholds.json \
     --output /tmp/ai-quality-comparison.json
   ```

Exit 2 indicates invalid/incomplete input; exit 3 indicates an unmet promotion gate.
Passing a contract probe or generating this package does not establish semantic
quality or change production routing. A CLI attestation does not independently
authenticate the reviewer; retain normal review provenance with the artifacts.
