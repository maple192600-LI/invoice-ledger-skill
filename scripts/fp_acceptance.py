"""Run repeatable acceptance checks against a private invoice sample folder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from invoice_ledger.cli import SUPPORTED_INPUT_SUFFIXES, _load_runtime_config  # noqa: E402
from invoice_ledger.pipeline.unit_processor import process_invoice_input  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate private invoices against an explicit JSON manifest.")
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--expectations", required=True)
    parser.add_argument("--config", default="config/runtime_ocr_auto.yaml")
    parser.add_argument("--report")
    return parser


def _text(value: object) -> str | None:
    return None if value is None else str(value)


def _compare_record(expected: dict[str, Any], record: Any) -> list[str]:
    issues: list[str] = []
    if record.schema_id != expected["schema_id"]:
        issues.append(f"schema_id: expected {expected['schema_id']}, got {record.schema_id}")
    if "status" in expected and record.quality.status.value != expected["status"]:
        issues.append(f"status: expected {expected['status']}, got {record.quality.status.value}")
    for field_name, expected_value in expected.get("invoice", {}).items():
        actual = _text(getattr(record.invoice, field_name))
        if actual != str(expected_value):
            issues.append(f"invoice.{field_name}: expected {expected_value}, got {actual}")
    if "item_count" in expected and len(record.items) != int(expected["item_count"]):
        issues.append(f"item_count: expected {expected['item_count']}, got {len(record.items)}")
    for field_name, expected_values in expected.get("item_values", {}).items():
        actual_values = [_text(getattr(item, field_name, None)) for item in record.items]
        for expected_value in expected_values:
            if str(expected_value) not in actual_values:
                issues.append(
                    f"items.{field_name}: missing {expected_value}; got {actual_values}"
                )
    return issues


def run_acceptance(
    input_dir: Path,
    expectations_path: Path,
    runtime_config: dict[str, Any],
) -> dict[str, Any]:
    manifest = json.loads(expectations_path.read_text(encoding="utf-8"))
    expected_rows = manifest.get("invoices", [])
    expected_by_name = {row["file"]: row for row in expected_rows}
    input_paths = sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
    )
    actual_names = {path.name for path in input_paths}
    expected_names = set(expected_by_name)
    failures: list[dict[str, Any]] = []
    for missing in sorted(expected_names - actual_names):
        failures.append({"file": missing, "issues": ["expected file is missing"]})
    for unexpected in sorted(actual_names - expected_names):
        failures.append({"file": unexpected, "issues": ["file has no expectation"]})

    checked = 0
    for path in input_paths:
        expected = expected_by_name.get(path.name)
        if expected is None:
            continue
        result = process_invoice_input(path, runtime_config, "acceptance", "acceptance")
        records = [unit["invoice_record"] for unit in result["unit_results"]]
        issues = (
            [f"expected one invoice unit, got {len(records)}"]
            if len(records) != 1
            else _compare_record(expected, records[0])
        )
        checked += 1
        if issues:
            failures.append({"file": path.name, "issues": issues})
    return {
        "status": "passed" if not failures else "failed",
        "expected_input_count": len(expected_rows),
        "actual_input_count": len(input_paths),
        "checked": checked,
        "failure_count": len(failures),
        "failures": failures,
    }


def main() -> int:
    args = _parser().parse_args()
    report = run_acceptance(
        Path(args.input_dir),
        Path(args.expectations),
        _load_runtime_config(args.config),
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        Path(args.report).write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
