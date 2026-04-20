# Security Policy

## Supported Code Lines

ThreatLens does not currently publish a formal long-term support matrix in this repository.

Until versioned support policy is defined, assume:

| Version | Supported |
|---|---|
| `main` / latest default branch commit | Yes |
| Older snapshots and forks | Best effort only |

## Reporting a Vulnerability

Please do not open a public GitHub issue for an unpatched vulnerability.

Report security issues privately to `patrik@local`. If GitHub private vulnerability reporting is enabled for this repository, that path is also acceptable.

Include as much of the following as you can:

- affected commit, branch, tag, or container image
- deployment mode (`docker compose`, custom reverse proxy, local dev, and so on)
- reproduction steps or proof of concept
- impact assessment
- any mitigations or configuration changes already identified

## Scope Notes

- ThreatLens is a self-hosted application, so secure deployment settings matter. Please report weak secure defaults as well as direct code defects.
- Findings involving outbound fetches, AI integrations, webhook delivery, auth/session handling, token scope enforcement, or secret storage are especially useful.
- If a report depends on non-default insecure settings, say so clearly.

## Disclosure

Please give maintainers reasonable time to investigate and prepare a fix before public disclosure. When a fix ships, prefer coordinated disclosure with clear operator upgrade guidance.
