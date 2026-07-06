"""CLI orchestration for the invoice draft ledger pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from datetime import datetime
from hashlib import sha1
from shutil import copy2
from typing import Any, Sequence

import yaml

from .contracts import (
    RecognitionStatus,
    RunSummary,
    WriteResult,
)
from .output.evidence import save_evidence_bundle
from .output.recognition_notices import build_recognition_notices
from .pipeline.unit_processor import process_invoice_input
from .output.template_profile import load_template_profile, validate_template_workbook
from .output.template_writer import write_with_template_profile


SUPPORTED_INPUT_SUFFIXES = {".pdf", ".txt", ".md", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run invoice parsing and OCR-backed draft ledger pipeline."
    )
    parser.add_argument("--input", required=False, help="Input invoice file path.")
    parser.add_argument("--input-dir", required=False, help="Directory containing invoice files.")
    parser.add_argument(
        "--draft-ledger",
        "--workbook",
        dest="draft_ledger",
        required=False,
        help="Working ledger Excel workbook path. The file is written in place unless --copy-output is used.",
    )
    parser.add_argument("--config", required=False, help="Runtime config YAML path.")
    parser.add_argument("--target-sheet", required=False, help="Target worksheet name.")
    parser.add_argument("--output-dir", required=False, help="Directory for evidence and JSON output.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate arguments, input paths, config, and workbook/template compatibility without OCR or Excel writing.",
    )
    parser.add_argument(
        "--save-evidence",
        default="auto",
        choices=["auto", "always", "never"],
        help="Evidence mode.",
    )
    parser.add_argument(
        "--json-output",
        default="summary",
        choices=["summary", "full"],
        help="JSON stdout detail. Use summary for agent runs; use full only for debugging.",
    )
    parser.add_argument("--run-id", default=None, help="Optional run id.")
    parser.add_argument("--update-existing", action="store_true", help="Update existing draft rows.")
    parser.add_argument("--template-profile", default=None, help="Template profile YAML path.")
    parser.add_argument(
        "--write-in-place",
        action="store_true",
        help="Deprecated compatibility flag; formal collection writes directly to --draft-ledger by default.",
    )
    parser.add_argument(
        "--copy-output",
        action="store_true",
        help="Write to a copied workbook under --output-dir instead of the original ledger.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Clear existing rows in profile-managed sheets before writing new rows.",
    )
    return parser


def _validate_required_runtime_args(args: argparse.Namespace) -> str | None:
    required = {
        "draft_ledger": "draft ledger",
        "config": "config",
        "target_sheet": "target sheet",
        "output_dir": "output dir",
    }
    missing = [label for name, label in required.items() if not getattr(args, name)]
    if missing:
        return "Missing required runtime argument(s): " + ", ".join(missing)
    if bool(args.input) == bool(args.input_dir):
        return "Provide exactly one input source: --input or --input-dir."
    if args.write_in_place and args.copy_output:
        return "Refusing conflicting output options: --write-in-place cannot be used with --copy-output."
    if args.replace_existing and not args.copy_output:
        return "Refusing --replace-existing without --copy-output because formal collection writes to the original ledger."
    if args.update_existing:
        return "Refusing --update-existing because profile-managed row update is not implemented yet."
    return None


def _validate_input_paths(args: argparse.Namespace) -> str | None:
    if args.input:
        input_path = Path(args.input)
        if not input_path.is_file():
            return f"Input file not found: {input_path}"
        if input_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
            return f"Unsupported input file type: {input_path.suffix}"
        return None
    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        return f"Input directory not found: {input_dir}"
    return None


def _validate_workbook_and_profile(args: argparse.Namespace, runtime_config: dict) -> dict[str, Any] | str:
    template_profile = args.template_profile or runtime_config.get("excel", {}).get("template_profile")
    if not template_profile:
        return "Missing template profile for Excel write."
    source_workbook = Path(args.draft_ledger)
    if not source_workbook.is_file():
        return f"Working ledger workbook not found: {source_workbook}"
    template_profile_config = load_template_profile(template_profile)
    profile_detail_sheet = _profile_detail_sheet(template_profile_config)
    if profile_detail_sheet and args.target_sheet != profile_detail_sheet:
        return f"Target sheet {args.target_sheet!r} does not match template profile detail sheet {profile_detail_sheet!r}."
    drift_report = validate_template_workbook(source_workbook, template_profile_config)
    if drift_report.get("blocked_write") is True or drift_report["status"] != "passed":
        return json.dumps(
            {
                "message": "Template workbook does not match profile",
                "template_drift_report": drift_report,
            },
            ensure_ascii=False,
        )
    return {
        "template_profile": template_profile,
        "template_drift_report": drift_report,
    }


def _make_run_id(input_path: str) -> str:
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_hash = sha1(f"{input_path}|{now}".encode("utf-8")).hexdigest()[:6]
    return f"run_{now}_{short_hash}"
def _load_runtime_config(config_path: str | None) -> dict:
    if not config_path:
        return {}
    with Path(config_path).open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)
    return loaded if isinstance(loaded, dict) else {}


def _profile_detail_sheet(profile: dict[str, Any]) -> str | None:
    detail = profile.get("sheets", {}).get("detail")
    if isinstance(detail, dict) and detail.get("name"):
        return str(detail["name"])
    return None


def _input_paths(args: argparse.Namespace) -> list[Path]:
    if args.input:
        return [Path(args.input)]
    input_dir = Path(args.input_dir)
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_INPUT_SUFFIXES
    )


def _payload_status(run_summary: RunSummary) -> str:
    blocked_units = run_summary.failed_units + run_summary.unmodeled_units
    if blocked_units == 0:
        return "completed"
    if run_summary.ready_rows > 0 or run_summary.review_required_rows > 0:
        return "partial"
    return "uncompleted"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _write_json_artifact(output_dir: Path, filename: str, value: Any) -> None:
    (output_dir / filename).write_text(
        json.dumps(_jsonable(value), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _user_message(run_summary: RunSummary, output_workbook: str | None) -> str:
    written = run_summary.ready_rows + run_summary.review_required_rows
    not_written = run_summary.failed_units + run_summary.unmodeled_units
    duplicate_messages = [
        message
        for message in run_summary.write_result.messages
        if "疑似重复" in message
    ] if run_summary.write_result is not None else []
    lines = [f"本次处理完成：共 {run_summary.invoice_units} 张发票。"]
    if not_written == 0 and run_summary.review_required_rows == 0 and not duplicate_messages:
        lines[0] = f"本次处理完成：共 {run_summary.invoice_units} 张发票，已全部写入发票信息采集。"
        lines.append("未发现需要复核或失败的页面。")
    elif not_written == 0:
        lines.extend(
            [
                f"已写入：{written} 张。",
                f"待复核：{run_summary.review_required_rows} 张。",
                "待复核原因已写入 Excel 的“识别提示”页。",
            ]
        )
    else:
        lines.extend(
            [
                f"已写入：{written} 张。",
                f"待复核：{run_summary.review_required_rows} 张。",
                f"未写入：{not_written} 张。",
                "未写入和待复核原因已写入 Excel 的“识别提示”页。",
            ]
        )
    if duplicate_messages:
        lines.append(f"疑似重复未写入：{len(duplicate_messages)} 张。")
        lines.append("疑似重复原因已写入 Excel 的“识别提示”页。")
        lines.extend(duplicate_messages)
    if output_workbook:
        lines.append(f"目标 Excel：{output_workbook}")
    return "\n".join(lines)


def run_cli(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    validation_error = _validate_required_runtime_args(args)
    if validation_error:
        print(validation_error, file=sys.stderr)
        return 2

    input_error = _validate_input_paths(args)
    if input_error:
        print(input_error, file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    input_source = args.input or args.input_dir
    run_id = args.run_id or _make_run_id(input_source)
    processed_at = datetime.now().replace(microsecond=0).isoformat()

    runtime_config = _load_runtime_config(args.config)
    workbook_validation = _validate_workbook_and_profile(args, runtime_config)
    if isinstance(workbook_validation, str):
        print(workbook_validation, file=sys.stderr)
        return 2
    template_profile = workbook_validation["template_profile"]
    input_paths = _input_paths(args)
    if not input_paths:
        print("No supported invoice input files found in input directory.", file=sys.stderr)
        return 2
    if args.check_only:
        payload = {
            "status": "passed",
            "check_only": True,
            "input": input_source,
            "input_count": len(input_paths),
            "draft_ledger": args.draft_ledger,
            "target_sheet": args.target_sheet,
            "output_dir": str(output_dir),
            "template_profile": template_profile,
            "message": "Check passed: arguments, input paths, config, and workbook/template compatibility are valid. OCR was not run and Excel was not modified.",
        }
        print(json.dumps(payload, ensure_ascii=False))
        print("检查通过：参数、输入、配置和工作台账模板兼容性可用。未运行 OCR，未修改 Excel。", file=sys.stderr)
        return 0
    input_results = [
        process_invoice_input(input_path, runtime_config, run_id, processed_at)
        for input_path in input_paths
    ]
    unit_results = [
        unit_result
        for input_result in input_results
        for unit_result in input_result["unit_results"]
    ]
    first_input_result = input_results[0]
    first_unit_result = unit_results[0]
    file_profile = first_input_result["file_profile"]
    schema_decision = first_unit_result["schema_decision"]
    field_candidates = first_unit_result["field_candidates"]
    invoice_record = first_unit_result["invoice_record"]
    ledger_rows = [
        row
        for result in unit_results
        for row in result["ledger_rows"]
    ]
    recognition_notices = build_recognition_notices(unit_results, ledger_rows)

    output_workbook: str | None = None
    source_workbook = Path(args.draft_ledger)
    if args.copy_output:
        output_workbook_path = output_dir / f"{source_workbook.stem}.{run_id}.draft.xlsx"
        if output_workbook_path.exists():
            print(
                f"Refusing to overwrite existing output workbook: {output_workbook_path}",
                file=sys.stderr,
            )
            return 2
        copy2(source_workbook, output_workbook_path)
    else:
        output_workbook_path = source_workbook
    write_result = write_with_template_profile(
        workbook_path=output_workbook_path,
        template_profile_path=template_profile,
        ledger_rows=ledger_rows,
        recognition_notices=recognition_notices,
        run_id=run_id,
        clear_existing=args.replace_existing,
    )
    output_workbook = str(output_workbook_path)
    ready_rows = sum(1 for row in ledger_rows if row.recognition_status == RecognitionStatus.READY)
    review_required_rows = sum(
        1 for row in ledger_rows if row.recognition_status == RecognitionStatus.REVIEW_REQUIRED
    )
    run_summary = RunSummary(
        run_id=run_id,
        input_count=len(input_paths),
        invoice_units=len(unit_results),
        ready_rows=ready_rows,
        review_required_rows=review_required_rows,
        unmodeled_units=sum(
            1
            for result in unit_results
            if result["invoice_record"].quality.status == RecognitionStatus.UNMODELED
        ),
        failed_units=sum(
            1
            for result in unit_results
            if result["invoice_record"].quality.status == RecognitionStatus.FAILED
        ),
        write_result=write_result,
        output_dir=str(output_dir),
    )
    payload_status = _payload_status(run_summary)
    user_message = _user_message(run_summary, output_workbook)

    if args.save_evidence in {"always", "auto"}:
        from .contracts import TextUnits

        if len(unit_results) > 1:
            _write_json_artifact(output_dir, "run_summary.json", run_summary)
            _write_json_artifact(output_dir, "write_result.json", write_result)
        for index, result in enumerate(unit_results, start=1):
            evidence_text_units = result["text_units"]
            if evidence_text_units is None:
                evidence_text_units = TextUnits(
                    invoice_unit_id=result["invoice_unit"].invoice_unit_id,
                    source="none",
                    units=[],
                )
            evidence_dir = output_dir
            if len(unit_results) > 1:
                page_part = "-".join(str(page) for page in result["invoice_unit"].page_range)
                evidence_dir = output_dir / "units" / f"{index:03d}_page_{page_part or 'none'}"
                evidence_dir.mkdir(parents=True, exist_ok=True)
            save_evidence_bundle(
                output_dir=evidence_dir,
                file_profile=result["file_profile"],
                invoice_units=result["invoice_units"],
                text_units=evidence_text_units,
                schema_decision=result["schema_decision"],
                field_candidates=result["field_candidates"],
                invoice_record=result["invoice_record"],
                ledger_rows=result["ledger_rows"],
                write_result=write_result,
                run_summary=run_summary,
                ocr_result=result["ocr_result"],
            )

    full_payload = {
        "run_id": run_id,
        "status": payload_status,
        "input": args.input or args.input_dir,
        "input_count": len(input_paths),
        "draft_ledger": args.draft_ledger,
        "target_sheet": args.target_sheet,
        "output_dir": str(output_dir),
        "save_evidence": args.save_evidence,
        "file_profile": file_profile.model_dump(mode="json"),
        "schema_decision": schema_decision.model_dump(mode="json"),
        "field_candidates": field_candidates.model_dump(mode="json"),
        "invoice_record": invoice_record.model_dump(mode="json"),
        "ledger_rows": [row.model_dump(mode="json") for row in ledger_rows],
        "recognition_notices": [notice.model_dump(mode="json") for notice in recognition_notices],
        "write_result": write_result.model_dump(mode="json"),
        "run_summary": run_summary.model_dump(mode="json"),
        "output_workbook": output_workbook,
        "user_message": user_message,
        "results": [
            {
                "input": result["input"],
                "status": result["invoice_record"].quality.status.value,
                "invoice_unit": result["invoice_unit"].model_dump(mode="json"),
                "ledger_row_count": len(result["ledger_rows"]),
                "invoice_record": result["invoice_record"].model_dump(mode="json"),
            }
            for result in unit_results
        ],
    }
    summary_payload = {
        "run_id": run_id,
        "status": payload_status,
        "input": args.input or args.input_dir,
        "input_count": len(input_paths),
        "invoice_units": run_summary.invoice_units,
        "ready_rows": run_summary.ready_rows,
        "review_required_rows": run_summary.review_required_rows,
        "unmodeled_units": run_summary.unmodeled_units,
        "failed_units": run_summary.failed_units,
        "added_rows": write_result.added_rows,
        "skipped_duplicate_rows": write_result.skipped_duplicate_rows,
        "updated_rows": write_result.updated_rows,
        "output_dir": str(output_dir),
        "output_workbook": output_workbook,
        "save_evidence": args.save_evidence,
        "user_message": user_message,
        "write_messages": write_result.messages,
    }
    payload = full_payload if args.json_output == "full" else summary_payload
    print(json.dumps(payload, ensure_ascii=False))
    print(user_message, file=sys.stderr)
    return 0
