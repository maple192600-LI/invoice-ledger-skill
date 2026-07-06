"""Helpers for locating duplicate rows in template workbooks."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable


ValueForField = Callable[[str, dict[str, Any], dict[str, Any], int], Any]


def existing_draft_row_ids(ws, columns: dict[str, int]) -> set[str]:
    draft_column = columns.get("draft_row_id")
    if not draft_column:
        return set()
    return {
        str(ws.cell(row_number, draft_column).value)
        for row_number in range(2, ws.max_row + 1)
        if ws.cell(row_number, draft_column).value not in {None, ""}
    }


def existing_draft_row_id_rows(ws, columns: dict[str, int]) -> dict[str, int]:
    draft_column = columns.get("draft_row_id")
    if not draft_column:
        return {}
    return {
        str(ws.cell(row_number, draft_column).value): row_number
        for row_number in range(2, ws.max_row + 1)
        if ws.cell(row_number, draft_column).value not in {None, ""}
    }


def invoice_line_key_column(fields: dict[str, Any], columns: dict[str, int]) -> int | None:
    line_key_column = columns.get("invoice_line_key")
    if not line_key_column:
        for field_name, spec in fields.items():
            if isinstance(spec, dict) and spec.get("source") == "invoice_line_key":
                line_key_column = columns.get(str(field_name))
                break
    return line_key_column


def existing_invoice_line_keys(ws, fields: dict[str, Any], columns: dict[str, int]) -> set[str]:
    line_key_column = invoice_line_key_column(fields, columns)
    if not line_key_column:
        return set()
    return {
        str(ws.cell(row_number, line_key_column).value)
        for row_number in range(2, ws.max_row + 1)
        if ws.cell(row_number, line_key_column).value not in {None, ""}
    }


def existing_invoice_line_key_rows(ws, fields: dict[str, Any], columns: dict[str, int]) -> dict[str, int]:
    line_key_column = invoice_line_key_column(fields, columns)
    if not line_key_column:
        return {}
    return {
        str(ws.cell(row_number, line_key_column).value): row_number
        for row_number in range(2, ws.max_row + 1)
        if ws.cell(row_number, line_key_column).value not in {None, ""}
    }


def fingerprint_value(value: Any) -> str:
    if value in {None, ""}:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, (int, float)):
        try:
            return format(Decimal(str(value)).normalize(), "f")
        except InvalidOperation:
            return str(value).strip()
    return str(value).strip()


def fingerprint_field_names(columns: dict[str, int], field_names: tuple[str, ...]) -> list[str]:
    return [field_name for field_name in field_names if field_name in columns]


def row_fingerprint_from_sheet(
    ws,
    columns: dict[str, int],
    row_number: int,
    field_names: tuple[str, ...],
) -> tuple[str, ...] | None:
    names = fingerprint_field_names(columns, field_names)
    values = [fingerprint_value(ws.cell(row_number, columns[field_name]).value) for field_name in names]
    return tuple(values) if any(values) else None


def row_fingerprint_from_values(
    fields: dict[str, Any],
    columns: dict[str, int],
    values: dict[str, Any],
    field_names: tuple[str, ...],
    value_for_field: ValueForField,
) -> tuple[str, ...] | None:
    fingerprint_values = []
    for field_name in fingerprint_field_names(columns, field_names):
        spec = fields.get(field_name, {})
        value = value_for_field(field_name, spec if isinstance(spec, dict) else {}, values, 2)
        fingerprint_values.append(fingerprint_value(value))
    return tuple(fingerprint_values) if any(fingerprint_values) else None


def existing_row_fingerprints(ws, columns: dict[str, int], field_names: tuple[str, ...]) -> set[tuple[str, ...]]:
    fingerprints: set[tuple[str, ...]] = set()
    for row_number in range(2, ws.max_row + 1):
        fingerprint = row_fingerprint_from_sheet(ws, columns, row_number, field_names)
        if fingerprint is not None:
            fingerprints.add(fingerprint)
    return fingerprints


def existing_row_fingerprint_rows(ws, columns: dict[str, int], field_names: tuple[str, ...]) -> dict[tuple[str, ...], int]:
    fingerprints: dict[tuple[str, ...], int] = {}
    for row_number in range(2, ws.max_row + 1):
        fingerprint = row_fingerprint_from_sheet(ws, columns, row_number, field_names)
        if fingerprint is not None and fingerprint not in fingerprints:
            fingerprints[fingerprint] = row_number
    return fingerprints


def first_row_by_invoice_number(ws, columns: dict[str, int], invoice_no: str | None) -> int | None:
    wanted = fingerprint_value(invoice_no)
    if not wanted:
        return None
    candidate_columns = [
        column
        for field_name in ("invoice_no", "digital_invoice_no")
        if (column := columns.get(field_name))
    ]
    for row_number in range(2, ws.max_row + 1):
        for column in candidate_columns:
            if fingerprint_value(ws.cell(row_number, column).value) == wanted:
                return row_number
    return None


def sheet_text(ws, columns: dict[str, int], row_number: int, field_name: str) -> str | None:
    column = columns.get(field_name)
    if not column:
        return None
    value = ws.cell(row_number, column).value
    if value in {None, ""}:
        return None
    return str(value)


def existing_row_context(ws, columns: dict[str, int], row_number: int | None) -> dict[str, Any]:
    if row_number is None:
        return {}
    invoice_no = sheet_text(ws, columns, row_number, "invoice_no") or sheet_text(
        ws,
        columns,
        row_number,
        "digital_invoice_no",
    )
    return {
        "excel_row": row_number,
        "source_file": sheet_text(ws, columns, row_number, "source_file"),
        "ticket_id": sheet_text(ws, columns, row_number, "draft_row_id"),
        "invoice_no": invoice_no,
        "item_name": sheet_text(ws, columns, row_number, "item_name"),
        "line_amount": sheet_text(ws, columns, row_number, "line_amount"),
        "line_total_with_tax": sheet_text(ws, columns, row_number, "line_total_with_tax"),
    }
