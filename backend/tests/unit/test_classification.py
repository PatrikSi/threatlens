from app.services.classification import CLASSIFICATION_CATEGORIES, classify_item_content


def test_classification_taxonomy_limited_to_ten():
    assert len(CLASSIFICATION_CATEGORIES) <= 10
    assert "vulnerability" in CLASSIFICATION_CATEGORIES
    assert "apt_campaign" in CLASSIFICATION_CATEGORIES
    assert "multi" in CLASSIFICATION_CATEGORIES


def test_classify_vulnerability_article():
    result = classify_item_content(
        title="Microsoft Office vulnerability (CVE-2026-21509) in active exploitation",
        summary="Out-of-band update and CVSS details released by Microsoft.",
        article_text="Patch Tuesday guidance indicates active exploitation across Office products.",
        feed_name="Threat Research",
    )
    assert result.primary_category == "vulnerability"
    assert result.confidence >= 0.35


def test_classify_apt_campaign_article():
    result = classify_item_content(
        title="HoneyMyte APT evolves with a kernel-mode rootkit",
        summary="The campaign targeted regional organizations with espionage goals.",
        article_text="Researchers link Mustang Panda operations to a new backdoor chain.",
        feed_name="Securelist",
    )
    assert result.primary_category == "apt_campaign"
    assert "apt_campaign" in result.scores


def test_classify_multi_when_two_classes_are_strong():
    result = classify_item_content(
        title="Supply chain campaign exploits CVE-2026-9999 in active attacks",
        summary="Threat actor used malicious updates during exploitation.",
        article_text="Researchers observed CVE exploitation and supply chain tampering in the same operation.",
        feed_name="Threat Research",
    )
    assert result.primary_category == "multi"
    assert len(result.secondary_categories) >= 2
