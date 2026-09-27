from datetime import datetime, timezone
from uuid import uuid4
from app.services.ai_extraction import build_verified_extraction
from app.services.team_evidence_selection import select_assessment_passages
from tests.unit.test_ai_extraction import ARTICLE, extraction_payload, source_messages


def test_hunts_can_receive_exact_late_article_passages():
    article_id, retrieved = uuid4(), datetime.now(timezone.utc)
    extraction = build_verified_extraction(extraction_payload(), messages=source_messages(), article_id=article_id,
        article_retrieved_at=retrieved, source_version=1, source_hash="a" * 64, article_text_length=len(ARTICLE))
    for entity in extraction["entities"]:
        for evidence in entity["evidence"]:
            evidence["start"] += 20000
            evidence["end"] += 20000
    text, metadata = select_assessment_passages(extraction, source_version=1, article_id=str(article_id),
        retrieved_at=retrieved.isoformat(), context={"priorities": ["malware"]}, prefix="Introduction. " * 2000)
    assert len(text) <= 16000
    assert metadata["selection"] == "verified_section_passages"
    assert any(passage["start"] > 20000 for passage in metadata["selected_passages"])
    assert extraction["entities"][0]["evidence"][0]["quote"] in text
    assert extraction["entities"][0]["description"] not in text


def test_stale_extraction_falls_back_to_current_primary_evidence():
    text, metadata = select_assessment_passages({"source_version": 0}, source_version=1, article_id=str(uuid4()),
        retrieved_at="now", context={}, prefix="Current text")
    assert text == "Current text"
    assert metadata["selection"] == "article_prefix"
