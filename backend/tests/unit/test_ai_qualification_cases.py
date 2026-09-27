import pytest

from app.services.ai_qualification_cases import qualification_messages, validate_qualification


@pytest.mark.parametrize("body", [
    "The source reports scheduled task persistence. [S1]",
    "| Behavior | Source |\n| --- | --- |\n| Scheduled tasks | [S1] |",
    "| 3 tasks | Source |\n| --- | --- |\n| Scheduled tasks | [S1] |",
    "| Behavior | Source |\n| --- | --- |\n| [Scheduled tasks](https://example.test/3) | [S1] |",
])
def test_report_qualification_requires_numeric_data_not_paragraph_headers_or_citation_ids(body):
    with pytest.raises(ValueError, match="numeric Markdown table"):
        validate_qualification("report_section", {"body_markdown": body, "citations": ["S1"]},
            qualification_messages("report_section"))


def test_numeric_report_probe_provides_a_source_value_and_requires_row_citation():
    messages = qualification_messages("report_section")
    assert "3 scheduled tasks" in messages[-1]["content"]
    body = "The sample contains scheduled tasks. [S1]\n\n| Tasks | Source |\n| --- | --- |\n| 3 | [S1] |"
    validate_qualification("report_section", {"body_markdown": body, "citations": ["S1"]}, messages)
    with pytest.raises(ValueError, match="valid source citation"):
        validate_qualification("report_section", {
            "body_markdown": body.replace("| 3 | [S1] |", "| 3 | none |"), "citations": ["S1"],
        }, messages)
