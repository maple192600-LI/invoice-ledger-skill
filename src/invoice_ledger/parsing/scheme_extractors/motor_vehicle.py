"""数电机动车销售统一发票字段抽取。"""

from __future__ import annotations

from decimal import Decimal
import re
from typing import Any

from ...contracts import FieldCandidate, TextUnits
from ...input_profile.text_units import logical_text_lines
from .._helpers import TAX_RATE_RE, _add, _clean_money
from .._line_items import _add_json_item
from .._line_item_ocr_table import _read_pdf_spans


TAX_ID_RE = re.compile(r"(?<![0-9A-Z])([0-9A-Z]{15,20})(?![0-9A-Z])")
MONEY_2_RE = re.compile(r"-?\d[\d,]*\.\d{2}")

# 坐标兜底 span 原文 pattern（金额命中后必须 _clean_money 去千分位逗号，否则 Decimal 崩）
_MV_MONEY = MONEY_2_RE.pattern
_MV_NAME = r"[一-鿿][一-鿿（）()A-Za-z0-9－-]{2,}"
_MV_TAX_ID = r"[0-9A-Z]{15,20}"
_MV_VEHICLE = r"[一-鿿][一-鿿A-Za-z0-9－-]{1,}"
_MV_SPEC = r"[一-鿿A-Za-z0-9][一-鿿A-Za-z0-9－-]{1,}"
_MV_ORIGIN = r"[一-鿿][一-鿿A-Za-z0-9]{1,}"
_MV_CODE = r"[A-Z0-9\-]{3,}"
_MV_VIN = r"[A-Z0-9]{4,}"
_MV_RATE = r"\d{1,2}%"
_MV_MONEY_FIELDS = frozenset({"amount_total", "tax_total", "total_with_tax"})


def _line(lines: list[str], label: str) -> str | None:
    return next((line for line in lines if label in line), None)


def _money(line: str | None) -> str | None:
    if not line:
        return None
    match = MONEY_2_RE.search(line)
    return _clean_money(match.group(0)) if match else None


def _tax_id(line: str | None) -> str | None:
    if not line:
        return None
    match = TAX_ID_RE.search(line)
    return match.group(1) if match else None


def _value_after(line: str | None, label: str) -> str | None:
    if not line or label not in line:
        return None
    value = line.split(label, 1)[1].strip(" ：:")
    token = value.split()[0] if value else ""
    return token.strip("：:；;，,") or None


def _motor_context(cert: str | None, origin: str | None, engine: str | None, vehicle_id: str | None) -> str | None:
    context = "；".join(
        f"{label}：{value}"
        for label, value in (
            ("合格证号", cert),
            ("产地", origin),
            ("发动机号码", engine),
            ("车辆识别代号/车架号码", vehicle_id),
        )
        if value
    )
    return context or None


def _build_motor_item(
    item_name: str | None,
    spec: str | None,
    origin: str | None,
    cert: str | None,
    engine: str | None,
    vehicle_id: str | None,
    rate: str | None,
    amount: str | None,
    tax: str | None,
) -> dict[str, Any] | None:
    if not (item_name and amount and tax):
        return None
    return {
        "item_name": item_name,
        "context_remark": _motor_context(cert, origin, engine, vehicle_id),
        "spec_model": spec,
        "unit": None,
        "quantity": None,
        "unit_price": None,
        "line_amount": amount,
        "tax_rate": rate,
        "line_tax_amount": tax,
        "line_total_with_tax": _clean_money(str(Decimal(amount) + Decimal(tax))),
    }


def extract(
    text_units: TextUnits,
    lines: list[str],
    fields: dict[str, list[FieldCandidate]],
    schema: dict[str, Any],
) -> None:
    visual_lines = [line.text for line in logical_text_lines(text_units)]
    title = _line(visual_lines, "电子发票（机动车销售统一发票）")
    if title:
        _add(fields, "invoice_type", "电子发票（机动车销售统一发票）", title, 0.98)

    buyer_line = _line(visual_lines, "购买方名称")
    seller_line = _line(visual_lines, "销货单位名称")
    seller_tax_line = _line(visual_lines, "纳税人识别号")
    buyer_name = _value_after(buyer_line, "购买方名称")
    seller_name = _value_after(seller_line, "销货单位名称")
    for field_name, value, evidence in (
        ("buyer_name", buyer_name, buyer_line),
        ("buyer_tax_id", _tax_id(buyer_line), buyer_line),
        ("seller_name", seller_name, seller_line),
        ("seller_tax_id", _tax_id(seller_tax_line), seller_tax_line),
    ):
        _add(fields, field_name, value, evidence or "", 0.96)

    amount_line = _line(visual_lines, "不含税价")
    tax_line = _line(visual_lines, "增值税税率")
    total_line = _line(visual_lines, "价税合计")
    amount = _money(amount_line)
    tax = _money(tax_line)
    total = _money(total_line)
    for field_name, value, evidence in (
        ("amount_total", amount, amount_line),
        ("tax_total", tax, tax_line),
        ("total_with_tax", total, total_line),
    ):
        _add(fields, field_name, value, evidence or "", 0.98)

    vehicle_line = _line(visual_lines, "车辆类型")
    vehicle_values = (
        vehicle_line.split("车辆类型", 1)[1].split("产地", 1)[0].split()
        if vehicle_line and "车辆类型" in vehicle_line
        else []
    )
    item_name = vehicle_values[0] if vehicle_values else None
    spec_model = vehicle_values[1] if len(vehicle_values) > 1 else None
    origin = vehicle_values[2] if len(vehicle_values) > 2 else None
    certificate_line = _line(visual_lines, "合格证号")
    engine_line = _line(visual_lines, "发动机号码")
    certificate = _value_after(certificate_line, "合格证号")
    engine = _value_after(engine_line, "发动机号码")
    vehicle_id = _value_after(engine_line, "车辆识别代号/车架号码")
    rate_match = TAX_RATE_RE.search(tax_line or "")
    tax_rate = rate_match.group(1) if rate_match else None
    item = _build_motor_item(item_name, spec_model, origin, certificate, engine, vehicle_id, tax_rate, amount, tax)
    if item:
        _add_json_item(fields, item, 1, "机动车车辆类型及票面金额结构", 0.96)

    # 演示版坐标兜底：span 分离致文本流抓空，按 span 坐标补（只增不减，已有值不碰）
    if text_units.source_file:
        try:
            from ..field_candidates import _find_value_by_coordinate
            mv_spans = _read_pdf_spans(text_units.source_file, list(text_units.page_range))
        except Exception:
            mv_spans = []
        if mv_spans:
            for field_name, label, pattern in (
                ("buyer_name", "购买方名称", _MV_NAME),
                ("buyer_tax_id", "统一社会信用代码", _MV_TAX_ID),
                ("seller_name", "销货单位名称", _MV_NAME),
                ("seller_tax_id", "纳税人识别号", _MV_TAX_ID),
                ("amount_total", "不含税价", _MV_MONEY),
                ("tax_total", "税额", _MV_MONEY),
                ("total_with_tax", "价税合计", _MV_MONEY),
            ):
                if not fields.get(field_name):
                    value = _find_value_by_coordinate(mv_spans, label, "right", pattern)
                    if value and field_name in _MV_MONEY_FIELDS:
                        value = _clean_money(value)
                    if value:
                        _add(fields, field_name, value, "机动车坐标兜底", 0.9)
            if not fields.get("items"):
                mv_item = _build_motor_item(
                    _find_value_by_coordinate(mv_spans, "车辆类型", "right", _MV_VEHICLE),
                    _find_value_by_coordinate(mv_spans, "厂牌型号", "right", _MV_SPEC),
                    _find_value_by_coordinate(mv_spans, "产地", "right", _MV_ORIGIN),
                    _find_value_by_coordinate(mv_spans, "合格证号", "right", _MV_CODE),
                    _find_value_by_coordinate(mv_spans, "发动机号码", "right", _MV_CODE),
                    _find_value_by_coordinate(mv_spans, "车辆识别代号/车架号码", "right", _MV_VIN),
                    _find_value_by_coordinate(mv_spans, "增值税税率", "right", _MV_RATE),
                    next((c.value for c in fields.get("amount_total", [])), None),
                    next((c.value for c in fields.get("tax_total", [])), None),
                )
                if mv_item:
                    _add_json_item(fields, mv_item, 1, "机动车坐标兜底明细", 0.9)
