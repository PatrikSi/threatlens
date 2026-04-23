# Security Policy

## Supported Code Lines

ThreatLens support status follows immutable public tags. The default branch is active development and may be ahead of the latest supported release. If no public tag has been published yet, every branch, commit, and container build should be treated as preview-only.

Support expectations:

| Code Line | Supported |
|---|---|
| Latest published tag | Yes |
| `main` / default branch | Preview of the next release |
| Older tags, snapshots, and forks | Historical reference only |

Repository issues and discussions are best-effort community support channels, not a contractual support SLA.

## Private Reporting

Preferred private path, if this repository has it enabled:

- GitHub private vulnerability reporting: `https://github.com/PatrikSi/threatlens/security/advisories/new`

If that page returns `404` or the repository UI does not show a private reporting form, no private reporting channel is currently published in-repo. In that case, use the public issue tracker only to request a non-public follow-up path:

- `https://github.com/PatrikSi/threatlens/issues/new`

Please do not publish unpatched vulnerability details in a public issue. Public coordination requests should stay high-level and should not include:

- exploit steps
- proof-of-concept code
- secrets, tokens, session cookies, or production data
- hostnames, IPs, or tenant-specific details that would increase exposure

Use the first public message only to share:

- affected commit, branch, tag, or container image
- a short impact summary
- whether you need an urgent maintainer response because the issue is being exploited or exposes live credentials
- a request for a non-public follow-up channel

Once you have a private path, include as much of the following as you can:

- affected commit, branch, tag, or container image
- deployment mode (`docker compose`, custom reverse proxy, local dev, and so on)
- reproduction steps or proof of concept
- impact assessment
- any mitigations or configuration changes already identified

## Response Targets

These are best-effort maintainer goals, not guaranteed SLAs:

- Initial acknowledgment target: within 5 business days
- Status updates target: at least weekly while a fix or mitigation is in progress
- Please call out active exploitation, exposed credentials, or internet-reachable defaults in the first message so triage can be prioritized appropriately

## Scope Notes

- ThreatLens is a self-hosted application, so secure deployment settings matter. Please report weak secure defaults as well as direct code defects.
- Findings involving outbound fetches, AI integrations, webhook delivery, auth/session handling, token scope enforcement, or secret storage are especially useful.
- If a report depends on non-default insecure settings, say so clearly.

## Disclosure

Please give maintainers reasonable time to investigate and prepare a fix before detailed public disclosure. Prefer disclosure after a mitigation, advisory, or tagged release is available so operators have clear upgrade guidance in the issue tracker, changelog, and release notes.
