"""Small synthetic contract probes; successful probes are not quality approval."""
import json
import hashlib
import re
from markdown_it import MarkdownIt
from app.services.ai_extraction import EXTRACTION_PROMPT, validate_structured_extraction
from app.services.report_grounding import validate_stage_output
from app.services.team_assessment_contract import ASSESSMENT_SYSTEM_PROMPT, validate_team_assessment_output

SOURCE = "Example malware BeaconExample uses scheduled tasks for persistence. docs.example.org is a documentation reference, not malicious infrastructure."
CASE_VERSION = "2026-09-contracts-v2"
FEATURES = ("extraction", "report", "hunt")


def qualification_messages(feature: str) -> list[dict[str, str]]:
    if feature == "extraction":
        system = "Return only JSON with structured_extraction. " + EXTRACTION_PROMPT
        body = {"item": {"title": "Synthetic qualification", "summary": "", "article_text": SOURCE}}
    elif feature == "report":
        system = ('Return JSON with findings, an array of {text, citations: ["S1"], evidence_quotes: '
                  '[{citation: "S1", quote: exact source quote}]}. Use only supplied evidence. Do not infer maliciousness of references.')
        body = {"evidence": ["[S1] " + SOURCE]}
    elif feature == "report_section":
        system = ('Return JSON {body_markdown: string, citations: ["S1"], key_points: []}. '
                  'Write a short report paragraph and a numeric Markdown table. Every narrative paragraph and table data row '
                  'must contain [S1]. Use only supplied findings, with no invented incident counts.')
        body = {"section": {"title": "Qualification section"}, "findings": [{
            "text": SOURCE + " The synthetic sample contains 3 scheduled tasks.", "citations": ["S1"],
        }]}
    elif feature == "hunt":
        system = ASSESSMENT_SYSTEM_PROMPT
        body = {"task": "team_assessment", "item": {"title": "Synthetic qualification", "summary": "", "article_text": SOURCE},
                "hunts_enabled": True, "team_context": {"technology_stack": ["Windows"], "available_telemetry": ["Scheduled task events"]}}
    else:
        raise ValueError("Unknown qualification feature")
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(body)}]


def validate_qualification(feature: str, payload: dict, messages: list[dict]) -> None:
    body = json.loads(messages[-1]["content"])
    if feature == "extraction":
        result = validate_structured_extraction(payload.get("structured_extraction"), source=body["item"])
        if not result["entities"]:
            raise ValueError("The extraction probe returned no entities for explicit source facts.")
    elif feature == "report":
        validate_stage_output(payload, stage=body)
        if not payload.get("findings"):
            raise ValueError("The report probe returned no findings for explicit source facts.")
    elif feature == "report_section":
        validate_stage_output(payload, stage=body)
        if not _has_numeric_table_row(payload["body_markdown"]):
            raise ValueError("The report probe must include a numeric Markdown table data row with its source citation.")
    elif feature == "hunt":
        result = validate_team_assessment_output(payload, messages)
        if not result["hunts"]:
            raise ValueError("The hunt probe returned no reviewable hypothesis.")
    else:
        raise ValueError("Unknown qualification feature")


def _has_numeric_table_row(body: str) -> bool:
    in_data_cell = False
    for token in MarkdownIt("commonmark").enable("table").parse(body):
        if token.type == "td_open":
            in_data_cell = True
        elif token.type == "td_close":
            in_data_cell = False
        elif in_data_cell and token.type == "inline":
            # Citation identifiers and HTML/link destinations are not values.
            visible = " ".join(child.content for child in token.children or []
                               if child.type in {"text", "code_inline"})
            if re.search(r"\d", re.sub(r"\[S\d+\]", "", visible)):
                return True
    return False


def qualification_plan_fingerprint(features: list[str]) -> str:
    probes = [probe for feature in features for probe in (["report", "report_section"] if feature == "report" else [feature])]
    encoded = json.dumps({"case_version": CASE_VERSION,
        "messages": [(probe, qualification_messages(probe)) for probe in probes]}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
