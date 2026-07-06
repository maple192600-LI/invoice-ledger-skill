"""Per-invoice-unit processing for CLI and eval orchestration."""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from typing import Any

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
    RecognitionStatus,
    SchemaDecision,
    SchemaDecisionStatus,
)
from ..errors import InvoiceLedgerError
from ..input_profile.file_profile import profile_input_file
from ..input_profile.invoice_units import build_invoice_units
from ..input_profile.ocr_adapter import run_ocr_batch
from ..input_profile.pdf_context import PdfProcessingContext
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


def process_invoice_unit(
    unit: InvoiceUnit,
    file_profile: FileProfile,
    runtime_config: dict[str, Any],
    run_id: str,
    processed_at: str,
    pdf_context: PdfProcessingContext | None = None,
    preloaded_ocr_results: dict[str, OcrResult] | None = None,
) -> dict[str, Any]:
    text_units = None
    ocr_result = None
    if file_profile.status != RecognitionStatus.READY or unit.status != RecognitionStatus.READY:
        reason = "; ".join(unit.messages or file_profile.messages)
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
        ledger_rows = []
    else:
        try:
            if unit.unit_type in {"image", "pdf_ocr_page"}:
                if preloaded_ocr_results and unit.invoice_unit_id in preloaded_ocr_results:
                    ocr_result = preloaded_ocr_results[unit.invoice_unit_id]
                    text_units = text_units_from_ocr_result(unit, ocr_result)
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
            schema_decision = failed_decision(unit.invoice_unit_id, exc.message)
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
                exc.message,
            )
            ledger_rows = []

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
    }


def process_invoice_input(
    input_path: Path,
    runtime_config: dict[str, Any],
    run_id: str,
    processed_at: str,
) -> dict[str, Any]:
    ocr_enabled = runtime_config.get("ocr", {}).get("enabled") is True
    input_file_type = FileType.PDF if Path(input_path).suffix.lower() == ".pdf" else None
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
        preloaded_ocr_results = _preload_ocr_results(
            invoice_units,
            runtime_config,
            pdf_context=pdf_context,
        )
        unit_results = [
            process_invoice_unit(
                unit,
                file_profile,
                runtime_config,
                run_id,
                processed_at,
                pdf_context=pdf_context,
                preloaded_ocr_results=preloaded_ocr_results,
            )
            for unit in invoice_units
        ]
    return {
        "input": str(input_path),
        "file_profile": file_profile,
        "invoice_units": invoice_units,
        "unit_results": unit_results,
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
    return run_ocr_batch(ready_ocr_units, runtime_config, pdf_context=pdf_context)
