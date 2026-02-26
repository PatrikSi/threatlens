from app.services.ioc_extraction import extract_iocs


def test_extract_iocs_atomic_values():
    matches = extract_iocs(
        title="CVE-2026-12345 exploit targets 203.0.113.7",
        summary="Indicators include bad.example.com and hash d41d8cd98f00b204e9800998ecf8427e",
        article_text=None,
    )

    as_pairs = {(entry.type, entry.value_norm) for entry in matches}
    assert ("cve", "CVE-2026-12345") in as_pairs
    assert ("ipv4", "203.0.113.7") in as_pairs
    assert ("domain", "bad.example.com") in as_pairs
    assert ("hash_md5", "d41d8cd98f00b204e9800998ecf8427e") in as_pairs


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
