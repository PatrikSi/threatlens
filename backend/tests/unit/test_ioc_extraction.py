import subprocess
import sys

import pytest

from app.services.ioc_extraction import extract_iocs


def test_hash_lengths_boundaries_and_repeated_occurrences_are_preserved():
    hashes = ["A" * 64, "B" * 40, "C" * 32]
    matches = extract_iocs(title=" ".join(hashes + ["a" * 65, "b" * 41, "c" * 33]),
                           summary=" ".join(hashes), article_text="prefix" + "d" * 64)
    assert [(match.type, match.value_norm, match.source_section) for match in matches] == [
        (kind, value.lower(), section)
        for section in ("title", "summary")
        for kind, value in zip(("hash_sha256", "hash_sha1", "hash_md5"), hashes, strict=True)
    ]


def test_dense_hash_document_completes_with_bounded_cpu():
    # This 1.6 MB document is within the article budget. Pairwise overlap scans
    # took hundreds of millions of comparisons; linear scans fit a modest CPU
    # budget even on CI. Run separately so an accidental regression is killed.
    result = subprocess.run([sys.executable, "-c", """
import resource
from app.services.ioc_extraction import extract_iocs
resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
text = ' '.join(f'{index:064x}' for index in range(25000))
matches = extract_iocs(title='', summary=None, article_text=text)
assert len(matches) == 25000
assert len({match.value_norm for match in matches}) == 25000
"""], timeout=15, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_extract_iocs_atomic_values():
    matches = extract_iocs(
        title="CVE-2026-12345 exploit targets 203.0.113.7 and 2001:0DB8:0:0::7",
        summary="Indicators include bad.example.com and hash d41d8cd98f00b204e9800998ecf8427e",
        article_text=None,
    )

    as_pairs = {(entry.type, entry.value_norm) for entry in matches}
    assert ("cve", "CVE-2026-12345") in as_pairs
    assert ("ipv4", "203.0.113.7") in as_pairs
    assert ("ipv6", "2001:db8::7") in as_pairs
    assert ("domain", "bad.example.com") in as_pairs
    assert ("hash_md5", "d41d8cd98f00b204e9800998ecf8427e") in as_pairs


def test_extract_iocs_rejects_ipv6_zone_ids_and_non_address_colon_tokens():
    matches = extract_iocs(
        title="Local fe80::1%eth0 and MAC aa:bb:cc:dd:ee:ff are not portable IOCs",
        summary="The event occurred at 12:30:45 UTC.",
        article_text=None,
    )

    assert all(entry.type != "ipv6" for entry in matches)


@pytest.mark.parametrize(
    "text",
    (
        "C++::method",
        "namespace::type",
        "std::vector",
        "key:: value",
        "foo2001:db8::1bar",
        "bare :: placeholder",
        "12:30:45 UTC",
        "aa:bb:cc:dd:ee:ff",
        "fe80::1%eth0",
        "2001:db8:00000::1",
        "1:2:3:4:5:6:7:8:9",
    ),
)
def test_extract_iocs_rejects_ambiguous_or_invalid_ipv6_tokens(text: str):
    matches = extract_iocs(title=text, summary=None, article_text=None)

    assert all(entry.type != "ipv6" for entry in matches)


@pytest.mark.parametrize(
    ("text", "normalized"),
    (
        ("Observed 2001:db8::7 today", "2001:db8::7"),
        ("Observed 2001:0DB8:0:0:0:0:0:7 today", "2001:db8::7"),
        ("Loopback [::1] responded", "::1"),
        ("Prefix endpoint 2001:db8:: was routed", "2001:db8::"),
        ("All-letter dead:beef:cafe:babe::face matched", "dead:beef:cafe:babe::face"),
        ("URL https://[2001:db8::7]/path", "2001:db8::7"),
    ),
)
def test_extract_iocs_preserves_conservative_valid_ipv6_tokens(
    text: str,
    normalized: str,
):
    matches = extract_iocs(title=text, summary=None, article_text=None)

    assert ("ipv6", normalized) in {
        (entry.type, entry.value_norm) for entry in matches
    }


def test_extract_iocs_vendor_and_program_terms():
    matches = extract_iocs(
        title="Microsoft warns of new Active Directory abuse",
        summary=None,
        article_text="The campaign also impacted VMware ESXi hosts.",
    )

    as_pairs = {(entry.type, entry.value_norm) for entry in matches}
    assert ("vendor", "microsoft") in as_pairs
    assert ("program", "active directory") in as_pairs
    assert ("vendor", "vmware") in as_pairs
    assert ("program", "vmware esxi") in as_pairs
