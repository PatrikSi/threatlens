# Security Policy

## Supported Code Lines

ThreatLens uses tag-based releases. The default branch is active development and may be ahead of the latest supported release.

Once public tags exist, use this support model:

| Code Line | Supported |
|---|---|
| Latest published tag | Yes |
| `main` / default branch | Preview of the next release |
| Older tags, snapshots, and forks | Historical reference only |

## Reporting a Vulnerability

Please do not open a public GitHub issue for an unpatched vulnerability.

ThreatLens does not currently publish a dedicated private security reporting channel in this repository.

Until a private channel is published, use the public issue tracker only to request a private follow-up path:

- `https://github.com/PatrikSi/threatlens/issues/new`

Public coordination requests should stay high-level. Do not include:

- exploit steps
- proof-of-concept code
- secrets, tokens, session cookies, or production data
- hostnames, IPs, or tenant-specific details that would increase exposure

Use the first public message only to share:

- affected commit, branch, tag, or container image
- a short impact summary
- whether you need an urgent maintainer response because the issue is being exploited or exposes live credentials
- a request for a non-public follow-up channel

Once maintainers provide a private path, include as much of the following as you can:

- affected commit, branch, tag, or container image
- deployment mode (`docker compose`, custom reverse proxy, local dev, and so on)
- reproduction steps or proof of concept
- impact assessment
- any mitigations or configuration changes already identified

If the repository later enables GitHub private vulnerability reporting or publishes another private contact path, that new channel should replace the public issue bootstrap above.

## Scope Notes

- ThreatLens is a self-hosted application, so secure deployment settings matter. Please report weak secure defaults as well as direct code defects.
- Findings involving outbound fetches, AI integrations, webhook delivery, auth/session handling, token scope enforcement, or secret storage are especially useful.
- If a report depends on non-default insecure settings, say so clearly.

## Disclosure

Please give maintainers reasonable time to investigate and prepare a fix before public disclosure. When a fix ships, prefer coordinated disclosure with clear operator upgrade guidance in the issue tracker, changelog, and release notes.
