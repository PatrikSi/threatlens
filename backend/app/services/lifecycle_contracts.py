from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class TargetPreview:
    eligible_count: int
    protected_count: int = 0
    protected_counts: dict[str, int] = field(default_factory=dict)
    oldest_candidate_at: datetime | None = None
    count_is_lower_bound: bool = False
    is_partial: bool = False
    eligible_bytes: int | None = None


@dataclass(frozen=True)
class TargetBatch:
    evaluated_count: int
    affected_count: int
    protected_count: int = 0
    skipped_count: int = 0
    details: dict[str, int | str | bool] = field(default_factory=dict)
    affected_bytes: int | None = None


@dataclass(frozen=True)
class CandidateQuery:
    model: type
    timestamp: object
    predicate: object
