from pathlib import Path
import pytest
import subprocess
import sys

from app.services import ioc_extraction
from app.services.ioc_extraction import extract_iocs, normalize_ioc_search_value


def extract(text: str):
    return extract_iocs(title=text, summary=None, article_text=None)


@pytest.mark.parametrize(("raw", "kind", "normalized"), [
    ("bad[.]example", "domain", "bad.example"),
    ("bad(.)EXAMPLE", "domain", "bad.example"),
    ("203[.]0[.]113[.]7", "ipv4", "203.0.113.7"),
    ("hxxps://Bad[.]Example/Drop/File.EXE?Token=AbC", "url", "https://bad.example/Drop/File.EXE?Token=AbC"),
    ("HTTPS[:]//WWW.Example.COM:443/A%2FB?q=YES#Top", "url", "https://www.example.com/A%2FB?q=YES#Top"),
    ("Analyst[@]Bad[.]Example", "email", "Analyst@bad.example"),
    ("analyst(@)bad(.)example", "email", "analyst@bad.example"),
    ("BÜCHER.example", "domain", "xn--bcher-kva.example"),
    ("https://[2001:0DB8::7]:8443/a", "url", "https://[2001:db8::7]:8443/a"),
    ("hxxp(:)//bad(.)example/a", "url", "http://bad.example/a"),
])
def test_network_normalization_preserves_original_evidence(raw, kind, normalized):
    text = f"İ Context: {raw} was reported in this source."
    entry = next(item for item in extract(text) if item.type == kind)
    assert entry.value_norm == normalized
    assert entry.value_raw == raw
    assert text[entry.source_start:entry.source_end] == raw
    assert entry.evidence_text in text
    assert raw in entry.evidence_text
    assert normalize_ioc_search_value(raw) == (kind, normalized)


def test_transformation_labels_only_describe_the_corresponding_match():
    url, host = [entry for entry in extract("hxxps://bad[.]example/A") if entry.type in {"url", "domain"}]
    assert url.transformations == ("refang_scheme", "refang_dot")
    assert host.transformations == ("refang_dot",)
    assert host.value_raw == "bad[.]example"


def test_urls_preserve_significant_path_query_escaping_and_balanced_punctuation():
    values = {entry.value_norm for entry in extract(
        "See (https://example.org/a_(b)?Case=AbC%2fD)."
    ) if entry.type == "url"}
    assert values == {"https://example.org/a_(b)?Case=AbC%2fD"}


def test_email_domains_are_not_separate_indicators_and_url_paths_are_not_domains():
    values = {(entry.type, entry.value_norm) for entry in extract(
        "Analyst+tag@www.EXAMPLE.com https://host.example/publisher.example/a"
    )}
    assert ("email", "Analyst+tag@www.example.com") in values
    assert ("domain", "host.example") in values
    assert ("domain", "example.com") not in values
    assert ("domain", "publisher.example") not in values


def test_unicode_url_host_evidence_offsets_use_original_host_spelling():
    raw = "https://İ.example/path"
    host = next(entry for entry in extract(raw) if entry.type == "domain")
    assert host.value_raw == "İ.example"
    assert raw[host.source_start:host.source_end] == "İ.example"


def test_invalid_mailbox_does_not_become_domain_evidence():
    assert not any(entry.type == "domain" for entry in extract("a" * 70 + ".name@example.com"))


@pytest.mark.parametrize("value", [
    "https://user:secret@example.com/path",
    "https://example.com:99999/path",
    "https://[fe80::1%25eth0]/path",
    "https://example.com\\evil.com/path",
    "http://example.com\nInjected",
    "a..b@example.com",
    "a@-invalid.example",
    "a@invalid_.example",
    "ftp://example.com/file",
])
def test_invalid_complete_search_values_are_rejected(value):
    assert normalize_ioc_search_value(value) is None


def test_long_tokens_are_skipped_whole_without_truncated_network_values():
    assert extract("https://evil.example/" + "a" * 5000) == []
    assert extract("a" * 5000 + ".example") == []


def test_source_and_occurrence_limits_reject_partial_snapshots(monkeypatch):
    monkeypatch.setattr(ioc_extraction, "MAX_SOURCE_CHARS", 40)
    with pytest.raises(ioc_extraction.IOCExtractionLimitError, match="character"):
        extract("example.org " * 10)
    monkeypatch.setattr(ioc_extraction, "MAX_EXTRACTED_OCCURRENCES", 1)
    with pytest.raises(ioc_extraction.IOCExtractionLimitError, match="occurrence"):
        extract("one.example two.example")


def test_unicode_prefix_does_not_shift_dictionary_evidence():
    text = "İ Microsoft Windows exposure"
    values = extract(text)
    assert {entry.value_norm for entry in values} >= {"microsoft", "windows"}
    for entry in values:
        assert text[entry.source_start:entry.source_end] == entry.value_raw


def test_legacy_search_kinds_and_domain_www_normalization_still_work():
    assert normalize_ioc_search_value("WWW.Example.COM") == ("domain", "example.com")
    assert normalize_ioc_search_value("2001:0DB8::7") == ("ipv6", "2001:db8::7")
    assert normalize_ioc_search_value("[::1]") == ("ipv6", "::1")
    assert normalize_ioc_search_value("CVE-2026-12345") == ("cve", "CVE-2026-12345")


def test_dense_punctuation_and_url_boundaries_have_bounded_cpu():
    result = subprocess.run([sys.executable, "-c", """
import resource
from app.services.ioc_extraction import extract_iocs
resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
text = ('.' * 4000 + ' ') * 400
text += ('https://sample.example/path' + ')' * 4000 + ' ') * 400
matches = extract_iocs(title='', summary=None, article_text=text)
assert len(matches) == 800
assert all(entry.value_norm in {'https://sample.example/path', 'sample.example'} for entry in matches)
"""], timeout=20, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr


def test_large_defanged_inventory_has_bounded_cpu_and_mapping_memory():
    result = subprocess.run([sys.executable, "-c", """
import resource
from app.services.ioc_extraction import extract_iocs
resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
resource.setrlimit(resource.RLIMIT_AS, (256 * 1024**2, 256 * 1024**2))
baseline = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
text = ' '.join(f'x{index:05d}' + 'a' * 35 + '[.]malware[.]test' for index in range(50000))
matches = extract_iocs(title='', summary=None, article_text=text)
assert len(matches) == 50000
assert all(entry.transformations == ('refang_dot',) for entry in matches)
assert all(text[entry.source_start:entry.source_end] == entry.value_raw for entry in matches)
assert resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - baseline < 128 * 1024
"""], timeout=20, capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    assert result.returncode == 0, result.stderr
