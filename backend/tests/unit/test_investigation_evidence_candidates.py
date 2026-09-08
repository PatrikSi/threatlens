from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.core.token_scopes import (
    SCOPE_READ_ALERTS,
    SCOPE_READ_ITEMS,
    SCOPE_READ_REPORTS,
    SCOPE_WRITE_INVESTIGATIONS,
)
from app.core.data_policy_route_manifest import (
    ROUTE_GOVERNANCE_MANIFEST,
    RouteGovernanceClass,
)
from app.models.ioc import IOC
from app.services import investigation_evidence_candidates as candidate_service
from app.services.authorization import AuthorizationContext
from app.services.investigation_evidence_candidates import (
    analyze_candidate_query,
    list_evidence_candidates,
    source_capabilities,
)
from app.services.investigations import InvestigationValidationError


@pytest.mark.parametrize(
    ("query", "kind", "normalized", "ioc_type"),
    (
        (None, "empty", None, None),
        ("   ", "empty", None, None),
        (
            "11111111-1111-4111-8111-111111111111",
            "uuid",
            "11111111-1111-4111-8111-111111111111",
            None,
        ),
        ("CVE-2026-12345", "ioc", "CVE-2026-12345", "cve"),
        ("192.0.2.10", "ioc", "192.0.2.10", "ipv4"),
        ("2001:0DB8:0:0:0:0:0:7", "ioc", "2001:db8::7", "ipv6"),
        ("[2001:db8::7]", "ioc", "2001:db8::7", "ipv6"),
        ("WWW.Example.COM", "ioc", "example.com", "domain"),
        ("A" * 32, "ioc", "a" * 32, "hash_md5"),
        ("B" * 40, "ioc", "b" * 40, "hash_sha1"),
        ("C" * 64, "ioc", "c" * 64, "hash_sha256"),
        ("credential theft", "text", "credential theft", None),
    ),
)
def test_candidate_query_analysis_is_deterministic(
    query: str | None,
    kind: str,
    normalized: str | None,
    ioc_type: str | None,
):
    analysis = analyze_candidate_query(query)

    assert analysis.kind == kind
    assert analysis.normalized_value == normalized
    assert analysis.detected_ioc_type == ioc_type


def test_candidate_query_analysis_normalizes_http_urls_without_fragments():
    analysis = analyze_candidate_query(
        "HTTPS://WWW.Example.COM:8443/path/?utm_source=mail&q=1#secret"
    )

    assert analysis.kind == "url"
    assert analysis.normalized_value == "https://www.example.com:8443/path?q=1"
    assert analysis.detected_ioc_type == "domain"
    assert analysis.ioc_search_value == "example.com"


def test_candidate_query_analysis_normalizes_ipv6_urls_and_host_type():
    analysis = analyze_candidate_query(
        "HTTPS://[2001:0DB8:0:0:0:0:0:7]:8443/path#fragment"
    )

    assert analysis.kind == "url"
    assert analysis.normalized_value == "https://[2001:db8::7]:8443/path"
    assert analysis.detected_ioc_type == "ipv6"
    assert analysis.ioc_search_value == "2001:db8::7"


def test_candidate_query_analysis_handles_malformed_url_ports_as_text():
    analysis = analyze_candidate_query("http://example.com:not-a-port")

    assert analysis.kind == "text"
    assert analysis.normalized_value == "http://example.com:not-a-port"


def test_candidate_query_analysis_does_not_echo_url_userinfo():
    with pytest.raises(
        InvestigationValidationError,
        match="cannot include credentials",
    ) as exc_info:
        analyze_candidate_query("https://operator:secret@example.com/path")

    assert "operator" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


def test_candidate_query_analysis_rejects_malformed_url_userinfo_without_echoing():
    with pytest.raises(
        InvestigationValidationError,
        match="cannot include credentials",
    ) as exc_info:
        analyze_candidate_query("https://operator:secret@[bad")

    assert "operator" not in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


@pytest.mark.parametrize("query", ("signal\nvalue", "signal\x00value"))
def test_candidate_query_analysis_rejects_control_characters(query: str):
    with pytest.raises(InvestigationValidationError):
        analyze_candidate_query(query)


@pytest.mark.parametrize("query", ("a", "ab", "%", "_"))
def test_candidate_query_analysis_rejects_short_fuzzy_text(query: str):
    with pytest.raises(
        InvestigationValidationError,
        match="at least 3 characters",
    ):
        analyze_candidate_query(query)


def test_source_capabilities_lock_only_sources_with_missing_permissions():
    authorization = AuthorizationContext(
        principal_type="user",
        principal_id=uuid.uuid4(),
        legacy_role="analyst",
        account_eligible=True,
        roles=(),
        groups=(),
        grants=frozenset(
            {
                SCOPE_WRITE_INVESTIGATIONS,
                SCOPE_READ_ITEMS,
                SCOPE_READ_ALERTS,
            }
        ),
        credential_grants=None,
        permissions=frozenset(),
        provenance={},
        policy_revision=7,
    )

    capabilities = {
        entry.source_type: entry for entry in source_capabilities(authorization)
    }

    assert capabilities["item"].available is True
    assert capabilities["ioc"].available is True
    assert capabilities["alert_occurrence"].available is True
    assert capabilities["report"].available is False
    assert capabilities["report"].required_permissions == ["read:reports"]
    assert capabilities["report"].unavailable_reason == "Requires read:reports."


def test_candidate_service_caps_each_source_window_and_reports_lower_bound(
    monkeypatch,
):
    observed_limits: list[int] = []

    def _loader(_db, **kwargs):
        observed_limits.append(kwargs["limit"])
        return [], candidate_service.MAX_SOURCE_TOTAL, len(observed_limits) == 4

    monkeypatch.setattr(
        candidate_service,
        "_SOURCE_LOADERS",
        {source_type: _loader for source_type in candidate_service.SOURCE_TYPES},
    )
    authorization = AuthorizationContext(
        principal_type="user",
        principal_id=uuid.uuid4(),
        legacy_role="analyst",
        account_eligible=True,
        roles=(),
        groups=(),
        grants=frozenset(
            {
                SCOPE_WRITE_INVESTIGATIONS,
                SCOPE_READ_ITEMS,
                SCOPE_READ_REPORTS,
                SCOPE_READ_ALERTS,
            }
        ),
        credential_grants=None,
        permissions=frozenset(),
        provenance={},
        policy_revision=7,
    )

    response = list_evidence_candidates(
        None,
        investigation=SimpleNamespace(id=uuid.uuid4()),
        user=SimpleNamespace(id=uuid.uuid4()),
        authorization=authorization,
        data_access=object(),
        q=None,
        requested_source_types=candidate_service.SOURCE_TYPES,
        range_value="7d",
        page=20,
        page_size=50,
    )

    assert response.candidates == []
    assert response.total == 4 * candidate_service.MAX_SOURCE_TOTAL
    assert response.total_truncated is True
    assert observed_limits == [1_000, 1_000, 1_000, 1_000]


def test_bounded_total_stops_counting_after_the_source_result_limit():
    class CapturingSession:
        statement = None

        def scalar(self, statement):
            self.statement = statement
            return candidate_service.MAX_SOURCE_TOTAL + 1

    session = CapturingSession()

    total, truncated = candidate_service._bounded_total(
        session,
        candidate_service.select(candidate_service.literal(1)),
    )

    assert total == candidate_service.MAX_SOURCE_TOTAL
    assert truncated is True
    compiled = session.statement.compile()
    assert candidate_service.MAX_SOURCE_TOTAL + 1 in compiled.params.values()


def test_candidate_route_is_request_context_and_publishes_bounded_openapi_contract():
    from app.main import app
    from app.schemas.investigation import InvestigationEvidenceCandidateSearch

    path = "/v1/investigations/{investigation_id}/evidence-candidates"
    entry = next(
        candidate
        for candidate in ROUTE_GOVERNANCE_MANIFEST.entries
        if candidate.operation.path_format == path
        and candidate.operation.method == "POST"
    )
    operation = app.openapi()["paths"][path]["post"]
    request_schema = InvestigationEvidenceCandidateSearch.model_json_schema()

    assert entry.governance_class is RouteGovernanceClass.REQUEST_CONTEXT
    assert operation["x-threatlens-required-token-scopes"] == [
        SCOPE_WRITE_INVESTIGATIONS
    ]
    assert operation["requestBody"]["required"] is True
    assert request_schema["properties"]["range"]["enum"] == [
        "24h",
        "7d",
        "30d",
        "90d",
    ]
    assert request_schema["properties"]["page"]["maximum"] == 20
    assert request_schema["properties"]["page_size"]["maximum"] == 50
    assert request_schema["properties"]["q"]["anyOf"][0]["maxLength"] == 255
    assert "as_of" in request_schema["properties"]
    member_parameters = {
        parameter["name"]: parameter
        for parameter in app.openapi()["paths"]["/v1/investigations/member-candidates"][
            "get"
        ]["parameters"]
    }
    assert member_parameters["page_size"]["schema"]["maximum"] == 50


def test_candidate_search_anchor_is_bounded_and_stable():
    now = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    anchor = now - timedelta(minutes=15)

    assert (
        candidate_service._resolve_effective_until(
            as_of=anchor,
            now=now,
        )
        == anchor
    )
    with pytest.raises(InvestigationValidationError, match="expired"):
        candidate_service._resolve_effective_until(
            as_of=now - timedelta(hours=2),
            now=now,
        )
    with pytest.raises(InvestigationValidationError, match="future"):
        candidate_service._resolve_effective_until(
            as_of=now + timedelta(minutes=1),
            now=now,
        )


def test_ioc_candidate_contains_search_has_a_trigram_index_contract():
    index = next(
        candidate
        for candidate in IOC.__table__.indexes
        if candidate.name == "ix_iocs_value_norm_trgm"
    )

    assert index.dialect_options["postgresql"]["using"] == "gin"
    assert index.dialect_options["postgresql"]["ops"] == {
        "value_norm_lower": "gin_trgm_ops"
    }
