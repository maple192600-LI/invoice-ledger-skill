"""铁路电子客票字段抽取。"""

from __future__ import annotations

from typing import Any

from ...contracts import (
    FieldCandidate,
    TextUnits,
)

from .._helpers import _add, _first_regex, _joined, _money_after_labels, _number_after_label, _schema_output_default
from .._invoice_fields import _date_after_labels
from .._line_items import _add_json_item
from .._parties import _company_name_from_text, _railway_station_names
from .._totals import _add_non_tax_totals


def _railway_coord_value(text_units, label, pattern, clean=False):
    """演示版 span 分离致文本流抓空时，按 span 坐标补 invoice_no/票价（只增不减）。"""
    try:
        from .._line_item_ocr_table import _read_pdf_spans
        from ..field_candidates import _find_value_by_coordinate
        from .._helpers import _clean_money
        spans = _read_pdf_spans(text_units.source_file, list(text_units.page_range))
    except Exception:
        return None
    hit = _find_value_by_coordinate(spans, label, "right", pattern)
    if not hit:
        return None
    return _clean_money(hit) if clean else hit


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    text = _joined(lines)
    invoice_type = next(
        (line.strip() for line in lines if "铁路电子客票" in line),
        "电子发票（铁路电子客票）",
    )
    _add(fields, "invoice_type", invoice_type, "railway ticket title", 0.96)

    invoice_no = _number_after_label(lines, "发票号码", r"\d{8,20}")
    if not invoice_no and text_units.source_file:
        invoice_no = _railway_coord_value(text_units, "发票号码", r"\d{8,20}")
    if invoice_no:
        _add(fields, "invoice_no", invoice_no, "railway invoice number", 0.96)

    invoice_date = _date_after_labels(lines, ["开票日期"])
    if invoice_date:
        _add(fields, "invoice_date", invoice_date, "railway invoice date", 0.98)

    buyer_tax_id = _first_regex(r"统一社会信用代码\s*[:：]?\s*([0-9A-Z]{15,20})", text)
    if buyer_tax_id:
        _add(fields, "buyer_tax_id", buyer_tax_id, "railway buyer tax id", 0.94)
    buyer_name = _company_name_from_text(text)
    if buyer_name:
        _add(fields, "buyer_name", buyer_name, "railway buyer company", 0.92)

    seller_name = "中国铁路" if "中国铁路" in text else _schema_output_default(schema, "seller_name", "")
    if seller_name:
        _add(fields, "seller_name", seller_name, "railway seller default", 0.88)

    total = _money_after_labels(lines, ["退票费", "票价"])
    if not total and text_units.source_file:
        total = _railway_coord_value(text_units, "退票费", r"[¥￥]?\s*-?\d[\d,]*\.\d+", clean=True)
        if not total:
            total = _railway_coord_value(text_units, "票价", r"[¥￥]?\s*-?\d[\d,]*\.\d+", clean=True)
    _add_non_tax_totals(fields, total, "railway ticket amount", 0.94)

    if not total:
        return
    stations = _railway_station_names(lines)
    if any("退票费" in line for line in lines):
        item_name = "铁路退票费"
    elif len(stations) >= 2:
        item_name = f"{stations[0]}至{stations[1]}铁路客票"
    else:
        item_name = "铁路客票"
    _add_json_item(
        fields,
        {
            "item_name": item_name,
            "spec_model": None,
            "unit": "张",
            "quantity": "1",
            "unit_price": total,
            "line_amount": total,
            "tax_rate": None,
            "line_tax_amount": "0.00",
            "line_total_with_tax": total,
        },
        1,
        "railway ticket item",
        0.9,
    )

