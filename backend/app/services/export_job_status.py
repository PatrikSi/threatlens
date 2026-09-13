"""Evaluate a page of export statuses under one request's authorization fences."""

from __future__ import annotations

import uuid
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.export_job import ExportJob
from app.models.feed import Feed
from app.models.item import Item
from app.services.authorization import AuthorizationContext, fence_authorization_context
from app.services.data_access_policy import DataAccessContext, fence_data_access_context
from app.services.export_job_access import (
    ExportJobAccessDenied,
    authorize_export_job,
    fence_export_authorization,
    load_export_authorization,
    load_export_sources,
)
from app.services.export_job_contracts import ExportAuthorizationSnapshot, ExportSource

SOURCE_LOOKUP_BATCH_SIZE = 500
MAX_CACHED_SOURCE_IDENTITIES = 20_000
_AuthorizationKey = tuple[str, uuid.UUID, ExportAuthorizationSnapshot]
_CurrentSource = tuple[uuid.UUID, uuid.UUID]


@dataclass(frozen=True)
class _AuthorizedGroup:
    representative: ExportJob
    snapshot: ExportAuthorizationSnapshot
    authorization: AuthorizationContext
    access: DataAccessContext


def export_job_visibility(
    db: Session,
    jobs: Sequence[ExportJob],
    *,
    current_authorization: AuthorizationContext | None,
    current_access: DataAccessContext | None,
) -> dict[uuid.UUID, bool]:
    """Return metadata visibility without retaining article bodies or a global cache.

    IAM/handling revisions and owner/credential rows remain stable for this
    request. Source rows are checked in bounded batches, as for an individual
    status, and shared only within this evaluation. Authorization is checked
    again after source work to observe time-based credential/grant expiry.
    """
    if not jobs:
        return {}
    if current_authorization is not None:
        fence_authorization_context(db, current_authorization)
    if current_access is not None and current_authorization is not None:
        fence_data_access_context(db, current_access)
    keys, groups = _authorize_groups(db, jobs)
    if current_access is not None and current_authorization is None:
        # Internal callers without a request IAM snapshot must let the first
        # accepting group acquire IAM before the handling-policy fence.
        fence_data_access_context(db, current_access)
    membership = _SourceMembership(db)
    visible = {job.id: False for job in jobs}
    for job in jobs:
        key = keys.get(job.id)
        group = groups.get(key) if key is not None else None
        if group is None:
            continue
        try:
            accesses = (
                (group.access,)
                if current_access is None
                else (group.access, current_access)
            )
            visible[job.id] = membership.allows(load_export_sources(job), accesses)
        except ExportJobAccessDenied:
            pass
        finally:
            # Deferred encrypted snapshots can be megabytes each. Keep only
            # this job's decoded source list while validating the next job.
            db.expire(job, ["source_encrypted"])
    valid_groups = {
        key for key, group in groups.items() if _authorization_still_current(db, group)
    }
    return {
        job_id: permitted and keys.get(job_id) in valid_groups
        for job_id, permitted in visible.items()
    }


def _authorize_groups(
    db: Session,
    jobs: Sequence[ExportJob],
) -> tuple[
    dict[uuid.UUID, _AuthorizationKey], dict[_AuthorizationKey, _AuthorizedGroup]
]:
    keys: dict[uuid.UUID, _AuthorizationKey] = {}
    representatives: dict[_AuthorizationKey, ExportJob] = {}
    for job in jobs:
        try:
            snapshot = load_export_authorization(job)
        except ExportJobAccessDenied:
            continue
        key = (job.principal_type, job.principal_id, snapshot)
        keys[job.id] = key
        representatives.setdefault(key, job)
    groups: dict[_AuthorizationKey, _AuthorizedGroup] = {}
    # Normally a page has one owner. Sorting also makes the helper safe when
    # an internal caller supplies several owners or accepting credentials.
    ordered = sorted(
        representatives,
        key=lambda key: (
            key[0],
            key[1].hex,
            key[2].credential_kind,
            key[2].credential_id.hex,
        ),
    )
    for key in ordered:
        job, snapshot = representatives[key], key[2]
        try:
            authorization, access = authorize_export_job(db, job, snapshot=snapshot)
            fence_export_authorization(
                db, job, authorization, access, snapshot=snapshot
            )
            groups[key] = _AuthorizedGroup(job, snapshot, authorization, access)
        except ExportJobAccessDenied:
            continue
    return keys, groups


def _authorization_still_current(db: Session, group: _AuthorizedGroup) -> bool:
    try:
        authorization, access = authorize_export_job(
            db,
            group.representative,
            snapshot=group.snapshot,
        )
        return authorization == group.authorization and access == group.access
    except ExportJobAccessDenied:
        return False


class _SourceMembership:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.rows: OrderedDict[uuid.UUID, _CurrentSource | None] = OrderedDict()

    def allows(
        self, sources: Sequence[ExportSource], accesses: Sequence[DataAccessContext]
    ) -> bool:
        if any(
            not access.allows(source.captured_label_id)
            for source in sources
            for access in accesses
        ):
            return False
        for offset in range(0, len(sources), SOURCE_LOOKUP_BATCH_SIZE):
            chunk = sources[offset : offset + SOURCE_LOOKUP_BATCH_SIZE]
            self._load_missing(chunk)
            for source in chunk:
                current = self.rows[source.item_id]
                self.rows.move_to_end(source.item_id)
                if current is None or current[0] != source.feed_id:
                    return False
                if any(not access.allows(current[1]) for access in accesses):
                    return False
        return True

    def _load_missing(self, sources: Sequence[ExportSource]) -> None:
        missing = {
            source.item_id for source in sources if source.item_id not in self.rows
        }
        if not missing:
            return
        rows = self.db.execute(
            select(Item.id, Item.feed_id, Feed.handling_label_id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(Item.id.in_(missing))
        ).all()
        current = {item_id: (feed_id, label_id) for item_id, feed_id, label_id in rows}
        for item_id in missing:
            self.rows[item_id] = current.get(item_id)
        # Keep this whole batch resident, even at the cache boundary. Evict
        # older identities first; callers touch every resident batch member.
        protected = {source.item_id for source in sources}
        for item_id in protected:
            if item_id in self.rows:
                self.rows.move_to_end(item_id)
        while len(self.rows) > MAX_CACHED_SOURCE_IDENTITIES:
            self.rows.popitem(last=False)
