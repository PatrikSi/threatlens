# Team integrations and receiver credentials

Team managers can transfer a tested personal webhook from **Teams → Team
integrations**, adopt an existing team destination, pause or enable it, edit its
configuration, and issue or revoke receiver credentials. Administration requires
both `write:teams` and `write:notifications`, current manager-group membership and
an eligible operator account. Installation administration alone does not grant
team membership.

## Ownership and source authority

The team owns the destination and its execution history. Its delivery custodian
is the account whose current feature permissions and source handling-label
access authorize new deliveries. A manager explicitly adopts the destination
under their own access; selected feeds must all be available to them. Personal
signing/authentication profiles are cloned during adoption, and these encrypted
clones survive removal of their custodian. Use the current custodian's webhook
credential settings to rotate the cloned profile.

Removing or disabling the custodian, removing their team membership, or disabling
the team prevents new external deliveries. A different manager can adopt and
revalidate the destination. Pending deliveries retain their original principal;
a custody change does not quietly send previously queued data using someone
else's privileges. Existing execution receipts retain their original source
authority and action identity. Later loss of that authority generates a withdrawal.
Adoption never reactivates a withdrawn action, resets a hunt, or clears completed
findings. Normal approval and routing must create any replacement action.

Deleting a user preserves team destinations, their cloned credentials, execution
receipts and outstanding withdrawal obligations. Personal destinations retain
the previous deletion behavior. Team deletion is restricted while retained
integration obligations or history reference it. An initial personal-to-team
transfer updates at most 5,000 retained receipts; above that boundary create a new
team destination and keep the previous receiver available to process withdrawals.

## Scoped receiver credentials

Issue a named token in a team destination's **Receiver credentials** panel. The UI
issues a 90-day token; the API permits an explicit timezone-aware expiry up to one
year. At most five unexpired, unrevoked tokens may exist per destination, allowing
overlapping rotation. Only the hash is stored; the plaintext is returned once.

These `tlrecv_…` credentials are accepted only on:

- `POST /v1/notifications/automation/receivers/executions/{id}/callbacks`
- `GET /v1/notifications/automation/receivers/updates`
- `POST /v1/notifications/automation/receivers/updates/{id}/ack`

They cannot read articles or findings, administer integrations, access another
destination, or launch hunts through ThreatLens. Callback responses contain only
the submitted execution's identity, sequence and state. The opaque policy feed
continues working after the custodian loses evidence access or leaves, until the
receiver token expires or is revoked. Credentials remain bound to the retained
team and destination even when configuration is removed. Team managers can still
list/revoke retained credentials through the management API.

Supply exactly one `Authorization: Bearer …` header, never a URL credential.
Revocation is fenced against in-flight database operations; expiry is rechecked
after waits. Contention returns `503` with `Retry-After: 5`; replay the same callback
identity and sequence. A timeout does not authorize a new hunt launch.

The reference receiver recognizes these tokens automatically and switches its
callback/control requests to the dedicated routes. Personal API tokens continue
to use the existing routes. Human team members can inspect shared execution
history only with the required feature permissions and current evidence access.

## Monitoring and retention

Operations exposes external execution update age, unknown outcomes and oldest
unacknowledged withdrawal age. Their default freshness objective is one hour;
these are actionable diagnostics, not evidence that an external hunt failed.
Inspect receiver connectivity, retry queues and the existing external job before
taking action. Publication consumers have separate withdrawal acknowledgement
and reconciliation metrics (one hour and five minutes respectively).

Maintenance archives at most 100 cold receipts per sweep, only when the execution
is completed/failed, its intelligence was withdrawn/replaced, every policy update
was acknowledged, and the outcome and acknowledgements are at least 180 days old.
Unknown or unacknowledged work is never archived. **Include archived execution
history** restores cold rows in the execution dialog. Archival removes rows from
the default worklist; evidence, findings, action identities and callback digests
are retained so historical investigations and deduplication remain valid. This
is not destructive database compaction; budget long-term evidence storage.
