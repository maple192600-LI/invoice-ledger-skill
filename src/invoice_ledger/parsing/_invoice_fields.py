"""发票号码、代码、类型、开票日期等核心标识字段抽取。"""

from __future__ import annotations

import re
from typing import Any

from ..contracts import (
    FieldCandidate,
    normalize_date,
)
from ..schema.schema_loader import field_aliases

from ._helpers import DATE_RE, LONG_NUMBER_RE, _add, _is_identity_number_context, _is_identity_number_line, _is_secondary_identity_line, _is_secondary_invoice_identity_context, _is_tax_id_value


def _extract_invoice_number(lines: list[str], fields: dict[str, list[FieldCandidate]], schema: dict[str, Any]) -> None:
    label_number_candidates: list[tuple[str, str]] = []
    aliases = field_aliases(schema, "invoice_no")
    for index, line in enumerate(lines):
        if (
            _is_secondary_invoice_identity_context(line)
            or _is_secondary_identity_line(lines, index)
            or _is_identity_number_context(line)
        ):
            continue
        if any(alias in line for alias in aliases):
            for match in LONG_NUMBER_RE.finditer(line):
                value = match.group(0)
                if not _is_tax_id_value(value):
                    label_number_candidates.append((value, line))
            for lookahead_index in range(index + 1, min(len(lines), index + 6)):
                lookahead = lines[lookahead_index]
                if _is_secondary_invoice_identity_context(lookahead):
                    continue
                if _is_secondary_identity_line(lines, lookahead_index):
                    continue
                if _is_identity_number_line(lines, lookahead_index):
                    continue
                for match in LONG_NUMBER_RE.finditer(lookahead):
                    value = match.group(0)
                    if not _is_tax_id_value(value):
                        label_number_candidates.append((value, lookahead))
                        break
                if label_number_candidates:
                    break

    for index, line in enumerate(lines):
        if not DATE_RE.search(line):
            continue
        previous = []
        for candidate_index in range(max(0, index - 3), index):
            candidate = lines[candidate_index].strip()
            if (
                candidate
                and not _is_secondary_invoice_identity_context(candidate)
                and not _is_secondary_identity_line(lines, candidate_index)
                and not _is_identity_number_line(lines, candidate_index)
            ):
                previous.append(candidate)
        standalone_numbers = [candidate for candidate in previous if re.fullmatch(r"\d{8,20}", candidate)]
        if standalone_numbers:
            label_number_candidates.append((standalone_numbers[-1], standalone_numbers[-1]))

    for value, evidence in label_number_candidates:
        _add(fields, "invoice_no", value, evidence, 0.95)


def _extract_invoice_code(lines: list[str], fields: dict[str, list[FieldCandidate]], schema: dict[str, Any]) -> None:
    aliases = field_aliases(schema, "invoice_code")
    for index, line in enumerate(lines):
        if (
            _is_secondary_invoice_identity_context(line)
            or _is_secondary_identity_line(lines, index)
            or _is_identity_number_context(line)
        ):
            continue
        if not any(alias in line for alias in aliases):
            continue
        for match in re.finditer(r"\b\d{10,12}\b", line):
            _add(fields, "invoice_code", match.group(0), line, 0.9)
        for lookahead_index in range(index + 1, min(len(lines), index + 8)):
            lookahead = lines[lookahead_index]
            if _is_secondary_invoice_identity_context(lookahead):
                continue
            if _is_secondary_identity_line(lines, lookahead_index):
                continue
            if _is_identity_number_line(lines, lookahead_index):
                continue
            match = re.fullmatch(r"\d{10,12}", lookahead.strip())
            if match:
                _add(fields, "invoice_code", match.group(0), lookahead, 0.9)
                return

    for index, line in enumerate(lines):
        if not DATE_RE.search(line):
            continue
        previous = []
        for candidate_index in range(max(0, index - 3), index):
            candidate = lines[candidate_index].strip()
            if (
                candidate
                and not _is_secondary_identity_line(lines, candidate_index)
                and not _is_identity_number_line(lines, candidate_index)
            ):
                previous.append(candidate)
        standalone_numbers = [candidate for candidate in previous if re.fullmatch(r"\d{8,20}", candidate)]
        if len(standalone_numbers) >= 2 and len(standalone_numbers[-2]) >= 10:
            _add(fields, "invoice_code", standalone_numbers[-2], standalone_numbers[-2], 0.85)
            return


def _extract_invoice_type(lines: list[str], fields: dict[str, list[FieldCandidate]], schema: dict[str, Any]) -> None:
    aliases = field_aliases(schema, "invoice_type")
    for line in lines[:10]:
        if any(alias in line for alias in aliases):
            _add(fields, "invoice_type", line, line, 0.8)
            return


def _extract_dates(lines: list[str], fields: dict[str, list[FieldCandidate]], schema: dict[str, Any]) -> None:
    aliases = field_aliases(schema, "invoice_date")
    for index, line in enumerate(lines):
        for match in DATE_RE.finditer(line):
            try:
                value = normalize_date(match.group(0))
            except ValueError:
                continue
            nearby = "\n".join(lines[max(0, index - 3) : index + 1])
            previous = [candidate.strip() for candidate in lines[max(0, index - 3) : index] if candidate.strip()]
            previous_numbers = [candidate for candidate in previous if re.fullmatch(r"\d{8,20}", candidate)]
            confidence = 0.98 if any(alias in nearby for alias in aliases) or len(previous_numbers) >= 2 else 0.7
            _add(fields, "invoice_date", value, line, confidence)


def _date_after_labels(lines: list[str], labels: list[str]) -> str | None:
    for index, line in enumerate(lines):
        if not any(label in line for label in labels):
            continue
        for candidate in [line, *lines[index + 1 : index + 4]]:
            match = DATE_RE.search(candidate)
            if not match:
                continue
            try:
                return normalize_date(match.group(0))
            except ValueError:
                continue
    return None

