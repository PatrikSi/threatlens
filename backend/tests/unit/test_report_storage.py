import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.schemas.reports import (
    ReportCreateRequest,
    ReportSectionConfig,
    ReportSectionSetError,
)
from app.services.report_storage import create_report_from_plan


def test_report_storage_defensively_rejects_disabled_section_set(db_session):
    now = datetime.now(timezone.utc)
    payload = ReportCreateRequest.model_construct(
        template_id=None,
        title=None,
        period_start=now - timedelta(days=1),
        period_end=now,
        filters=None,
        excluded_item_ids=[],
        prompt=None,
        sections=[
            ReportSectionConfig(
                key="summary",
                title="Summary",
                enabled=False,
            )
        ],
        deliver_when_ready=False,
        delivery_mode="summary",
    )

    with pytest.raises(ReportSectionSetError, match="section must be enabled"):
        create_report_from_plan(
            db_session,
            user_id=uuid.uuid4(),
            payload=payload,
            plan=SimpleNamespace(),
            template=None,
            active=SimpleNamespace(),
        )
