import json
from pathlib import Path

import pytest

from app.services.attack_catalog import (
    detection_strategies_for_techniques,
    detection_strategy_reference,
    load_attack_catalog,
    technique_reference,
    validate_attack_references,
)
from scripts.update_attack_catalog import build_catalog


def test_official_technique_strategy_mapping_and_safe_canonical_links():
    assert technique_reference("T1059.001") == {
        "id": "T1059.001", "name": "PowerShell", "url": "https://attack.mitre.org/techniques/T1059/001/",
    }
    strategies = detection_strategies_for_techniques(["T1059.001"])
    assert [entry["id"] for entry in strategies] == ["DET0455"]
    assert strategies[0]["technique_ids"] == ["T1059.001"]
    validate_attack_references(["T1059.001"], ["DET0455"])
    assert technique_reference("T1066") is None  # Retired technique is not selectable.
    assert technique_reference("https://attacker.example/") is None
    assert detection_strategy_reference("DS0001") is None


@pytest.mark.parametrize(("techniques", "strategies"), [
    (["T9999"], []), (["T1059.001"], ["DET9999"]),
    (["T1059.003"], ["DET0455"]), ([], ["DET0455"]),
    (["T1059.001", "T1059.001"], []), (["T1059.001"], ["DET0455", "DET0455"]),
])
def test_invalid_unknown_duplicate_or_unrelated_references_fail(techniques, strategies):
    with pytest.raises(ValueError):
        validate_attack_references(techniques, strategies)


def test_reference_copies_cannot_mutate_the_shared_catalog():
    reference = technique_reference("T1059.001")
    reference["url"] = "https://attacker.example/"
    strategy = detection_strategy_reference("DET0455")
    strategy["technique_ids"].clear()
    assert technique_reference("T1059.001")["url"].startswith("https://attack.mitre.org/")
    assert detection_strategy_reference("DET0455")["technique_ids"] == ["T1059.001"]
    with pytest.raises(TypeError):
        load_attack_catalog().techniques["new"] = reference


def test_catalog_has_provenance_license_and_closed_relationships():
    catalog = load_attack_catalog()
    assert catalog.version == "19.2"
    assert len(catalog.techniques) == 697
    assert len(catalog.detection_strategies) == 697
    for strategy in catalog.detection_strategies.values():
        assert strategy.technique_ids
        assert all(identifier in catalog.techniques for identifier in strategy.technique_ids)
        assert strategy.url == f"https://attack.mitre.org/detectionstrategies/{strategy.id}/"
    path = Path(__file__).resolve().parents[2] / "app/data/attack_catalog.json"
    metadata = json.loads(path.read_text())
    assert len(metadata["source_sha256"]) == 64
    assert "/master/" not in metadata["source_url"]
    assert "© 2026 The MITRE Corporation" in (path.parent / metadata["license"]).read_text()


def test_strategy_results_are_bounded_and_invalid_limits_rejected():
    catalog = load_attack_catalog()
    selected = list(catalog.techniques)[:40]
    assert len(detection_strategies_for_techniques(selected, limit=2)) == 2
    for limit in (0, 25):
        with pytest.raises(ValueError):
            detection_strategies_for_techniques(selected, limit=limit)


def test_refresh_excludes_revoked_objects_and_unrelated_relationships():
    def obj(kind, stix_id, external_id, **fields):
        return {"type": kind, "id": stix_id, "name": external_id,
                "external_references": [{"source_name": "mitre-attack", "external_id": external_id}], **fields}

    bundle = {"objects": [
        {"type": "x-mitre-collection", "name": "Enterprise ATT&CK", "x_mitre_version": "99.0", "modified": "test"},
        obj("attack-pattern", "technique-1", "T1000"),
        obj("attack-pattern", "technique-2", "T1001", revoked=True),
        obj("attack-pattern", "technique-3", "T1002", x_mitre_deprecated=True),
        obj("x-mitre-detection-strategy", "strategy-1", "DET0001"),
        obj("x-mitre-detection-strategy", "strategy-2", "DET0002"),
        {"type": "relationship", "relationship_type": "detects", "source_ref": "strategy-1", "target_ref": "technique-1"},
        {"type": "relationship", "relationship_type": "detects", "source_ref": "strategy-2", "target_ref": "technique-2"},
    ]}
    catalog = build_catalog(bundle, source_url="source", source_sha256="a" * 64)
    assert list(catalog["techniques"]) == ["T1000"]
    assert list(catalog["detection_strategies"]) == ["DET0001"]
    assert catalog["detection_strategies"]["DET0001"]["technique_ids"] == ["T1000"]
