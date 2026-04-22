# Security Policy

## Supported Code Lines

ThreatLens uses tag-based public releases. The default branch is active development and may be ahead of the latest supported release.

Until the first public tag exists, this repository should be treated as preview code rather than a stable release line.

Once public tags exist, use this support model:

| Code Line | Supported |
|---|---|
| Latest published tag | Yes |
| `main` / default branch | Preview of the next release |
| Older tags, snapshots, and forks | Historical reference only |

## Reporting a Vulnerability

Please do not open a public GitHub issue for an unpatched vulnerability.

This repository intentionally uses non-routable placeholder addresses under `example.invalid` until maintainers publish a real public security contact. Do not send reports to `security-contact@example.invalid`.

Before cutting a public release, maintainers must do at least one of the following:

- enable GitHub private vulnerability reporting for the repository
- replace the placeholder contact in this file and `README.md` with a monitored private security mailbox or equivalent intake path

Preferred reporting order:

1. GitHub private vulnerability reporting, if enabled for this repository
2. A real private contact explicitly published in this file

If neither private path exists yet, do not post exploit details publicly. Instead, open a minimal issue requesting a private reporting channel or wait for maintainers to publish one before sharing sensitive details.

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
