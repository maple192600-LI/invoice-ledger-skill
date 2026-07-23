"""Shared PDF document context for multi-unit processing."""

from __future__ import annotations

from pathlib import Path
import shutil
import tempfile

import fitz


class PdfProcessingContext:
    def __init__(self, source_file: str | Path) -> None:
        self.source_file = Path(source_file)
        self._doc = None
        self._temp_dir: Path | None = None

    def __enter__(self) -> "PdfProcessingContext":
        _ = self.doc
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    @property
    def doc(self):
        if self._doc is None:
            self._doc = fitz.open(self.source_file)
        return self._doc

    def render_page(self, page_number: int, dpi: int) -> Path:
        if self._temp_dir is None:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="invoice_ledger_ocr_pages_"))
        output_path = (
            self._temp_dir / f"{self.source_file.stem}.page{page_number}.{dpi}dpi.png"
        )
        page = self.doc.load_page(page_number - 1)
        scale = dpi / 72
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        pix.save(output_path)
        return output_path

    def close(self) -> None:
        if self._doc is not None:
            self._doc.close()
            self._doc = None
        if self._temp_dir is not None:
            shutil.rmtree(self._temp_dir, ignore_errors=True)
            self._temp_dir = None
