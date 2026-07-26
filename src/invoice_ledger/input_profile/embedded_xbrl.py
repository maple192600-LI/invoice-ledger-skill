"""A1 通道：PDF 内嵌 XBRL 检测与解析（财政部电子凭证标准），直读为 InvoiceRecord。

铁路电子客票（rai_issuer）已用真实样本验证；其他票种（atr/einv/efi/inv_tlf）按标准
应内嵌但本仓库无样本，detect_config_id 识别配置、to_invoice_record 留接口返回 None
以回退 B 轨（"无样本不宣称"）。零新依赖（PyMuPDF 取内嵌文件 + 标准库 xml.etree）。
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import xml.etree.ElementTree as ET

from .._paths import PROJECT_ROOT
from ..contracts import (
    InvoiceFields,
    InvoiceItem,
    InvoiceQuality,
    InvoiceRecord,
    InvoiceSource,
    RecognitionStatus,
)

XBRL_CONFIG_DIR = PROJECT_ROOT / "config" / "xbrl"
NIL_NS = "{http://www.w3.org/2001/XMLSchema-instance}nil"

# 命名空间 URI 末段 -> 配置 id（与 taxonomy 版本解耦，按末段匹配）
_NS_SEGMENT_TO_CONFIG = {
    "rai": "rai_issuer",
    "atr": "atr_issuer",
    "einv": "einv_ord_receiver",
    "inv_tlf": "inv_tlf_receiver",
    "efi": "efi",
}


def find_embedded_xbrl(doc) -> tuple[str, str] | None:
    """遍历内嵌文件，返回第一个可识别为 XBRL 的 (config_id, text)；否则 None。

    不依赖附件名后缀（实测铁路退票票内嵌文件 name="0"），按内容命名空间识别。
    """
    count = doc.embfile_count() if hasattr(doc, "embfile_count") else 0
    for index in range(count):
        try:
            raw = doc.embfile_get(index)
        except Exception:
            continue
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        config_id = detect_config_id(text)
        if config_id:
            return config_id, text
    return None


def detect_config_id(xbrl_text: str) -> str | None:
    for segment, config_id in _NS_SEGMENT_TO_CONFIG.items():
        if f"/{segment}" not in xbrl_text:
            continue
        if (XBRL_CONFIG_DIR / f"{config_id}.xml").exists():
            return config_id
    return None


def _load_config_fields(config_id: str) -> list[tuple[str, str, str]]:
    path = XBRL_CONFIG_DIR / f"{config_id}.xml"
    tree = ET.parse(path)
    fields: list[tuple[str, str, str]] = []
    for json_object in tree.iter("jsonObject"):
        for child in list(json_object):
            tag = child.tag.split("}")[-1]
            if tag == "jsonObject":
                continue
            fields.append((
                child.get("elementName", tag),
                child.get("elementNsName", ""),
                child.get("elementValueType", "string"),
            ))
        break
    return fields


def _convert(raw: str, value_type: str):
    raw = (raw or "").strip()
    if not raw:
        return None
    if value_type == "number":
        try:
            return Decimal(raw)
        except InvalidOperation:
            return None
    if value_type == "date":
        return raw[:10]
    return raw


def parse_xbrl(xbrl_text: str, config_id: str) -> dict:
    """按 config 字段定义提取 XBRL 实例值（按元素 localname 匹配，与 taxonomy 版本解耦）。"""
    config_fields = _load_config_fields(config_id)
    root = ET.fromstring(xbrl_text)
    values: dict[str, str] = {}
    for el in root.iter():
        local = el.tag.split("}")[-1]
        if not el.text or not el.text.strip():
            continue
        if el.get(NIL_NS) == "true":
            continue
        if local not in values:
            values[local] = el.text.strip()
    parsed: dict = {}
    for element_name, _ns, value_type in config_fields:
        if element_name in values:
            parsed[element_name] = _convert(values[element_name], value_type)
    return parsed


def _rate_to_percent(rate) -> str | None:
    if rate is None:
        return None
    try:
        d = Decimal(str(rate))
    except InvalidOperation:
        return None
    pct = (d * Decimal("100")).normalize()
    integral = pct.to_integral_value()
    return f"{integral}%" if integral == pct else f"{pct}%"


def _rai_to_invoice_record(parsed: dict, source: InvoiceSource, unit_id: str) -> InvoiceRecord:
    fare = parsed.get("Fare")  # 票价（含税 = 价税合计）
    amount_excl = parsed.get("TotalAmountExcludingTax")
    tax_amount = parsed.get("TaxAmount")
    tax_rate = _rate_to_percent(parsed.get("TaxRate"))
    business = (parsed.get("TypeOfBusiness") or "").strip()
    departure = parsed.get("DepartureStation")
    destination = parsed.get("DestinationStation")

    if business == "退":
        item_name = "铁路退票费"
        remark = "数据取自票面内嵌凭证数据；退票费进项抵扣请与主管税务机关确认。"
    else:
        item_name = f"{departure}至{destination}铁路客票" if departure and destination else "铁路客票"
        remark = "数据取自票面内嵌凭证数据。"

    items = [
        InvoiceItem(
            line_no=1,
            item_name=item_name,
            unit="张",
            quantity=Decimal("1"),
            line_amount=amount_excl,
            tax_rate=tax_rate,
            line_tax_amount=tax_amount,
            line_total_with_tax=fare,
        )
    ]
    return InvoiceRecord(
        invoice_unit_id=unit_id,
        schema_id="railway-ticket",
        variant_id="digital-railway-ticket",
        source=source,
        invoice=InvoiceFields(
            invoice_no=parsed.get("ElectronicInvoiceRailwayETicketNumber"),
            invoice_date=parsed.get("DateOfIssue"),
            buyer_name=parsed.get("NameOfPurchaser"),
            buyer_tax_id=parsed.get("UnifiedSocialCreditCodeOfPurchaser"),
            seller_name=parsed.get("NameOfSeller") or "中国铁路",
            invoice_type=parsed.get("TypeOfVoucher") or "电子发票（铁路电子客票）",
            amount_total=amount_excl,
            tax_total=tax_amount,
            total_with_tax=fare,
        ),
        items=items,
        quality=InvoiceQuality(status=RecognitionStatus.READY, confidence=0.95, remark=remark, data_source="structured"),
    )


def to_invoice_record(config_id: str, parsed: dict, source: InvoiceSource, unit_id: str) -> InvoiceRecord | None:
    if config_id == "rai_issuer":
        return _rai_to_invoice_record(parsed, source, unit_id)
    # atr/einv/efi/inv_tlf：按标准应内嵌但本仓库无真实样本，留接口不宣称，回退 B 轨。
    return None
