"""Authorized stored-data retrieval for a fixed set of read-only MCP tools.

All functions run in the caller's transaction. Keep it open until delivery ends:
the shared policy, principal and credential fences protect the returned facts.
No tool fetches URLs, runs providers, queues work or changes domain records.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models.article import Article
from app.models.data_policy import (
    DataAccessEnvelope,
    DataAccessEnvelopeLabel,
    DataAccessEnvelopeSource,
)
from app.models.feed import Feed
from app.models.investigation import (
    Investigation,
    InvestigationEvidence,
    InvestigationNote,
)
from app.models.item import Item
from app.models.item_ai_enrichment import ItemAIEnrichment
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.models.team_item_assessment import TeamItemAssessment
from app.models.user import User
from app.schemas.mcp_reads import (
    ArticleEvidenceArguments,
    InvestigationArguments,
    MCPReadResult,
    ReadTruncation,
    ReportArguments,
    SearchArticlesArguments,
    TeamAssessmentArguments,
)
from app.services.data_access_envelopes import (
    DATA_ACCESS_RESOURCE_INVESTIGATION,
    DATA_ACCESS_RESOURCE_REPORT,
    require_data_access_for_egress,
)
from app.services.data_access_policy import handling_label_access_predicate
from app.services.ai_extraction import item_extraction_response
from app.services.export_job_access import (
    ExportJobAccessDenied,
    fence_export_authorization,
)
from app.services.investigation_read_access import (
    load_composed_investigation_read_access,
)
from app.services.mcp_read_contracts import (
    MAX_LINEAGE_SOURCES,
    MAX_RESPONSE_BYTES,
    MAX_STORED_ASSESSMENT_BYTES,
    MAX_STORED_EXTRACTION_BYTES,
    MCPReadContext,
    MCPReadError,
    bounded_result,
    decode_cursor,
    encode_cursor,
)
from app.services.report_read_access import get_accessible_report
from app.services.team_assessment_access import (
    AssessmentRequest,
    RequestPrincipal,
    load_assessment_state,
    result_is_stale,
)
from app.services.team_access import lock_team_for_current_access

_ARGUMENTS = {
    "search_articles": SearchArticlesArguments,
    "get_article_evidence": ArticleEvidenceArguments,
    "get_team_assessment": TeamAssessmentArguments,
    "get_investigation": InvestigationArguments,
    "get_report": ReportArguments,
}
_PERMISSIONS = {
    "search_articles": ("read:mcp", "read:items"),
    "get_article_evidence": ("read:mcp", "read:items"),
    "get_team_assessment": ("read:mcp", "read:items", "read:teams"),
    "get_investigation": ("read:mcp", "read:investigations"),
    "get_report": ("read:mcp", "read:reports"),
}
_HUMAN_TOOLS = frozenset({"get_team_assessment", "get_investigation"})


def tool_input_schema(name: str) -> dict:
    return _ARGUMENTS[name].model_json_schema()


def tool_output_schema() -> dict:
    return MCPReadResult.model_json_schema()


def tool_required_permissions(name: str) -> tuple[str, ...]:
    return _PERMISSIONS[name]


def available_read_tools(context: MCPReadContext) -> tuple[str, ...]:
    return tuple(
        name
        for name, permissions in _PERMISSIONS.items()
        if all(context.authorization.has(permission) for permission in permissions)
        and (name not in _HUMAN_TOOLS or isinstance(context.principal, User))
    )


def call_read_tool(
    db: Session,
    *,
    context: MCPReadContext,
    tool_name: str,
    arguments: dict,
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    canonical_base_url: str = "",
) -> dict:
    if tool_name not in _ARGUMENTS:
        raise MCPReadError("unknown_tool", "Unknown read tool.")
    payload = _ARGUMENTS[tool_name].model_validate(arguments)
    _authorize(db, context, tool_name)
    handlers = {
        "search_articles": _search_articles,
        "get_article_evidence": _article_evidence,
        "get_team_assessment": _team_assessment,
        "get_investigation": _investigation,
        "get_report": _report,
    }
    with db.no_autoflush:
        result = handlers[tool_name](
            db, context, payload, canonical_base_url.rstrip("/")
        )
    bounded = bounded_result(result, max_bytes=max_response_bytes)
    previous_count = len(result.data.get("articles", ()))
    while (
        tool_name == "search_articles"
        and len(bounded["data"]["articles"]) < previous_count
    ):
        previous_count = len(bounded["data"]["articles"])
        last = bounded["data"]["articles"][-1]
        bounded["data"]["has_more"] = True
        bounded["next_cursor"] = encode_cursor(
            context,
            payload,
            last_seen=datetime.fromisoformat(last["first_seen_at"]),
            last_id=last["item_id"],
            snapshot_at=datetime.fromisoformat(
                bounded["freshness"]["search_snapshot_at"]
            ),
        )
        bounded = bounded_result(
            MCPReadResult.model_validate(bounded), max_bytes=max_response_bytes
        )
    return bounded


def _authorize(db, context, name):
    principal_type = (
        "user" if isinstance(context.principal, User) else "service_account"
    )
    if (
        context.principal.id != context.authorization.principal_id
        or context.principal.id != context.data_access.principal_id
        or context.authorization.principal_type != principal_type
        or context.data_access.principal_type != principal_type
        or name not in available_read_tools(context)
    ):
        raise MCPReadError(
            "access_denied", "The current credential does not permit this tool."
        )
    try:
        fence_export_authorization(
            db,
            RequestPrincipal(context.principal.id, principal_type=principal_type),
            context.authorization,
            context.data_access,
            snapshot=context.credential_snapshot,
            required_permissions=_PERMISSIONS[name],
        )
    except ExportJobAccessDenied as exc:
        raise MCPReadError(
            "access_denied", "The current credential no longer permits this tool."
        ) from exc


def _missing():
    return MCPReadError(
        "not_found", "Record not found or unavailable to this credential."
    )


def _result(context, *, data, link, kind, freshness=None, fields=(), next_cursor=None):
    fields = sorted(set(fields))
    return MCPReadResult(
        data=data,
        canonical_link=link,
        next_cursor=next_cursor,
        provenance={
            "system": "ThreatLens",
            "resource_type": kind,
            "content_trust": "untrusted_stored_content",
            "authorization_policy_revision": context.authorization.policy_revision,
            "data_policy_revision": context.data_access.policy_revision,
            "data_policy_mode": context.data_access.mode,
        },
        freshness={
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            **(freshness or {}),
        },
        truncation=ReadTruncation(
            truncated=bool(fields),
            fields=fields,
            reasons=["field_or_collection_limit"] if fields else [],
        ),
    )


def _project(column, name, cap=2000):
    return func.substr(column, 1, cap + 1).label(name)


def _record(row, *, caps, prefix, cuts):
    value = dict(row._mapping)
    for name, cap in caps.items():
        if isinstance(value.get(name), str) and len(value[name]) > cap:
            value[name] = value[name][:cap]
            cuts.append(f"{prefix}.{name}")
    return value


def _article_query(context):
    return (
        select(
            Item.id.label("item_id"),
            Item.feed_id,
            _project(Feed.name, "feed_name", 255),
            _project(Item.title, "title", 1000),
            _project(Item.summary, "summary", 2000),
            _project(Item.canonical_url, "source_canonical_url", 2000),
            _project(Item.url, "source_url", 2000),
            Item.published_at,
            Item.first_seen_at,
            Item.updated_at,
            Article.id.label("article_id"),
            Article.retrieved_at.label("article_retrieved_at"),
        )
        .select_from(Item)
        .join(Feed, Feed.id == Item.feed_id)
        .outerjoin(
            Article,
            Article.item_id == Item.id,
        )
        .where(
            handling_label_access_predicate(Feed.handling_label_id, context.data_access)
        )
    )


_ARTICLE_CAPS = {
    "feed_name": 255,
    "title": 1000,
    "summary": 2000,
    "source_canonical_url": 2000,
    "source_url": 2000,
}


def _search_articles(db, context, args, base):
    cursor = decode_cursor(context, args)
    snapshot = cursor[2] if cursor else datetime.now(timezone.utc)
    statement = _article_query(context).where(Item.first_seen_at <= snapshot)
    if args.q:
        q = args.q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        statement = statement.where(
            or_(
                func.lower(Item.title).like(f"%{q}%", escape="\\"),
                func.lower(func.coalesce(Item.summary, "")).like(f"%{q}%", escape="\\"),
            )
        )
    if args.feed_id:
        statement = statement.where(Item.feed_id == args.feed_id)
    if args.since:
        statement = statement.where(Item.first_seen_at >= args.since)
    if args.until:
        statement = statement.where(Item.first_seen_at <= args.until)
    if cursor:
        statement = statement.where(
            or_(
                Item.first_seen_at < cursor[0],
                and_(
                    Item.first_seen_at == cursor[0],
                    Item.id < cursor[1],
                ),
            )
        )
    rows = db.execute(
        statement.order_by(Item.first_seen_at.desc(), Item.id.desc()).limit(
            args.limit + 1
        )
    ).all()
    cuts, entries = [], []
    for index, row in enumerate(rows[: args.limit]):
        entry = _record(
            row, caps=_ARTICLE_CAPS, prefix=f"data.articles.{index}", cuts=cuts
        )
        entry["canonical_link"] = f"{base}/api/v1/items/{row.item_id}"
        entries.append(entry)
    more = len(rows) > args.limit
    next_cursor = None
    if more:
        last = rows[args.limit - 1]
        next_cursor = encode_cursor(
            context,
            args,
            last_seen=last.first_seen_at,
            last_id=last.item_id,
            snapshot_at=snapshot,
        )
    return _result(
        context,
        data={
            "articles": entries,
            "has_more": more,
            "date_basis": "first_seen_at",
            "order": "first_seen_at_desc,item_id_desc",
        },
        link=f"{base}/",
        kind="article_search",
        fields=cuts,
        freshness={"search_snapshot_at": snapshot.isoformat()},
        next_cursor=next_cursor,
    )


def _article_evidence(db, context, args, base):
    row = db.execute(
        _article_query(context)
        .add_columns(
            _project(Article.text, "article_text", args.text_limit),
            Article.content_purged_at,
            Article.extraction_method,
            Item.classification_required_version.label("source_version"),
        )
        .where(Item.id == args.item_id)
    ).one_or_none()
    if row is None:
        raise _missing()
    cuts = []
    data = _record(
        row,
        caps={**_ARTICLE_CAPS, "article_text": args.text_limit},
        prefix="data",
        cuts=cuts,
    )
    data["content_available"] = bool(data["article_text"])
    extraction, stale, omitted, omission_reason, generated_at = _article_extraction(
        db, row, data
    )
    data.update(
        {
            "structured_extraction": extraction,
            "extraction_stale": stale,
            "structured_extraction_omitted": omitted,
            "structured_extraction_omission_reason": omission_reason,
            "primary_source_fallback": extraction is None or stale,
        }
    )
    if omitted:
        cuts.append("data.structured_extraction")
    return _result(
        context,
        data=data,
        link=f"{base}/api/v1/items/{args.item_id}",
        kind="article",
        fields=cuts,
        freshness={
            "article_retrieved_at": row.article_retrieved_at,
            "item_updated_at": row.updated_at,
            "extraction_generated_at": generated_at,
            "extraction_stale": stale,
        },
    )


def _article_extraction(db, source_row, data):
    stored = ItemAIEnrichment.structured_extraction_json
    stored_bytes = func.octet_length(cast(stored, String))
    enrichment = db.execute(
        select(
            ItemAIEnrichment.status,
            ItemAIEnrichment.source_hash,
            ItemAIEnrichment.generated_at,
            stored_bytes.label("stored_bytes"),
            case(
                (stored_bytes <= MAX_STORED_EXTRACTION_BYTES, stored), else_=None
            ).label("structured_extraction_json"),
        ).where(ItemAIEnrichment.item_id == source_row.item_id)
    ).one_or_none()
    if enrichment is None:
        return None, False, False, None, None
    if (enrichment.stored_bytes or 0) > MAX_STORED_EXTRACTION_BYTES:
        return None, True, True, "stored_extraction_byte_limit", enrichment.generated_at
    # The ordinary API helper only inspects source identity and text presence.
    # Its bounded prefix preserves that presence without loading the full body.
    item = SimpleNamespace(
        id=source_row.item_id, classification_required_version=source_row.source_version
    )
    article = (
        SimpleNamespace(
            id=source_row.article_id,
            retrieved_at=source_row.article_retrieved_at,
            text=data["article_text"],
        )
        if source_row.article_id
        else None
    )
    extraction, stale = item_extraction_response(enrichment, item=item, article=article)
    invalid = extraction is None and enrichment.structured_extraction_json is not None
    return (
        extraction.model_dump(mode="json") if extraction else None,
        stale,
        invalid,
        "invalid_stored_extraction" if invalid else None,
        enrichment.generated_at,
    )


def _bounded_egress(db, context, kind, identifier):
    """Use the shared export decision after bounding its lineage materialization."""
    envelope_id = db.scalar(
        select(DataAccessEnvelope.id).where(
            DataAccessEnvelope.resource_type == kind,
            DataAccessEnvelope.resource_id == identifier,
        )
    )
    if envelope_id is not None:
        for model in (DataAccessEnvelopeSource, DataAccessEnvelopeLabel):
            over_limit = db.scalar(
                select(model.envelope_id)
                .where(
                    model.envelope_id == envelope_id,
                )
                .offset(MAX_LINEAGE_SOURCES)
                .limit(1)
            )
            if over_limit is not None:
                raise MCPReadError(
                    "resource_too_large",
                    "This record exceeds the MCP provenance limit. Open its canonical record.",
                )
        denied_current_feed = db.scalar(
            select(DataAccessEnvelopeSource.id)
            .join(
                Feed,
                Feed.id == DataAccessEnvelopeSource.source_feed_id,
            )
            .where(
                DataAccessEnvelopeSource.envelope_id == envelope_id,
                ~handling_label_access_predicate(
                    Feed.handling_label_id, context.data_access
                ),
            )
            .limit(1)
        )
        if denied_current_feed is not None:
            raise _missing()
    return require_data_access_for_egress(
        db, resource_type=kind, resource_id=identifier, context=context.data_access
    )


def _team_assessment(db, context, args, base):
    if (
        lock_team_for_current_access(
            db, team_id=args.team_id, user_id=context.principal.id, for_update=False
        )
        is None
    ):
        raise _missing()
    if (
        db.scalar(
            select(Item.id)
            .join(Feed, Feed.id == Item.feed_id)
            .where(
                Item.id == args.item_id,
                handling_label_access_predicate(
                    Feed.handling_label_id, context.data_access
                ),
            )
        )
        is None
    ):
        raise _missing()
    # Hold the row stable between the size projection and the shared domain reader.
    assessment_id = db.scalar(
        select(TeamItemAssessment.id)
        .where(
            TeamItemAssessment.team_id == args.team_id,
            TeamItemAssessment.item_id == args.item_id,
        )
        .with_for_update(read=True)
    )
    if assessment_id is not None:
        sizes = db.execute(
            select(
                *(
                    func.octet_length(cast(column, String))
                    for column in (
                        TeamItemAssessment.result_json,
                        TeamItemAssessment.source_encrypted,
                        TeamItemAssessment.result_source_encrypted,
                        TeamItemAssessment.authorization_encrypted,
                    )
                )
            ).where(TeamItemAssessment.id == assessment_id)
        ).one()
        if sum(value or 0 for value in sizes) > MAX_STORED_ASSESSMENT_BYTES:
            raise MCPReadError(
                "resource_too_large", "This assessment exceeds the MCP retrieval limit."
            )
    actor = AssessmentRequest(
        context.principal,
        context.authorization,
        context.data_access,
        context.credential_snapshot,
    )
    state = load_assessment_state(
        db,
        actor=actor,
        team_id=args.team_id,
        item_id=args.item_id,
        write=False,
        run_status_only=True,
    )
    row = state.assessment
    data = {"item_id": args.item_id, "team_id": args.team_id, "assessment": None}
    if row is not None:
        # The shared reader checks both current feed and the captured result label.
        if not state.result_visible:
            raise _missing()
        data["assessment"] = {
            "id": row.id,
            "version": row.version,
            "status": state.run.status
            if state.run
            else "ready"
            if row.result_json
            else "unavailable",
            "context_version": row.result_context_version,
            "source_version": row.result_source_version,
            "generated_at": row.generated_at,
            "stale": result_is_stale(state),
            "result": row.result_json,
        }
    return _result(
        context,
        data=data,
        link=f"{base}/api/v1/items/{args.item_id}/team-assessment?team_id={args.team_id}",
        kind="team_assessment",
        freshness={
            "generated_at": row.generated_at if row else None,
            "stale": result_is_stale(state),
            "article_retrieved_at": state.article_retrieved_at,
        },
    )


def _investigation(db, context, args, base):
    access = load_composed_investigation_read_access(
        db,
        investigation_id=args.investigation_id,
        user=context.principal,
        data_access=context.data_access,
        defer_description=True,
    )
    row = access.investigation
    if row is None:
        raise _missing()
    _bounded_egress(db, context, DATA_ACCESS_RESOURCE_INVESTIGATION, row.id)
    cuts = []
    evidence = db.execute(
        select(
            InvestigationEvidence.id,
            InvestigationEvidence.source_type,
            InvestigationEvidence.source_id,
            _project(InvestigationEvidence.title_snapshot, "title", 512),
            _project(InvestigationEvidence.description_snapshot, "description", 2000),
            _project(InvestigationEvidence.url_snapshot, "source_url", 2000),
            _project(InvestigationEvidence.note, "note", 2000),
            InvestigationEvidence.created_at,
        )
        .where(InvestigationEvidence.investigation_id == row.id)
        .order_by(
            InvestigationEvidence.created_at.desc(),
            InvestigationEvidence.id.desc(),
        )
        .limit(args.limit + 1)
    ).all()
    notes = db.execute(
        select(
            InvestigationNote.id,
            _project(InvestigationNote.body, "body", 4000),
            InvestigationNote.version,
            InvestigationNote.updated_at,
        )
        .where(
            InvestigationNote.investigation_id == row.id,
            InvestigationNote.deleted_at.is_(None),
        )
        .order_by(
            InvestigationNote.created_at.desc(),
            InvestigationNote.id.desc(),
        )
        .limit(args.limit + 1)
    ).all()
    if len(evidence) > args.limit:
        cuts.append("data.evidence")
    if len(notes) > args.limit:
        cuts.append("data.notes")
    description = (
        db.scalar(
            select(_project(Investigation.description, "description", 4000)).where(
                Investigation.id == row.id
            )
        )
        or ""
    )
    if len(description) > 4000:
        cuts.append("data.description")
    data = {
        "id": row.id,
        "team_id": row.team_id,
        "title": row.title,
        "description": description[:4000],
        "status": row.status,
        "severity": row.severity,
        "visibility": row.visibility,
        "version": row.version,
        "updated_at": row.updated_at,
        "evidence": [
            _record(
                entry,
                caps={
                    "title": 512,
                    "description": 2000,
                    "source_url": 2000,
                    "note": 2000,
                },
                prefix=f"data.evidence.{index}",
                cuts=cuts,
            )
            for index, entry in enumerate(evidence[: args.limit])
        ],
        "notes": [
            _record(entry, caps={"body": 4000}, prefix=f"data.notes.{index}", cuts=cuts)
            for index, entry in enumerate(notes[: args.limit])
        ],
    }
    return _result(
        context,
        data=data,
        link=f"{base}/investigations/{row.id}",
        kind="investigation",
        fields=cuts,
        freshness={"updated_at": row.updated_at, "version": row.version},
    )


def _report(db, context, args, base):
    report = get_accessible_report(
        db,
        report_id=args.report_id,
        data_access=context.data_access,
        read_lock=True,
        load_fields=(
            Report.id,
            Report.title,
            Report.status,
            Report.publication_status,
            Report.generated_at,
            Report.updated_at,
            Report.editorial_version,
        ),
    )
    if report is None:
        raise _missing()
    _bounded_egress(db, context, DATA_ACCESS_RESOURCE_REPORT, report.id)
    cuts = []
    summary = db.scalar(
        select(_project(Report.summary_text, "summary", 4000)).where(
            Report.id == report.id
        )
    )
    if summary and len(summary) > 4000:
        cuts.append("data.summary")
    sections = db.execute(
        select(
            ReportSection.section_key.label("key"),
            ReportSection.title,
            ReportSection.status,
            _project(ReportSection.body_markdown, "body_markdown", 8000),
            ReportSection.updated_at,
        )
        .where(ReportSection.report_id == report.id)
        .order_by(ReportSection.position, ReportSection.id)
        .limit(args.limit + 1)
    ).all()
    sources = db.execute(
        select(
            ReportSourceItem.citation_key,
            ReportSourceItem.item_id,
            ReportSourceItem.included,
            _project(ReportSourceItem.title_snapshot, "title", 1000),
            _project(ReportSourceItem.feed_name_snapshot, "feed_name", 255),
            _project(ReportSourceItem.url_snapshot, "source_url", 2000),
            ReportSourceItem.published_at_snapshot.label("published_at"),
        )
        .where(ReportSourceItem.report_id == report.id)
        .order_by(ReportSourceItem.rank, ReportSourceItem.id)
        .limit(args.limit + 1)
    ).all()
    if len(sections) > args.limit:
        cuts.append("data.sections")
    if len(sources) > args.limit:
        cuts.append("data.sources")
    data = {
        "id": report.id,
        "title": report.title,
        "status": report.status,
        "publication_status": report.publication_status,
        "editorial_version": report.editorial_version,
        "summary": summary[:4000] if summary else summary,
        "sections": [
            _record(
                entry,
                caps={"body_markdown": 8000},
                prefix=f"data.sections.{index}",
                cuts=cuts,
            )
            for index, entry in enumerate(sections[: args.limit])
        ],
        "sources": [
            _record(
                entry,
                caps={"title": 1000, "feed_name": 255, "source_url": 2000},
                prefix=f"data.sources.{index}",
                cuts=cuts,
            )
            for index, entry in enumerate(sources[: args.limit])
        ],
    }
    return _result(
        context,
        data=data,
        link=f"{base}/reporting/{report.id}",
        kind="report",
        fields=cuts,
        freshness={
            "generated_at": report.generated_at,
            "updated_at": report.updated_at,
            "editorial_version": report.editorial_version,
        },
    )
