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

Use the repository security page first:

- `https://github.com/PatrikSi/threatlens/security`
- Direct private-report entry point when enabled: `https://github.com/PatrikSi/threatlens/security/advisories/new`

If private vulnerability reporting is not available in your current repository access mode, open a minimal issue at `https://github.com/PatrikSi/threatlens/issues/new` asking maintainers to establish a private channel, and do not include exploit details or sensitive data in the issue body.

Preferred reporting order:

1. GitHub private vulnerability reporting from the repository security page, if enabled
2. Maintainer instructions returned through the repository issue tracker or security page

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

Please give maintainers reasonable time to investigate and prepare a fix before public disclosure. When a fix ships, prefer coordinated disclosure with clear operator upgrade guidance in the issue tracker, changelog, and release notes.
