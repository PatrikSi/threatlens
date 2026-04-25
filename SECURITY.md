# Security Policy

## Supported Code Lines

ThreatLens uses immutable public tags when maintainers cut a release. The default branch is the current development line and receives best-effort maintainer support. Until the first public tag exists, operators should pin the exact commit SHA and image digest they deploy; there is not yet a separate stable release branch.

Support expectations:

| Code Line | Supported |
|---|---|
| Latest published tag | Yes, when tags exist |
| `main` / default branch | Current development line with best-effort support |
| Older tags, snapshots, and forks | Historical reference only |

GitHub issue-based coordination remains a best-effort maintainer workflow, not a contractual support SLA.

If no public tag exists yet, use the deployed commit SHA, container image digest, and checked-in OpenAPI contract digest as your release anchor when reporting issues.

## Reporting

Use the repository's GitHub private vulnerability reporting flow when it is available from the Security tab. That path is preferred for vulnerability reports because it avoids exposing exploit details before maintainers can prepare a fix.

If private vulnerability reporting is unavailable, use the issue tracker only to request maintainer follow-up and, if needed, a non-public coordination path:

- `https://github.com/PatrikSi/threatlens/issues/new`

Please do not publish unpatched vulnerability details in an issue. Initial coordination requests should stay high-level and should not include:

- exploit steps
- proof-of-concept code
- secrets, tokens, session cookies, or production data
- hostnames, IPs, or tenant-specific details that would increase exposure

Use the first issue only to share:

- affected commit, branch, tag, or container image
- a short impact summary
- whether you need an urgent maintainer response because the issue is being exploited or exposes live credentials
- a request for non-public follow-up if the issue needs deeper coordination

Maintainers may continue coordination through a non-public channel after the initial report. If no non-public path is available for a report, keep using the issue only for coordination and avoid posting details that would materially increase exposure before a fix or mitigation is available.

Once you have a private reporting or coordination path, include as much of the following as you can:

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
