"""Disk cache helpers for deterministic OCR results."""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any

from .._paths import PROJECT_ROOT
from ..contracts import InvoiceUnit, OcrResult, OcrStatus


CACHE_SCHEMA_VERSION = "ocr-result-v1"


def cache_enabled(ocr_config: dict[str, Any]) -> bool:
    return ocr_config.get("cache_enabled", True) is not False


def cache_dir(ocr_config: dict[str, Any]) -> Path:
    configured = ocr_config.get("cache_dir") or os.environ.get("INVOICE_LEDGER_OCR_CACHE_DIR")
    if configured:
        return Path(configured)
    return PROJECT_ROOT / ".ocr_cache"


def _file_digest(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_fingerprint(
    unit: InvoiceUnit,
    ocr_config: dict[str, Any],
    runtime: dict[str, Any],
) -> str:
    source = Path(unit.source_file)
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "source_sha256": _file_digest(source),
        "source_suffix": source.suffix.lower(),
        "page_range": unit.page_range,
        "unit_type": unit.unit_type,
        "provider": "paddle",
        "device": ocr_config.get("device", "gpu:0"),
        "lang": ocr_config.get("lang", "ch"),
        "ocr_version": ocr_config.get("ocr_version", "PP-OCRv6"),
        "render_dpi": int(ocr_config.get("render_dpi", 200)),
        "text_detection_model_name": ocr_config.get("text_detection_model_name"),
        "text_recognition_model_name": ocr_config.get("text_recognition_model_name"),
        "use_doc_orientation_classify": bool(
            ocr_config.get("use_doc_orientation_classify", False)
        ),
        "use_doc_unwarping": bool(ocr_config.get("use_doc_unwarping", False)),
        "use_textline_orientation": bool(ocr_config.get("use_textline_orientation", False)),
        "paddle_version": runtime.get("paddle_version"),
        "paddleocr_version": runtime.get("paddleocr_version"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return sha256(encoded).hexdigest()


def cache_path(
    unit: InvoiceUnit,
    ocr_config: dict[str, Any],
    runtime: dict[str, Any],
) -> Path:
    return cache_dir(ocr_config) / f"{cache_fingerprint(unit, ocr_config, runtime)}.json"


def read_cached_result(
    unit: InvoiceUnit,
    ocr_config: dict[str, Any],
    runtime: dict[str, Any],
) -> OcrResult | None:
    if not cache_enabled(ocr_config):
        return None
    if not Path(unit.source_file).exists():
        return None
    path = cache_path(unit, ocr_config, runtime)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cached = OcrResult(**payload)
    except Exception:
        path.unlink(missing_ok=True)
        return None
    cached_runtime = {**cached.runtime, **runtime, "cache_hit": True}
    return cached.model_copy(
        update={
            "invoice_unit_id": unit.invoice_unit_id,
            "source_file": unit.source_file,
            "page_range": unit.page_range,
            "runtime": cached_runtime,
        }
    )


def write_cached_result(
    result: OcrResult,
    unit: InvoiceUnit,
    ocr_config: dict[str, Any],
    runtime: dict[str, Any],
) -> None:
    if not cache_enabled(ocr_config) or result.status != OcrStatus.READY:
        return
    if not Path(unit.source_file).exists():
        return
    path = cache_path(unit, ocr_config, runtime)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp_path.replace(path)
    except Exception:
        return
