import json
import os
import re
import tempfile
import uuid
import zipfile
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.schemas.exports import ArticleExportFilters, ArticleExportOptions, ExportFormat
from app.services.export_documents import (
    build_manifest,
    csv_header_bytes,
    ioc_to_csv_bytes,
    record_to_csv_bytes,
    record_to_document,
)
from app.services.export_misp import build_misp_event
from app.services.export_models import ExportRecord
from app.services.export_pdf import build_article_pdf, build_pdf_filename
from app.services.export_stix import iter_stix_objects

JSON_MEDIA_TYPE = "application/json"
ZIP_MEDIA_TYPE = "application/zip"
FORMAT_DETAILS = {
    "csv": ("csv", "text/csv; charset=utf-8"),
    "jsonl": ("jsonl", "application/x-ndjson"),
    "threat_bundle": ("zip", ZIP_MEDIA_TYPE),
    "stix": ("stix.json", "application/stix+json"),
    "misp": ("misp.json", JSON_MEDIA_TYPE),
    "pdf_bundle": ("pdf.zip", ZIP_MEDIA_TYPE),
}


class ExportSizeLimitError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExportArtifact:
    path: Path
    filename: str
    media_type: str
    item_count: int
    file_size: int
    uncompressed_bytes: int


@dataclass
class ExportSizeBudget:
    maximum: int
    used: int = 0

    def account(self, size: int) -> None:
        self.used += max(0, size)
        if self.used > self.maximum:
            raise ExportSizeLimitError(
                f"Export exceeded the configured uncompressed size limit of {self.maximum} bytes"
            )


def generate_export_artifact(
    records: Iterable[ExportRecord],
    *,
    item_count: int,
    export_format: ExportFormat,
    filters: ArticleExportFilters,
    options: ArticleExportOptions,
    max_uncompressed_bytes: int,
) -> ExportArtifact:
    suffix, media_type = FORMAT_DETAILS[export_format]
    exported_at = datetime.now(timezone.utc)
    filename = _build_export_filename(options.filename_prefix, exported_at=exported_at, suffix=suffix)
    budget = ExportSizeBudget(maximum=max_uncompressed_bytes)

    with _temporary_artifact_path(suffix=f".{suffix}") as path:
        if export_format == "csv":
            _write_csv(path, records=records, options=options, budget=budget)
        elif export_format == "jsonl":
            _write_jsonl(path, records=records, options=options, budget=budget)
        elif export_format == "threat_bundle":
            _write_threat_bundle(
                path,
                records=records,
                item_count=item_count,
                exported_at=exported_at,
                filters=filters,
                options=options,
                budget=budget,
            )
        elif export_format == "stix":
            _write_stix(path, records=records, options=options, budget=budget)
        elif export_format == "misp":
            _write_misp(path, records=records, options=options, budget=budget)
        elif export_format == "pdf_bundle":
            _write_pdf_bundle(
                path,
                records=records,
                item_count=item_count,
                exported_at=exported_at,
                filters=filters,
                options=options,
                budget=budget,
            )
        else:  # pragma: no cover - protected by the request schema
            raise ValueError(f"Unsupported export format: {export_format}")

        file_size = path.stat().st_size
        if file_size > max_uncompressed_bytes:
            raise ExportSizeLimitError(
                f"Export file exceeded the configured size limit of {max_uncompressed_bytes} bytes"
            )
        return ExportArtifact(
            path=path,
            filename=filename,
            media_type=media_type,
            item_count=item_count,
            file_size=file_size,
            uncompressed_bytes=budget.used,
        )


def remove_export_artifact(path: Path) -> None:
    path.unlink(missing_ok=True)


def _write_csv(
    path: Path,
    *,
    records: Iterable[ExportRecord],
    options: ArticleExportOptions,
    budget: ExportSizeBudget,
) -> None:
    with path.open("wb") as output:
        _write_bounded(output, csv_header_bytes(), budget=budget)
        for record in records:
            _write_bounded(output, record_to_csv_bytes(record, options=options), budget=budget)


def _write_jsonl(
    path: Path,
    *,
    records: Iterable[ExportRecord],
    options: ArticleExportOptions,
    budget: ExportSizeBudget,
) -> None:
    with path.open("wb") as output:
        for record in records:
            payload = _json_bytes(record_to_document(record, options=options)) + b"\n"
            _write_bounded(output, payload, budget=budget)


def _write_stix(
    path: Path,
    *,
    records: Iterable[ExportRecord],
    options: ArticleExportOptions,
    budget: ExportSizeBudget,
) -> None:
    with path.open("wb") as output:
        _write_bounded(
            output,
            _json_bytes({"type": "bundle", "id": f"bundle--{uuid.uuid4()}"})[:-1] + b',"objects":[',
            budget=budget,
        )
        first = True
        for stix_object in iter_stix_objects(records, options=options):
            if not first:
                _write_bounded(output, b",", budget=budget)
            first = False
            _write_bounded(output, stix_object.serialize().encode("utf-8"), budget=budget)
        _write_bounded(output, b"]}", budget=budget)


def _write_misp(
    path: Path,
    *,
    records: Iterable[ExportRecord],
    options: ArticleExportOptions,
    budget: ExportSizeBudget,
) -> None:
    with path.open("wb") as output:
        _write_bounded(output, b'{"response":[', budget=budget)
        first = True
        for record in records:
            if not first:
                _write_bounded(output, b",", budget=budget)
            first = False
            event = json.loads(build_misp_event(record, options=options).to_json())
            _write_bounded(output, _json_bytes({"Event": event}), budget=budget)
        _write_bounded(output, b"]}", budget=budget)


def _write_threat_bundle(
    path: Path,
    *,
    records: Iterable[ExportRecord],
    item_count: int,
    exported_at: datetime,
    filters: ArticleExportFilters,
    options: ArticleExportOptions,
    budget: ExportSizeBudget,
) -> None:
    file_names = ["manifest.json", "articles.jsonl", "articles.csv"]
    include_ioc_csv = options.include_iocs and options.include_ioc_csv
    if include_ioc_csv:
        file_names.append("iocs.csv")

    with tempfile.TemporaryDirectory(prefix="threatlens-export-bundle-") as directory:
        inner_dir = Path(directory)
        jsonl_path = inner_dir / "articles.jsonl"
        csv_path = inner_dir / "articles.csv"
        ioc_path = inner_dir / "iocs.csv"
        with jsonl_path.open("wb") as jsonl_output, csv_path.open("wb") as csv_output:
            _write_bounded(csv_output, csv_header_bytes(), budget=budget)
            ioc_output = ioc_path.open("wb") if include_ioc_csv else None
            try:
                if ioc_output is not None:
                    _write_bounded(ioc_output, csv_header_bytes(ioc_export=True), budget=budget)
                for record in records:
                    document = _json_bytes(record_to_document(record, options=options)) + b"\n"
                    _write_bounded(jsonl_output, document, budget=budget)
                    _write_bounded(csv_output, record_to_csv_bytes(record, options=options), budget=budget)
                    if ioc_output is not None:
                        for ioc in record.iocs:
                            _write_bounded(ioc_output, ioc_to_csv_bytes(record, ioc), budget=budget)
            finally:
                if ioc_output is not None:
                    ioc_output.close()

        manifest = build_manifest(
            item_count=item_count,
            exported_at=exported_at,
            filters=filters,
            options=options,
            files=file_names,
            export_format="threat_bundle",
        )
        manifest_bytes = _json_bytes(manifest, pretty=True)
        budget.account(len(manifest_bytes))
        (inner_dir / "manifest.json").write_bytes(manifest_bytes)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            for file_name in file_names:
                archive.write(inner_dir / file_name, arcname=file_name)


def _write_pdf_bundle(
    path: Path,
    *,
    records: Iterable[ExportRecord],
    item_count: int,
    exported_at: datetime,
    filters: ArticleExportFilters,
    options: ArticleExportOptions,
    budget: ExportSizeBudget,
) -> None:
    pdf_names: list[str] = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for record in records:
            pdf_bytes = build_article_pdf(record, options=options)
            budget.account(len(pdf_bytes))
            pdf_name = f"articles/{build_pdf_filename(record)}"
            pdf_names.append(pdf_name)
            archive.writestr(pdf_name, pdf_bytes)
        manifest = build_manifest(
            item_count=item_count,
            exported_at=exported_at,
            filters=filters,
            options=options,
            files=["manifest.json", *pdf_names],
            export_format="pdf_bundle",
        )
        manifest_bytes = _json_bytes(manifest, pretty=True)
        budget.account(len(manifest_bytes))
        archive.writestr("manifest.json", manifest_bytes)


def _write_bounded(output: BinaryIO, data: bytes, *, budget: ExportSizeBudget) -> None:
    budget.account(len(data))
    output.write(data)


def _json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
    ).encode("utf-8")


def _build_export_filename(prefix: str | None, *, exported_at: datetime, suffix: str) -> str:
    safe_prefix = re.sub(r"[^A-Za-z0-9._-]+", "-", prefix or "threatlens-articles").strip(".-_")
    safe_prefix = safe_prefix[:80] or "threatlens-articles"
    timestamp = exported_at.strftime("%Y%m%d-%H%M%SZ")
    return f"{safe_prefix}-{timestamp}.{suffix}"


@contextmanager
def _temporary_artifact_path(*, suffix: str):
    descriptor, raw_path = tempfile.mkstemp(prefix="threatlens-export-", suffix=suffix)
    os.close(descriptor)
    path = Path(raw_path)
    try:
        yield path
    except Exception:
        path.unlink(missing_ok=True)
        raise
