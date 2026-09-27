"""Select bounded exact quotations across current extraction sections for hunts."""
from __future__ import annotations

import re
from app.schemas.ai_extraction import StructuredExtractionResponse


def select_assessment_passages(extraction: dict | None, *, source_version: int, article_id: str,
                               retrieved_at: str, context: dict, prefix: str, limit: int = 16000) -> tuple[str, dict]:
    """Use verified primary quotations, never AI entity descriptions as evidence.

    Every passage retains its source coordinates. Ranking is deterministic and
    team-specific; it does not assign a truth score. Invalid/stale snapshots fall
    back to the existing bounded primary text prefix.
    """
    fallback = (prefix[:limit], {"selection": "article_prefix", "selected_passages": []})
    try:
        parsed = StructuredExtractionResponse.model_validate(extraction)
    except (ValueError, TypeError):
        return fallback
    if (parsed.source_version != source_version or str(parsed.article_id) != article_id
            or parsed.article_retrieved_at.isoformat() != retrieved_at):
        return fallback
    keywords = set(re.findall(r"[a-z0-9]{3,}", " ".join(str(value) for value in context.values()).lower()))
    passages = {}
    for entry in [*parsed.entities, *parsed.relationships]:
        for evidence in entry.evidence:
            if evidence.source != "article_text":
                continue
            key = (evidence.start, evidence.end, evidence.quote)
            score = len(keywords & set(re.findall(r"[a-z0-9]{3,}", evidence.quote.lower())))
            passages[key] = max(passages.get(key, 0), score)
    selected = []
    # Retain a small introduction and reserve most space for section evidence,
    # including late technical details that previously could never reach hunts.
    parts = [prefix[:min(4000, limit // 4)]]
    used = len(parts[0])
    for (start, end, quote), _score in sorted(passages.items(), key=lambda pair: (-pair[1], pair[0][0])):
        if used + len(quote) + 2 > limit:
            continue
        prompt_start = used + 2
        parts.append(quote)
        used += len(quote) + 2
        selected.append({"start": start, "end": end, "prompt_start": prompt_start, "prompt_end": used})
    if not selected:
        return fallback
    return "\n\n".join(parts), {"selection": "verified_section_passages", "selected_passages": selected,
        "source_hash": parsed.source_hash, "source_version": parsed.source_version,
        "coverage": parsed.coverage.model_dump(mode="json") if parsed.coverage else None}


def trim_assessment_passages(text: str, selection: dict, *, limit: int) -> tuple[str, dict]:
    """Trim at whole-passage boundaries and account only for supplied evidence."""
    passages = selection.get("selected_passages", [])
    for passage in passages:
        if passage.get("prompt_start", len(text)) < limit < passage.get("prompt_end", len(text)):
            limit = max(0, passage["prompt_start"] - 2)
            break
    retained = [passage for passage in passages if passage.get("prompt_end", len(text) + 1) <= limit]
    return text[:limit], {
        **selection,
        "selection": "verified_section_passages" if retained else "article_prefix",
        "selected_passages": retained,
    }
