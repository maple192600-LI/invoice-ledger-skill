"""Input file profiling for the invoice draft ledger pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import fitz

from ..contracts import FileProfile, FileType, PdfPageProfile, RecognitionStatus, TextLayerQuality

if TYPE_CHECKING:
    from .pdf_context import PdfProcessingContext


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def _detect_file_type(path: Path) -> FileType:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return FileType.PDF
    if suffix == ".md":
        return FileType.MARKDOWN
    if suffix == ".txt":
        return FileType.TEXT
    if suffix in IMAGE_SUFFIXES:
        return FileType.IMAGE
    return FileType.UNKNOWN


def _profile_text_file(path: Path, file_type: FileType) -> FileProfile:
    text = path.read_text(encoding="utf-8")
    has_text = bool(text.strip())
    return FileProfile(
        input_file=str(path),
        file_type=file_type,
        page_count=1,
        has_text_layer=has_text,
        text_layer_quality=TextLayerQuality.GOOD if has_text else TextLayerQuality.NONE,
        ocr_required=False,
        unit_strategy="single" if has_text else "unsupported",
        status=RecognitionStatus.READY if has_text else RecognitionStatus.FAILED,
        messages=[] if has_text else ["文本文件为空，无法进入解析。"],
    )


def _page_quality(text_length: int) -> TextLayerQuality:
    if text_length >= 40:
        return TextLayerQuality.GOOD
    if text_length > 0:
        return TextLayerQuality.POOR
    return TextLayerQuality.NONE


def _overall_pdf_quality(pages: list[PdfPageProfile]) -> TextLayerQuality:
    qualities = {page.text_layer_quality for page in pages}
    if qualities == {TextLayerQuality.GOOD}:
        return TextLayerQuality.GOOD
    if qualities == {TextLayerQuality.NONE}:
        return TextLayerQuality.NONE
    if qualities == {TextLayerQuality.POOR}:
        return TextLayerQuality.POOR
    if qualities:
        return TextLayerQuality.MIXED
    return TextLayerQuality.UNKNOWN


def _page_profile(page_number: int, text_length: int, ocr_enabled: bool) -> PdfPageProfile:
    quality = _page_quality(text_length)
    ocr_required = quality != TextLayerQuality.GOOD
    status = RecognitionStatus.READY
    messages: list[str] = []
    if ocr_required and not ocr_enabled:
        status = RecognitionStatus.FAILED
        messages = ["该 PDF 页面需要 OCR，当前配置未启用 OCR。"]
    return PdfPageProfile(
        page=page_number,
        has_text_layer=text_length > 0,
        text_layer_quality=quality,
        ocr_required=ocr_required,
        status=status,
        messages=messages,
    )


def _profile_pdf(
    path: Path,
    ocr_enabled: bool = False,
    pdf_context: "PdfProcessingContext | None" = None,
) -> FileProfile:
    doc = pdf_context.doc if pdf_context is not None else fitz.open(path)
    try:
        page_count = doc.page_count
        text_lengths = [len((page.get_text("text") or "").strip()) for page in doc]
    finally:
        if pdf_context is None:
            doc.close()

    page_profiles = [
        _page_profile(index + 1, length, ocr_enabled)
        for index, length in enumerate(text_lengths)
    ]
    has_text = any(page.has_text_layer for page in page_profiles)
    overall_quality = _overall_pdf_quality(page_profiles)
    needs_ocr = any(page.ocr_required for page in page_profiles)
    has_ready_page = any(page.status == RecognitionStatus.READY for page in page_profiles)

    if page_count != 1:
        status = RecognitionStatus.READY if has_ready_page else RecognitionStatus.FAILED
        messages = [] if has_ready_page else ["多页 PDF 中所有页面都需要 OCR，当前配置未启用 OCR。"]
        return FileProfile(
            input_file=str(path),
            file_type=FileType.PDF,
            page_count=page_count,
            has_text_layer=has_text,
            text_layer_quality=overall_quality,
            ocr_required=needs_ocr,
            unit_strategy="split_by_page" if has_ready_page else "unsupported",
            status=status,
            messages=messages,
            pages=page_profiles,
        )

    if not has_text:
        status = RecognitionStatus.READY if ocr_enabled else RecognitionStatus.FAILED
        return FileProfile(
            input_file=str(path),
            file_type=FileType.PDF,
            page_count=page_count,
            has_text_layer=False,
            text_layer_quality=TextLayerQuality.NONE,
            ocr_required=True,
            unit_strategy="single" if ocr_enabled else "unsupported",
            status=status,
            messages=[] if ocr_enabled else ["PDF 无可用文本层，当前配置未启用 OCR。"],
            pages=page_profiles,
        )

    quality = overall_quality
    ocr_required = quality == TextLayerQuality.POOR
    status = RecognitionStatus.READY if quality == TextLayerQuality.GOOD or ocr_enabled else RecognitionStatus.FAILED
    messages = [] if status == RecognitionStatus.READY else ["PDF 文本层质量不足，当前配置未启用 OCR。"]
    return FileProfile(
        input_file=str(path),
        file_type=FileType.PDF,
        page_count=page_count,
        has_text_layer=True,
        text_layer_quality=quality,
        ocr_required=ocr_required,
        unit_strategy="single" if status == RecognitionStatus.READY else "unsupported",
        status=status,
        messages=messages,
        pages=page_profiles,
    )


def profile_input_file(
    path: str | Path,
    ocr_enabled: bool = False,
    pdf_context: "PdfProcessingContext | None" = None,
) -> FileProfile:
    input_path = Path(path)
    file_type = _detect_file_type(input_path)

    if not input_path.exists():
        return FileProfile(
            input_file=str(input_path),
            file_type=file_type,
            page_count=0,
            has_text_layer=False,
            text_layer_quality=TextLayerQuality.UNKNOWN,
            ocr_required=False,
            unit_strategy="unsupported",
            status=RecognitionStatus.FAILED,
            messages=["输入文件不存在。"],
        )

    if file_type in {FileType.TEXT, FileType.MARKDOWN}:
        return _profile_text_file(input_path, file_type)
    if file_type == FileType.PDF:
        return _profile_pdf(input_path, ocr_enabled=ocr_enabled, pdf_context=pdf_context)
    if file_type == FileType.IMAGE:
        status = RecognitionStatus.READY if ocr_enabled else RecognitionStatus.FAILED
        return FileProfile(
            input_file=str(input_path),
            file_type=file_type,
            page_count=1,
            has_text_layer=False,
            text_layer_quality=TextLayerQuality.NONE,
            ocr_required=True,
            unit_strategy="single" if ocr_enabled else "unsupported",
            status=status,
            messages=[] if ocr_enabled else ["图片发票需要 OCR，当前配置未启用 OCR。"],
        )

    return FileProfile(
        input_file=str(input_path),
        file_type=FileType.UNKNOWN,
        page_count=0,
        has_text_layer=False,
        text_layer_quality=TextLayerQuality.UNKNOWN,
        ocr_required=False,
        unit_strategy="unsupported",
        status=RecognitionStatus.FAILED,
        messages=["当前阶段不支持该文件类型。"],
    )
