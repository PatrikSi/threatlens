"""Generated report fixture only; editorial commands use the real application."""

from datetime import datetime, timedelta, timezone
from typing import Callable
import uuid

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.article import Article
from app.models.feed import Feed
from app.models.integration import IntegrationEvent
from app.models.item import Item
from app.models.report import Report
from app.models.report_section import ReportSection
from app.models.report_source_item import ReportSourceItem
from app.models.user import User
from app.services.data_access_runtime import ensure_report_data_access_envelope


class EditorialSeed(BaseModel):
    ownerId: uuid.UUID


def install_editorial_controls(harness: FastAPI, require_control: Callable) -> None:
    @harness.post(
        "/__browser__/editorial-report", dependencies=[Depends(require_control)]
    )
    def create_report(payload: EditorialSeed):
        with SessionLocal.begin() as db:
            if db.get(User, payload.ownerId) is None:
                raise HTTPException(404, "Fixture owner not found")
            feed = db.scalar(select(Feed).limit(1))
            if feed is None:
                raise HTTPException(409, "Fixture feed not initialized")
            identity = uuid.uuid4()
            now = datetime.now(timezone.utc)
            item = Item(
                id=identity,
                feed_id=feed.id,
                source_guid=str(identity),
                url=f"https://source.example.com/{identity}",
                canonical_url=f"https://source.example.com/{identity}",
                title=f"Editorial source {identity}",
                summary="Retained source evidence.",
                dedupe_key=str(identity),
                content_hash="b" * 64,
                status="content_fetched",
            )
            db.add(item)
            db.flush()
            db.add(
                Article(
                    item_id=item.id,
                    final_url=item.url,
                    http_status=200,
                    text="The source reports twelve affected systems.",
                )
            )
            report = Report(
                owner_user_id=payload.ownerId,
                title=f"Editorial report {identity}",
                status="ready",
                generation_stage="ready",
                generated_at=now,
                period_start=now - timedelta(days=7),
                period_end=now,
                summary_text="Twelve affected systems [S1].",
                source_count=1,
                included_source_count=1,
                citation_count=1,
                delivery_requested=True,
                coverage_json={"evidence_contract_version": 1},
            )
            db.add(report)
            db.flush()
            db.add_all(
                [
                    ReportSection(
                        report_id=report.id,
                        section_key="executive_summary",
                        title="Executive Summary",
                        position=1,
                        status="ready",
                        body_markdown="The source reports twelve affected systems [S1].",
                        key_points_json=["Twelve affected systems"],
                        citations_json=["S1"],
                    ),
                    ReportSourceItem(
                        report_id=report.id,
                        item_id=item.id,
                        citation_key="S1",
                        included=True,
                        rank=1,
                        title_snapshot=item.title,
                        feed_name_snapshot=feed.name,
                        url_snapshot=item.url,
                        first_seen_at_snapshot=item.first_seen_at,
                        evidence_text="The source reports twelve affected systems.",
                        tags_snapshot_json=[],
                        iocs_snapshot_json=[],
                        estimated_tokens=12,
                    ),
                ]
            )
            db.flush()
            ensure_report_data_access_envelope(db, report_id=report.id)
            return {"id": str(report.id), "title": report.title, "itemId": str(item.id)}

    @harness.post(
        "/__browser__/editorial-deliveries/{report_id}",
        dependencies=[Depends(require_control)],
    )
    def delivery_count(report_id: uuid.UUID):
        with SessionLocal() as db:
            count = db.scalar(
                select(func.count())
                .select_from(IntegrationEvent)
                .where(
                    IntegrationEvent.event_type == "report_ready",
                    IntegrationEvent.source_id == str(report_id),
                )
            )
            return {"count": count}
