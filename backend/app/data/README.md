# ATT&CK reference catalog

`attack_catalog.json` contains the active Enterprise ATT&CK technique and detection
strategy identifiers, names, canonical links, and official `detects` relationships
from ATT&CK 19.2. Revoked and deprecated objects are excluded. It contains no
adversary procedures or executable detection content.

The source URL identifies an immutable commit in MITRE's official
[attack-stix-data repository](https://github.com/mitre-attack/attack-stix-data).
The catalog records the source SHA-256 and collection modification time. The
upstream [detection strategy documentation](https://attack.mitre.org/detectionstrategies/)
explains how strategies relate to techniques and analytics.

The API loads this bundled catalog without network requests. Generated references
are checked against its identifiers and relationships; links come from the local
catalog rather than provider output. These checks establish valid references,
not the correctness of an AI mapping or coverage by an organization's telemetry.

MITRE's copyright and permission terms are reproduced in
[`LICENSE.mitre-attack.txt`](LICENSE.mitre-attack.txt), which travels with the
catalog in the backend image. ATT&CK® is a registered trademark of The MITRE
Corporation. The catalog is a derived subset; ThreatLens is not endorsed by MITRE.

To refresh it, download a versioned Enterprise STIX bundle and the license from an
immutable commit in the official repository, inspect the license for changes,
and run from the repository root:

```bash
python3 backend/scripts/update_attack_catalog.py /tmp/enterprise-attack.json \
  --source-url https://raw.githubusercontent.com/mitre-attack/attack-stix-data/COMMIT/enterprise-attack/enterprise-attack-VERSION.json
```

Replace `COMMIT` with the full 40-character commit and `VERSION` with the collection
version. Verify the downloaded bytes belong to that URL, update the license copy
when needed, review the generated diff, and run
`backend/.venv/bin/python -m pytest backend/tests/unit/test_attack_catalog.py`.
The maintainer script performs no network requests. Runtime upgrades never fetch
or replace this data automatically.
