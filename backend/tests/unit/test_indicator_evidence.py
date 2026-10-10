import pytest

from app.services.indicator_evidence import indicator_exclusion


@pytest.mark.parametrize(
    "kind,value",
    [
        ("ipv4", "192.0.2.1"),
        ("ipv6", "2001:db8::1"),
        ("url", "https://192.0.2.1/path"),
        ("url", "https://[2001:db8::1]/path"),
        ("url", "http://198.51.100.20:8080/"),
        ("url", "https://203.0.113.8/"),
    ],
)
def test_documentation_addresses_are_excluded_in_direct_and_url_form(kind, value):
    assert indicator_exclusion(kind, value, None) == ["reserved_example"]


@pytest.mark.parametrize(
    "value",
    ["http://10.1.2.3/path", "http://[fd00::1]/path", "https://suspicious-host.net/"],
)
def test_ordinary_private_addresses_and_domains_are_not_example_exclusions(value):
    assert indicator_exclusion("url", value, None) == []
