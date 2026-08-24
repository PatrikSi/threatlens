from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.ai import AISettingsUpdate
from app.schemas.reports import (
    ReportCreateRequest,
    ReportPreviewRequest,
    ReportScheduleCreate,
    ReportSectionConfig,
    ReportTemplateCreate,
)


def test_ai_settings_reject_context_budget_with_no_usable_input():
    with pytest.raises(ValidationError, match="leave at least 512 tokens"):
        AISettingsUpdate(
            report_context_window_tokens=2048,
            report_reserved_output_tokens=1600,
            report_context_safety_percent=15,
        )


def test_report_period_must_be_forward_moving():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="period_start must be earlier"):
        ReportCreateRequest(
            period_start=now,
            period_end=now,
            sections=[ReportSectionConfig(key="summary", title="Summary")],
        )


def test_schedule_rejects_invalid_cadence_window_pair():
    with pytest.raises(ValidationError, match="weekly schedules cannot"):
        ReportScheduleCreate(
            template_id="11111111-1111-4111-8111-111111111101",
            name="Bad weekly schedule",
            cadence="weekly",
            window_type="previous_complete_month",
        )


def test_schedule_rejects_unknown_timezone():
    with pytest.raises(ValidationError, match="valid IANA"):
        ReportScheduleCreate(
            template_id="11111111-1111-4111-8111-111111111101",
            name="Bad timezone",
            timezone="Mars/Olympus",
        )


@pytest.mark.parametrize("state_filter", [{"is_read": True}, {"is_starred": False}])
def test_shared_report_inputs_reject_private_user_state(state_filter):
    with pytest.raises(ValidationError, match="private read or starred state"):
        ReportPreviewRequest(filters=state_filter)


def test_report_create_rejects_duplicate_section_keys():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="section keys must be unique"):
        ReportCreateRequest(
            period_start=now,
            period_end=now + timedelta(days=1),
            sections=[
                ReportSectionConfig(key="summary", title="Summary"),
                ReportSectionConfig(key="summary", title="Another summary"),
            ],
        )


def test_report_create_rejects_all_disabled_sections():
    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError, match="section must be enabled"):
        ReportCreateRequest(
            period_start=now,
            period_end=now + timedelta(days=1),
            sections=[
                ReportSectionConfig(
                    key="summary",
                    title="Summary",
                    enabled=False,
                )
            ],
        )


def test_report_template_rejects_all_disabled_sections():
    with pytest.raises(ValidationError, match="section must be enabled"):
        ReportTemplateCreate(
            name="Disabled template",
            sections=[
                ReportSectionConfig(
                    key="summary",
                    title="Summary",
                    enabled=False,
                )
            ],
        )


def test_report_preview_allows_empty_sections_for_source_estimation():
    preview = ReportPreviewRequest(sections=[])

    assert preview.sections == []


def test_report_preview_rejects_invalid_nonempty_section_set():
    with pytest.raises(ValidationError, match="section keys must be unique"):
        ReportPreviewRequest(
            sections=[
                ReportSectionConfig(key="summary", title="Summary"),
                ReportSectionConfig(key="summary", title="Duplicate"),
            ]
        )
