import uuid

from sqlalchemy import event

from app.core.config import get_settings
from app.models.team_item_assessment import TeamAssessmentRevision, TeamItemAssessment
from app.services.secret_storage import decrypt_json, encrypt_json
from app.services.encrypted_data_inventory import scan_encrypted_data_inventory
from tests.integration.test_team_assessments_api import assessment_setup, _queue  # noqa: F401


def test_queued_authority_inventory_detects_lost_keys_and_accepts_rotation_fallback(
    client, db_session, assessment_setup, auth_headers, monkeypatch,
):
    old_key = "team-assessment-inventory-old-key-" + "x" * 32
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", old_key)
    get_settings.cache_clear()
    _queue(client, assessment_setup, auth_headers["analyst"])
    initial = scan_encrypted_data_inventory(db_session)
    assert initial.team_assessment_authorizations.encrypted_fields == 2
    assert initial.team_assessment_authorizations.unreadable_fields == 0

    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "team-assessment-new-key-" + "y" * 32)
    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", old_key)
    get_settings.cache_clear()
    assert scan_encrypted_data_inventory(db_session).team_assessment_authorizations.unreadable_fields == 0

    monkeypatch.setenv("APP_DATA_ENCRYPTION_PREVIOUS_KEYS", "")
    get_settings.cache_clear()
    lost_key = scan_encrypted_data_inventory(db_session)
    assert lost_key.team_assessment_authorizations.unreadable_fields == 2
    assert lost_key.status == "critical"
    assert lost_key.ok is False
    assert lost_key.summary.unreadable_fields >= 2


def test_inventory_does_not_materialize_private_assessment_results(
    client, db_session, assessment_setup, auth_headers,
):
    queued = _queue(client, assessment_setup, auth_headers["analyst"])
    row = db_session.get(TeamItemAssessment, uuid.UUID(queued["assessment"]["id"]))
    row.result_json = {"private_text": "x" * 2_000_000}
    db_session.commit()
    statements = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        if "team_item_assessments" in statement:
            statements.append(statement)

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", capture)
    try:
        inventory = scan_encrypted_data_inventory(db_session)
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert inventory.team_assessment_authorizations.total_records == 1
    assert inventory.team_assessment_authorizations.unreadable_fields == 1
    assert statements and all("team_item_assessments.result_json," not in statement for statement in statements)
    assert any("result_json IS NOT NULL AS has_result" in statement for statement in statements)


def test_inventory_includes_immutable_prior_result_authority(
    client, db_session, assessment_setup, auth_headers, monkeypatch,
):
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "prior-result-key-" + "a" * 40)
    get_settings.cache_clear()
    queued = _queue(client, assessment_setup, auth_headers["analyst"])
    row = db_session.get(TeamItemAssessment, uuid.UUID(queued["assessment"]["id"]))
    authorization = decrypt_json(row.authorization_encrypted)
    source = decrypt_json(row.source_encrypted)
    db_session.add(TeamAssessmentRevision(
        assessment_id=row.id, version=1, result_json={},
        result_context_version=0, result_source_version=row.source_version,
        result_source_encrypted=row.source_encrypted, change_kind="regenerate",
    ))
    db_session.flush()
    monkeypatch.setenv("APP_DATA_ENCRYPTION_KEY", "new-result-key-" + "b" * 40)
    get_settings.cache_clear()
    row.authorization_encrypted = encrypt_json(authorization)
    row.source_encrypted = encrypt_json(source)
    db_session.commit()
    inventory = scan_encrypted_data_inventory(db_session)
    assert inventory.team_assessment_authorizations.total_records == 2
    assert inventory.team_assessment_authorizations.encrypted_fields == 3
    assert inventory.team_assessment_authorizations.unreadable_fields == 1
    assert inventory.status == "critical"
