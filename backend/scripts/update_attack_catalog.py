#!/usr/bin/env python3
"""Build a compact reference catalog from a downloaded official ATT&CK bundle.

This is an offline maintainer tool. Download and review the official versioned
STIX file and license separately; no runtime service downloads ATT&CK data.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path


def build_catalog(bundle: dict, *, source_url: str, source_sha256: str) -> dict:
    objects = bundle["objects"]
    collection = next(obj for obj in objects if obj.get("type") == "x-mitre-collection")
    if collection.get("name") != "Enterprise ATT&CK":
        raise ValueError("Expected an Enterprise ATT&CK collection")
    techniques, strategies, external_ids = {}, {}, {}
    for obj in objects:
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue
        kind = obj.get("type")
        if kind not in {"attack-pattern", "x-mitre-detection-strategy"}:
            continue
        reference = next((ref for ref in obj.get("external_references", [])
                          if ref.get("source_name") == "mitre-attack"), None)
        if reference is None:
            raise ValueError("Active catalog object has no MITRE identifier")
        external_id = reference["external_id"]
        expected = r"T\d{4}(?:\.\d{3})?" if kind == "attack-pattern" else r"DET\d{4}"
        if re.fullmatch(expected, external_id) is None:
            raise ValueError("Unexpected MITRE identifier format")
        entry = {"name": obj["name"]}
        if kind == "attack-pattern":
            entry["url"] = "https://attack.mitre.org/techniques/" + external_id.replace(".", "/") + "/"
            target = techniques
        else:
            entry["url"] = "https://attack.mitre.org/detectionstrategies/" + external_id + "/"
            entry["technique_ids"] = []
            target = strategies
        if external_id in target:
            raise ValueError("Duplicate active MITRE identifier")
        target[external_id] = entry
        external_ids[obj["id"]] = external_id
    for obj in objects:
        if obj.get("type") != "relationship" or obj.get("relationship_type") != "detects":
            continue
        if obj.get("revoked") or obj.get("x_mitre_deprecated"):
            continue
        source = external_ids.get(obj["source_ref"])
        target = external_ids.get(obj["target_ref"])
        if source in strategies and target in techniques:
            strategies[source]["technique_ids"].append(target)
    strategies = {key: {**value, "technique_ids": sorted(set(value["technique_ids"]))}
                  for key, value in strategies.items() if value["technique_ids"]}
    return {
        "schema_version": 1,
        "attack_version": collection["x_mitre_version"],
        "collection_modified": collection["modified"],
        "source_url": source_url,
        "source_sha256": source_sha256,
        "license": "LICENSE.mitre-attack.txt",
        "techniques": dict(sorted(techniques.items())),
        "detection_strategies": dict(sorted(strategies.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "app/data/attack_catalog.json")
    args = parser.parse_args()
    if re.fullmatch(
        r"https://raw\.githubusercontent\.com/mitre-attack/attack-stix-data/[0-9a-f]{40}/enterprise-attack/enterprise-attack-\d+\.\d+\.json",
        args.source_url,
    ) is None:
        parser.error("source URL must identify a versioned official bundle at an immutable commit")
    with args.bundle.open("rb") as source:
        data = source.read(100_000_001)
    if len(data) > 100_000_000:
        parser.error("bundle exceeds the 100 MB maintainer-tool limit")
    catalog = build_catalog(json.loads(data), source_url=args.source_url, source_sha256=hashlib.sha256(data).hexdigest())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"ATT&CK {catalog['attack_version']}: {len(catalog['techniques'])} techniques, "
          f"{len(catalog['detection_strategies'])} detection strategies")


if __name__ == "__main__":
    main()
