"""Per-invoice-unit processing for CLI and eval orchestration."""

from __future__ import annotations

import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import ParseError

import fitz

from ..contracts import (
    FieldCandidates,
    FileType,
    FileProfile,
    InvoiceFields,
    InvoiceQuality,
    InvoiceRecord,
    InvoiceSource,
    InvoiceUnit,
    OcrResult,
    OcrStatus,
    RecognitionStatus,
    SchemaDecision,
    SchemaDecisionStatus,
)
from ..errors import InvoiceLedgerError
from ..input_profile.file_profile import _detect_file_type, profile_input_file
from ..input_profile.invoice_units import build_invoice_units, merge_invoice_units
from ..input_profile.ocr_adapter import run_ocr_batch
from ..input_profile.pdf_context import PdfProcessingContext
from ..input_profile.einvoice_xml import parse_einvoice_xml
from ..input_profile.embedded_xbrl import find_embedded_xbrl, parse_xbrl, to_invoice_record as xbrl_to_record
from ..input_profile.text_extraction import (
    extract_ocr_text_units,
    extract_text_units,
    text_units_from_ocr_result,
)
from ..output.ledger_rows import build_ledger_rows
from ..parsing.field_candidates import generate_field_candidates
from ..parsing.field_resolver import resolve_invoice_record
from ..schema.schema_router import decide_schema
from ..validation.deductible_vat import apply_deductible_vat_rules
from ..validation.record_validator import validate_invoice_record


_PAGE_SEQUENCE_PATTERN = re.compile(r"共\s*(\d+)\s*页\s*第\s*(\d+)\s*页")
_REPEATED_INVOICE_FIELDS = (
    "invoice_no",
    "invoice_date",
    "buyer_name",
    "buyer_tax_id",
    "seller_name",
    "seller_tax_id",
)


def _page_sequence(unit_result: dict[str, Any]) -> tuple[int, int] | None:
    text_units = unit_result.get("text_units")
    if text_units is None or text_units.source == "ocr":
        return None
    match = _PAGE_SEQUENCE_PATTERN.search(
        "\n".join(unit.text for unit in text_units.units)
    )
    return (int(match.group(1)), int(match.group(2))) if match else None


def _page_invoice_identity(unit_result: dict[str, Any]) -> tuple[str, ...] | None:
    invoice = unit_result["invoice_record"].invoice
    values = tuple(
        str(getattr(invoice, field) or "").strip()
        for field in _REPEATED_INVOICE_FIELDS
    )
    return values if all(values) else None


def _merge_confirmed_multipage_units(
    file_profile: FileProfile,
    page_results: list[dict[str, Any]],
) -> list[InvoiceUnit]:
    units: list[InvoiceUnit] = []
    index = 0
    while index < len(page_results):
        sequence = _page_sequence(page_results[index])
        if sequence is None or sequence[0] < 2 or sequence[1] != 1:
            units.append(page_results[index]["invoice_unit"])
            index += 1
            continue

        page_count = sequence[0]
        candidates = page_results[index:index + page_count]
        identity = _page_invoice_identity(page_results[index])
        confirmed = (
            len(candidates) == page_count
            and identity is not None
            and all(
                _page_sequence(result) == (page_count, page_number)
                and _page_invoice_identity(result) == identity
                for page_number, result in enumerate(candidates, start=1)
            )
        )
        if not confirmed:
            units.append(page_results[index]["invoice_unit"])
            index += 1
            continue

        units.append(
            merge_invoice_units(
                file_profile,
                [result["invoice_unit"] for result in candidates],
            )
        )
        index += page_count
    return units


def failed_decision(unit_id: str, reason: str) -> SchemaDecision:
    return SchemaDecision(
        invoice_unit_id=unit_id,
        schema_id=None,
        variant_id=None,
        confidence=0.0,
        decision=SchemaDecisionStatus.FAILED,
        reason=[reason],
    )


def record_for_unprocessable(
    unit_id: str,
    source_file: str,
    page_range: list[int],
    status: RecognitionStatus,
    remark: str,
) -> InvoiceRecord:
    return InvoiceRecord(
        invoice_unit_id=unit_id,
        schema_id=None,
        variant_id=None,
        source=InvoiceSource(source_file=source_file, page_range=page_range),
        invoice=InvoiceFields(),
        items=[],
        quality=InvoiceQuality(status=status, confidence=0.0, remark=remark),
    )


def _ocr_disabled_result(unit: InvoiceUnit, provider: str = "unsupported") -> OcrResult:
    return OcrResult(
        invoice_unit_id=unit.invoice_unit_id,
        status=OcrStatus.UNSUPPORTED,
        provider=provider,
        source_file=unit.source_file,
        page_range=unit.page_range,
        messages=["OCR 未运行：当前为预检或配置未启用 OCR。"],
    )


def _failed_unit_result(
    unit: InvoiceUnit,
    file_profile: FileProfile,
    reason: str,
    ocr_result: OcrResult | None = None,
    selected_source: str = "none",
) -> dict[str, Any]:
    schema_decision = failed_decision(unit.invoice_unit_id, reason)
    field_candidates = FieldCandidates(
        invoice_unit_id=unit.invoice_unit_id,
        schema_id="failed",
        fields={},
    )
    invoice_record = record_for_unprocessable(
        unit.invoice_unit_id,
        unit.source_file,
        unit.page_range,
        RecognitionStatus.FAILED,
        reason,
    )
    return {
        "input": unit.source_file,
        "file_profile": file_profile,
        "invoice_unit": unit,
        "invoice_units": [unit],
        "text_units": None,
        "schema_decision": schema_decision,
        "field_candidates": field_candidates,
        "invoice_record": invoice_record,
        "ledger_rows": [],
        "ocr_result": ocr_result,
        "selected_source": selected_source,
    }


def process_invoice_unit(
    unit: InvoiceUnit,
    file_profile: FileProfile,
    runtime_config: dict[str, Any],
    run_id: str,
    processed_at: str,
    pdf_context: PdfProcessingContext | None = None,
    preloaded_ocr_results: dict[str, OcrResult] | None = None,
    source_mode: str | None = None,
    allow_ocr: bool = True,
) -> dict[str, Any]:
    text_units = None
    ocr_result = None
    if file_profile.status != RecognitionStatus.READY or unit.status != RecognitionStatus.READY:
        reason = "; ".join(unit.messages or file_profile.messages)
        return _failed_unit_result(unit, file_profile, reason)
    else:
        try:
            use_ocr = source_mode == "ocr" or (
                source_mode is None and unit.unit_type in {"image", "pdf_ocr_page"}
            )
            if use_ocr:
                if preloaded_ocr_results is not None:
                    ocr_result = preloaded_ocr_results.get(unit.invoice_unit_id)
                    if ocr_result is None:
                        ocr_result = OcrResult(
                            invoice_unit_id=unit.invoice_unit_id,
                            status=OcrStatus.FAILED,
                            provider=str(runtime_config.get("ocr", {}).get("provider", "unknown")),
                            source_file=unit.source_file,
                            page_range=unit.page_range,
                            messages=["OCR 批次未返回该处理单元的结果。"],
                        )
                    text_units = text_units_from_ocr_result(unit, ocr_result)
                elif not allow_ocr:
                    ocr_result = _ocr_disabled_result(
                        unit,
                        str(runtime_config.get("ocr", {}).get("provider", "unsupported")),
                    )
                    return _failed_unit_result(
                        unit,
                        file_profile,
                        "; ".join(ocr_result.messages),
                        ocr_result=ocr_result,
                        selected_source="none",
                    )
                else:
                    text_units, ocr_result = extract_ocr_text_units(
                        unit,
                        runtime_config=runtime_config,
                        pdf_context=pdf_context,
                    )
            else:
                text_units = extract_text_units(
                    unit,
                    runtime_config=runtime_config,
                    pdf_context=pdf_context,
                )
            schema_decision = decide_schema(text_units)
            field_candidates = generate_field_candidates(text_units, schema_decision)
            invoice_record = resolve_invoice_record(unit, schema_decision, field_candidates)
            invoice_record = apply_deductible_vat_rules(
                invoice_record,
                runtime_config.get("deductible_vat"),
            )
            invoice_record = validate_invoice_record(invoice_record)
            ledger_rows = build_ledger_rows(invoice_record, run_id=run_id, processed_at=processed_at)
        except InvoiceLedgerError as exc:
            possible_ocr_result = exc.details.get("ocr_result")
            if possible_ocr_result is not None:
                ocr_result = possible_ocr_result
            return _failed_unit_result(
                unit,
                file_profile,
                exc.message,
                ocr_result=ocr_result,
                selected_source="ocr" if text_units is not None and text_units.source == "ocr" else "none",
            )

    return {
        "input": unit.source_file,
        "file_profile": file_profile,
        "invoice_unit": unit,
        "invoice_units": [unit],
        "text_units": text_units,
        "schema_decision": schema_decision,
        "field_candidates": field_candidates,
        "invoice_record": invoice_record,
        "ledger_rows": ledger_rows,
        "ocr_result": ocr_result,
        "selected_source": text_units.source if text_units is not None else "none",
    }


def _direct_unit_result(
    unit: InvoiceUnit,
    file_profile: FileProfile,
    input_path: Path,
    run_id: str,
    processed_at: str,
    record: InvoiceRecord,
) -> dict[str, Any]:
    """A 轨通用组装：已有结构化 InvoiceRecord，走校验+行生成，组装 unit_result。

    跳过 OCR/文本层/schema_router/field_resolver/deductible_vat——数据来自票面内嵌
    XBRL 或总局 XML，金额含税语义明确，无需版面识别与税额拆分。
    """
    record = validate_invoice_record(record)
    ledger_rows = build_ledger_rows(record, run_id=run_id, processed_at=processed_at)
    schema_decision = SchemaDecision(
        invoice_unit_id=unit.invoice_unit_id,
        schema_id=record.schema_id,
        variant_id=record.variant_id,
        confidence=record.quality.confidence,
        decision=SchemaDecisionStatus.MATCHED,
        reason=["数据取自结构化凭证（内嵌 XBRL / 总局 XML），未经过版面识别。"],
    )
    field_candidates = FieldCandidates(
        invoice_unit_id=unit.invoice_unit_id,
        schema_id=record.schema_id or "digital-direct",
        fields={},
    )
    return {
        "input": str(input_path),
        "file_profile": file_profile,
        "invoice_unit": unit,
        "invoice_units": [unit],
        "text_units": None,
        "schema_decision": schema_decision,
        "field_candidates": field_candidates,
        "invoice_record": record,
        "ledger_rows": ledger_rows,
        "ocr_result": None,
        "selected_source": "structured",
        "fallback_attempted": False,
        "fallback_reason": None,
        "direct_status": record.quality.status.value,
        "ocr_status": None,
        "selection_reason": "structured_direct",
    }


_STATUS_RANK = {
    RecognitionStatus.READY: 3,
    RecognitionStatus.REVIEW_REQUIRED: 2,
    RecognitionStatus.UNMODELED: 1,
    RecognitionStatus.FAILED: 0,
}


def _status_rank(status: RecognitionStatus) -> int:
    return _STATUS_RANK.get(status, -1)


def _explicitly_unmodeled(result: dict[str, Any]) -> bool:
    decision = result.get("schema_decision")
    if decision is None or decision.decision != SchemaDecisionStatus.UNMODELED:
        return False
    reasons = " ".join(decision.reason or [])
    return any(term in reasons for term in ("未建模", "不支持", "未支持", "当前票种"))


def _needs_ocr_fallback(result: dict[str, Any], unit: InvoiceUnit) -> bool:
    if unit.unit_type != "pdf_page":
        return False
    status = result["invoice_record"].quality.status
    if status == RecognitionStatus.READY or _explicitly_unmodeled(result):
        return False
    return True


def _ocr_unit(unit: InvoiceUnit) -> InvoiceUnit:
    if unit.unit_type == "pdf_page":
        return unit.model_copy(update={"unit_type": "pdf_ocr_page"})
    return unit


def _select_recognition_result(
    direct_result: dict[str, Any] | None,
    ocr_result: dict[str, Any] | None,
    fallback_reason: str | None = None,
) -> dict[str, Any]:
    if direct_result is None:
        selected = ocr_result
        if selected is None:
            raise ValueError("至少需要一个识别结果。")
        selected = dict(selected)
        selected.update(
            {
                "selected_source": "ocr",
                "fallback_attempted": False,
                "fallback_reason": fallback_reason or "ocr_required",
                "direct_status": None,
                "ocr_status": selected["invoice_record"].quality.status.value,
                "selection_reason": "ocr_required",
            }
        )
        return selected

    if ocr_result is None:
        selected = dict(direct_result)
        selected.update(
            {
                "selected_source": selected.get("selected_source", "pdf_text"),
                "fallback_attempted": False,
                "fallback_reason": fallback_reason,
                "direct_status": selected["invoice_record"].quality.status.value,
                "ocr_status": None,
                "selection_reason": "direct_ready_or_ocr_not_available",
            }
        )
        return selected

    direct_status = direct_result["invoice_record"].quality.status
    ocr_status = ocr_result["invoice_record"].quality.status
    # 平级时保留文本结果，避免两次识别的字段被隐式拼接。
    choose_ocr = _status_rank(ocr_status) > _status_rank(direct_status)
    selected = ocr_result if choose_ocr else direct_result
    selected = dict(selected)
    if not choose_ocr:
        selected["ocr_result"] = ocr_result.get("ocr_result")
    selected.update(
        {
            "selected_source": "ocr" if choose_ocr else direct_result.get("selected_source", "pdf_text"),
            "fallback_attempted": True,
            "fallback_reason": fallback_reason or "direct_not_ready",
            "direct_status": direct_status.value,
            "ocr_status": ocr_status.value,
            "selection_reason": (
                "ocr_status_higher" if choose_ocr else "same_or_higher_direct_status"
            ),
        }
    )
    return selected


def _process_invoice_input(
    input_path: Path,
    runtime_config: dict[str, Any],
    run_id: str,
    processed_at: str,
    allow_ocr: bool = True,
) -> dict[str, Any]:
    ocr_enabled = runtime_config.get("ocr", {}).get("enabled") is True
    suffix = Path(input_path).suffix.lower()
    input_file_type = (
        FileType.PDF if suffix == ".pdf" else FileType.XML if suffix == ".xml" else None
    )
    context_manager = (
        PdfProcessingContext(input_path)
        if input_file_type == FileType.PDF and Path(input_path).exists()
        else nullcontext(None)
    )
    with context_manager as pdf_context:
        file_profile = profile_input_file(
            str(input_path),
            ocr_enabled=ocr_enabled,
            pdf_context=pdf_context,
        )
        invoice_units = build_invoice_units(file_profile)

        # A1: PDF 内嵌 XBRL 优先（命中则跳过 OCR 与文本层提取）
        if input_file_type == FileType.PDF and pdf_context is not None and invoice_units:
            embedded = find_embedded_xbrl(pdf_context.doc)
            if embedded is not None:
                config_id, xbrl_text = embedded
                unit = invoice_units[0].model_copy(update={"unit_type": "embedded_xbrl"})
                source = InvoiceSource(source_file=str(input_path), page_range=unit.page_range)
                record = xbrl_to_record(config_id, parse_xbrl(xbrl_text, config_id), source, unit.invoice_unit_id)
                if record is not None:
                    unit_result = _direct_unit_result(unit, file_profile, input_path, run_id, processed_at, record)
                    return {
                        "input": str(input_path),
                        "file_profile": file_profile,
                        "invoice_units": [unit],
                        "unit_results": [unit_result],
                    }

        # A2: 独立 XML 文件（总局 EInvoice 格式）
        if input_file_type == FileType.XML and invoice_units and invoice_units[0].status == RecognitionStatus.READY:
            unit = invoice_units[0]
            source = InvoiceSource(source_file=str(input_path), page_range=unit.page_range)
            text = Path(input_path).read_text(encoding="utf-8", errors="replace")
            record = parse_einvoice_xml(text, source, unit.invoice_unit_id)
            if record is not None:
                unit_result = _direct_unit_result(unit, file_profile, input_path, run_id, processed_at, record)
                return {
                    "input": str(input_path),
                    "file_profile": file_profile,
                    "invoice_units": invoice_units,
                    "unit_results": [unit_result],
                }

        direct_results: dict[str, dict[str, Any]] = {}
        ocr_candidates: list[InvoiceUnit] = []
        for unit in invoice_units:
            if unit.unit_type in {"image", "pdf_ocr_page"}:
                if allow_ocr and ocr_enabled:
                    ocr_candidates.append(unit)
                else:
                    direct_results[unit.invoice_unit_id] = process_invoice_unit(
                        unit,
                        file_profile,
                        runtime_config,
                        run_id,
                        processed_at,
                        pdf_context=pdf_context,
                        allow_ocr=False,
                    )
                continue

            direct_result = process_invoice_unit(
                unit,
                file_profile,
                runtime_config,
                run_id,
                processed_at,
                pdf_context=pdf_context,
                allow_ocr=allow_ocr,
            )
            direct_results[unit.invoice_unit_id] = direct_result
            if _needs_ocr_fallback(direct_result, unit):
                if allow_ocr and ocr_enabled:
                    ocr_candidates.append(_ocr_unit(unit))
                else:
                    direct_result["fallback_reason"] = "direct_not_ready"

        preloaded_ocr_results = _preload_ocr_results(
            ocr_candidates,
            runtime_config,
            pdf_context=pdf_context,
        ) if allow_ocr and ocr_candidates else {}
        ocr_candidate_ids = {unit.invoice_unit_id for unit in ocr_candidates}
        page_results: list[dict[str, Any]] = []
        for unit in invoice_units:
            direct_result = direct_results.get(unit.invoice_unit_id)
            ocr_unit = _ocr_unit(unit)
            if unit.invoice_unit_id in ocr_candidate_ids:
                ocr_result = process_invoice_unit(
                    ocr_unit,
                    file_profile,
                    runtime_config,
                    run_id,
                    processed_at,
                    pdf_context=pdf_context,
                    preloaded_ocr_results=preloaded_ocr_results,
                    source_mode="ocr",
                    allow_ocr=allow_ocr,
                )
                page_results.append(
                    _select_recognition_result(
                        direct_result,
                        ocr_result,
                        fallback_reason=(
                            "ocr_required" if direct_result is None else "direct_not_ready"
                        ),
                    )
                )
            elif direct_result is not None:
                page_results.append(
                    _select_recognition_result(
                        direct_result,
                        None,
                        fallback_reason=direct_result.get("fallback_reason"),
                    )
                )
        invoice_units = _merge_confirmed_multipage_units(file_profile, page_results)
        page_results_by_id = {
            result["invoice_unit"].invoice_unit_id: result
            for result in page_results
        }
        unit_results = [
            page_results_by_id.get(unit.invoice_unit_id)
            or process_invoice_unit(
                unit,
                file_profile,
                runtime_config,
                run_id,
                processed_at,
                pdf_context=pdf_context,
                allow_ocr=allow_ocr,
            )
            for unit in invoice_units
        ]
    return {
        "input": str(input_path),
        "file_profile": file_profile,
        "invoice_units": invoice_units,
        "unit_results": unit_results,
    }


def process_invoice_input(
    input_path: Path,
    runtime_config: dict[str, Any],
    run_id: str,
    processed_at: str,
    allow_ocr: bool = True,
) -> dict[str, Any]:
    try:
        return _process_invoice_input(
            input_path,
            runtime_config,
            run_id,
            processed_at,
            allow_ocr=allow_ocr,
        )
    except (OSError, UnicodeError, ParseError, fitz.FileDataError) as exc:
        reason = f"读取输入文件失败（{type(exc).__name__}）：{exc}"
        file_profile = FileProfile(
            input_file=str(input_path),
            file_type=_detect_file_type(Path(input_path)),
            status=RecognitionStatus.FAILED,
            unit_strategy="unsupported",
            messages=[reason],
        )
        invoice_units = build_invoice_units(file_profile)
        return {
            "input": str(input_path),
            "file_profile": file_profile,
            "invoice_units": invoice_units,
            "unit_results": [
                process_invoice_unit(
                    unit,
                    file_profile,
                    runtime_config,
                    run_id,
                    processed_at,
                    allow_ocr=allow_ocr,
                )
                for unit in invoice_units
            ],
        }


def _preload_ocr_results(
    invoice_units: list[InvoiceUnit],
    runtime_config: dict[str, Any],
    pdf_context: PdfProcessingContext | None = None,
) -> dict[str, OcrResult]:
    ready_ocr_units = [
        unit
        for unit in invoice_units
        if unit.status == RecognitionStatus.READY and unit.unit_type in {"image", "pdf_ocr_page"}
    ]
    if not ready_ocr_units:
        return {}
    return run_ocr_batch(ready_ocr_units, runtime_config, pdf_context=pdf_context)
