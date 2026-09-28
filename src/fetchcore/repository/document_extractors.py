"""Fail-closed extraction adapters for repository document knowledge.

These adapters normalize observable document structure into deterministic units.
They never execute macros, formulas, JavaScript, external links, or network calls.
Optional third-party parsers are imported lazily so the AgentsOrch base install
remains dependency-light.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time
from importlib import import_module
from typing import Any, Protocol, cast


@dataclass(frozen=True)
class DocumentUnit:
    """One deterministic normalized unit derived from an authoritative document."""

    discriminator: str
    content: str
    section_kind: str
    section_name: str
    locator: dict[str, object] = field(default_factory=dict)


class DocumentSourceExtractor(Protocol):
    """Translate one bounded repository document into observable normalized units."""

    format_id: str
    indexing_contract_version: str

    def extract(self, payload: bytes, *, path: str) -> tuple[DocumentUnit, ...]: ...


class DocumentExtractionError(RuntimeError):
    """A document could not be safely or deterministically extracted."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


class DocumentExtractorUnavailable(DocumentExtractionError):
    """The optional parser required by one document format is not installed."""

    def __init__(self, package: str, format_id: str) -> None:
        super().__init__(
            "extractor_unavailable",
            f"{format_id} extraction requires optional dependency {package!r}",
        )
        self.package = package
        self.format_id = format_id


class PdfDocumentExtractor:
    """Extract one normalized text unit per PDF page using pypdf."""

    format_id = "pdf"
    indexing_contract_version = "1"

    def extract(self, payload: bytes, *, path: str) -> tuple[DocumentUnit, ...]:
        try:
            pypdf = cast(Any, import_module("pypdf"))
        except ImportError as exc:
            raise DocumentExtractorUnavailable("pypdf", self.format_id) from exc

        try:
            reader = pypdf.PdfReader(io.BytesIO(payload), strict=False)
        except Exception as exc:  # parser-specific exception hierarchy is optional
            raise DocumentExtractionError(
                "invalid_document",
                f"could not parse PDF document {path!r}",
            ) from exc
        if bool(getattr(reader, "is_encrypted", False)):
            raise DocumentExtractionError(
                "encrypted_document",
                "encrypted PDF is not extracted without an explicit "
                f"decryption policy: {path!r}",
            )

        page_count = len(reader.pages)
        units: list[DocumentUnit] = []
        for page_number, page in enumerate(reader.pages, start=1):
            try:
                raw = page.extract_text() or ""
            except Exception as exc:
                raise DocumentExtractionError(
                    "page_extraction_error",
                    f"could not extract PDF page {page_number} from {path!r}",
                ) from exc
            content = _normalize_document_text(raw)
            if not content:
                try:
                    has_images = bool(getattr(page, "images", ()))
                except Exception as exc:
                    raise DocumentExtractionError(
                        "page_extraction_error",
                        f"could not inspect PDF page {page_number} from {path!r}",
                    ) from exc
                if has_images:
                    raise DocumentExtractionError(
                        "non_text_pdf_page",
                        f"PDF page {page_number} from {path!r} has images but no "
                        "extractable text; OCR is not enabled",
                    )
                continue
            units.append(
                DocumentUnit(
                    discriminator=f"pdf:page:{page_number}",
                    content=content,
                    section_kind="pdf-page",
                    section_name=str(page_number),
                    locator={
                        "format": "pdf",
                        "page": page_number,
                        "page_count": page_count,
                    },
                )
            )
        return tuple(units)


class XlsxDocumentExtractor:
    """Extract bounded worksheet row ranges while preserving cell coordinates."""

    format_id = "xlsx"
    indexing_contract_version = "1"
    rows_per_unit = 80
    max_sheet_rows = 20_000
    max_sheet_columns = 512
    max_sheet_cells = 200_000
    max_zip_members = 10_000
    max_uncompressed_bytes = 128_000_000

    def extract(self, payload: bytes, *, path: str) -> tuple[DocumentUnit, ...]:
        _validate_xlsx_container(
            payload,
            max_members=self.max_zip_members,
            max_uncompressed_bytes=self.max_uncompressed_bytes,
        )
        try:
            openpyxl = cast(Any, import_module("openpyxl"))
        except ImportError as exc:
            raise DocumentExtractorUnavailable("openpyxl", self.format_id) from exc

        try:
            workbook = openpyxl.load_workbook(
                filename=io.BytesIO(payload),
                read_only=True,
                data_only=False,
                keep_links=False,
            )
        except Exception as exc:
            raise DocumentExtractionError(
                "invalid_document",
                f"could not parse XLSX workbook {path!r}",
            ) from exc

        units: list[DocumentUnit] = []
        try:
            for worksheet in workbook.worksheets:
                max_row = int(worksheet.max_row or 0)
                max_column = int(worksheet.max_column or 0)
                if max_row > self.max_sheet_rows or max_column > self.max_sheet_columns:
                    raise DocumentExtractionError(
                        "worksheet_limits_exceeded",
                        f"worksheet {worksheet.title!r} in {path!r} "
                        "exceeds row/column limits",
                    )
                if max_row and max_column and max_row * max_column > self.max_sheet_cells:
                    raise DocumentExtractionError(
                        "worksheet_limits_exceeded",
                        f"worksheet {worksheet.title!r} in {path!r} exceeds cell limits",
                    )
                units.extend(self._worksheet_units(worksheet))
        finally:
            workbook.close()
        return tuple(units)

    def _worksheet_units(self, worksheet: Any) -> tuple[DocumentUnit, ...]:
        observed_rows: list[tuple[int, tuple[tuple[str, str], ...]]] = []
        for row in worksheet.iter_rows():
            cells: list[tuple[str, str]] = []
            row_number: int | None = None
            for cell in row:
                value = _render_cell_value(cell.value)
                if value is None:
                    continue
                coordinate = str(cell.coordinate)
                if row_number is None:
                    row_number = int(cell.row)
                cells.append((coordinate, value))
            if row_number is not None and cells:
                observed_rows.append((row_number, tuple(cells)))

        if not observed_rows:
            return ()

        units: list[DocumentUnit] = []
        for offset in range(0, len(observed_rows), self.rows_per_unit):
            group = observed_rows[offset : offset + self.rows_per_unit]
            row_start = group[0][0]
            row_end = group[-1][0]
            coordinates = [
                coordinate
                for _, cells in group
                for coordinate, _ in cells
            ]
            columns = tuple(_column_name(value) for value in coordinates)
            first_column = min(columns, key=_column_index)
            last_column = max(columns, key=_column_index)
            cell_range = f"{first_column}{row_start}:{last_column}{row_end}"
            content = "\n".join(
                "\t".join(f"{coordinate}={value}" for coordinate, value in cells)
                for _, cells in group
            )
            section_name = f"{_quote_sheet_name(str(worksheet.title))}!{cell_range}"
            units.append(
                DocumentUnit(
                    discriminator=(
                        f"xlsx:sheet:{_discriminator(str(worksheet.title))}:"
                        f"rows:{row_start}-{row_end}"
                    ),
                    content=content,
                    section_kind="xlsx-worksheet-range",
                    section_name=section_name,
                    locator={
                        "format": "xlsx",
                        "sheet": str(worksheet.title),
                        "range": cell_range,
                        "row_start": row_start,
                        "row_end": row_end,
                        "cell_count": len(coordinates),
                    },
                )
            )
        return tuple(units)


def default_document_extractors() -> Mapping[str, DocumentSourceExtractor]:
    """Return format adapters without importing their optional parser packages."""

    return {
        ".pdf": PdfDocumentExtractor(),
        ".xlsx": XlsxDocumentExtractor(),
    }


def _normalize_document_text(value: str) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in normalized.split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def _render_cell_value(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, float):
        return format(value, ".15g")
    rendered = str(value)
    return rendered if rendered else None


def _validate_xlsx_container(
    payload: bytes,
    *,
    max_members: int,
    max_uncompressed_bytes: int,
) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            if len(infos) > max_members:
                raise DocumentExtractionError(
                    "archive_limits_exceeded",
                    "XLSX archive contains too many members",
                )
            total = 0
            for info in infos:
                if info.flag_bits & 0x1:
                    raise DocumentExtractionError(
                        "encrypted_document",
                        "encrypted XLSX containers are not extracted",
                    )
                total += int(info.file_size)
                if total > max_uncompressed_bytes:
                    raise DocumentExtractionError(
                        "archive_limits_exceeded",
                        "XLSX archive exceeds the uncompressed byte limit",
                    )
    except DocumentExtractionError:
        raise
    except (OSError, zipfile.BadZipFile) as exc:
        raise DocumentExtractionError(
            "invalid_document",
            "invalid XLSX container",
        ) from exc


def _column_name(coordinate: str) -> str:
    match = re.match(r"([A-Z]+)", coordinate.upper())
    if match is None:
        raise DocumentExtractionError(
            "invalid_document",
            f"invalid cell coordinate {coordinate!r}",
        )
    return match.group(1)


def _column_index(name: str) -> int:
    value = 0
    for character in name:
        value = value * 26 + (ord(character) - ord("A") + 1)
    return value


def _quote_sheet_name(name: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", name):
        return name
    return "'" + name.replace("'", "''") + "'"


def _discriminator(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-")
    return normalized or "sheet"
