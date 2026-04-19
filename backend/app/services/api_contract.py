from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from fastapi import FastAPI


def build_openapi_schema_document(app: FastAPI) -> str:
    return json.dumps(app.openapi(), indent=2) + "\n"


def render_api_reference_markdown(
    app: FastAPI,
    *,
    service_base_path: str,
    proxy_base_path: str,
    openapi_service_path: str,
    openapi_proxy_path: str,
) -> str:
    schema = app.openapi()
    operations_by_tag: dict[str, list[tuple[str, str, dict[str, Any]]]] = defaultdict(list)

    for path in sorted(schema.get("paths", {})):
        methods = schema["paths"][path]
        for method in sorted(methods):
            operation = methods[method]
            tag = (operation.get("tags") or ["untagged"])[0]
            operations_by_tag[tag].append((method.upper(), path, operation))

    ordered_tags = [tag["name"] for tag in schema.get("tags", []) if tag["name"] in operations_by_tag]
    for tag in sorted(operations_by_tag):
        if tag not in ordered_tags:
            ordered_tags.append(tag)

    lines = [
        "# Backend API Reference",
        "",
        "This file is generated from the live FastAPI OpenAPI schema. Do not edit it by hand.",
        "",
        "## Published Contract",
        "",
        f"- API service base path: `{service_base_path}`",
        f"- Web proxy base path: `{proxy_base_path}`",
        "- Legacy unversioned endpoints remain available for compatibility but are excluded from the published schema.",
        f"- Machine-readable OpenAPI schema on the API service: `{openapi_service_path}`",
        f"- Machine-readable OpenAPI schema through the web proxy: `{openapi_proxy_path}`",
        "",
        "## Security Schemes",
        "",
    ]

    security_schemes = schema.get("components", {}).get("securitySchemes", {})
    if security_schemes:
        for name in sorted(security_schemes):
            definition = security_schemes[name]
            scheme_type = definition.get("type", "unknown")
            description = definition.get("description")
            line = f"- `{name}`: `{scheme_type}`"
            if description:
                line += f" - {description.strip()}"
            lines.append(line)
    else:
        lines.append("- None")

    for tag in ordered_tags:
        lines.extend(["", f"## {tag.title().replace('_', ' ')}", ""])
        for method, path, operation in operations_by_tag[tag]:
            lines.append(f"### `{method} {path}`")
            summary = _normalize_summary(operation.get("summary") or _humanize_operation_id(operation.get("operationId")))
            if summary:
                lines.append(f"- Summary: {summary}")
            lines.append(f"- Auth: {_format_security_requirements(operation.get('security', []))}")
            parameters = operation.get("parameters", [])
            if parameters:
                lines.append("- Parameters:")
                for parameter in parameters:
                    lines.append(
                        f"  - `{parameter['name']}` ({parameter['in']}, {'required' if parameter.get('required') else 'optional'}): "
                        f"{_describe_schema(parameter.get('schema'))}"
                    )
            request_body = operation.get("requestBody")
            if request_body is not None:
                lines.append(f"- Request body: {_describe_request_body(request_body)}")
            lines.append(f"- Responses: {_describe_responses(operation.get('responses', {}))}")

    lines.append("")
    return "\n".join(lines)


def _humanize_operation_id(operation_id: Any) -> str | None:
    if not isinstance(operation_id, str) or not operation_id.strip():
        return None
    return operation_id.replace("_", " ")


def _normalize_summary(summary: str | None) -> str | None:
    if summary is None:
        return None
    normalized = summary.strip()
    if normalized.endswith(" Route"):
        normalized = normalized[: -len(" Route")]
    return normalized or None


def _format_security_requirements(security: list[dict[str, list[str]]]) -> str:
    if not security:
        return "none"

    rendered = []
    for requirement in security:
        if not requirement:
            rendered.append("none")
            continue
        names = []
        for name, scopes in requirement.items():
            if scopes:
                names.append(f"{name} ({', '.join(scopes)})")
            else:
                names.append(name)
        rendered.append(" + ".join(names))
    return " or ".join(rendered)


def _describe_request_body(request_body: dict[str, Any]) -> str:
    content = request_body.get("content", {})
    variants = []
    for media_type in sorted(content):
        variants.append(f"`{media_type}` -> {_describe_schema(content[media_type].get('schema'))}")
    if not variants:
        return "present"
    return "; ".join(variants)


def _describe_responses(responses: dict[str, Any]) -> str:
    parts = []
    for status_code in sorted(responses):
        response = responses[status_code]
        content = response.get("content", {})
        schemas = []
        for media_type in sorted(content):
            schemas.append(f"`{media_type}` -> {_describe_schema(content[media_type].get('schema'))}")
        if schemas:
            parts.append(f"`{status_code}` {'; '.join(schemas)}")
        else:
            parts.append(f"`{status_code}`")
    return ", ".join(parts) if parts else "none"


def _describe_schema(schema: dict[str, Any] | None) -> str:
    if not schema:
        return "unspecified"
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    schema_type = schema.get("type")
    if schema_type == "array":
        return f"array[{_describe_schema(schema.get('items'))}]"
    if schema_type == "object":
        title = schema.get("title")
        if title:
            return title
        return "object"
    if schema_type:
        if "enum" in schema:
            enum_values = ", ".join(repr(value) for value in schema["enum"])
            return f"{schema_type} ({enum_values})"
        return schema_type
    title = schema.get("title")
    if title:
        return title
    return "schema"
