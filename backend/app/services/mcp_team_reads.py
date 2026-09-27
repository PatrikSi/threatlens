"""MCP adapters over the same current-access projections used by the team UI."""

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


def _result(context, data, base, team_id, kind):
    from app.services.mcp_read_service import _result as build_result

    return build_result(
        context,
        data=data,
        link=f"{base}/teams/{team_id}" if team_id else f"{base}/",
        kind=kind,
        next_cursor=data.get("next_cursor"),
    )


def read_indicator_assessments(db, context, args, base):
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
    return _result(context, data, base, args.team_id, "indicator_assessments")


def read_hunt_queue(db, context, args, base):
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
        context, page.model_dump(mode="json"), base, args.team_id, "hunt_queue"
    )


def read_publications(db, context, args, base):
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
        base,
        args.team_id,
        "reviewed_publications",
    )


def read_technique(db, context, args, base):
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
        base,
        None,
        "attack_technique",
    )
