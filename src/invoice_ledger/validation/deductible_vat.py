"""Config-driven deductible VAT split rules."""

from __future__ import annotations

from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any

import yaml

from ..contracts import InvoiceItem, InvoiceRecord, RecognitionStatus, normalize_amount
from ..schema.schema_loader import PROJECT_ROOT


def _load_rules_from_path(path_text: str | None) -> list[dict[str, Any]]:
    if not path_text:
        return []
    path = Path(path_text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    with path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)
    rules = loaded.get("rules", []) if isinstance(loaded, dict) else []
    return [rule for rule in rules if isinstance(rule, dict)]


def _configured_rules(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not config or config.get("enabled") is not True:
        return []
    inline_rules = config.get("rules", [])
    if isinstance(inline_rules, list) and inline_rules:
        return [rule for rule in inline_rules if isinstance(rule, dict)]
    return _load_rules_from_path(config.get("rules_path"))


def _rate_decimal(rate: str) -> Decimal:
    return Decimal(rate.strip().replace("%", "")) / Decimal("100")


def _tax_inclusive_split(total: Decimal, rate: str) -> tuple[Decimal, Decimal]:
    rate_decimal = _rate_decimal(rate)
    tax = normalize_amount(total / (Decimal("1.00") + rate_decimal) * rate_decimal)
    amount = normalize_amount(total - tax)
    return amount or Decimal("0.00"), tax or Decimal("0.00")


def _line_total(item: InvoiceItem) -> Decimal | None:
    return item.line_total_with_tax or item.line_amount


def _mark_review_required(record: InvoiceRecord, message: str) -> None:
    record.quality.status = RecognitionStatus.REVIEW_REQUIRED
    record.quality.confidence = min(record.quality.confidence, 0.7)
    existing = record.quality.remark
    record.quality.remark = message if not existing or existing == message else f"{message}; {existing}"


def _has_existing_invoice_tax(record: InvoiceRecord) -> bool:
    return record.invoice.tax_total is not None and record.invoice.tax_total != Decimal("0.00")


def _has_existing_item_tax(record: InvoiceRecord) -> bool:
    return any(
        item.line_tax_amount is not None and item.line_tax_amount != Decimal("0.00")
        for item in record.items
    )


def _mark_negative_values_for_review(record: InvoiceRecord) -> bool:
    negative_items = [
        item.item_name or f"line {item.line_no}"
        for item in record.items
        if (_line_total(item) is not None and _line_total(item) < Decimal("0.00"))
        or (item.line_tax_amount is not None and item.line_tax_amount < Decimal("0.00"))
    ]
    negative_invoice_tax = record.invoice.tax_total is not None and record.invoice.tax_total < Decimal("0.00")
    if not negative_items and not negative_invoice_tax:
        return False
    detail = "、".join(negative_items).strip("、") if negative_items else "发票税额"
    _mark_review_required(record, f"待复核：可抵扣税额规则遇到负数项目 {detail}".strip())
    return True


def _matches_rule(record: InvoiceRecord, rule: dict[str, Any]) -> bool:
    schema_ids = rule.get("schema_ids", [])
    if not (isinstance(schema_ids, list) and record.schema_id in schema_ids):
        return False
    variant_ids = rule.get("variant_ids")
    if isinstance(variant_ids, list) and variant_ids:
        return record.variant_id in variant_ids
    return True


def _item_name_matches(item: InvoiceItem, names: list[str]) -> bool:
    item_name = item.item_name or ""
    return any(name in item_name for name in names)


def _split_item(item: InvoiceItem, rate: str) -> None:
    total = _line_total(item)
    if total is None:
        return
    amount, tax = _tax_inclusive_split(total, rate)
    item.tax_rate = rate
    item.line_amount = amount
    item.line_tax_amount = tax
    item.line_total_with_tax = total


def _split_items_as_group(items: list[InvoiceItem], rate: str) -> bool:
    item_totals = [(item, _line_total(item)) for item in items]
    if any(total is None for _item, total in item_totals):
        return False
    if any(total is not None and total < Decimal("0.00") for _item, total in item_totals):
        return False
    group_total = sum((total or Decimal("0.00")) for _item, total in item_totals)
    _group_amount, group_tax = _tax_inclusive_split(group_total, rate)
    rate_decimal = _rate_decimal(rate)
    target_cents = int((group_tax * Decimal("100")).to_integral_value())
    allocations: list[tuple[int, Decimal, InvoiceItem, Decimal]] = []
    for index, (item, total) in enumerate(item_totals):
        if total is None:
            return False
        raw_cents = total / (Decimal("1.00") + rate_decimal) * rate_decimal * Decimal("100")
        floor_cents = int(raw_cents.to_integral_value(rounding=ROUND_FLOOR))
        allocations.append((floor_cents, raw_cents - Decimal(floor_cents), item, total))

    remaining_cents = target_cents - sum(floor_cents for floor_cents, _remainder, _item, _total in allocations)
    if remaining_cents < 0:
        return False
    allocated_cents = [floor_cents for floor_cents, _remainder, _item, _total in allocations]
    for index in sorted(range(len(allocations)), key=lambda item_index: allocations[item_index][1], reverse=True):
        if remaining_cents <= 0:
            break
        allocated_cents[index] += 1
        remaining_cents -= 1

    for cents, (_floor_cents, _remainder, item, total) in zip(allocated_cents, allocations):
        tax = Decimal(cents) / Decimal("100")
        item.tax_rate = rate
        item.line_tax_amount = tax
        item.line_amount = normalize_amount(total - tax)
        item.line_total_with_tax = total
    return True


def _zero_tax_item(item: InvoiceItem) -> None:
    total = _line_total(item)
    if total is None:
        return
    item.line_amount = total
    item.line_tax_amount = Decimal("0.00")
    item.line_total_with_tax = total


def _apply_component_rule(record: InvoiceRecord, rule: dict[str, Any]) -> bool:
    taxable_names = [str(name) for name in rule.get("taxable_item_names", [])]
    if not taxable_names:
        return False
    non_taxable_names = [str(name) for name in rule.get("non_taxable_item_names", [])]
    rate = str(rule["rate"])
    taxable_items: list[InvoiceItem] = []
    non_taxable_items: list[InvoiceItem] = []
    for item in record.items:
        if _item_name_matches(item, taxable_names):
            taxable_items.append(item)
        elif non_taxable_names:
            if not _item_name_matches(item, non_taxable_names):
                _mark_review_required(
                    record,
                    f"待复核：可抵扣税额规则未覆盖项目 {item.item_name or ''}".strip(),
                )
                return False
            non_taxable_items.append(item)
        else:
            non_taxable_items.append(item)
    negative_items = [item for item in taxable_items if (_line_total(item) or Decimal("0.00")) < Decimal("0.00")]
    if negative_items:
        names = "、".join(item.item_name or "" for item in negative_items).strip("、")
        _mark_review_required(record, f"待复核：可抵扣税额规则遇到负数项目 {names}".strip())
        return False
    if not taxable_items or not _split_items_as_group(taxable_items, rate):
        return False
    for item in non_taxable_items:
        _zero_tax_item(item)
    return True


def _apply_total_rule(record: InvoiceRecord, rule: dict[str, Any]) -> bool:
    rate = str(rule["rate"])
    return _split_items_as_group(record.items, rate)


def _refresh_invoice_totals(record: InvoiceRecord) -> None:
    amount_total = sum((item.line_amount or Decimal("0.00")) for item in record.items)
    tax_total = sum((item.line_tax_amount or Decimal("0.00")) for item in record.items)
    total_with_tax = sum((_line_total(item) or Decimal("0.00")) for item in record.items)
    record.invoice.amount_total = normalize_amount(amount_total)
    record.invoice.tax_total = normalize_amount(tax_total)
    record.invoice.total_with_tax = normalize_amount(total_with_tax)


def apply_deductible_vat_rules(
    record: InvoiceRecord,
    config: dict[str, Any] | None = None,
) -> InvoiceRecord:
    """Apply configured deductible VAT split rules before validation."""

    if not record.items:
        return record

    for rule in _configured_rules(config):
        if not _matches_rule(record, rule):
            continue
        if _mark_negative_values_for_review(record):
            return record
        if _has_existing_invoice_tax(record) or _has_existing_item_tax(record):
            return record
        method = rule.get("method")
        if method == "split_tax_inclusive_components":
            applied = _apply_component_rule(record, rule)
        elif method == "split_tax_inclusive_total":
            applied = _apply_total_rule(record, rule)
        else:
            applied = False
        if applied:
            _refresh_invoice_totals(record)
            return record

    return record
