"""Team-managed distribution registrations and narrowly scoped receiver controls."""

from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import (
    get_authorization_context,
    get_data_access_context,
    require_permissions,
)
from app.api.database_operations import interactive_request as _operation
from app.services.team_assessment_access import assessment_request
from app.db.session import get_db
from app.models.publication_consumer import PublicationConsumer
from app.models.user import User
from app.schemas.publication_consumers import (
    ConsumerAcknowledgement,
    ConsumerCreate,
    ConsumerCreated,
    ConsumerReset,
    ConsumerResponse,
    SubscriptionCommand,
)
from app.services import publication_consumers as service
from app.services.audit import record_audit
from app.services.data_access_policy import DataAccessContext
from app.services.indicator_assessments import fence_indicator_request


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


router = APIRouter(
    tags=["reviewed intelligence distribution"], dependencies=[Depends(_no_store)]
)


def _manager(
    request: Request,
    user: User = Depends(
        require_permissions("read:items", "read:teams", "write:teams")
    ),
    access: DataAccessContext = Depends(get_data_access_context),
):
    return assessment_request(
        request,
        user=user,
        authorization=get_authorization_context(request),
        access=access,
    )


@router.post(
    "/teams/{team_id}/publication-consumers",
    response_model=ConsumerCreated,
    status_code=201,
)
def register_publication_consumer(
    team_id: uuid.UUID,
    payload: ConsumerCreate,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    def run():
        row, token = service.create_consumer(
            db,
            actor,
            team_id,
            name=payload.name,
            expires_days=payload.expires_days,
            idempotency_key=payload.idempotency_key,
        )
        return ConsumerCreated(
            **ConsumerResponse.model_validate(row).model_dump(), token=token
        )

    return _operation(db, run)


@router.get(
    "/teams/{team_id}/publication-consumers", response_model=list[ConsumerResponse]
)
def list_publication_consumers(
    team_id: uuid.UUID, db: Session = Depends(get_db), actor=Depends(_manager)
):
    def run():
        fence_indicator_request(db, actor, team_id=team_id, write=True, manage=True)
        return [
            ConsumerResponse.model_validate(row)
            for row in db.scalars(
                select(PublicationConsumer)
                .where(PublicationConsumer.team_id == team_id)
                .order_by(PublicationConsumer.created_at, PublicationConsumer.id)
                .limit(20)
            )
        ]

    return _operation(db, run)


@router.post(
    "/teams/{team_id}/publication-consumers/{consumer_id}/subscriptions",
    status_code=204,
)
def subscribe_publication_consumer(
    team_id: uuid.UUID,
    consumer_id: uuid.UUID,
    payload: SubscriptionCommand,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    _operation(
        db,
        lambda: service.subscribe(
            db, actor, team_id, consumer_id, payload.publication_id
        ),
    )


@router.post(
    "/teams/{team_id}/publication-consumers/{consumer_id}/rotate",
    response_model=ConsumerCreated,
)
def rotate_publication_consumer(
    team_id: uuid.UUID,
    consumer_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    def run():
        row = service.manager_consumer(db, actor, team_id, consumer_id)
        token = "tlpc_" + secrets.token_urlsafe(40)
        row.token_hash = hashlib.sha256(token.encode()).hexdigest()
        row.expires_at = datetime.now(timezone.utc) + timedelta(days=90)
        row.revoked_at = None
        record_audit(
            db,
            actor_user_id=actor.user.id,
            action="intelligence.consumer.rotate",
            resource_type="publication_consumer",
            resource_id=str(row.id),
        )
        return ConsumerCreated(
            **ConsumerResponse.model_validate(row).model_dump(), token=token
        )

    return _operation(db, run)


@router.delete("/teams/{team_id}/publication-consumers/{consumer_id}", status_code=204)
def revoke_publication_consumer(
    team_id: uuid.UUID,
    consumer_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    def run():
        row = service.manager_consumer(db, actor, team_id, consumer_id)
        row.revoked_at = row.revoked_at or datetime.now(timezone.utc)
        record_audit(
            db,
            actor_user_id=actor.user.id,
            action="intelligence.consumer.revoke",
            resource_type="publication_consumer",
            resource_id=str(row.id),
        )

    _operation(db, run)


def _consumer_authorization(
    request: Request,
    authorization: str | None = Header(default=None, max_length=512),
) -> str:
    # A proxy and application must resolve the same bearer identity. Never
    # accept query credentials, even beside a valid header: URLs reach logs.
    if len(request.headers.getlist("authorization")) != 1 or any(
        key.lower()
        in {"access_token", "token", "api_token", "api_key", "authorization"}
        for key in request.query_params
    ):
        raise service.error(
            "consumer_bearer_required",
            "Use exactly one Authorization bearer header. Query credentials are not accepted.",
            401,
        )
    return authorization or ""


@router.get("/publication-distribution/status")
def publication_consumer_status(
    authorization: str = Depends(_consumer_authorization),
    db: Session = Depends(get_db),
):
    def run():
        row = service.authenticate_consumer(db, authorization)
        return {
            "consumer_id": str(row.id),
            "generation": row.generation,
            "replay_floor": row.replay_floor,
            "latest_sequence": row.sequence,
        }

    return _operation(db, run)


@router.get("/publication-distribution/changes")
def publication_consumer_changes(
    after: int = Query(0, ge=0),
    generation: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    authorization: str = Depends(_consumer_authorization),
    db: Session = Depends(get_db),
):
    def run():
        row = service.authenticate_consumer(db, authorization)
        service.prune_acknowledged_changes(db, row)
        return service.consumer_feed(
            db, row, after=after, generation=generation, limit=limit
        )

    return _operation(db, run)


@router.post("/publication-distribution/acknowledgements")
def acknowledge_publication_changes(
    payload: ConsumerAcknowledgement,
    authorization: str = Depends(_consumer_authorization),
    db: Session = Depends(get_db),
):
    return _operation(
        db,
        lambda: {
            "acknowledged": service.acknowledge(
                db,
                service.authenticate_consumer(db, authorization),
                generation=payload.generation,
                change_ids=payload.change_ids,
            )
        },
    )


@router.post("/publication-distribution/reset")
def reset_publication_consumer(
    payload: ConsumerReset,
    authorization: str = Depends(_consumer_authorization),
    db: Session = Depends(get_db),
):
    return _operation(
        db,
        lambda: service.reset_consumer(
            db,
            service.authenticate_consumer(db, authorization),
            expected_generation=payload.expected_generation,
            discarded=payload.discarded_previous_publications,
        ),
    )


@router.post(
    "/teams/{team_id}/publication-consumers/{consumer_id}/retire", status_code=204
)
def retire_publication_consumer(
    team_id: uuid.UUID,
    consumer_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    def run():
        row = service.manager_consumer(db, actor, team_id, consumer_id)
        service.retire_consumer(db, row, actor_user_id=actor.user.id)

    _operation(db, run)


@router.post(
    "/teams/{team_id}/publication-consumers/{consumer_id}/archive", status_code=204
)
def archive_publication_consumer(
    team_id: uuid.UUID,
    consumer_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor=Depends(_manager),
):
    def run():
        row = service.manager_consumer(db, actor, team_id, consumer_id)
        service.archive_consumer(db, row, actor_user_id=actor.user.id)

    _operation(db, run)
