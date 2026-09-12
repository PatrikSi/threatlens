import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_authorization_context, require_permissions
from app.core.api_errors import ApiHTTPException
from app.core.token_scopes import SCOPE_READ_VIEWS, SCOPE_WRITE_VIEWS
from app.db.session import get_db
from app.models.saved_view import SavedView
from app.models.user import User
from app.schemas.view import SavedViewCreate, SavedViewResponse, SavedViewUpdate
from app.services.audit import record_audit
from app.services.authorization import AuthorizationContext
from app.services.team_access import (
    assert_current_team_access,
    require_team_access,
    team_access_predicate,
)

router = APIRouter(prefix="/views", tags=["views"])
MAX_SAVED_VIEW_WINDOWS = 12


def _serialize_saved_view(view: SavedView, request: Request) -> SavedViewResponse:
    context = get_authorization_context(request)
    writable = bool(
        context
        and context.has(SCOPE_WRITE_VIEWS)
        and (view.team_id is None or context.has("write:teams"))
    )
    return SavedViewResponse.model_validate(view).model_copy(
        update={"can_edit": writable, "can_delete": writable}
    )


def _ensure_saved_view_window_limit(payload: SavedViewCreate | SavedViewUpdate) -> None:
    query = payload.query_json
    if query is not None and len(query.windows) > MAX_SAVED_VIEW_WINDOWS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Saved views can contain at most {MAX_SAVED_VIEW_WINDOWS} panels",
        )


@router.get("", response_model=list[SavedViewResponse])
def list_views(
    request: Request,
    team_id: uuid.UUID | None = Query(default=None),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_READ_VIEWS)),
):
    views = db.scalars(
        select(SavedView)
        .where(
            or_(
                SavedView.user_id == user.id,
                team_access_predicate(SavedView.team_id, user.id),
            )
        )
        .where(SavedView.team_id == team_id if team_id is not None else True)
        .order_by(SavedView.created_at.desc())
    ).all()
    return [_serialize_saved_view(view, request) for view in views]


@router.post("", response_model=SavedViewResponse, status_code=status.HTTP_201_CREATED)
def create_view(
    payload: SavedViewCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_WRITE_VIEWS)),
):
    _ensure_saved_view_window_limit(payload)
    if payload.team_id is not None:
        require_team_access(
            db,
            team_id=payload.team_id,
            user=user,
            authorization=_shared_view_authorization(request),
        )
    view = SavedView(
        user_id=user.id if payload.team_id is None else None,
        team_id=payload.team_id,
        name=payload.name,
        query_json=_view_query(payload, shared=payload.team_id is not None),
    )
    db.add(view)
    db.flush()
    record_audit(
        db,
        actor_user_id=user.id,
        action="views.create",
        resource_type="saved_view",
        resource_id=str(view.id),
        metadata={"name": view.name},
    )
    db.commit()
    db.refresh(view)
    return _serialize_saved_view(view, request)


@router.patch("/{view_id}", response_model=SavedViewResponse)
def update_view(
    view_id: uuid.UUID,
    payload: SavedViewUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_WRITE_VIEWS)),
):
    _ensure_saved_view_window_limit(payload)
    view = _view_for_write(db, view_id=view_id, user=user, request=request)
    _require_view_revision(view, payload.expected_revision)

    if payload.name is not None:
        view.name = payload.name
    if payload.query_json is not None:
        view.query_json = _view_query(payload, shared=view.team_id is not None)
    view.revision += 1

    record_audit(
        db,
        actor_user_id=user.id,
        action="views.update",
        resource_type="saved_view",
        resource_id=str(view.id),
        metadata={"name": view.name},
    )
    db.commit()
    db.refresh(view)
    return _serialize_saved_view(view, request)


@router.delete("/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_view(
    view_id: uuid.UUID,
    request: Request,
    expected_revision: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    user: User = Depends(require_permissions(SCOPE_WRITE_VIEWS)),
):
    view = _view_for_write(db, view_id=view_id, user=user, request=request)
    _require_view_revision(view, expected_revision)

    db.delete(view)
    record_audit(
        db,
        actor_user_id=user.id,
        action="views.delete",
        resource_type="saved_view",
        resource_id=str(view_id),
    )
    db.commit()


def _shared_view_authorization(request: Request) -> AuthorizationContext:
    context = get_authorization_context(request)
    if context is None or not context.has("write:teams"):
        raise ApiHTTPException(
            status_code=403,
            error_code="team_write_permission_required",
            detail="Changing shared views requires write:teams as well as write:views.",
        )
    return context


def _view_for_write(
    db: Session, *, view_id: uuid.UUID, user: User, request: Request
) -> SavedView:
    query = select(SavedView).where(
        SavedView.id == view_id,
        or_(
            and_(SavedView.team_id.is_(None), SavedView.user_id == user.id),
            team_access_predicate(SavedView.team_id, user.id),
        ),
    )
    initial = db.scalar(query)
    if initial is None:
        raise HTTPException(status_code=404, detail="View not found")
    if initial.team_id is not None:
        require_team_access(
            db,
            team_id=initial.team_id,
            user=user,
            authorization=_shared_view_authorization(request),
        )
    view = db.scalar(query.with_for_update().execution_options(populate_existing=True))
    if view is None:
        raise HTTPException(status_code=404, detail="View not found")
    if view.team_id is not None:
        assert_current_team_access(db, team_id=view.team_id, user_id=user.id)
    return view


def _require_view_revision(view: SavedView, expected: int | None) -> None:
    if view.team_id is not None and expected is None:
        raise ApiHTTPException(
            status_code=428,
            error_code="view_revision_required",
            detail="Shared view changes require the revision that was loaded. Refresh the view and try again.",
        )
    if expected is not None and expected != view.revision:
        raise ApiHTTPException(
            status_code=409,
            error_code="view_revision_conflict",
            detail="This view changed while you were editing. Reload it before saving.",
            error_context={"current_revision": view.revision},
        )


def _view_query(payload: SavedViewCreate | SavedViewUpdate, *, shared: bool) -> dict:
    assert payload.query_json is not None
    query = payload.query_json.model_dump(mode="python")
    if shared:
        # Personal scratch notes, rule selections and brief references are not
        # workspace defaults and must not be published by copying a layout.
        query["alert_filters"]["selected_alert_ids"] = []
        for window in query["windows"]:
            window["scratch_note"] = ""
            window["selected_daily_brief_id"] = None
            if window.get("alert_filters"):
                window["alert_filters"]["selected_alert_ids"] = []
    return query
