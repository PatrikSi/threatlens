# Conditions on the same indicator

Webhook subscriptions retain the original event-level `all`, `any` and `not`
semantics. An event-level domain condition and role condition can match different
indicators. Use `indicators_any` or `indicators_all` when those conditions must
apply to the same indicator:

```json
{
  "op": "indicators_any",
  "conditions": [
    {"field": "ioc_type", "operator": "in", "value": ["domain"]},
    {"field": "analyst_verdict", "operator": "in", "value": ["malicious"]},
    {"field": "extraction_confidence", "operator": "gte", "value": 0.9}
  ]
}
```

Every child condition must match an individual indicator. `indicators_any`
requires at least one eligible match; `indicators_all` requires every eligible
indicator to match and at least one eligible indicator to exist. Excluded
indicators never qualify. A current analyst verdict is separate from the AI
role, extraction confidence and maliciousness confidence. An absent verdict is
unknown, not an implied benign or malicious result.

Inside these groups, use indicator type, AI role, analyst verdict or either
confidence field. Boolean groups can combine these fields, but indicator groups
cannot contain another indicator group. Event fields such as feed, team and age
belong outside the indicator group. Existing limits of 32 nodes and four levels
still apply.

Incomplete or malformed inventories remain unknown, including when negated.
Unknown individual scores cannot satisfy a negative condition. A known match may
still satisfy an `indicators_any` group when another indicator has missing
evidence. Empty eligible inventories do not directly satisfy either quantifier.

In **Settings → Integrations → Webhooks**, the condition group's **Match** selector
offers both same-indicator modes. The non-sending preview identifies matching,
nonmatching and excluded indicator values. Preview details are bounded to 250
indicator rows across groups; a summary explicitly reports omitted rows while
the evaluator still checks the complete bounded inventory. Long preview values
end in an ellipsis; the structured payload retains the complete value.

These predicates select events; they do not remove other indicators from the
automation payload. Receivers must apply each indicator's own verdict and
exclusions before taking action. Upgrade API and notification workers together
before enabling the new predicates: older workers reject the unfamiliar saved
conditions rather than interpreting them as an event-level match.
