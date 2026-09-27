"""MCP adapters over the same current-access projections used by the team UI."""

from typing import Any

from sqlalchemy.orm import Session

from app.schemas.mcp_reads import (
    IndicatorArguments,
    HuntQueueArguments,
    PublicationArguments,
    TechniqueArguments,
    MCPReadResult,
)
from app.services.indicator_assessments import list_indicators
from app.services.indicator_publications import list_publications
from app.services.mcp_read_contracts import MCPReadContext
from app.services.team_assessment_access import AssessmentRequest
from app.services.team_hunt_worklist import list_team_hunts


def _actor(context: MCPReadContext) -> AssessmentRequest:
    return AssessmentRequest(
        context.principal,
        context.authorization,
        context.data_access,
        context.credential_snapshot,
    )


def _result(
    context: MCPReadContext, data: dict[str, Any], link: str, kind: str
) -> MCPReadResult:
    from app.services.mcp_read_service import _result as build_result

    return build_result(
        context,
        data=data,
        link=link,
        kind=kind,
        next_cursor=data.get("next_cursor"),
    )


def read_indicator_assessments(
    db: Session, context: MCPReadContext, args: IndicatorArguments, base: str
) -> MCPReadResult:
    page = list_indicators(
        db,
        actor=_actor(context),
        item_id=args.item_id,
        team_id=args.team_id,
        page=args.page,
        page_size=args.limit,
    )
    data = page.model_dump(mode="json")
    data["has_more"] = args.page * args.limit < page.total
    data["next_page"] = args.page + 1 if data["has_more"] else None
    link = f"{base}/api/v1/items/{args.item_id}/indicators"
    if args.team_id is not None:
        link += f"?team_id={args.team_id}"
    return _result(context, data, link, "indicator_assessments")


def read_hunt_queue(
    db: Session, context: MCPReadContext, args: HuntQueueArguments, base: str
) -> MCPReadResult:
    page = list_team_hunts(
        db,
        actor=_actor(context),
        team_id=args.team_id,
        status=args.status,
        ownership=args.ownership,
        order=args.order,
        priority=args.priority,
        overdue=args.overdue,
        cursor=args.cursor,
        limit=args.limit,
    )
    return _result(
        context,
        page.model_dump(mode="json"),
        f"{base}/teams?team={args.team_id}&panel=hunts",
        "hunt_queue",
    )


def read_publications(
    db: Session, context: MCPReadContext, args: PublicationArguments, base: str
) -> MCPReadResult:
    page = list_publications(
        db,
        actor=_actor(context),
        team_id=args.team_id,
        cursor=args.cursor,
        limit=args.limit,
    )
    return _result(
        context,
        page.model_dump(mode="json"),
        f"{base}/api/v1/teams/{args.team_id}/indicator-publications",
        "reviewed_publications",
    )


def read_technique(
    db: Session, context: MCPReadContext, args: TechniqueArguments, base: str
) -> MCPReadResult:
    from app.services.attack_catalog import (
        load_attack_catalog,
        detection_strategies_for_techniques,
    )
    from app.services.mcp_read_contracts import MCPReadError

    catalog = load_attack_catalog()
    technique = catalog.techniques.get(args.technique_id)
    if technique is None:
        raise MCPReadError(
            "not_found", "Technique is not present in the installed ATT&CK catalog."
        )
    return _result(
        context,
        {
            "technique": {
                "id": technique.id,
                "name": technique.name,
                "url": technique.url,
            },
            "detection_strategies": detection_strategies_for_techniques([technique.id]),
        },
        technique.url,
        "attack_technique",
    )
