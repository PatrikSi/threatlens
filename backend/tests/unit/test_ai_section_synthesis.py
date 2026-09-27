import pytest
from app.services.ai_section_synthesis import validate_section_synthesis, synthesis_messages
from app.services.report_grounding import ReportGroundingError


def test_synthesis_requires_real_section_citations_on_every_claim():
    sections = [{"index": 0, "start": 0, "end": 8000, "extraction": {"entities": [], "relationships": []},
        "completion": {"payload": {"summary_text": "The source describes scheduled task persistence."}}}]
    messages = synthesis_messages(sections)
    validate_section_synthesis({"summary_text": "The source describes scheduled task persistence. [S1]"}, messages)
    with pytest.raises(ReportGroundingError):
        validate_section_synthesis({"summary_text": "The source describes persistence. [S99]"}, messages)
    with pytest.raises(ReportGroundingError):
        validate_section_synthesis({"summary_text": "An unsupported paragraph.\n\nAnother claim. [S1]"}, messages)
