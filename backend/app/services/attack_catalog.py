"""Offline official ATT&CK identifiers and links; no provider-supplied URLs."""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TypedDict


class AttackReference(TypedDict):
    id: str
    name: str
    url: str


class DetectionStrategyReference(AttackReference):
    technique_ids: list[str]


@dataclass(frozen=True)
class CatalogReference:
    id: str
    name: str
    url: str
    technique_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttackCatalog:
    version: str
    techniques: Mapping[str, CatalogReference]
    detection_strategies: Mapping[str, CatalogReference]


@lru_cache(maxsize=1)
def load_attack_catalog() -> AttackCatalog:
    path = Path(__file__).resolve().parents[1] / "data/attack_catalog.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ValueError("Unsupported bundled ATT&CK catalog version")
    techniques = {key: CatalogReference(id=key, name=value["name"], url=value["url"])
                  for key, value in payload["techniques"].items()}
    strategies = {key: CatalogReference(id=key, name=value["name"], url=value["url"],
                                      technique_ids=tuple(value["technique_ids"]))
                  for key, value in payload["detection_strategies"].items()}
    return AttackCatalog(payload["attack_version"], MappingProxyType(techniques), MappingProxyType(strategies))


def technique_reference(identifier: str) -> AttackReference | None:
    entry = load_attack_catalog().techniques.get(identifier)
    return {"id": entry.id, "name": entry.name, "url": entry.url} if entry else None


def detection_strategy_reference(identifier: str) -> DetectionStrategyReference | None:
    entry = load_attack_catalog().detection_strategies.get(identifier)
    if entry is None:
        return None
    return {"id": entry.id, "name": entry.name, "url": entry.url, "technique_ids": list(entry.technique_ids)}


def validate_attack_references(technique_ids: Sequence[str], strategy_ids: Sequence[str]) -> None:
    catalog = load_attack_catalog()
    if len(set(technique_ids)) != len(technique_ids) or len(set(strategy_ids)) != len(strategy_ids):
        raise ValueError("ATT&CK references must not contain duplicate identifiers")
    if any(identifier not in catalog.techniques for identifier in technique_ids):
        raise ValueError("ATT&CK technique is not in the bundled Enterprise catalog")
    if any(identifier not in catalog.detection_strategies for identifier in strategy_ids):
        raise ValueError("ATT&CK detection strategy is not in the bundled Enterprise catalog")
    selected = set(technique_ids)
    if any(not selected.intersection(catalog.detection_strategies[identifier].technique_ids) for identifier in strategy_ids):
        raise ValueError("ATT&CK detection strategies must match a selected technique")


def detection_strategies_for_techniques(
    technique_ids: Sequence[str], *, limit: int = 12,
) -> list[DetectionStrategyReference]:
    """Bounded official suggestions; a mapping does not prove local coverage."""
    if not 1 <= limit <= 24:
        raise ValueError("Detection strategy result limit must be between 1 and 24")
    validate_attack_references(technique_ids, [])
    selected = set(technique_ids)
    results: list[DetectionStrategyReference] = []
    for entry in load_attack_catalog().detection_strategies.values():
        if selected.intersection(entry.technique_ids):
            results.append({"id": entry.id, "name": entry.name, "url": entry.url,
                            "technique_ids": list(entry.technique_ids)})
            if len(results) == limit:
                break
    return results
