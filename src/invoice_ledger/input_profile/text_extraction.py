"""Text extraction for supported text-layer and OCR inputs."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import fitz

from ..contracts import InvoiceUnit, OcrResult, OcrStatus, RecognitionStatus, TextUnits
from ..errors import InvoiceLedgerError
from .ocr_adapter import run_ocr
from .text_units import normalize_text_blocks

if TYPE_CHECKING:
    from .pdf_context import PdfProcessingContext


def _extract_text_file(unit: InvoiceUnit) -> TextUnits:
    path = Path(unit.source_file)
    lines = path.read_text(encoding="utf-8").splitlines()
    blocks = [
        {"text": line, "page": 1, "order": index, "source": "text_file"}
        for index, line in enumerate(lines, start=1)
    ]
    return normalize_text_blocks(unit.invoice_unit_id, "text_file", blocks)


def _split_pdf_block_lines(
    text: str,
    bbox: tuple[float, float, float, float],
    page_index: int,
    start_order: int,
) -> list[dict[str, object]]:
    x0, y0, x1, y1 = bbox
    lines = str(text).splitlines()
    if not lines:
        return []
    line_height = (y1 - y0) / len(lines) if y1 > y0 else 0.0
    blocks: list[dict[str, object]] = []
    for offset, line in enumerate(lines):
        line_y0 = y0 + line_height * offset
        line_y1 = y0 + line_height * (offset + 1) if line_height else y1
        blocks.append(
            {
                "text": line,
                "page": page_index,
                "bbox": [x0, line_y0, x1, line_y1],
                "confidence": None,
                "order": start_order + offset,
                "source": "pdf_text",
            }
        )
    return blocks


def _extract_pdf_text(
    unit: InvoiceUnit,
    pdf_context: "PdfProcessingContext | None" = None,
) -> TextUnits:
    path = Path(unit.source_file)
    doc = pdf_context.doc if pdf_context is not None else fitz.open(path)
    blocks: list[dict[str, object]] = []
    try:
        for page_index in unit.page_range or range(1, doc.page_count + 1):
            page = doc.load_page(page_index - 1)
            page_blocks = page.get_text("blocks")
            if page_blocks:
                for block in page_blocks:
                    x0, y0, x1, y1, text, *_ = block
                    blocks.extend(
                        _split_pdf_block_lines(
                            text=str(text),
                            bbox=(float(x0), float(y0), float(x1), float(y1)),
                            page_index=page_index,
                            start_order=len(blocks) + 1,
                        )
                    )
            else:
                text = page.get_text("text") or ""
                for line in text.splitlines():
                    blocks.append(
                        {
                            "text": line,
                            "page": page_index,
                            "bbox": [0, 0, 0, 0],
                            "confidence": None,
                            "order": len(blocks) + 1,
                            "source": "pdf_text",
                        }
                    )
    finally:
        if pdf_context is None:
            doc.close()
    return normalize_text_blocks(unit.invoice_unit_id, "pdf_text", blocks)


def extract_ocr_text_units(
    unit: InvoiceUnit,
    runtime_config: dict | None = None,
    pdf_context: "PdfProcessingContext | None" = None,
) -> tuple[TextUnits, OcrResult]:
    ocr_result = run_ocr(unit, runtime_config=runtime_config or {}, pdf_context=pdf_context)
    return text_units_from_ocr_result(unit, ocr_result), ocr_result


def text_units_from_ocr_result(unit: InvoiceUnit, ocr_result: OcrResult) -> TextUnits:
    if ocr_result.status != OcrStatus.READY:
        message = "; ".join(ocr_result.messages) or "OCR failed."
        error = InvoiceLedgerError(
            message,
            layer="ocr_adapter",
            suggestion="Check OCR provider configuration and model availability.",
        )
        error.details["ocr_result"] = ocr_result
        raise error
    blocks = [block.model_dump(mode="python") for block in ocr_result.blocks]
    return normalize_text_blocks(unit.invoice_unit_id, "ocr", blocks)


def extract_text_units(
    unit: InvoiceUnit,
    runtime_config: dict | None = None,
    pdf_context: "PdfProcessingContext | None" = None,
) -> TextUnits:
    if unit.status != RecognitionStatus.READY:
        raise InvoiceLedgerError(
            "Cannot extract text from unsupported invoice unit.",
            layer="text_extraction",
            suggestion="Check file_profile and invoice_unit messages.",
        )
    if unit.unit_type == "text_file":
        return _extract_text_file(unit)
    if unit.unit_type == "pdf_page":
        return _extract_pdf_text(unit, pdf_context=pdf_context)
    if unit.unit_type in {"image", "pdf_ocr_page"}:
        text_units, _ = extract_ocr_text_units(
            unit,
            runtime_config=runtime_config,
            pdf_context=pdf_context,
        )
        return text_units
    raise InvoiceLedgerError(
        f"Unsupported invoice unit type: {unit.unit_type}",
        layer="text_extraction",
        suggestion="Check file profile, OCR config, or supported text-layer inputs.",
    )
