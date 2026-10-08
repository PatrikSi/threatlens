# inferred-relationship

Content SHA256: `328b1d4d32009942176fb936835998a775fc789284a3bf292650b844011e91e8`

Corpus status: `pending_analyst_review`.

## Source

````json
{
  "title": "Evaluation source",
  "summary": "",
  "article_text": "FrostWolf and Ember are mentioned in separate investigations. The report establishes no relationship between them."
}
````

## Expected entities to verify

````json
[
  {
    "kind": "actor",
    "name": "FrostWolf",
    "assertion": "reported",
    "indicator_role": null
  },
  {
    "kind": "malware",
    "name": "Ember",
    "assertion": "reported",
    "indicator_role": null
  }
]
````

## Analyst rubric

Do not infer that FrostWolf uses Ember from co-occurrence.

Record corrections in a new dataset revision before approving. No case or model-output judgment is approved by this package.
