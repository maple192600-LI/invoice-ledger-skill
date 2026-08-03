from pathlib import Path
from tempfile import TemporaryDirectory
import json
from contextlib import redirect_stdout
from io import StringIO
import unittest
from unittest.mock import patch

import fitz

from invoice_ledger.cli import run_cli
from invoice_ledger.contracts import OcrResult, OcrStatus, OcrTextBlock, RecognitionStatus
from invoice_ledger.pipeline.unit_processor import process_invoice_input


READY_TEXT = """电子发票（普通发票）
发票号码：00000000000000000001
开票日期：2026年07月26日
购买方名称：示例采购集团有限公司
统一社会信用代码/纳税人识别号：TESTBUYER000000001
销售方名称：ETC客户服务机构
统一社会信用代码/纳税人识别号：TESTSELLER00000001
ETC充值
*预付卡充值* 服务 次 1 100.00 100.00 不征税
不征税
价税合计（小写）¥100.00"""


def _pdf(path: Path, text: str | None) -> None:
    doc = fitz.open()
    page = doc.new_page()
    if text:
        page.insert_text((36, 36), text, fontsize=8, fontname="china-s")
    doc.save(path)
    doc.close()


def _multipage_pdf(path: Path, page_texts: list[str | None]) -> None:
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page()
        if text:
            page.insert_text((36, 36), text, fontsize=8, fontname="china-s")
    doc.save(path)
    doc.close()


def _ocr_result(unit_id: str, source_file: str, text: str, page: int = 1) -> OcrResult:
    blocks = [
        OcrTextBlock(text=line, page=page, order=index)
        for index, line in enumerate(text.splitlines(), start=1)
        if line.strip()
    ]
    return OcrResult(
        invoice_unit_id=unit_id,
        status=OcrStatus.READY,
        provider="fake",
        source_file=source_file,
        page_range=[page],
        blocks=blocks,
    )


class OcrRoutingTests(unittest.TestCase):
    def test_ready_text_pdf_does_not_call_ocr(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ready.pdf"
            _pdf(path, READY_TEXT)
            with patch("invoice_ledger.pipeline.unit_processor.run_ocr_batch") as run_ocr:
                result = process_invoice_input(
                    path,
                    {"ocr": {"enabled": True, "provider": "paddle"}},
                    "route-ready",
                    "2026-08-04T00:00:00",
                )
            run_ocr.assert_not_called()
            unit_result = result["unit_results"][0]
            self.assertEqual(RecognitionStatus.READY, unit_result["invoice_record"].quality.status)
            self.assertEqual("pdf_text", unit_result["selected_source"])

    def test_mixed_pdf_only_runs_ocr_for_page_without_text(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mixed.pdf"
            _multipage_pdf(path, [READY_TEXT, None])
            ocr_pages = []

            def fake_batch(units, runtime_config, pdf_context=None):
                ocr_pages.extend(unit.page_range for unit in units)
                return {
                    unit.invoice_unit_id: _ocr_result(
                        unit.invoice_unit_id,
                        unit.source_file,
                        READY_TEXT,
                        page=unit.page_range[0],
                    )
                    for unit in units
                }

            with patch("invoice_ledger.pipeline.unit_processor.run_ocr_batch", fake_batch):
                result = process_invoice_input(
                    path,
                    {"ocr": {"enabled": True, "provider": "paddle"}},
                    "route-mixed-pages",
                    "2026-08-04T00:00:00",
                )

            self.assertEqual([[2]], ocr_pages)
            self.assertEqual(
                ["pdf_text", "ocr"],
                [item["selected_source"] for item in result["unit_results"]],
            )

    def test_bad_text_pdf_falls_back_to_ocr_as_a_whole_result(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "garbled.pdf"
            _pdf(path, "乱码乱码乱码乱码乱码乱码乱码乱码乱码乱码乱码乱码")

            calls = []

            def fake_batch(units, runtime_config, pdf_context=None):
                calls.append(units)
                return {
                    unit.invoice_unit_id: _ocr_result(
                        unit.invoice_unit_id, unit.source_file, READY_TEXT
                    )
                    for unit in units
                }

            with patch("invoice_ledger.pipeline.unit_processor.run_ocr_batch", fake_batch):
                result = process_invoice_input(
                    path,
                    {"ocr": {"enabled": True, "provider": "paddle"}},
                    "route-fallback",
                    "2026-08-04T00:00:00",
                )
            self.assertEqual(1, len(calls))
            unit_result = result["unit_results"][0]
            self.assertEqual(RecognitionStatus.READY, unit_result["invoice_record"].quality.status)
            self.assertEqual("ocr", unit_result["selected_source"])
            self.assertEqual("direct_not_ready", unit_result["fallback_reason"])

    def test_same_status_fallback_keeps_complete_text_result(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "unmodeled.pdf"
            _pdf(path, "原生文本无法匹配票种结构")

            def fake_batch(units, runtime_config, pdf_context=None):
                return {
                    unit.invoice_unit_id: _ocr_result(
                        unit.invoice_unit_id,
                        unit.source_file,
                        "OCR文本同样无法匹配票种结构",
                    )
                    for unit in units
                }

            with patch("invoice_ledger.pipeline.unit_processor.run_ocr_batch", fake_batch):
                result = process_invoice_input(
                    path,
                    {"ocr": {"enabled": True, "provider": "paddle"}},
                    "route-same-status",
                    "2026-08-04T00:00:00",
                )

            unit_result = result["unit_results"][0]
            self.assertEqual("pdf_text", unit_result["selected_source"])
            self.assertEqual("unmodeled", unit_result["direct_status"])
            self.assertEqual("unmodeled", unit_result["ocr_status"])
            self.assertEqual("same_or_higher_direct_status", unit_result["selection_reason"])

    def test_check_only_reports_routes_without_running_ocr_or_writing_excel(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "inputs"
            input_dir.mkdir()
            _pdf(input_dir / "text.pdf", READY_TEXT)
            _pdf(input_dir / "scan.pdf", None)
            _pdf(input_dir / "garbled.pdf", "乱码乱码乱码乱码乱码乱码乱码乱码乱码乱码乱码乱码")
            ledger = root / "ledger.xlsx"
            ledger.write_bytes(Path("发票采集台账.xlsx").read_bytes())
            original_ledger = ledger.read_bytes()
            output = root / "output"

            with patch("invoice_ledger.cli._ocr_runtime_available", return_value=True):
                with patch("invoice_ledger.pipeline.unit_processor.run_ocr_batch") as run_ocr:
                    stdout = StringIO()
                    with redirect_stdout(stdout):
                        code = run_cli(
                            [
                                "--input-dir",
                                str(input_dir),
                                "--draft-ledger",
                                str(ledger),
                                "--config",
                                "config/runtime_ocr_auto.yaml",
                                "--output-dir",
                                str(output),
                                "--check-only",
                            ]
                        )
            self.assertEqual(0, code)
            run_ocr.assert_not_called()
            payload = json.loads(stdout.getvalue().strip())
            self.assertEqual(1, payload["direct_ready_files"])
            self.assertEqual(1, payload["ocr_required_files"])
            self.assertEqual(1, payload["ocr_fallback_files"])
            self.assertEqual(2, payload["ocr_required_pages"])
            self.assertEqual(0, payload["unsupported_files"])
            self.assertTrue(payload["check_only"])
            self.assertEqual(original_ledger, ledger.read_bytes())

    def test_ocr_failure_isolated_to_image_or_scan_unit(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            _pdf(path, None)
            calls = []

            def failed_batch(units, runtime_config, pdf_context=None):
                calls.append([unit.invoice_unit_id for unit in units])
                return {
                    unit.invoice_unit_id: OcrResult(
                        invoice_unit_id=unit.invoice_unit_id,
                        status=OcrStatus.FAILED,
                        provider="fake",
                        source_file=unit.source_file,
                        page_range=unit.page_range,
                        messages=["simulated OCR failure"],
                    )
                    for unit in units
                }

            with patch("invoice_ledger.pipeline.unit_processor.run_ocr_batch", failed_batch):
                result = process_invoice_input(
                    path,
                    {"ocr": {"enabled": True, "provider": "paddle"}},
                    "route-failure",
                    "2026-08-04T00:00:00",
                )
            self.assertEqual(1, len(calls))
            self.assertEqual(RecognitionStatus.FAILED, result["unit_results"][0]["invoice_record"].quality.status)
            self.assertEqual("ocr", result["unit_results"][0]["selected_source"])

    def test_ocr_programming_error_is_not_disguised_as_invoice_failure(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "scan.pdf"
            _pdf(path, None)
            with patch(
                "invoice_ledger.pipeline.unit_processor.run_ocr_batch",
                side_effect=RuntimeError("simulated programming error"),
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated programming error"):
                    process_invoice_input(
                        path,
                        {"ocr": {"enabled": True, "provider": "paddle"}},
                        "route-programming-error",
                        "2026-08-04T00:00:00",
                    )

    def test_check_only_returns_three_when_ocr_is_needed_but_unavailable(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "inputs"
            input_dir.mkdir()
            _pdf(input_dir / "scan.pdf", None)
            ledger = root / "ledger.xlsx"
            ledger.write_bytes(Path("发票采集台账.xlsx").read_bytes())
            with patch("invoice_ledger.cli._ocr_runtime_available", return_value=False):
                code = run_cli(
                    [
                        "--input-dir",
                        str(input_dir),
                        "--draft-ledger",
                        str(ledger),
                        "--config",
                        "config/runtime_ocr_auto.yaml",
                        "--output-dir",
                        str(root / "output"),
                        "--check-only",
                    ]
                )
            self.assertEqual(3, code)

    def test_check_only_passes_text_batch_without_ocr_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "inputs"
            input_dir.mkdir()
            _pdf(input_dir / "text.pdf", READY_TEXT)
            ledger = root / "ledger.xlsx"
            ledger.write_bytes(Path("发票采集台账.xlsx").read_bytes())
            stdout = StringIO()
            with patch("invoice_ledger.cli._ocr_runtime_available", return_value=False):
                with redirect_stdout(stdout):
                    code = run_cli(
                        [
                            "--input-dir",
                            str(input_dir),
                            "--draft-ledger",
                            str(ledger),
                            "--output-dir",
                            str(root / "output"),
                            "--check-only",
                        ]
                    )

            payload = json.loads(stdout.getvalue().strip())
            self.assertEqual(0, code)
            self.assertEqual("passed", payload["status"])
            self.assertEqual(0, payload["ocr_required_pages"])

    def test_check_only_blocks_text_only_config_for_scan(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            input_dir = root / "inputs"
            input_dir.mkdir()
            _pdf(input_dir / "scan.pdf", None)
            ledger = root / "ledger.xlsx"
            ledger.write_bytes(Path("发票采集台账.xlsx").read_bytes())
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = run_cli(
                    [
                        "--input-dir",
                        str(input_dir),
                        "--draft-ledger",
                        str(ledger),
                        "--config",
                        "config/runtime.yaml",
                        "--output-dir",
                        str(root / "output"),
                        "--check-only",
                    ]
                )

            payload = json.loads(stdout.getvalue().strip())
            self.assertEqual(2, code)
            self.assertEqual("blocked", payload["status"])
            self.assertEqual(1, payload["ocr_required_pages"])


if __name__ == "__main__":
    unittest.main()
