"""Data contracts for the invoice draft ledger pipeline."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from enum import Enum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator


class RecognitionStatus(str, Enum):
    READY = "ready"
    REVIEW_REQUIRED = "review_required"
    UNMODELED = "unmodeled"
    FAILED = "failed"


class CorrectionStatus(str, Enum):
    ORIGINAL = "original"
    CORRECTED = "corrected"


class SchemaDecisionStatus(str, Enum):
    MATCHED = "matched"
    UNMODELED = "unmodeled"
    AMBIGUOUS = "ambiguous"
    FAILED = "failed"


class WriteAction(str, Enum):
    ADDED = "added"
    SKIPPED_DUPLICATE = "skipped_duplicate"
    UPDATED = "updated"
    FAILED = "failed"


class FileType(str, Enum):
    PDF = "pdf"
    MARKDOWN = "markdown"
    TEXT = "text"
    IMAGE = "image"
    UNKNOWN = "unknown"


class TextLayerQuality(str, Enum):
    GOOD = "good"
    MIXED = "mixed"
    POOR = "poor"
    NONE = "none"
    UNKNOWN = "unknown"


class OcrStatus(str, Enum):
    READY = "ready"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


def normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()

    text = str(value).strip()
    if not text or text in {"未知", "N/A", "null", "None"}:
        return None

    match = re.search(r"(\d{4})\s*[年/-]\s*(\d{1,2})\s*[月/-]\s*(\d{1,2})\s*日?", text)
    if not match:
        raise ValueError(f"Unsupported date format: {value!r}")

    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day).isoformat()


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    text = str(value).strip()
    if not text or text in {"未知", "N/A", "null", "None"}:
        return None
    text = (
        text.replace(",", "")
        .replace("￥", "")
        .replace("¥", "")
        .replace("％", "%")
        .strip()
    )
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Unsupported decimal format: {value!r}") from exc


def normalize_amount(value: Any) -> Decimal | None:
    number = _to_decimal(value)
    if number is None:
        return None
    try:
        return number.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation as exc:
        raise ValueError(f"Unsupported amount format: {value!r}") from exc


def normalize_decimal(value: Any) -> Decimal | None:
    return _to_decimal(value)


def format_money(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return f"{normalize_amount(value):.2f}"


def format_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    text = format(value, "f")
    if "." in text:
        return text.rstrip("0").rstrip(".")
    return text


class ContractModel(BaseModel):
    model_config = ConfigDict(validate_assignment=True)


class PdfPageProfile(ContractModel):
    page: int
    has_text_layer: bool
    text_layer_quality: TextLayerQuality
    ocr_required: bool = False
    status: RecognitionStatus = RecognitionStatus.READY
    messages: list[str] = Field(default_factory=list)


class FileProfile(ContractModel):
    input_file: str
    file_type: FileType
    page_count: int | None = None
    has_text_layer: bool | None = None
    text_layer_quality: TextLayerQuality = TextLayerQuality.UNKNOWN
    ocr_required: bool = False
    unit_strategy: str = "unsupported"
    status: RecognitionStatus
    messages: list[str] = Field(default_factory=list)
    pages: list[PdfPageProfile] = Field(default_factory=list)


class InvoiceUnit(ContractModel):
    invoice_unit_id: str
    source_file: str
    page_range: list[int] = Field(default_factory=list)
    unit_type: str
    status: RecognitionStatus
    messages: list[str] = Field(default_factory=list)


class TextUnit(ContractModel):
    text: str
    page: int
    bbox: list[float] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    order: int
    source: str


class TextUnits(ContractModel):
    invoice_unit_id: str
    source: str
    units: list[TextUnit] = Field(default_factory=list)


class OcrTextBlock(ContractModel):
    text: str
    page: int
    bbox: list[float] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    order: int
    source: str = "ocr"


class OcrResult(ContractModel):
    invoice_unit_id: str
    status: OcrStatus
    provider: str
    source_file: str
    page_range: list[int]
    blocks: list[OcrTextBlock] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    runtime: dict[str, Any] = Field(default_factory=dict)


class SchemaDecision(ContractModel):
    invoice_unit_id: str
    schema_id: str | None = None
    variant_id: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    decision: SchemaDecisionStatus
    reason: list[str] = Field(default_factory=list)


class FieldCandidate(ContractModel):
    value: str
    source: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: str
    risk: list[str] = Field(default_factory=list)


class FieldCandidates(ContractModel):
    invoice_unit_id: str
    schema_id: str
    fields: dict[str, list[FieldCandidate]] = Field(default_factory=dict)


class InvoiceSource(ContractModel):
    source_file: str
    page_range: list[int]


class InvoiceFields(ContractModel):
    invoice_code: str | None = None
    invoice_no: str | None = None
    invoice_date: str | None = None
    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    invoice_type: str | None = None
    amount_total: Decimal | None = None
    tax_total: Decimal | None = None
    total_with_tax: Decimal | None = None

    @field_validator("invoice_date", mode="before")
    @classmethod
    def _normalize_invoice_date(cls, value: Any) -> str | None:
        return normalize_date(value)

    @field_validator("amount_total", "tax_total", "total_with_tax", mode="before")
    @classmethod
    def _normalize_money(cls, value: Any) -> Decimal | None:
        return normalize_amount(value)

    @field_serializer("amount_total", "tax_total", "total_with_tax", when_used="json")
    def _serialize_money(self, value: Decimal | None) -> str | None:
        return format_money(value)


class InvoiceItem(ContractModel):
    line_no: int
    item_name: str | None = None
    service_location: str | None = None
    project_name: str | None = None
    spec_model: str | None = None
    unit: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_amount: Decimal | None = None
    tax_rate: str | None = None
    line_tax_amount: Decimal | None = None
    line_total_with_tax: Decimal | None = None

    @field_validator("quantity", "unit_price", mode="before")
    @classmethod
    def _normalize_decimal(cls, value: Any) -> Decimal | None:
        return normalize_decimal(value)

    @field_validator("line_amount", "line_tax_amount", "line_total_with_tax", mode="before")
    @classmethod
    def _normalize_money(cls, value: Any) -> Decimal | None:
        return normalize_amount(value)

    @field_serializer("quantity", "unit_price", when_used="json")
    def _serialize_decimal(self, value: Decimal | None) -> str | None:
        return format_decimal(value)

    @field_serializer("line_amount", "line_tax_amount", "line_total_with_tax", when_used="json")
    def _serialize_money(self, value: Decimal | None) -> str | None:
        return format_money(value)


class InvoiceQuality(ContractModel):
    status: RecognitionStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    remark: str = ""
    field_decisions: dict[str, dict[str, Any]] = Field(default_factory=dict)


class InvoiceRecord(ContractModel):
    invoice_unit_id: str
    schema_id: str | None
    variant_id: str | None
    source: InvoiceSource
    invoice: InvoiceFields
    items: list[InvoiceItem] = Field(default_factory=list)
    quality: InvoiceQuality


class LedgerRow(ContractModel):
    row_type: str | None = None
    draft_row_id: str
    run_id: str
    source_file: str
    page_range: list[int]
    invoice_unit_id: str
    schema_id: str | None = None
    variant_id: str | None = None
    invoice_key: str
    invoice_line_key: str
    line_no: int
    processed_at: str
    invoice_code: str | None = None
    invoice_no: str | None = None
    invoice_date: str | None = None
    buyer_name: str | None = None
    buyer_tax_id: str | None = None
    seller_name: str | None = None
    seller_tax_id: str | None = None
    invoice_type: str | None = None
    item_name: str | None = None
    spec_model: str | None = None
    unit: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_amount: Decimal | None = None
    tax_rate: str | None = None
    line_tax_amount: Decimal | None = None
    line_total_with_tax: Decimal | None = None
    invoice_amount_total: Decimal | None = None
    invoice_tax_total: Decimal | None = None
    invoice_total_with_tax: Decimal | None = None
    recognition_status: RecognitionStatus
    reconciliation_status: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    review_remark: str = ""
    context_remark: str = ""
    remark: str = ""
    correction_status: CorrectionStatus = CorrectionStatus.ORIGINAL
    correction_time: str | None = None
    correction_remark: str | None = None

    @field_validator("invoice_date", mode="before")
    @classmethod
    def _normalize_invoice_date(cls, value: Any) -> str | None:
        return normalize_date(value)

    @field_validator("quantity", "unit_price", mode="before")
    @classmethod
    def _normalize_decimal(cls, value: Any) -> Decimal | None:
        return normalize_decimal(value)

    @field_validator(
        "line_amount",
        "line_tax_amount",
        "line_total_with_tax",
        "invoice_amount_total",
        "invoice_tax_total",
        "invoice_total_with_tax",
        mode="before",
    )
    @classmethod
    def _normalize_money(cls, value: Any) -> Decimal | None:
        return normalize_amount(value)

    @field_serializer("quantity", "unit_price", when_used="json")
    def _serialize_decimal(self, value: Decimal | None) -> str | None:
        return format_decimal(value)

    @field_serializer(
        "line_amount",
        "line_tax_amount",
        "line_total_with_tax",
        "invoice_amount_total",
        "invoice_tax_total",
        "invoice_total_with_tax",
        when_used="json",
    )
    def _serialize_money(self, value: Decimal | None) -> str | None:
        return format_money(value)


class RecognitionNotice(ContractModel):
    notice_id: str
    source_file: str
    page_range: list[int] = Field(default_factory=list)
    page_text: str
    severity: str
    issue_type: str
    invoice_no: str | None = None
    amount_total: Decimal | None = None
    check_location: str
    action: str
    invoice_unit_id: str

    @field_serializer("amount_total", when_used="json")
    def _serialize_amount_total(self, value: Decimal | None) -> str | None:
        return format_money(value)


class WriteResult(ContractModel):
    run_id: str
    target_sheet: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    added_rows: int = 0
    skipped_duplicate_rows: int = 0
    updated_rows: int = 0
    review_required_rows: int = 0
    failed_rows: int = 0
    messages: list[str] = Field(default_factory=list)


class RunSummary(ContractModel):
    run_id: str
    input_count: int = 0
    invoice_units: int = 0
    ready_rows: int = 0
    review_required_rows: int = 0
    unmodeled_units: int = 0
    failed_units: int = 0
    write_result: WriteResult | None = None
    output_dir: str


class FeedbackDiagnosis(ContractModel):
    feedback_id: str
    matched_rows: list[dict[str, Any]] = Field(default_factory=list)
    target_invoice_unit_id: str | None = None
    error_layer: str
    diagnosis: str
    suggested_fix: list[str] = Field(default_factory=list)
    requires_user_confirmation: bool = True
    verification_result: str = "pending"
    write_result: WriteResult | None = None
