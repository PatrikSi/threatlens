from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.report_template import ReportTemplate
from app.schemas.exports import ArticleExportFilters
from app.schemas.reports import (
    ReportPromptConfig,
    ReportSectionConfig,
    ReportTemplateCreate,
    ReportTemplateResponse,
    ReportTemplateUpdate,
    validate_report_section_set,
)


class ReportTemplateError(ValueError):
    pass


def list_visible_report_templates(db: Session, *, user_id: uuid.UUID) -> list[ReportTemplate]:
    return list(
        db.scalars(
            select(ReportTemplate)
            .where(or_(ReportTemplate.visibility == "shared", ReportTemplate.owner_user_id == user_id))
            .order_by(ReportTemplate.builtin_key.asc().nullslast(), ReportTemplate.name.asc())
        ).all()
    )


def get_visible_report_template(db: Session, *, template_id: uuid.UUID, user_id: uuid.UUID) -> ReportTemplate | None:
    return db.scalar(
        select(ReportTemplate).where(
            ReportTemplate.id == template_id,
            or_(ReportTemplate.visibility == "shared", ReportTemplate.owner_user_id == user_id),
        )
    )


def create_report_template(
    db: Session,
    *,
    user_id: uuid.UUID,
    payload: ReportTemplateCreate,
) -> ReportTemplate:
    template = ReportTemplate(owner_user_id=user_id, builtin_key=None)
    _apply_template_payload(template, payload)
    db.add(template)
    db.flush()
    return template


def update_report_template(
    template: ReportTemplate,
    *,
    payload: ReportTemplateUpdate,
) -> None:
    if template.builtin_key:
        raise ReportTemplateError("Built-in report templates are immutable. Clone one to customize it.")
    _apply_template_payload(template, payload)


def delete_report_template(db: Session, *, template: ReportTemplate) -> None:
    if template.builtin_key:
        raise ReportTemplateError("Built-in report templates cannot be deleted.")
    db.delete(template)


def clone_report_template(
    db: Session,
    *,
    template: ReportTemplate,
    user_id: uuid.UUID,
) -> ReportTemplate:
    clone = ReportTemplate(
        owner_user_id=user_id,
        builtin_key=None,
        name=f"{template.name} copy"[:255],
        description=template.description,
        report_type=template.report_type,
        visibility="private",
        audience=template.audience,
        objective=template.objective,
        tone=template.tone,
        detail_level=template.detail_level,
        use_company_context=template.use_company_context,
        custom_instructions=template.custom_instructions,
        focus_topics_json=list(template.focus_topics_json or []),
        excluded_topics_json=list(template.excluded_topics_json or []),
        sections_json=list(template.sections_json or []),
        default_filters_json=dict(template.default_filters_json or {}),
    )
    db.add(clone)
    db.flush()
    return clone


def report_template_response(template: ReportTemplate) -> ReportTemplateResponse:
    return ReportTemplateResponse(
        id=template.id,
        owner_user_id=template.owner_user_id,
        builtin_key=template.builtin_key,
        name=template.name,
        description=template.description,
        report_type=template.report_type,
        visibility=template.visibility,
        prompt=ReportPromptConfig(
            audience=template.audience,
            objective=template.objective,
            tone=template.tone,
            detail_level=template.detail_level,
            use_company_context=template.use_company_context,
            custom_instructions=template.custom_instructions,
            focus_topics=list(template.focus_topics_json or []),
            excluded_topics=list(template.excluded_topics_json or []),
        ),
        sections=[ReportSectionConfig.model_validate(entry) for entry in template.sections_json or []],
        default_filters=ArticleExportFilters.model_validate(template.default_filters_json or {}),
        created_at=template.created_at,
        updated_at=template.updated_at,
    )


def _apply_template_payload(
    template: ReportTemplate,
    payload: ReportTemplateCreate | ReportTemplateUpdate,
) -> None:
    validate_report_section_set(payload.sections)
    template.name = payload.name
    template.description = payload.description
    template.report_type = payload.report_type
    template.visibility = payload.visibility
    template.audience = payload.prompt.audience
    template.objective = payload.prompt.objective
    template.tone = payload.prompt.tone
    template.detail_level = payload.prompt.detail_level
    template.use_company_context = payload.prompt.use_company_context
    template.custom_instructions = payload.prompt.custom_instructions
    template.focus_topics_json = list(payload.prompt.focus_topics)
    template.excluded_topics_json = list(payload.prompt.excluded_topics)
    template.sections_json = [section.model_dump(mode="json") for section in payload.sections]
    template.default_filters_json = payload.default_filters.model_dump(mode="json")
