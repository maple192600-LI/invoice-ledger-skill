"""Primitive parsing helpers: constants, numeric/text checks, and geometry accessors."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re

from ..contracts import TextUnit, normalize_amount

TAX_ID_RE = re.compile(r"\b[0-9A-Z]{15,20}\b")
DATE_RE = re.compile(r"\d{4}\s*[年/-]\s*\d{1,2}\s*[月/-]\s*\d{1,2}\s*日?")
MONEY_RE = re.compile(r"[¥￥]?\s*(-?\d[\d,]*(?:\s*\.\s*\d+)?)")
LONG_NUMBER_RE = re.compile(r"\b\d{8,20}\b")
TAX_RATE_RE = re.compile(r"(\d+(?:\.\d+)?%|不征税|免税|零税率)")
DEFAULT_UNIT_TOKENS = {"个", "次", "张"}
DEFAULT_TEXTUAL_SPEC_TOKENS = set[str]()
NON_PARTY_FIELD_NAMES = (
    "invoice_code",
    "invoice_no",
    "invoice_date",
    "invoice_type",
    "amount_total",
    "tax_total",
    "total_with_tax",
    "item_name",
    "spec_model",
    "unit",
    "quantity",
    "unit_price",
    "line_amount",
    "tax_rate",
    "line_tax_amount",
    "service_location",
    "project_name",
)
TAX_ID_EXCLUDED_CONTEXT_TERMS = ("银行账号", "开户银行", "开户行", "账号", "电话", "地址")
IDENTITY_NUMBER_CONTEXT_TERMS = (
    "纳税人识别号",
    "统一社会信用代码",
    "社会信用代码",
    "身份证",
    "证件号码",
    "识别号",
)


def _compact_number(value: str | Decimal | None) -> str | Decimal | None:
    if isinstance(value, str):
        return (
            value.replace(",", "")
            .replace("￥", "")
            .replace("¥", "")
            .replace(" ", "")
            .strip()
        )
    return value


def _clean_money(value: str | Decimal | None) -> str | None:
    try:
        amount = normalize_amount(_compact_number(value))
    except ValueError:
        return None
    return f"{amount:.2f}" if amount is not None else None


def _money_matches(text: str) -> list[str]:
    values: list[str] = []
    for match in MONEY_RE.finditer(text):
        value = _clean_money(match.group(1))
        if value is not None:
            values.append(value)
    return values


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _safe_decimal(text: str) -> Decimal | None:
    try:
        return Decimal(str(_compact_number(text)))
    except InvalidOperation:
        return None


def _has_unclosed_parenthesis(text: str) -> bool:
    return text.count("(") + text.count("（") > text.count(")") + text.count("）")


def _bbox(unit: TextUnit) -> list[float]:
    return unit.bbox or [0.0, 0.0, 0.0, 0.0]


def _x0(unit: TextUnit) -> float:
    return float(_bbox(unit)[0])


def _center_x(unit: TextUnit) -> float:
    bbox = _bbox(unit)
    return (float(bbox[0]) + float(bbox[2])) / 2


def _y0(unit: TextUnit) -> float:
    return float(_bbox(unit)[1])


def _center_y(unit: TextUnit) -> float:
    bbox = _bbox(unit)
    return (float(bbox[1]) + float(bbox[3])) / 2


def _split_two_numbers(text: str) -> tuple[str | None, str | None]:
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if len(numbers) >= 2:
        return numbers[0], numbers[1]
    return None, None


def _decimal_scale(text: str) -> int:
    value = _safe_decimal(text)
    if value is None:
        return 0
    exponent = value.as_tuple().exponent
    return abs(exponent) if exponent < 0 else 0


def _last_number(parts: list[str]) -> str | None:
    for part in reversed(parts):
        text = part.strip().replace("¥", "").replace("￥", "")
        if _is_number_text(text):
            return text
    return None


def _last_money(parts: list[str]) -> str | None:
    for part in reversed(parts):
        text = part.strip().replace("¥", "").replace("￥", "")
        if _is_money_text(text):
            return _clean_money(text)
    return None


def _last_rate(parts: list[str]) -> str | None:
    for part in reversed(parts):
        text = part.strip()
        if TAX_RATE_RE.fullmatch(text):
            return text
    return None


def _money_values(text: str) -> list[str]:
    if not any(marker in text for marker in [".", ",", "¥", "￥", "CNY", "YQ"]):
        return []
    values = _money_matches(text)
    return [value for value in values if value is not None]


def _has_chinese(text: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def _is_number_text(text: str) -> bool:
    normalized = (
        text.strip()
        .replace(",", "")
        .replace("￥", "")
        .replace("¥", "")
    )
    normalized = re.sub(r"\s*\.\s*", ".", normalized)
    return bool(re.fullmatch(r"-?\d+(?:\.\d+)?", normalized))


def _is_money_text(text: str) -> bool:
    return bool(re.fullmatch(r"[¥￥]?-?\d[\d,]*(?:\s*\.\s*\d+)?(?:\s+不征税)?", text.strip()))


def _looks_like_spec(text: str) -> bool:
    return bool(re.search(r"\d", text)) and not _is_number_text(text) and not TAX_RATE_RE.fullmatch(text)


def _is_total_marker(text: str) -> bool:
    return text in {"合", "计", "合计"} or "价税合计" in text


def _join_item_parts(parts: list[str]) -> str | None:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "".join(cleaned) or None


def _unit_from_parts(parts: list[str]) -> str | None:
    cleaned = [part.strip() for part in parts if part and part.strip()]
    if not cleaned:
        return None
    return "".join(cleaned).replace("(", "（").replace(")", "）")


def _split_embedded_spec(item_name: str | None, spec: str | None) -> tuple[str | None, str | None]:
    if not item_name or spec:
        return item_name, spec
    match = re.search(r"\s+(\d+(?:\.\d+)?m，\d+(?:\.\d+)?mm(?:，[^，\s]+)?)", item_name)
    if not match:
        return item_name, spec
    cleaned_name = (item_name[: match.start()] + item_name[match.end() :]).strip()
    return cleaned_name, match.group(1)
