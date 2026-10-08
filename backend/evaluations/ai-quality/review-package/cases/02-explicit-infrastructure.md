# explicit-infrastructure

Content SHA256: `cc99cc0a56a44e32b64f5aed32170943a8fa28af6defa459f47d38727b1c3703`

Corpus status: `pending_analyst_review`.

## Source

````json
{
  "title": "Evaluation source",
  "summary": "",
  "article_text": "The incident report identifies c2.example.net as malicious command infrastructure observed in this incident."
}
````

## Expected entities to verify

````json
[
  {
    "kind": "indicator",
    "name": "c2.example.net",
    "assertion": "reported",
    "indicator_role": "malicious_infrastructure"
  }
]
````

## Analyst rubric

Source-reported malicious infrastructure is still not independent verification.

Record corrections in a new dataset revision before approving. No case or model-output judgment is approved by this package.
