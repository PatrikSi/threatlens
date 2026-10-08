# benign-provider

Content SHA256: `0f56ee1ee552a86fe6000be2b857e3a202af7a3ba88bfdde076bc2d3095f350a`

Corpus status: `pending_analyst_review`.

## Source

````json
{
  "title": "Evaluation source",
  "summary": "",
  "article_text": "Investigators initially flagged updates.example.net, then confirmed it was legitimate vendor update infrastructure."
}
````

## Expected entities to verify

````json
[
  {
    "kind": "indicator",
    "name": "updates.example.net",
    "assertion": "reported",
    "indicator_role": "benign"
  }
]
````

## Analyst rubric

The later benign resolution supersedes the preliminary suspicion.

Record corrections in a new dataset revision before approving. No case or model-output judgment is approved by this package.
