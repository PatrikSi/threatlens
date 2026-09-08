from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlsplit

from sqlalchemy import (
    String,
    and_,
    case,
    cast,
    distinct,
    exists,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.orm import Session

from app.core.rbac import ROLE_ADMIN
from app.core.token_scopes import (
    SCOPE_READ_ALERTS,
    SCOPE_READ_ITEMS,
    SCOPE_READ_REPORTS,
)
from app.models.alert_occurrence import AlertOccurrence
from app.models.feed import Feed
from app.models.investigation import Investigation, InvestigationEvidence
from app.models.ioc import IOC, ItemIOC
from app.models.item import Item
from app.models.item_classification import ItemClassification
from app.models.report import Report
from app.models.user import User
from app.schemas.investigation import (
    InvestigationEvidenceCandidate,
    InvestigationEvidenceCandidateListResponse,
    InvestigationEvidenceCandidateRange,
    InvestigationEvidenceQueryAnalysis,
    InvestigationEvidenceSourceCapability,
    InvestigationEvidenceType,
)
from app.services.authorization import AuthorizationContext
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
    DATA_ACCESS_RESOURCE_REPORT,
    data_access_envelope_predicate,
)
from app.services.data_access_policy import (
    DataAccessContext,
    handling_label_access_predicate,
)
from app.services.investigation_read_access import (
    load_composed_investigation_read_access,
)
from app.services.investigations import (
    InvestigationConflictError,
    InvestigationNotFoundError,
    InvestigationPermissionError,
    InvestigationReadAuthorizationChangedError,
    InvestigationValidationError,
)
from app.services.ioc_extraction import normalize_ioc_search_value
from app.services.url_utils import normalize_url


SOURCE_TYPES: tuple[InvestigationEvidenceType, ...] = (
    "item",
    "ioc",
    "report",
    "alert_occurrence",
)
SOURCE_REQUIRED_PERMISSIONS: dict[InvestigationEvidenceType, tuple[str, ...]] = {
    "item": (SCOPE_READ_ITEMS,),
    "ioc": (SCOPE_READ_ITEMS,),
    "report": (SCOPE_READ_REPORTS,),
    "alert_occurrence": (SCOPE_READ_ALERTS, SCOPE_READ_ITEMS),
}
RANGE_DELTAS: dict[InvestigationEvidenceCandidateRange, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}
MAX_QUERY_LENGTH = 255
MIN_FUZZY_QUERY_LENGTH = 3
MAX_PAGE = 20
MAX_PAGE_SIZE = 50
MAX_SOURCE_TOTAL = MAX_PAGE * MAX_PAGE_SIZE
MAX_ANCHOR_AGE = timedelta(hours=1)
MAX_RELATED_ITEMS = 3
MAX_CANDIDATE_DESCRIPTION = 600
MAX_CANDIDATE_TITLE = 512
MAX_CANDIDATE_URL = 4_096
MAX_METADATA_KEYWORDS = 20
_WRITE_MEMBER_ROLES = frozenset({"owner", "editor"})
_SOURCE_ORDER = {source_type: index for index, source_type in enumerate(SOURCE_TYPES)}


@dataclass(frozen=True)
class CandidateQuery:
    raw: str
    kind: str
    normalized_value: str | None
    detected_ioc_type: str | None
    parsed_uuid: uuid.UUID | None = None
    ioc_search_value: str | None = None

    def response(self) -> InvestigationEvidenceQueryAnalysis:
        return InvestigationEvidenceQueryAnalysis(
            kind=self.kind,
            normalized_value=self.normalized_value,
            detected_ioc_type=self.detected_ioc_type,
        )


@dataclass(frozen=True)
class RankedCandidate:
    candidate: InvestigationEvidenceCandidate
    match_rank: int

    def sort_key(self) -> tuple[int, float, int, str]:
        observed_at = self.candidate.observed_at
        if observed_at is not None and observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        timestamp = observed_at.timestamp() if observed_at is not None else 0.0
        return (
            self.match_rank,
            -timestamp,
            _SOURCE_ORDER[self.candidate.source_type],
            str(self.candidate.source_id),
        )


def analyze_candidate_query(value: str | None) -> CandidateQuery:
    raw_value = value or ""
    if any(ord(character) < 32 or ord(character) == 127 for character in raw_value):
        raise InvestigationValidationError(
            "Evidence searches cannot contain control characters."
        )
    normalized_text = " ".join(raw_value.strip().split())
    if len(normalized_text) > MAX_QUERY_LENGTH:
        raise InvestigationValidationError(
            f"Evidence searches cannot exceed {MAX_QUERY_LENGTH} characters."
        )
    if not normalized_text:
        return CandidateQuery(
            raw="", kind="empty", normalized_value=None, detected_ioc_type=None
        )

    normalized_url, hostname = _normalize_search_url(normalized_text)
    if normalized_url is not None:
        host_ioc = normalize_ioc_search_value(hostname or "")
        return CandidateQuery(
            raw=normalized_text,
            kind="url",
            normalized_value=normalized_url,
            detected_ioc_type=host_ioc[0] if host_ioc else None,
            ioc_search_value=host_ioc[1] if host_ioc else hostname,
        )

    if _is_url_with_userinfo(normalized_text):
        raise InvestigationValidationError(
            "Evidence search URLs cannot include credentials."
        )

    normalized_ioc = normalize_ioc_search_value(normalized_text)
    if normalized_ioc is not None:
        ioc_type, ioc_value = normalized_ioc
        return CandidateQuery(
            raw=normalized_text,
            kind="ioc",
            normalized_value=ioc_value,
            detected_ioc_type=ioc_type,
            ioc_search_value=ioc_value,
        )

    try:
        parsed_uuid = uuid.UUID(normalized_text)
    except ValueError:
        parsed_uuid = None
    if parsed_uuid is not None:
        normalized_uuid = str(parsed_uuid)
        return CandidateQuery(
            raw=normalized_text,
            kind="uuid",
            normalized_value=normalized_uuid,
            detected_ioc_type=None,
            parsed_uuid=parsed_uuid,
        )

    if len(normalized_text) < MIN_FUZZY_QUERY_LENGTH:
        raise InvestigationValidationError(
            "Text evidence searches must contain at least "
            f"{MIN_FUZZY_QUERY_LENGTH} characters."
        )
    return CandidateQuery(
        raw=normalized_text,
        kind="text",
        normalized_value=normalized_text.lower(),
        detected_ioc_type=None,
        ioc_search_value=normalized_text.lower(),
    )


def authorize_evidence_candidate_search(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    data_access: DataAccessContext,
) -> Investigation:
    access = load_composed_investigation_read_access(
        db,
        investigation_id=investigation_id,
        user=user,
        data_access=data_access,
    )
    if access.authorization_changed:
        raise InvestigationReadAuthorizationChangedError(
            "Your account access changed while investigation data was loading. "
            "Sign in again and retry."
        )
    investigation = access.investigation
    if investigation is None:
        raise InvestigationNotFoundError()
    if access.member_role is None:
        raise InvestigationPermissionError(
            "Join this investigation as an owner or editor before adding evidence."
        )
    if access.member_role not in _WRITE_MEMBER_ROLES:
        raise InvestigationPermissionError(
            "Your investigation membership is read-only."
        )
    if investigation.status == "archived":
        raise InvestigationConflictError(
            "Archived investigations are read-only. Reopen it before adding evidence.",
            code="investigation_archived",
        )
    return investigation


def source_capabilities(
    authorization: AuthorizationContext,
) -> list[InvestigationEvidenceSourceCapability]:
    capabilities: list[InvestigationEvidenceSourceCapability] = []
    for source_type in SOURCE_TYPES:
        required = SOURCE_REQUIRED_PERMISSIONS[source_type]
        missing = [
            permission for permission in required if not authorization.has(permission)
        ]
        capabilities.append(
            InvestigationEvidenceSourceCapability(
                source_type=source_type,
                available=not missing,
                unavailable_reason=(
                    None if not missing else f"Requires {', '.join(missing)}."
                ),
                required_permissions=list(required),
            )
        )
    return capabilities


def list_evidence_candidates(
    db: Session,
    *,
    investigation: Investigation,
    user: User,
    authorization: AuthorizationContext,
    data_access: DataAccessContext,
    q: str | None,
    requested_source_types: Iterable[InvestigationEvidenceType],
    range_value: InvestigationEvidenceCandidateRange,
    as_of: datetime | None = None,
    page: int,
    page_size: int,
    now: datetime | None = None,
) -> InvestigationEvidenceCandidateListResponse:
    if page < 1 or page > MAX_PAGE:
        raise InvestigationValidationError(
            f"Evidence candidate pages must be between 1 and {MAX_PAGE}."
        )
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise InvestigationValidationError(
            f"Evidence candidate page sizes must be between 1 and {MAX_PAGE_SIZE}."
        )
    analysis = analyze_candidate_query(q)
    effective_until = _resolve_effective_until(as_of=as_of, now=now)
    effective_since = effective_until - RANGE_DELTAS[range_value]
    capabilities = source_capabilities(authorization)
    available = {
        capability.source_type for capability in capabilities if capability.available
    }
    requested = set(requested_source_types)
    selected = tuple(
        source_type
        for source_type in SOURCE_TYPES
        if source_type in requested and source_type in available
    )
    end = page * page_size
    ranked: list[RankedCandidate] = []
    total = 0
    total_truncated = False
    for source_type in selected:
        loader = _SOURCE_LOADERS[source_type]
        source_candidates, source_total, source_total_truncated = loader(
            db,
            investigation_id=investigation.id,
            user=user,
            data_access=data_access,
            analysis=analysis,
            since=effective_since,
            until=effective_until,
            limit=end,
        )
        ranked.extend(source_candidates)
        total += source_total
        total_truncated = total_truncated or source_total_truncated

    ranked.sort(key=RankedCandidate.sort_key)
    offset = (page - 1) * page_size
    page_candidates = [entry.candidate for entry in ranked[offset:end]]
    page_candidates = _include_related_items(
        db,
        candidates=page_candidates,
        data_access=data_access,
        analysis=analysis,
        since=effective_since,
        until=effective_until,
    )
    return InvestigationEvidenceCandidateListResponse(
        candidates=page_candidates,
        total=total,
        total_truncated=total_truncated,
        page=page,
        page_size=page_size,
        query_analysis=analysis.response(),
        source_capabilities=capabilities,
        effective_range=range_value,
        effective_since=effective_since,
        effective_until=effective_until,
    )


def _load_item_candidates(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    data_access: DataAccessContext,
    analysis: CandidateQuery,
    since: datetime,
    until: datetime,
    limit: int,
) -> tuple[list[RankedCandidate], int, bool]:
    del user
    predicates = [
        *_candidate_time_predicates(
            analysis,
            observed_at=Item.first_seen_at,
            since=since,
            until=until,
        ),
        handling_label_access_predicate(Feed.handling_label_id, data_access),
    ]
    match_condition, match_rank, match_reason = _item_match_expressions(analysis)
    if match_condition is not None:
        predicates.append(match_condition)
    attached = _attached_expression(investigation_id, "item", Item.id)
    base = (
        select(
            Item,
            Feed.name.label("feed_name"),
            ItemClassification.primary_category.label("classification"),
            attached.label("already_attached"),
            match_rank.label("match_rank"),
            match_reason.label("match_reason"),
        )
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(ItemClassification, ItemClassification.item_id == Item.id)
        .where(*predicates)
    )
    total, total_truncated = _bounded_total(db, base)
    rows = db.execute(
        base.order_by(match_rank.asc(), Item.first_seen_at.desc(), Item.id.asc()).limit(
            limit
        )
    ).all()
    return (
        [
            RankedCandidate(
                candidate=InvestigationEvidenceCandidate(
                    source_type="item",
                    source_id=row.Item.id,
                    title=_bounded(row.Item.title, MAX_CANDIDATE_TITLE)
                    or "Untitled article",
                    description=_bounded(row.Item.summary, MAX_CANDIDATE_DESCRIPTION),
                    url=_safe_url(row.Item.canonical_url or row.Item.url),
                    observed_at=row.Item.first_seen_at,
                    source_label=_bounded(row.feed_name, 255),
                    metadata={
                        "feed_id": str(row.Item.feed_id),
                        "published_at": _isoformat(row.Item.published_at),
                        "first_seen_at": _isoformat(row.Item.first_seen_at),
                        "classification": row.classification,
                    },
                    already_attached=bool(row.already_attached),
                    match_reason=row.match_reason,
                ),
                match_rank=int(row.match_rank),
            )
            for row in rows
        ],
        total,
        total_truncated,
    )


def _item_match_expressions(analysis: CandidateQuery):
    if analysis.kind == "empty":
        return None, _constant_rank(50), _constant_reason("recent")
    exact_id = Item.id == analysis.parsed_uuid if analysis.parsed_uuid else None
    if exact_id is not None:
        return exact_id, _constant_rank(0), _constant_reason("exact_id")
    search_text = (
        analysis.normalized_value
        if analysis.kind == "url" and analysis.normalized_value is not None
        else analysis.raw
    )
    lowered = search_text.lower()
    escaped = _escape_like(lowered)
    contains_pattern = f"%{escaped}%"
    prefix_pattern = f"{escaped}%"
    title = func.lower(Item.title)
    summary = func.lower(func.coalesce(Item.summary, ""))
    item_url = func.lower(cast(Item.url, String))
    canonical_url = func.lower(cast(func.coalesce(Item.canonical_url, ""), String))
    exact_url = (
        or_(item_url == lowered, canonical_url == lowered)
        if analysis.kind == "url"
        else None
    )
    exact_text = title == lowered
    related_ioc = _related_ioc_expression(analysis)
    prefix = or_(
        title.like(prefix_pattern, escape="\\"),
        item_url.like(prefix_pattern, escape="\\"),
        canonical_url.like(prefix_pattern, escape="\\"),
    )
    text_match = or_(
        title.like(contains_pattern, escape="\\"),
        summary.like(contains_pattern, escape="\\"),
        item_url.like(contains_pattern, escape="\\"),
        canonical_url.like(contains_pattern, escape="\\"),
    )
    conditions = [
        condition
        for condition in (
            exact_url,
            exact_text,
            related_ioc,
            prefix,
            text_match,
        )
        if condition is not None
    ]
    rank_whens = []
    reason_whens = []
    if exact_url is not None:
        rank_whens.append((exact_url, 0))
        reason_whens.append((exact_url, "exact_url"))
    rank_whens.append((exact_text, 1))
    reason_whens.append((exact_text, "exact_text"))
    if related_ioc is not None:
        rank_whens.append((related_ioc, 2))
        reason_whens.append((related_ioc, "related_ioc"))
    rank_whens.append((prefix, 3))
    reason_whens.append((prefix, "prefix"))
    return (
        or_(*conditions),
        case(*rank_whens, else_=4),
        case(*reason_whens, else_="text"),
    )


def _related_ioc_expression(analysis: CandidateQuery):
    if analysis.detected_ioc_type is None or analysis.ioc_search_value is None:
        return None
    return exists(
        select(ItemIOC.item_id)
        .join(IOC, IOC.id == ItemIOC.ioc_id)
        .where(
            ItemIOC.item_id == Item.id,
            IOC.type == analysis.detected_ioc_type,
            IOC.value_norm == analysis.ioc_search_value,
        )
    )


def _load_ioc_candidates(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    data_access: DataAccessContext,
    analysis: CandidateQuery,
    since: datetime,
    until: datetime,
    limit: int,
) -> tuple[list[RankedCandidate], int, bool]:
    del user
    predicates = [
        *_candidate_time_predicates(
            analysis,
            observed_at=Item.first_seen_at,
            since=since,
            until=until,
        ),
        handling_label_access_predicate(Feed.handling_label_id, data_access),
    ]
    match_condition, match_rank, match_reason = _ioc_match_expressions(analysis)
    if match_condition is not None:
        predicates.append(match_condition)
    attached = _attached_expression(investigation_id, "ioc", IOC.id)
    first_seen = func.min(Item.first_seen_at)
    last_seen = func.max(Item.first_seen_at)
    observation_count = func.count(distinct(ItemIOC.item_id))
    base = (
        select(
            IOC.id,
            IOC.type,
            IOC.value_raw,
            IOC.value_norm,
            first_seen.label("first_seen"),
            last_seen.label("last_seen"),
            observation_count.label("observation_count"),
            attached.label("already_attached"),
            match_rank.label("match_rank"),
            match_reason.label("match_reason"),
        )
        .join(ItemIOC, ItemIOC.ioc_id == IOC.id)
        .join(Item, Item.id == ItemIOC.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(*predicates)
        .group_by(IOC.id, IOC.type, IOC.value_raw, IOC.value_norm)
    )
    total, total_truncated = _bounded_total(db, base)
    rows = db.execute(
        base.order_by(match_rank.asc(), last_seen.desc(), IOC.id.asc()).limit(limit)
    ).all()
    return (
        [
            RankedCandidate(
                candidate=InvestigationEvidenceCandidate(
                    source_type="ioc",
                    source_id=row.id,
                    title=_bounded(
                        f"{_ioc_type_label(row.type)}: {row.value_raw}",
                        MAX_CANDIDATE_TITLE,
                    )
                    or "Indicator",
                    description=None,
                    url=None,
                    observed_at=row.last_seen,
                    source_label=_ioc_type_label(row.type),
                    metadata={
                        "ioc_type": row.type,
                        "value": _bounded(row.value_raw, 384),
                        "first_seen_at": _isoformat(row.first_seen),
                        "last_seen_at": _isoformat(row.last_seen),
                        "observation_count": int(row.observation_count or 0),
                        "related_items": [],
                    },
                    already_attached=bool(row.already_attached),
                    match_reason=row.match_reason,
                ),
                match_rank=int(row.match_rank),
            )
            for row in rows
        ],
        total,
        total_truncated,
    )


def _ioc_match_expressions(analysis: CandidateQuery):
    if analysis.kind == "empty":
        return None, _constant_rank(50), _constant_reason("recent")
    exact_id = IOC.id == analysis.parsed_uuid if analysis.parsed_uuid else None
    if exact_id is not None:
        return exact_id, _constant_rank(0), _constant_reason("exact_id")
    search_value = (analysis.ioc_search_value or analysis.raw).lower()
    escaped = _escape_like(search_value)
    value = func.lower(IOC.value_norm)
    exact_ioc = None
    if analysis.detected_ioc_type and analysis.ioc_search_value:
        exact_ioc = and_(
            IOC.type == analysis.detected_ioc_type,
            IOC.value_norm == analysis.ioc_search_value,
        )
    exact_value = value == search_value
    prefix = value.like(f"{escaped}%", escape="\\")
    text_match = value.like(f"%{escaped}%", escape="\\")
    conditions = [
        condition
        for condition in (exact_ioc, exact_value, prefix, text_match)
        if condition is not None
    ]
    rank_whens = []
    reason_whens = []
    if exact_ioc is not None:
        rank_whens.append((exact_ioc, 2 if analysis.kind == "url" else 0))
        reason_whens.append((exact_ioc, "exact_ioc"))
    rank_whens.extend(((exact_value, 1), (prefix, 2)))
    reason_whens.extend(((exact_value, "exact_ioc"), (prefix, "prefix")))
    return (
        or_(*conditions),
        case(*rank_whens, else_=3),
        case(*reason_whens, else_="text"),
    )


def _load_report_candidates(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    data_access: DataAccessContext,
    analysis: CandidateQuery,
    since: datetime,
    until: datetime,
    limit: int,
) -> tuple[list[RankedCandidate], int, bool]:
    observed_at = func.coalesce(Report.generated_at, Report.created_at)
    predicates = [
        *_candidate_time_predicates(
            analysis,
            observed_at=observed_at,
            since=since,
            until=until,
        ),
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_REPORT, Report.id, data_access
        ),
    ]
    if user.role != ROLE_ADMIN:
        predicates.append(Report.owner_user_id == user.id)
    match_condition, match_rank, match_reason = _text_source_match_expressions(
        analysis,
        source_id=Report.id,
        primary=Report.title,
        secondary=(Report.summary_text, Report.report_type),
    )
    if match_condition is not None:
        predicates.append(match_condition)
    attached = _attached_expression(investigation_id, "report", Report.id)
    base = select(
        Report,
        observed_at.label("observed_at"),
        attached.label("already_attached"),
        match_rank.label("match_rank"),
        match_reason.label("match_reason"),
    ).where(*predicates)
    total, total_truncated = _bounded_total(db, base)
    rows = db.execute(
        base.order_by(match_rank.asc(), observed_at.desc(), Report.id.asc()).limit(
            limit
        )
    ).all()
    return (
        [
            RankedCandidate(
                candidate=InvestigationEvidenceCandidate(
                    source_type="report",
                    source_id=row.Report.id,
                    title=_bounded(row.Report.title, MAX_CANDIDATE_TITLE)
                    or "Untitled report",
                    description=_bounded(
                        row.Report.summary_text, MAX_CANDIDATE_DESCRIPTION
                    ),
                    url=None,
                    observed_at=row.observed_at,
                    source_label=_humanize(row.Report.report_type),
                    metadata={
                        "report_type": row.Report.report_type,
                        "status": row.Report.status,
                        "period_start": _isoformat(row.Report.period_start),
                        "period_end": _isoformat(row.Report.period_end),
                        "generated_at": _isoformat(row.Report.generated_at),
                    },
                    already_attached=bool(row.already_attached),
                    match_reason=row.match_reason,
                ),
                match_rank=int(row.match_rank),
            )
            for row in rows
        ],
        total,
        total_truncated,
    )


def _load_alert_candidates(
    db: Session,
    *,
    investigation_id: uuid.UUID,
    user: User,
    data_access: DataAccessContext,
    analysis: CandidateQuery,
    since: datetime,
    until: datetime,
    limit: int,
) -> tuple[list[RankedCandidate], int, bool]:
    predicates = [
        AlertOccurrence.owner_user_id == user.id,
        *_candidate_time_predicates(
            analysis,
            observed_at=AlertOccurrence.created_at,
            since=since,
            until=until,
        ),
        data_access_envelope_predicate(
            DATA_ACCESS_RESOURCE_ALERT_OCCURRENCE,
            AlertOccurrence.id,
            data_access,
        ),
    ]
    match_condition, match_rank, match_reason = _text_source_match_expressions(
        analysis,
        source_id=AlertOccurrence.id,
        primary=AlertOccurrence.alert_name_snapshot,
        secondary=(
            AlertOccurrence.alert_category_snapshot,
            cast(AlertOccurrence.matched_keywords, String),
        ),
    )
    if match_condition is not None:
        predicates.append(match_condition)
    attached = _attached_expression(
        investigation_id, "alert_occurrence", AlertOccurrence.id
    )
    base = select(
        AlertOccurrence,
        attached.label("already_attached"),
        match_rank.label("match_rank"),
        match_reason.label("match_reason"),
    ).where(*predicates)
    total, total_truncated = _bounded_total(db, base)
    rows = db.execute(
        base.order_by(
            match_rank.asc(),
            AlertOccurrence.created_at.desc(),
            AlertOccurrence.id.asc(),
        ).limit(limit)
    ).all()
    candidates: list[RankedCandidate] = []
    for row in rows:
        occurrence = row.AlertOccurrence
        item_snapshot = _mapping(occurrence.source_snapshot_json, "item")
        item_title = _string(item_snapshot.get("title"))
        title = (
            f"{occurrence.alert_name_snapshot}: {item_title}"
            if item_title
            else occurrence.alert_name_snapshot
        )
        candidates.append(
            RankedCandidate(
                candidate=InvestigationEvidenceCandidate(
                    source_type="alert_occurrence",
                    source_id=occurrence.id,
                    title=_bounded(title, MAX_CANDIDATE_TITLE) or "Alert occurrence",
                    description=_bounded(
                        _string(item_snapshot.get("summary")),
                        MAX_CANDIDATE_DESCRIPTION,
                    ),
                    url=_safe_url(
                        _string(
                            item_snapshot.get("canonical_url")
                            or item_snapshot.get("url")
                        )
                    ),
                    observed_at=occurrence.created_at,
                    source_label=_humanize(occurrence.alert_category_snapshot),
                    metadata={
                        "severity": occurrence.severity_snapshot,
                        "lifecycle_state": occurrence.lifecycle_state,
                        "alert_category": occurrence.alert_category_snapshot,
                        "matched_keywords": [
                            _bounded(str(keyword), 255)
                            for keyword in list(occurrence.matched_keywords or [])[
                                :MAX_METADATA_KEYWORDS
                            ]
                        ],
                        "created_at": _isoformat(occurrence.created_at),
                    },
                    already_attached=bool(row.already_attached),
                    match_reason=row.match_reason,
                ),
                match_rank=int(row.match_rank),
            )
        )
    return candidates, total, total_truncated


def _text_source_match_expressions(
    analysis: CandidateQuery,
    *,
    source_id,
    primary,
    secondary: tuple,
):
    if analysis.kind == "empty":
        return None, _constant_rank(50), _constant_reason("recent")
    exact_id = source_id == analysis.parsed_uuid if analysis.parsed_uuid else None
    if exact_id is not None:
        return exact_id, _constant_rank(0), _constant_reason("exact_id")
    lowered = analysis.raw.lower()
    escaped = _escape_like(lowered)
    primary_lower = func.lower(primary)
    secondary_lower = [func.lower(func.coalesce(value, "")) for value in secondary]
    exact_text = primary_lower == lowered
    prefix = primary_lower.like(f"{escaped}%", escape="\\")
    text_match = or_(
        primary_lower.like(f"%{escaped}%", escape="\\"),
        *[value.like(f"%{escaped}%", escape="\\") for value in secondary_lower],
    )
    conditions = [
        condition
        for condition in (exact_text, prefix, text_match)
        if condition is not None
    ]
    rank_whens = []
    reason_whens = []
    rank_whens.append((exact_text, 1))
    reason_whens.append((exact_text, "exact_text"))
    rank_whens.append((prefix, 2))
    reason_whens.append((prefix, "prefix"))
    return (
        or_(*conditions),
        case(*rank_whens, else_=3),
        case(*reason_whens, else_="text"),
    )


def _include_related_items(
    db: Session,
    *,
    candidates: list[InvestigationEvidenceCandidate],
    data_access: DataAccessContext,
    analysis: CandidateQuery,
    since: datetime,
    until: datetime,
) -> list[InvestigationEvidenceCandidate]:
    # Empty-query browsing stays lightweight. Related-article correlation is
    # added once the analyst searches for a concrete indicator or source ID.
    if analysis.kind == "empty":
        return candidates
    ioc_ids = [
        candidate.source_id
        for candidate in candidates
        if candidate.source_type == "ioc"
    ]
    if not ioc_ids:
        return candidates
    row_number = func.row_number().over(
        partition_by=ItemIOC.ioc_id,
        order_by=(Item.first_seen_at.desc(), Item.id.asc()),
    )
    ranked = (
        select(
            ItemIOC.ioc_id.label("ioc_id"),
            Item.id.label("item_id"),
            Item.title.label("title"),
            Feed.name.label("feed_name"),
            Item.first_seen_at.label("first_seen_at"),
            row_number.label("row_number"),
        )
        .join(Item, Item.id == ItemIOC.item_id)
        .join(Feed, Feed.id == Item.feed_id)
        .where(
            ItemIOC.ioc_id.in_(ioc_ids),
            *_candidate_time_predicates(
                analysis,
                observed_at=Item.first_seen_at,
                since=since,
                until=until,
            ),
            handling_label_access_predicate(Feed.handling_label_id, data_access),
        )
        .subquery()
    )
    rows = db.execute(
        select(ranked).where(ranked.c.row_number <= MAX_RELATED_ITEMS)
    ).all()
    related_by_ioc: dict[uuid.UUID, list[dict]] = {ioc_id: [] for ioc_id in ioc_ids}
    for row in rows:
        related_by_ioc.setdefault(row.ioc_id, []).append(
            {
                "item_id": str(row.item_id),
                "title": _bounded(row.title, 255),
                "feed_name": _bounded(row.feed_name, 255),
                "first_seen_at": _isoformat(row.first_seen_at),
            }
        )
    enriched: list[InvestigationEvidenceCandidate] = []
    for candidate in candidates:
        if candidate.source_type != "ioc":
            enriched.append(candidate)
            continue
        metadata = dict(candidate.metadata)
        metadata["related_items"] = related_by_ioc.get(candidate.source_id, [])
        enriched.append(candidate.model_copy(update={"metadata": metadata}))
    return enriched


def _attached_expression(
    investigation_id: uuid.UUID,
    source_type: InvestigationEvidenceType,
    source_id_column,
):
    return exists(
        select(InvestigationEvidence.id).where(
            InvestigationEvidence.investigation_id == investigation_id,
            InvestigationEvidence.source_type == source_type,
            InvestigationEvidence.source_id == source_id_column,
        )
    )


def _constant_rank(value: int):
    return literal(value)


def _constant_reason(value: str):
    return literal(value)


def _bounded_total(db: Session, query) -> tuple[int, bool]:
    bounded_query = query.order_by(None).limit(MAX_SOURCE_TOTAL + 1).subquery()
    observed = int(db.scalar(select(func.count()).select_from(bounded_query)) or 0)
    return min(observed, MAX_SOURCE_TOTAL), observed > MAX_SOURCE_TOTAL


def _candidate_time_predicates(
    analysis: CandidateQuery,
    *,
    observed_at,
    since: datetime,
    until: datetime,
) -> list:
    if analysis.kind == "uuid":
        return []
    return [observed_at >= since, observed_at <= until]


def _normalize_search_url(value: str) -> tuple[str | None, str | None]:
    try:
        parsed = urlsplit(value)
        hostname_value = parsed.hostname
        has_userinfo = parsed.username is not None or parsed.password is not None
    except ValueError:
        return None, None
    if (
        parsed.scheme.lower() not in {"http", "https"}
        or not hostname_value
        or has_userinfo
    ):
        return None, None
    hostname = hostname_value.lower().rstrip(".")
    hostname_ioc = normalize_ioc_search_value(hostname)
    canonical_hostname = (
        hostname_ioc[1]
        if hostname_ioc and hostname_ioc[0] in {"domain", "ipv4", "ipv6"}
        else hostname
    )
    normalized = normalize_url(value)
    if not normalized:
        return None, None
    return normalized, canonical_hostname


def _is_url_with_userinfo(value: str) -> bool:
    lowered = value.lower()
    for prefix in ("http://", "https://"):
        if lowered.startswith(prefix):
            authority = value[len(prefix) :].split("/", 1)[0]
            authority = authority.split("?", 1)[0].split("#", 1)[0]
            if "@" in authority:
                return True
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme.lower() in {"http", "https"}
            and (parsed.username is not None or parsed.password is not None)
        )
    except ValueError:
        return False


def _safe_url(value: str | None) -> str | None:
    if value is None:
        return None
    normalized, _hostname = _normalize_search_url(value.strip())
    return _bounded(normalized, MAX_CANDIDATE_URL)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _bounded(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _mapping(value: object, key: str) -> dict:
    if not isinstance(value, dict):
        return {}
    nested = value.get(key)
    return nested if isinstance(nested, dict) else {}


def _string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _humanize(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().title()


def _ioc_type_label(value: str) -> str:
    labels = {
        "hash_sha256": "SHA-256",
        "hash_sha1": "SHA-1",
        "hash_md5": "MD5",
        "cve": "CVE",
        "ipv4": "IPv4",
        "ipv6": "IPv6",
        "domain": "Domain",
        "vendor": "Vendor",
        "program": "Program",
    }
    return labels.get(value, _humanize(value))


def _isoformat(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _resolve_effective_until(
    *,
    as_of: datetime | None,
    now: datetime | None,
) -> datetime:
    observed_now = _as_utc(now or datetime.now(timezone.utc))
    if as_of is None:
        return observed_now
    anchor = _as_utc(as_of)
    if anchor > observed_now + timedelta(seconds=30):
        raise InvestigationValidationError(
            "Evidence search anchors cannot be in the future."
        )
    if observed_now - anchor > MAX_ANCHOR_AGE:
        raise InvestigationValidationError(
            "This evidence search has expired. Restart it to load current results."
        )
    return anchor


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


_SOURCE_LOADERS = {
    "item": _load_item_candidates,
    "ioc": _load_ioc_candidates,
    "report": _load_report_candidates,
    "alert_occurrence": _load_alert_candidates,
}


__all__ = [
    "MAX_PAGE",
    "MAX_PAGE_SIZE",
    "MAX_SOURCE_TOTAL",
    "MAX_QUERY_LENGTH",
    "MIN_FUZZY_QUERY_LENGTH",
    "RANGE_DELTAS",
    "SOURCE_REQUIRED_PERMISSIONS",
    "SOURCE_TYPES",
    "analyze_candidate_query",
    "authorize_evidence_candidate_search",
    "list_evidence_candidates",
    "source_capabilities",
]
