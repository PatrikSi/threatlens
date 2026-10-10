# reference-domain

Content SHA256: `7f9f5b18c397837fe086fc40d951f0e0f8e04b34070cd314ba65848e48d3e385`

Corpus status: `pending_analyst_review`.

## Source

````json
{
  "title": "Evaluation source",
  "summary": "",
  "article_text": "Vendor documentation is at docs.example.org. It is a reference, not attack infrastructure."
}
````

## Expected entities to verify

````json
[
  {
    "kind": "indicator",
    "name": "docs.example.org",
    "assertion": "reported",
    "indicator_role": "reference"
  }
]
````

## Analyst rubric

Never label a documentation reference malicious solely because it appears in threat reporting.

Record corrections in a new dataset revision before approving. No case or model-output judgment is approved by this package.
