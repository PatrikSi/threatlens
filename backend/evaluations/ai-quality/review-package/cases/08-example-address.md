# example-address

Content SHA256: `cca8e2a60d9624807c65e623ce1170d250ba90ba1cd26d36c1d21a5389980e9b`

Corpus status: `pending_analyst_review`.

## Source

````json
{
  "title": "Evaluation source",
  "summary": "",
  "article_text": "For documentation examples only, use sample.example.org. This is not an observed indicator."
}
````

## Expected entities to verify

````json
[
  {
    "kind": "indicator",
    "name": "sample.example.org",
    "assertion": "reported",
    "indicator_role": "reference"
  }
]
````

## Analyst rubric

Documentation examples must never become malicious infrastructure.

Record corrections in a new dataset revision before approving. No case or model-output judgment is approved by this package.
