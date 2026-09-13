"""Version the evidence captured when a new report is planned."""

from __future__ import annotations


# Version 1 admits AI enrichment only with successful current-source provenance.
# This marks the immutable planning contract, not semantic claim verification.
REPORT_EVIDENCE_CONTRACT_VERSION = 1


def has_current_evidence_contract(coverage: object) -> bool:
    if not isinstance(coverage, dict):
        return False
    version = coverage.get("evidence_contract_version")
    return type(version) is int and version == REPORT_EVIDENCE_CONTRACT_VERSION
