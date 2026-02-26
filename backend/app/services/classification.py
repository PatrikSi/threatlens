from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Pattern

CLASSIFICATION_RULES_VERSION = "v1"

# Derived from the current live feed corpus and constrained to 10 labels.
CLASSIFICATION_CATEGORIES = (
    "vulnerability",
    "apt_campaign",
    "malware_ransomware",
    "phishing_social_engineering",
    "supply_chain",
    "incident_breach",
    "threat_intelligence_research",
    "defensive_guidance",
    "technology_ai",
    "multi",
)


@dataclass(frozen=True)
class ClassificationResult:
    primary_category: str
    secondary_categories: list[str]
    confidence: float
    scores: dict[str, float]
    matched_terms: dict[str, list[str]]
    source_hash: str
    rules_version: str = CLASSIFICATION_RULES_VERSION


@dataclass(frozen=True)
class _Rule:
    token: str
    pattern: Pattern[str]
    weight: float


def classify_item_content(
    *,
    title: str,
    summary: str | None,
    article_text: str | None,
    feed_name: str | None = None,
) -> ClassificationResult:
    full_text = " ".join(
        part.strip()
        for part in [
            title or "",
            summary or "",
            article_text or "",
            feed_name or "",
        ]
        if part and part.strip()
    ).lower()
    source_hash = compute_classification_source_hash(title=title, summary=summary, article_text=article_text)

    scores, matched_terms = _score_text(full_text, feed_name or "")
    ranked = sorted(scores.items(), key=lambda pair: pair[1], reverse=True)
    top_category, top_score = ranked[0]
    second_category, second_score = ranked[1]
    total_score = sum(score for _, score in ranked)

    if top_score <= 0:
        fallback = "technology_ai" if "ai" in full_text or "model" in full_text else "threat_intelligence_research"
        return ClassificationResult(
            primary_category=fallback,
            secondary_categories=[],
            confidence=0.2,
            scores={},
            matched_terms={},
            source_hash=source_hash,
        )

    if second_score >= max(3.0, top_score * 0.8):
        primary = "multi"
        secondary = [top_category, second_category]
        confidence = _clamp((top_score + second_score) / max(total_score, 1.0), 0.4, 0.99)
    else:
        primary = top_category
        secondary = [category for category, score in ranked[1:] if score >= max(2.0, top_score * 0.45)][:2]
        confidence = _clamp(top_score / max(total_score, 1.0), 0.35, 0.99)

    compact_scores = {category: round(score, 3) for category, score in ranked if score > 0}
    compact_terms = {category: tokens for category, tokens in matched_terms.items() if tokens}

    return ClassificationResult(
        primary_category=primary,
        secondary_categories=secondary,
        confidence=round(confidence, 3),
        scores=compact_scores,
        matched_terms=compact_terms,
        source_hash=source_hash,
    )


def compute_classification_source_hash(*, title: str, summary: str | None, article_text: str | None) -> str:
    payload = "\n".join(
        [
            title or "",
            summary or "",
            article_text or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _score_text(text: str, feed_name: str) -> tuple[dict[str, float], dict[str, list[str]]]:
    scores: dict[str, float] = {category: 0.0 for category in CLASSIFICATION_CATEGORIES if category != "multi"}
    matched_terms: dict[str, list[str]] = {category: [] for category in CLASSIFICATION_CATEGORIES if category != "multi"}

    for category, rules in _RULES.items():
        for rule in rules:
            match_count = sum(1 for _ in rule.pattern.finditer(text))
            if match_count <= 0:
                continue
            scores[category] += rule.weight * min(match_count, 3)
            matched_terms[category].append(rule.token)

    feed_lower = feed_name.lower()
    for token, category, weight in _FEED_PRIORS:
        if token in feed_lower:
            scores[category] += weight
            matched_terms[category].append(f"feed:{token}")

    return scores, matched_terms


def _build_rules(raw: dict[str, list[tuple[str, str, float]]]) -> dict[str, list[_Rule]]:
    compiled: dict[str, list[_Rule]] = {}
    for category, rules in raw.items():
        compiled[category] = [_Rule(token=token, pattern=re.compile(pattern), weight=weight) for token, pattern, weight in rules]
    return compiled


def _clamp(value: float, lower: float, upper: float) -> float:
    if value < lower:
        return lower
    if value > upper:
        return upper
    return value


_RAW_RULES: dict[str, list[tuple[str, str, float]]] = {
    "vulnerability": [
        ("cve", r"\bcve-\d{4}-\d{4,7}\b", 4.0),
        ("vulnerability", r"\bvulnerab(?:ility|ilities)\b", 2.2),
        ("cvss", r"\bcvss\b", 2.0),
        ("patch_tuesday", r"\bpatch tuesday\b", 2.3),
        ("zero_day", r"\bzero[- ]day\b", 2.6),
        ("exploit", r"\bactive exploitation\b|\bexploited\b|\bexploit\b", 1.6),
    ],
    "apt_campaign": [
        ("apt", r"\bapt\d+\b|\bapt\b", 2.4),
        ("threat_actor", r"\bthreat actor\b|\badversary\b|\bstate-sponsored\b", 2.0),
        ("campaign", r"\bcampaign\b|\boperation\b", 1.4),
        ("espionage", r"\bespionage\b|\bnation[- ]state\b", 2.2),
        ("mustang_panda", r"\bmustang panda\b|\bhoneymyte\b|\bbronze president\b", 3.0),
    ],
    "malware_ransomware": [
        ("ransomware", r"\bransomware\b", 3.0),
        ("stealer", r"\binfostealer\b|\bstealer\b", 2.3),
        ("backdoor", r"\bbackdoor\b", 1.8),
        ("rootkit", r"\brootkit\b", 2.4),
        ("rat", r"\bremote access trojan\b|\brat\b", 1.8),
        ("malware", r"\bmalware\b|\bmaas\b|\bmalware-as-a-service\b", 1.3),
    ],
    "phishing_social_engineering": [
        ("phishing", r"\bphishing\b|\bphish\b", 2.4),
        ("malvertising", r"\bmalvertising\b", 2.4),
        ("spam", r"\bspam\b|\bscam\b", 1.8),
        ("credential", r"\bcredential(?:s)?\b|\baccount takeover\b", 1.6),
        ("fraud", r"\bfraudulent\b|\bsocial engineering\b", 1.7),
    ],
    "supply_chain": [
        ("supply_chain", r"\bsupply chain\b", 3.0),
        ("malicious_update", r"\bmalicious update\b|\btrojanized update\b", 2.4),
        ("compromised_package", r"\bcompromised package\b|\bdependency confusion\b", 2.2),
        ("stolen_cert", r"\bstolen certificate\b|\bsigned with.*certificate\b", 1.8),
    ],
    "incident_breach": [
        ("breach", r"\bdata breach\b|\bbreach\b", 2.5),
        ("leak", r"\bleak(?:ed)?\b|\bexposed records\b", 2.0),
        ("incident_response", r"\bincident response\b|\bpostmortem\b", 1.9),
        ("compromise", r"\bcompromised\b|\bunauthorized access\b", 1.6),
    ],
    "threat_intelligence_research": [
        ("threat_intelligence", r"\bthreat intelligence\b|\bexecutive report\b", 2.4),
        ("analysis", r"\banalysis\b|\bresearch\b|\bobserved\b", 1.2),
        ("mitre_attck", r"\bmitre\b|\batt&ck\b", 2.2),
        ("landscape", r"\bthreat landscape\b|\btrends?\b", 1.8),
    ],
    "defensive_guidance": [
        ("playbook", r"\bplaybook\b", 2.4),
        ("how_to", r"\bhow to\b|\bguide\b|\btutorial\b", 1.9),
        ("hardening", r"\bhardening\b|\bbest practice(?:s)?\b", 2.0),
        ("detection", r"\bdetect(?:ing|ion)\b|\bmitigation\b|\bremediation\b", 1.6),
    ],
    "technology_ai": [
        ("ai", r"\bai\b|\bagentic\b|\bllm\b|\bmodel\b", 2.0),
        ("release", r"\brelease\b|\bnew feature\b|\bversion\b", 1.6),
        ("product", r"\bproduct\b|\bplatform\b|\bintegration\b", 1.4),
        ("cloud", r"\bcloud\b|\bdata center\b|\binfrastructure\b", 1.2),
    ],
}

_FEED_PRIORS: list[tuple[str, str, float]] = [
    ("securelist", "threat_intelligence_research", 1.4),
    ("threat research", "threat_intelligence_research", 1.4),
    ("storm center", "threat_intelligence_research", 1.2),
    ("orange cyberdefense", "threat_intelligence_research", 1.2),
    ("trainsec", "defensive_guidance", 1.0),
    ("entra news", "technology_ai", 1.0),
]

_RULES = _build_rules(_RAW_RULES)
