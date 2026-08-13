import uuid

from app.schemas.reports import (
    ReportPromptConfig,
    ReportSectionConfig,
    ReportTemplateCreate,
)
from app.services.report_templates import (
    clone_report_template,
    create_report_template,
    report_template_response,
)


def test_template_round_trip_and_clone_preserve_prompt_topics(db_session, seed_users):
    payload = ReportTemplateCreate(
        name="Identity watch",
        prompt=ReportPromptConfig(
            objective="Prioritize identity threats.",
            focus_topics=["identity", "edge"],
            excluded_topics=["consumer fraud"],
        ),
        sections=[ReportSectionConfig(key="summary", title="Summary")],
    )
    template = create_report_template(
        db_session,
        user_id=seed_users["analyst"].id,
        payload=payload,
    )
    clone = clone_report_template(
        db_session,
        template=template,
        user_id=seed_users["analyst"].id,
    )

    assert report_template_response(template).prompt.focus_topics == ["identity", "edge"]
    assert report_template_response(template).prompt.excluded_topics == ["consumer fraud"]
    assert clone.focus_topics_json == ["identity", "edge"]
    assert clone.excluded_topics_json == ["consumer fraud"]
    assert clone.id != uuid.UUID(int=0)
