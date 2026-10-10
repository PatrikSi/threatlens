# product-versions

Content SHA256: `25b735bc04d8d0296e1555a4b7b739654f56de33ae9b24435c7728777130467f`

Corpus status: `pending_analyst_review`.

## Source

````json
{
  "title": "Evaluation source",
  "summary": "",
  "article_text": "ProductX versions 4.1 and 4.2 are affected. Version 4.3 is fixed."
}
````

## Expected entities to verify

````json
[
  {
    "kind": "product",
    "name": "ProductX",
    "assertion": "reported",
    "indicator_role": null
  }
]
````

## Analyst rubric

Affected versions exclude the fixed version 4.3.

Record corrections in a new dataset revision before approving. No case or model-output judgment is approved by this package.
