"""Field candidate, schema config, and identity-context helpers."""

from __future__ import annotations

import re
from typing import Any

from ..contracts import FieldCandidate, TextUnits, normalize_date
from ..input_profile.text_units import LogicalTextLine
from ._helper_primitives import (
    DATE_RE,
    IDENTITY_NUMBER_CONTEXT_TERMS,
    LONG_NUMBER_RE,
    TAX_ID_EXCLUDED_CONTEXT_TERMS,
    TAX_ID_RE,
    _compact_number,
    _has_chinese,
    _money_values,
)


def _candidate(value: Any, evidence: str, confidence: float = 0.8, risk: list[str] | None = None) -> FieldCandidate:
    return FieldCandidate(
        value=str(value),
        source="text_unit",
        confidence=confidence,
        evidence=evidence,
        risk=risk or [],
    )


def _append_unique_risk(candidate: FieldCandidate, risk: str) -> None:
    if risk not in candidate.risk:
        candidate.risk.append(risk)


def _add(fields: dict[str, list[FieldCandidate]], field_name: str, value: Any, evidence: str, confidence: float = 0.8, risk: list[str] | None = None) -> None:
    if value is None or value == "":
        return
    text_value = str(value)
    existing = {candidate.value for candidate in fields.setdefault(field_name, [])}
    if text_value not in existing:
        fields[field_name].append(_candidate(text_value, evidence, confidence, risk))


def _role_near_line(lines: list[str], index: int) -> str | None:
    window = lines[max(0, index - 6):index + 1]
    for line in reversed(window):
        if any(term in line for term in TAX_ID_EXCLUDED_CONTEXT_TERMS):
            continue
        if "购买方" in line or "购 买 方" in line:
            return "buyer"
        if "销售方" in line or "销 售 方" in line:
            return "seller"
    return None


def _add_ocr_confidence_risks(text_units: TextUnits, fields: dict[str, list[FieldCandidate]]) -> None:
    if text_units.source != "ocr":
        return
    confidence_by_text = {
        unit.text.strip(): unit.confidence
        for unit in text_units.units
        if unit.text.strip() and unit.confidence is not None
    }
    low_confidence_texts = [
        unit.text.strip()
        for unit in text_units.units
        if unit.text.strip() and unit.confidence is not None and unit.confidence < 0.6
    ]
    normalized_low_confidence_texts = {
        str(_compact_number(text))
        for text in low_confidence_texts
        if len(str(_compact_number(text))) >= 4
    }
    for field_candidates in fields.values():
        for candidate in field_candidates:
            confidence = confidence_by_text.get(candidate.evidence.strip())
            if confidence is not None and confidence < 0.6:
                _append_unique_risk(candidate, "low_confidence_ocr")
                continue
            normalized_value = str(_compact_number(candidate.value))
            if any(
                candidate.value
                and (
                    candidate.value in text
                    or (len(text) >= 4 and text in candidate.value)
                )
                for text in low_confidence_texts
            ) or any(
                normalized_text in normalized_value
                for normalized_text in normalized_low_confidence_texts
            ):
                _append_unique_risk(candidate, "low_confidence_ocr")


def _lines(text_units: TextUnits) -> list[str]:
    return [unit.text.strip() for unit in text_units.units if unit.text.strip()]


def _joined(lines: list[str]) -> str:
    return "\n".join(lines)


def _date_candidates(lines: list[str]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for line in lines:
        for match in DATE_RE.finditer(line):
            try:
                found.append((normalize_date(match.group(0)) or "", line))
            except ValueError:
                continue
    return found


def _tax_ids(lines: list[str]) -> list[tuple[str, int, str]]:
    found: list[tuple[str, int, str]] = []
    for index, line in enumerate(lines):
        if any(term in line for term in TAX_ID_EXCLUDED_CONTEXT_TERMS):
            continue
        for match in TAX_ID_RE.finditer(line):
            value = match.group(0)
            if _is_tax_id_value(value):
                found.append((value, index, line))
    return found


def _is_tax_id_value(value: str) -> bool:
    return any(ch.isalpha() for ch in value) or len(value) in {15, 18}


def _line_confidence(line: LogicalTextLine, default: float = 0.96) -> float:
    if line.confidence is None:
        return default
    return max(0.75, min(float(line.confidence), default))


def _configured_set(schema: dict[str, Any], section: str, key: str, default: set[str]) -> set[str]:
    config = schema.get(section, {})
    if not isinstance(config, dict):
        return set(default)
    value = config.get(key, [])
    if not isinstance(value, list):
        return set(default)
    return {str(item) for item in value} or set(default)


def _schema_section(schema: dict[str, Any], section: str) -> dict[str, Any]:
    config = schema.get(section, {})
    return config if isinstance(config, dict) else {}


def _nested_section(schema: dict[str, Any], section: str, key: str) -> dict[str, Any]:
    parent = _schema_section(schema, section)
    config = parent.get(key, {})
    return config if isinstance(config, dict) else {}


def _cfg_float(config: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_int(config: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(config.get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_terms(config: dict[str, Any], key: str, default: list[str]) -> list[str]:
    value = config.get(key, default)
    if not isinstance(value, list):
        return list(default)
    return [str(item) for item in value]


def _required_float(config: dict[str, Any], key: str) -> float:
    if key not in config:
        raise ValueError(f"Missing required layout setting: {key}")
    try:
        return float(config[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric layout setting: {key}") from exc


def _required_int(config: dict[str, Any], key: str) -> int:
    if key not in config:
        raise ValueError(f"Missing required layout setting: {key}")
    try:
        return int(config[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer layout setting: {key}") from exc


def _required_terms(config: dict[str, Any], key: str) -> list[str]:
    value = config.get(key)
    if not isinstance(value, list):
        raise ValueError(f"Missing required layout setting: {key}")
    return [str(item) for item in value]


def _first_regex(pattern: str, text: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _clean_label_value(value: str) -> str:
    return value.strip().strip("：:;；,，.。").strip()


def _value_after_label(lines: list[str], labels: list[str], lookahead: int = 4) -> str | None:
    for index, line in enumerate(lines):
        for label in labels:
            if label not in line:
                continue
            value = _clean_label_value(line.split(label, 1)[-1])
            if value:
                return value
            for candidate in lines[index + 1 : index + lookahead + 1]:
                stripped = _clean_label_value(candidate)
                if stripped and _has_chinese(stripped):
                    return stripped
    return None


def _number_after_label(lines: list[str], label: str, pattern: str = r"\d{8,20}") -> str | None:
    bounded_pattern = rf"(?<![0-9A-Z])({pattern})(?![0-9A-Z])"
    for index, line in enumerate(lines):
        if label not in line:
            continue
        if _is_secondary_invoice_identity_context(line) or _is_identity_number_context(line):
            continue
        if _is_direct_secondary_identity_line(lines, index):
            continue
        inline_value = _clean_label_value(line.split(label, 1)[-1])
        inline = re.search(bounded_pattern, inline_value)
        if inline:
            return inline.group(1)
        if _is_secondary_identity_line(lines, index):
            continue
        for candidate_index in range(index + 1, min(len(lines), index + 4)):
            candidate = lines[candidate_index]
            if _is_secondary_invoice_identity_context(candidate) or _is_identity_number_context(candidate):
                continue
            candidate_value = _clean_label_value(candidate.split(label, 1)[-1] if label in candidate else candidate)
            if label in candidate and _is_direct_secondary_identity_line(lines, candidate_index):
                continue
            if _is_secondary_identity_line(lines, candidate_index) and label not in candidate:
                continue
            if _is_identity_number_line(lines, candidate_index) and label not in candidate:
                continue
            if re.fullmatch(pattern, candidate_value):
                return candidate_value
            if label in candidate:
                match = re.search(bounded_pattern, candidate_value)
                if match:
                    return match.group(1)
    return None


def _money_after_labels(lines: list[str], labels: list[str]) -> str | None:
    for index, line in enumerate(lines):
        if not any(label in line for label in labels):
            continue
        for candidate in [line, *lines[index + 1 : index + 3]]:
            values = _money_values(candidate)
            if values:
                return values[-1]
    return None


def _schema_output_default(schema: dict[str, Any], field_name: str, fallback: str) -> str:
    output_defaults = schema.get("output_defaults", {})
    if isinstance(output_defaults, dict) and output_defaults.get(field_name):
        return str(output_defaults[field_name])
    return fallback


def _is_secondary_invoice_identity_context(line: str) -> bool:
    compact = re.sub(r"\s+", "", line)
    return any(
        term in compact
        for term in (
            "原发票",
            "原蓝字",
            "对应蓝字",
            "红字信息",
            "红字发票",
            "红字发票信息",
            "红字发票信息表",
            "信息表编号",
            "冲红",
            "红冲",
        )
    )


def _is_secondary_identity_line(lines: list[str], index: int) -> bool:
    previous = [line.strip() for line in lines[max(0, index - 4):index] if line.strip()]
    return any(_is_secondary_invoice_identity_context(line) for line in previous)


def _is_direct_secondary_identity_line(lines: list[str], index: int) -> bool:
    for previous_index in range(index - 1, -1, -1):
        previous = lines[previous_index].strip()
        if previous:
            return _is_secondary_invoice_identity_context(previous) and LONG_NUMBER_RE.search(previous) is None
    return False


def _is_identity_number_line(lines: list[str], index: int) -> bool:
    if _is_identity_number_context(lines[index]):
        return True
    for previous_index in range(index - 1, -1, -1):
        previous = lines[previous_index].strip()
        if previous:
            return _is_identity_number_context(previous)
    return False


def _is_identity_number_context(line: str) -> bool:
    compact = re.sub(r"\s+", "", line).upper()
    return any(term in compact for term in IDENTITY_NUMBER_CONTEXT_TERMS)
