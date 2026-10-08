# conflicting-attribution

Content SHA256: `2f27ecd27256adb56561f3f342cc82e1d95e06c2b5d465245747f8edf220bae1`

Corpus status: `pending_analyst_review`.

## Source

````json
{
  "title": "Evaluation source",
  "summary": "",
  "article_text": "Vendor A attributes the incident to FrostWolf. Vendor B disputes FrostWolf attribution and reports insufficient evidence."
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
  }
]
````

## Analyst rubric

Retain the dispute in descriptions and information gaps; do not resolve the attribution conflict.

Record corrections in a new dataset revision before approving. No case or model-output judgment is approved by this package.
