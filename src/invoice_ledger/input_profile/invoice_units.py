"""Invoice unit creation for the invoice draft ledger pipeline."""

from __future__ import annotations

from hashlib import sha1
from pathlib import Path

from ..contracts import FileProfile, FileType, InvoiceUnit, RecognitionStatus


def _hash_file(path: Path) -> str:
    digest = sha1()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 128), b""):
            digest.update(chunk)
    return digest.hexdigest()[:16]


def _unit_id(profile: FileProfile, page_range: list[int]) -> str:
    path = Path(profile.input_file)
    file_hash = _hash_file(path) if path.exists() and path.is_file() else "missing"
    page_part = "-".join(str(page) for page in page_range) if page_range else "none"
    seed = f"{path.name}|{page_part}|{file_hash}"
    return "unit_" + sha1(seed.encode("utf-8")).hexdigest()[:16]


def build_invoice_units(profile: FileProfile) -> list[InvoiceUnit]:
    if profile.status == RecognitionStatus.READY and profile.unit_strategy == "split_by_page":
        units: list[InvoiceUnit] = []
        for page in profile.pages:
            units.append(
                InvoiceUnit(
                    invoice_unit_id=_unit_id(profile, [page.page]),
                    source_file=profile.input_file,
                    page_range=[page.page],
                    unit_type="pdf_ocr_page" if page.ocr_required else "pdf_page",
                    status=page.status,
                    messages=page.messages,
                )
            )
        return units

    if profile.status != RecognitionStatus.READY or profile.unit_strategy != "single":
        return [
            InvoiceUnit(
                invoice_unit_id=_unit_id(profile, []),
                source_file=profile.input_file,
                page_range=[],
                unit_type="unsupported",
                status=RecognitionStatus.FAILED,
                messages=profile.messages or ["当前输入不能进入发票单元处理。"],
            )
        ]

    if profile.file_type == FileType.PDF and profile.ocr_required:
        unit_type = "pdf_ocr_page"
    elif profile.file_type == FileType.PDF:
        unit_type = "pdf_page"
    elif profile.file_type in {FileType.TEXT, FileType.MARKDOWN}:
        unit_type = "text_file"
    elif profile.file_type == FileType.IMAGE and profile.ocr_required:
        unit_type = "image"
    else:
        unit_type = "unsupported"

    return [
        InvoiceUnit(
            invoice_unit_id=_unit_id(profile, [1]),
            source_file=profile.input_file,
            page_range=[1],
            unit_type=unit_type,
            status=RecognitionStatus.READY,
            messages=[],
        )
    ]
