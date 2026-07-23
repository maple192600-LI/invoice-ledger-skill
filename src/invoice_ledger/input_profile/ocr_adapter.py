"""OCR adapter for stage-two invoice inputs."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sysconfig
import tempfile
from typing import TYPE_CHECKING, Any

import fitz

from ..contracts import InvoiceUnit, OcrResult, OcrStatus, OcrTextBlock
from .ocr_cache import read_cached_result, write_cached_result

if TYPE_CHECKING:
    from .pdf_context import PdfProcessingContext


_PADDLE_OCR: Any | None = None
_PADDLE_OCR_KEY: tuple[tuple[str, Any], ...] | None = None
_DLL_PATHS_ADDED = False
_DLL_DIRECTORY_HANDLES: list[Any] = []


def _unsupported(unit: InvoiceUnit, provider: str, message: str) -> OcrResult:
    return OcrResult(
        invoice_unit_id=unit.invoice_unit_id,
        status=OcrStatus.UNSUPPORTED,
        provider=provider,
        source_file=unit.source_file,
        page_range=unit.page_range,
        blocks=[],
        messages=[message],
    )


def _failed(unit: InvoiceUnit, provider: str, message: str) -> OcrResult:
    return OcrResult(
        invoice_unit_id=unit.invoice_unit_id,
        status=OcrStatus.FAILED,
        provider=provider,
        source_file=unit.source_file,
        page_range=unit.page_range,
        blocks=[],
        messages=[message],
    )


def _add_windows_gpu_dll_paths() -> None:
    global _DLL_PATHS_ADDED
    if _DLL_PATHS_ADDED or os.name != "nt":
        return
    site_packages = Path(sysconfig.get_paths()["purelib"])
    dll_dirs = [
        site_packages / "nvidia" / "cu13" / "bin" / "x86_64",
        site_packages / "nvidia" / "cudnn" / "bin",
    ]
    existing_path = os.environ.get("PATH", "")
    prepend: list[str] = []
    for dll_dir in dll_dirs:
        if not dll_dir.exists():
            continue
        prepend.append(str(dll_dir))
        try:
            handle = os.add_dll_directory(str(dll_dir))
            _DLL_DIRECTORY_HANDLES.append(handle)
        except (AttributeError, OSError):
            pass
    if prepend:
        os.environ["PATH"] = ";".join(prepend + [existing_path])
    _DLL_PATHS_ADDED = True


def _render_pdf_page(
    unit: InvoiceUnit,
    dpi: int,
    pdf_context: "PdfProcessingContext | None" = None,
) -> Path:
    source = Path(unit.source_file)
    page_number = (unit.page_range or [1])[0]
    if pdf_context is not None:
        return pdf_context.render_page(page_number, dpi)
    output_dir = Path(tempfile.mkdtemp(prefix="invoice_ledger_ocr_pages_"))
    output_path = output_dir / f"{source.stem}.page{page_number}.{dpi}dpi.png"
    try:
        doc = fitz.open(source)
        try:
            page = doc.load_page(page_number - 1)
            scale = dpi / 72
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            pix.save(output_path)
        finally:
            doc.close()
    except Exception:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise
    return output_path


def _input_image_path(
    unit: InvoiceUnit,
    ocr_config: dict[str, Any],
    pdf_context: "PdfProcessingContext | None" = None,
) -> tuple[Path, Path | None]:
    if unit.unit_type == "pdf_ocr_page":
        rendered = _render_pdf_page(
            unit,
            int(ocr_config.get("render_dpi", 200)),
            pdf_context=pdf_context,
        )
        return rendered, None if pdf_context is not None else rendered.parent
    return Path(unit.source_file), None


def _ocr_key(ocr_config: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    keys = [
        "lang",
        "ocr_version",
        "device",
        "text_detection_model_name",
        "text_recognition_model_name",
        "use_doc_orientation_classify",
        "use_doc_unwarping",
        "use_textline_orientation",
    ]
    return tuple(sorted((key, ocr_config.get(key)) for key in keys))


def _get_paddle_ocr(ocr_config: dict[str, Any]) -> Any:
    global _PADDLE_OCR, _PADDLE_OCR_KEY
    key = _ocr_key(ocr_config)
    if _PADDLE_OCR is not None and _PADDLE_OCR_KEY == key:
        return _PADDLE_OCR

    _add_windows_gpu_dll_paths()
    from paddleocr import PaddleOCR

    kwargs = {
        "lang": ocr_config.get("lang", "ch"),
        "ocr_version": ocr_config.get("ocr_version", "PP-OCRv6"),
        "use_doc_orientation_classify": bool(
            ocr_config.get("use_doc_orientation_classify", False)
        ),
        "use_doc_unwarping": bool(ocr_config.get("use_doc_unwarping", False)),
        "use_textline_orientation": bool(ocr_config.get("use_textline_orientation", False)),
        "device": ocr_config.get("device", "gpu:0"),
    }
    if ocr_config.get("text_detection_model_name"):
        kwargs["text_detection_model_name"] = ocr_config["text_detection_model_name"]
    if ocr_config.get("text_recognition_model_name"):
        kwargs["text_recognition_model_name"] = ocr_config["text_recognition_model_name"]

    _PADDLE_OCR = PaddleOCR(**kwargs)
    _PADDLE_OCR_KEY = key
    return _PADDLE_OCR


def _paddle_runtime_info(ocr_config: dict[str, Any]) -> dict[str, Any]:
    runtime: dict[str, Any] = {
        "configured_device": ocr_config.get("device", "gpu:0"),
        "ocr_version": ocr_config.get("ocr_version", "PP-OCRv6"),
        "text_detection_model_name": ocr_config.get("text_detection_model_name"),
        "text_recognition_model_name": ocr_config.get("text_recognition_model_name"),
    }
    try:
        import paddle
        import paddleocr

        runtime.update(
            {
                "paddle_version": getattr(paddle, "__version__", None),
                "paddleocr_version": getattr(paddleocr, "__version__", None),
                "paddle_cuda_compiled": bool(paddle.device.is_compiled_with_cuda()),
                "paddle_device": paddle.device.get_device(),
            }
        )
    except Exception as exc:
        runtime["runtime_probe_error"] = str(exc)
    return runtime


def _poly_to_bbox(poly: Any) -> list[float] | None:
    if poly is None:
        return None
    points = poly.tolist() if hasattr(poly, "tolist") else poly
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _blocks_from_paddle_result(result: list[Any], page: int) -> list[OcrTextBlock]:
    blocks: list[OcrTextBlock] = []
    for page_result in result:
        texts = page_result.get("rec_texts", []) if isinstance(page_result, dict) else []
        scores = page_result.get("rec_scores", []) if isinstance(page_result, dict) else []
        polys = page_result.get("rec_polys", []) if isinstance(page_result, dict) else []
        for index, text in enumerate(texts, start=1):
            cleaned = str(text).strip()
            if not cleaned:
                continue
            score = scores[index - 1] if index - 1 < len(scores) else None
            poly = polys[index - 1] if index - 1 < len(polys) else None
            blocks.append(
                OcrTextBlock(
                    text=cleaned,
                    page=page,
                    bbox=_poly_to_bbox(poly),
                    confidence=float(score) if score is not None else None,
                    order=len(blocks) + 1,
                )
            )
    return blocks


def _paddle_ocr_result(
    unit: InvoiceUnit,
    blocks: list[OcrTextBlock],
    runtime: dict[str, Any],
) -> OcrResult:
    return OcrResult(
        invoice_unit_id=unit.invoice_unit_id,
        status=OcrStatus.READY if blocks else OcrStatus.FAILED,
        provider="paddle",
        source_file=unit.source_file,
        page_range=unit.page_range,
        blocks=blocks,
        messages=[] if blocks else ["PaddleOCR returned no text blocks."],
        runtime=runtime,
    )


def _split_batch_result(result: list[Any], expected_count: int) -> list[list[Any]]:
    if expected_count == 1:
        return [result]
    if not isinstance(result, list) or len(result) != expected_count:
        raise ValueError(
            f"PaddleOCR batch returned {len(result) if isinstance(result, list) else 'non-list'} "
            f"results for {expected_count} inputs."
        )
    return [item if isinstance(item, list) else [item] for item in result]


def _run_paddle(
    unit: InvoiceUnit,
    ocr_config: dict[str, Any],
    pdf_context: "PdfProcessingContext | None" = None,
) -> OcrResult:
    runtime = _paddle_runtime_info(ocr_config)
    cached = read_cached_result(unit, ocr_config, runtime)
    if cached is not None:
        return cached
    cleanup_dir: Path | None = None
    try:
        image_path, cleanup_dir = _input_image_path(unit, ocr_config, pdf_context=pdf_context)
        ocr = _get_paddle_ocr(ocr_config)
        result = ocr.predict(str(image_path))
        page = (unit.page_range or [1])[0]
        blocks = _blocks_from_paddle_result(result, page)
    except Exception as exc:
        device = ocr_config.get("device", "gpu:0")
        version = ocr_config.get("ocr_version", "PP-OCRv6")
        failed = _failed(unit, "paddle", f"PaddleOCR failed on {device} with {version}: {exc}")
        failed.runtime = runtime
        return failed
    finally:
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
    ocr_result = _paddle_ocr_result(unit, blocks, {**runtime, "cache_hit": False})
    write_cached_result(ocr_result, unit, ocr_config, runtime)
    return ocr_result


def _run_paddle_batch(
    units: list[InvoiceUnit],
    ocr_config: dict[str, Any],
    pdf_context: "PdfProcessingContext | None" = None,
) -> dict[str, OcrResult]:
    runtime = _paddle_runtime_info(ocr_config)
    results: dict[str, OcrResult] = {}
    missing_units: list[InvoiceUnit] = []
    image_paths: list[Path] = []
    cleanup_dirs: list[Path] = []
    for unit in units:
        cached = read_cached_result(unit, ocr_config, runtime)
        if cached is not None:
            results[unit.invoice_unit_id] = cached
            continue
        try:
            image_path, cleanup_dir = _input_image_path(
                unit,
                ocr_config,
                pdf_context=pdf_context,
            )
            missing_units.append(unit)
            image_paths.append(image_path)
            if cleanup_dir is not None:
                cleanup_dirs.append(cleanup_dir)
        except Exception as exc:
            failed = _failed(unit, "paddle", f"PaddleOCR render failed: {exc}")
            failed.runtime = runtime
            results[unit.invoice_unit_id] = failed

    if missing_units:
        try:
            ocr = _get_paddle_ocr(ocr_config)
            raw_result = ocr.predict([str(path) for path in image_paths])
            result_parts = _split_batch_result(raw_result, len(missing_units))
            for unit, result_part in zip(missing_units, result_parts):
                page = (unit.page_range or [1])[0]
                blocks = _blocks_from_paddle_result(result_part, page)
                ocr_result = _paddle_ocr_result(
                    unit,
                    blocks,
                    {**runtime, "cache_hit": False, "batch_size": len(missing_units)},
                )
                results[unit.invoice_unit_id] = ocr_result
                write_cached_result(ocr_result, unit, ocr_config, runtime)
        except Exception as exc:
            device = ocr_config.get("device", "gpu:0")
            version = ocr_config.get("ocr_version", "PP-OCRv6")
            for unit in missing_units:
                failed = _failed(
                    unit,
                    "paddle",
                    f"PaddleOCR batch failed on {device} with {version}: {exc}",
                )
                failed.runtime = runtime
                results[unit.invoice_unit_id] = failed
        finally:
            for cleanup_dir in cleanup_dirs:
                shutil.rmtree(cleanup_dir, ignore_errors=True)
    return results


def run_ocr_batch(
    units: list[InvoiceUnit],
    runtime_config: dict[str, Any] | None = None,
    pdf_context: "PdfProcessingContext | None" = None,
) -> dict[str, OcrResult]:
    config = runtime_config or {}
    ocr_config = config.get("ocr", {}) if isinstance(config, dict) else {}
    provider = str(ocr_config.get("provider", ocr_config.get("adapter", "unsupported")))
    if not units:
        return {}
    if ocr_config.get("enabled") is not True or provider != "paddle":
        return {
            unit.invoice_unit_id: run_ocr(
                unit,
                runtime_config=runtime_config,
                pdf_context=pdf_context,
            )
            for unit in units
        }
    return _run_paddle_batch(units, ocr_config, pdf_context=pdf_context)


def run_ocr(
    unit: InvoiceUnit,
    runtime_config: dict[str, Any] | None = None,
    pdf_context: "PdfProcessingContext | None" = None,
) -> OcrResult:
    config = runtime_config or {}
    ocr_config = config.get("ocr", {}) if isinstance(config, dict) else {}
    provider = str(ocr_config.get("provider", ocr_config.get("adapter", "unsupported")))
    if ocr_config.get("enabled") is not True:
        return _unsupported(unit, provider, "OCR 未启用。")
    if provider == "paddle":
        return _run_paddle(unit, ocr_config, pdf_context=pdf_context)
    return _unsupported(unit, provider, f"OCR provider is not available: {provider}")
