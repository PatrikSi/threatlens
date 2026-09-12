from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.schemas.alert import (
    AlertInterestCreate,
    AlertInterestPreviewRequest,
    AlertInterestUpdate,
    AlertOccurrenceSnoozeUpdate,
)


@pytest.mark.parametrize("unsafe", ["a\x00b", "a\ud800b", "a\udfffb"])
@pytest.mark.parametrize("field", ["name", "category", "keywords", "suppression_reason"])
@pytest.mark.parametrize("schema", [AlertInterestCreate, AlertInterestUpdate])
def test_watchlist_text_rejects_database_unsafe_characters(schema, field, unsafe):
    payload = {
        "name": "Watchlist",
        "category": "appliance",
        "keywords": ["fortinet"],
        "suppression_until": datetime.now(timezone.utc) + timedelta(hours=1),
        "suppression_reason": "Maintenance",
    }
    payload[field] = [unsafe] if field == "keywords" else unsafe
    with pytest.raises(ValidationError, match="cannot be stored|valid string"):
        schema.model_validate(payload)


def test_preview_and_snooze_validate_text_before_query_or_activity_storage():
    with pytest.raises(ValidationError, match="cannot be stored|valid string"):
        AlertInterestPreviewRequest(category="appliance", keywords=["bad\x00keyword"])
    with pytest.raises(ValidationError, match="cannot be stored|valid string"):
        AlertOccurrenceSnoozeUpdate(
            expected_version=1,
            snoozed_until=datetime.now(timezone.utc) + timedelta(hours=1),
            reason="bad\ud800reason",
        )


def test_valid_unicode_and_intentional_spacing_remain_unchanged():
    name = "  Équipe 日本 🔒  "
    rule = AlertInterestCreate(name=name, category="安全", keywords=[" zero day "])
    assert rule.name == name
    assert rule.keywords == [" zero day "]
