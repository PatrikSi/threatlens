from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.sql.elements import ColumnElement


@dataclass(frozen=True)
class PruningContext:
    cutoff: datetime
    eligibility: ColumnElement[bool]


@dataclass(frozen=True)
class PruningResult:
    children_pruned: int = 0
    parents_started: int = 0
