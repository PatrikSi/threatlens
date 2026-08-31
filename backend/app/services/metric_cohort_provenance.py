from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass


def captured_label_revision_cohort_key(
    *,
    captured_policy_revision: int,
    captured_label_ids: Iterable[uuid.UUID],
) -> str:
    """Hash only immutable capture-time policy facts into a cohort identity."""

    labels = "|".join(sorted(str(label_id) for label_id in set(captured_label_ids)))
    canonical = f"{max(0, int(captured_policy_revision))}:{labels}"
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


@dataclass(frozen=True, slots=True)
class MetricCohortIntegritySummary:
    cohort_count: int
    invalid_identity_count: int
    missing_captured_labels_count: int
    label_parity_mismatch_count: int
    metric_parity_mismatch_count: int
    incomplete_without_quarantine_count: int

    @property
    def valid(self) -> bool:
        return not any(
            (
                self.invalid_identity_count,
                self.missing_captured_labels_count,
                self.label_parity_mismatch_count,
                self.metric_parity_mismatch_count,
                self.incomplete_without_quarantine_count,
            )
        )


__all__ = ["MetricCohortIntegritySummary", "captured_label_revision_cohort_key"]
