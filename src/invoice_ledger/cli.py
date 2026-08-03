"""CLI orchestration for the invoice draft ledger pipeline."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys
from datetime import datetime
from hashlib import sha1
from shutil import copy2
from typing import Any, Sequence

import yaml

from ._paths import PROJECT_ROOT
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


SUPPORTED_INPUT_SUFFIXES = {".pdf", ".txt", ".md", ".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp", ".xml"}
AUTO_OCR_CONFIG = "config/runtime_ocr_auto.yaml"


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
    parser.add_argument(
        "--config",
        required=False,
        default=AUTO_OCR_CONFIG,
        help="Runtime config YAML path. Defaults to automatic OCR routing.",
    )
    parser.add_argument("--target-sheet", required=False, help="Target worksheet name.")
    parser.add_argument("--output-dir", required=False, help="Directory for evidence and JSON output.")
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate arguments, input paths, config, and workbook/template compatibility without OCR or Excel writing.",
    )
    parser.add_argument(
        "--save-evidence",
        default="failed",
        choices=["failed", "none"],
        help="Unit evidence mode. failed saves only failed, unmodeled, or review-required unit evidence; none saves no unit evidence.",
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
        help="Forbidden compatibility flag. Existing ledger data is append-only.",
    )
    return parser


def _validate_required_runtime_args(args: argparse.Namespace) -> str | None:
    required = {
        "draft_ledger": "draft ledger",
        "config": "config",
        "output_dir": "output dir",
    }
    missing = [label for name, label in required.items() if not getattr(args, name)]
    if missing:
        return "Missing required runtime argument(s): " + ", ".join(missing)
    if bool(args.input) == bool(args.input_dir):
        return "Provide exactly one input source: --input or --input-dir."
    if args.write_in_place and args.copy_output:
        return "Refusing conflicting output options: --write-in-place cannot be used with --copy-output."
    if args.replace_existing:
        return "Refusing --replace-existing because invoice ledgers are append-only."
    if args.update_existing:
        return "Refusing --update-existing because profile-managed row update is not implemented yet."
    return None


def _validate_input_paths(args: argparse.Namespace) -> str | None:
    if args.input:
        input_path = Path(args.input)
        if not input_path.is_file():
            return f"Input file not found: {input_path}"
        suffix = input_path.suffix.lower()
        if suffix == ".ofd":
            return "暂不支持 OFD 版式文件：请到电子税务局税务数字账户下载 PDF 版式文件后重试。"
        if suffix not in SUPPORTED_INPUT_SUFFIXES:
            return f"Unsupported input file type: {suffix}"
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
    template_profile_path = _project_path(template_profile)
    template_profile_config = load_template_profile(template_profile_path)
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
        "template_profile": str(template_profile_path),
        "template_drift_report": drift_report,
    }


def _make_run_id(input_path: str) -> str:
    now = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    short_hash = sha1(f"{input_path}|{now}".encode("utf-8")).hexdigest()[:6]
    return f"run_{now}_{short_hash}"


def _project_path(path: str | Path) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else PROJECT_ROOT / candidate


def _has_nvidia_gpu() -> bool:
    from shutil import which
    import subprocess

    nvidia_smi = which("nvidia-smi")
    if not nvidia_smi:
        return False
    result = subprocess.run(
        [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def _resolve_config_path(config_path: str | None) -> Path | None:
    if not config_path:
        return None
    candidate = _project_path(config_path)
    if Path(config_path).as_posix() == AUTO_OCR_CONFIG or candidate == PROJECT_ROOT / AUTO_OCR_CONFIG:
        selected = "config/runtime_ocr_gpu.yaml" if _has_nvidia_gpu() else "config/runtime_ocr_cpu.yaml"
        return _project_path(selected)
    return candidate


def _load_runtime_config(config_path: str | None) -> dict:
    if not config_path:
        return {}
    is_auto_ocr = (
        Path(config_path).as_posix() == AUTO_OCR_CONFIG
        or _project_path(config_path) == PROJECT_ROOT / AUTO_OCR_CONFIG
    )
    resolved_config = _resolve_config_path(config_path)
    with resolved_config.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)
    if is_auto_ocr and isinstance(loaded, dict):
        ocr_config = loaded.get("ocr")
        if isinstance(ocr_config, dict) and str(ocr_config.get("device", "")).startswith("gpu"):
            ocr_config["fallback_device"] = "cpu"
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


def _ocr_runtime_available() -> bool:
    return (
        importlib.util.find_spec("paddle") is not None
        and importlib.util.find_spec("paddleocr") is not None
    )


def _preflight_routes(
    input_paths: list[Path],
    runtime_config: dict[str, Any],
    run_id: str,
    processed_at: str,
) -> dict[str, Any]:
    results = [
        process_invoice_input(
            path,
            runtime_config,
            run_id,
            processed_at,
            allow_ocr=False,
        )
        for path in input_paths
    ]
    direct_files = 0
    direct_ready_files = 0
    structured_files = 0
    ocr_required_files = 0
    ocr_fallback_files = 0
    unsupported_files = 0
    ocr_required_pages = 0
    for result in results:
        unit_results = result["unit_results"]
        if not unit_results:
            unsupported_files += 1
            continue
        file_has_direct = False
        file_has_ready_direct = False
        file_has_structured = False
        file_has_ocr_required = False
        file_has_ocr_fallback = False
        file_has_unsupported = False
        for unit_result in unit_results:
            source = unit_result.get("selected_source")
            status = unit_result["invoice_record"].quality.status
            unit = unit_result["invoice_unit"]
            if source == "structured":
                file_has_structured = True
            elif source in {"pdf_text", "text_file"}:
                file_has_direct = True
                if status == RecognitionStatus.READY:
                    file_has_ready_direct = True
            elif unit.unit_type in {"image", "pdf_ocr_page"}:
                file_has_ocr_required = True
                ocr_required_pages += len(unit.page_range) or 1
            if unit_result.get("fallback_attempted") or unit_result.get("fallback_reason") == "direct_not_ready":
                file_has_ocr_fallback = True
                ocr_required_pages += len(unit.page_range) or 1
            if unit_result["invoice_record"].quality.status in {
                RecognitionStatus.UNMODELED,
                RecognitionStatus.FAILED,
            } and (
                (
                    source == "none"
                    and unit.unit_type not in {"image", "pdf_ocr_page"}
                    and unit_result.get("fallback_reason") != "direct_not_ready"
                )
                or any(
                    term in " ".join(unit_result["schema_decision"].reason or [])
                    for term in ("未建模", "不支持", "未支持", "当前票种")
                )
            ):
                file_has_unsupported = True
        direct_files += int(file_has_direct)
        direct_ready_files += int(file_has_ready_direct)
        structured_files += int(file_has_structured)
        ocr_required_files += int(file_has_ocr_required)
        ocr_fallback_files += int(file_has_ocr_fallback)
        unsupported_files += int(file_has_unsupported)
    ocr_enabled = runtime_config.get("ocr", {}).get("enabled") is True
    selected_device = runtime_config.get("ocr", {}).get("device")
    if ocr_required_pages and ocr_enabled and not _ocr_runtime_available():
        status = "ocr_required"
        return_code = 3
        message = "发现需要 OCR 的文件，但 OCR 环境尚未安装。"
    elif ocr_required_pages and not ocr_enabled:
        status = "blocked"
        return_code = 2
        message = "发现需要 OCR 的文件，但当前配置未启用 OCR。"
    else:
        status = "passed"
        return_code = 0
        message = "预检完成：已按文件和页面确定直接解析与 OCR 回退路径，未运行 OCR，未修改 Excel。"
    return {
        "status": status,
        "return_code": return_code,
        "check_only": True,
        "direct_files": direct_files,
        "direct_ready_files": direct_ready_files,
        "structured_files": structured_files,
        "ocr_required_files": ocr_required_files,
        "ocr_fallback_files": ocr_fallback_files,
        "unsupported_files": unsupported_files,
        "ocr_required_pages": ocr_required_pages,
        "ocr_enabled": ocr_enabled,
        "ocr_environment_available": _ocr_runtime_available() if ocr_required_pages else None,
        "selected_ocr_device": selected_device,
        "message": message,
    }


def _runtime_route_summary(unit_results: list[dict[str, Any]]) -> dict[str, int]:
    file_routes: dict[str, dict[str, bool | int]] = {}
    for result in unit_results:
        source_file = str(result.get("input") or result["invoice_unit"].source_file)
        route = file_routes.setdefault(
            source_file,
            {
                "direct": False,
                "direct_ready": False,
                "structured": False,
                "ocr_required": False,
                "ocr_fallback": False,
                "ocr_improved": False,
                "ocr_failed": False,
                "ocr_pages": 0,
            },
        )
        source = result.get("selected_source")
        status = result["invoice_record"].quality.status
        if source == "structured":
            route["structured"] = True
        elif source in {"pdf_text", "text_file"} or result.get("direct_status") is not None:
            route["direct"] = True
            route["direct_ready"] = bool(route["direct_ready"] or (
                result.get("direct_status") == RecognitionStatus.READY.value
                or (result.get("direct_status") is None and status == RecognitionStatus.READY)
            ))
        if result.get("fallback_reason") == "ocr_required":
            route["ocr_required"] = True
            route["ocr_pages"] += len(result["invoice_unit"].page_range) or 1
            if result.get("ocr_status") in {"failed", "unsupported"}:
                route["ocr_failed"] = True
        if result.get("fallback_attempted"):
            route["ocr_fallback"] = True
            route["ocr_pages"] += len(result["invoice_unit"].page_range) or 1
            if result.get("selection_reason") == "ocr_status_higher":
                route["ocr_improved"] = True
            if result.get("ocr_status") in {"failed", "unsupported"}:
                route["ocr_failed"] = True
    return {
        "direct_files": sum(bool(route["direct"]) for route in file_routes.values()),
        "direct_ready_files": sum(bool(route["direct_ready"]) for route in file_routes.values()),
        "structured_files": sum(bool(route["structured"]) for route in file_routes.values()),
        "ocr_required_files": sum(bool(route["ocr_required"]) for route in file_routes.values()),
        "ocr_fallback_files": sum(bool(route["ocr_fallback"]) for route in file_routes.values()),
        "ocr_improved_files": sum(bool(route["ocr_improved"]) for route in file_routes.values()),
        "ocr_failed_files": sum(bool(route["ocr_failed"]) for route in file_routes.values()),
        "ocr_required_pages": sum(int(route["ocr_pages"]) for route in file_routes.values()),
    }


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


def _unit_needs_evidence(unit_result: dict[str, Any]) -> bool:
    invoice_record = unit_result["invoice_record"]
    invoice_unit = unit_result["invoice_unit"]
    ledger_rows = unit_result["ledger_rows"]
    if invoice_record.quality.status != RecognitionStatus.READY:
        return True
    if invoice_unit.status != RecognitionStatus.READY:
        return True
    return any(
        row.recognition_status != RecognitionStatus.READY or bool(row.review_remark)
        for row in ledger_rows
    )


def _user_message(run_summary: RunSummary, output_workbook: str | None) -> str:
    recognized_units = run_summary.ready_units + run_summary.review_required_units
    recognized_rows = run_summary.ready_rows + run_summary.review_required_rows
    not_written = run_summary.failed_units + run_summary.unmodeled_units
    added_rows = run_summary.write_result.added_rows if run_summary.write_result is not None else 0
    write_messages = run_summary.write_result.messages if run_summary.write_result is not None else []
    skipped_duplicate_messages = [
        message
        for message in write_messages
        if "疑似重复" in message and "本次未写入" in message
    ]
    weak_duplicate_messages = [
        message
        for message in write_messages
        if "疑似重复（弱身份票）" in message and "本次已写入" in message
    ]
    lines = [
        f"本次处理完成：共处理 {run_summary.input_count} 个文件。",
        f"识别结果：{recognized_units} 张发票、{recognized_rows} 条明细；"
        f"本次新增写入 {added_rows} 条明细。",
    ]
    if run_summary.review_required_units:
        lines.append(
            f"待复核：{run_summary.review_required_units} 张发票，共 {run_summary.review_required_rows} 条明细。"
        )
        lines.append("待复核原因已写入 Excel 的“识别提示”页。")
    if not_written:
        lines.append(f"未形成可写入结果：{not_written} 个处理单元。")
        lines.append("未写入原因已写入 Excel 的“识别提示”页。")
    if (
        not not_written
        and not run_summary.review_required_units
        and not skipped_duplicate_messages
        and not weak_duplicate_messages
    ):
        lines.append("本批识别结果全部通过，无需复核。")
    if skipped_duplicate_messages:
        lines.append(f"疑似重复未写入：{len(skipped_duplicate_messages)} 张发票。")
    if weak_duplicate_messages:
        lines.append(f"弱身份疑似重复已写入：{len(weak_duplicate_messages)} 张发票，请人工确认。")
    if skipped_duplicate_messages or weak_duplicate_messages:
        lines.append("疑似重复详情已写入 Excel 的“识别提示”页。")
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
    if not args.target_sheet:
        args.target_sheet = runtime_config.get("excel", {}).get("default_target_sheet")
    if not args.target_sheet:
        print("Missing target sheet. Provide --target-sheet or set excel.default_target_sheet in config.", file=sys.stderr)
        return 2
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
        payload = _preflight_routes(input_paths, runtime_config, run_id, processed_at)
        payload.update(
            {
                "input": input_source,
                "input_count": len(input_paths),
                "draft_ledger": args.draft_ledger,
                "target_sheet": args.target_sheet,
                "output_dir": str(output_dir),
                "template_profile": template_profile,
            }
        )
        print(json.dumps(payload, ensure_ascii=False))
        print(payload["message"], file=sys.stderr)
        return int(payload["return_code"])
    input_results = [
        process_invoice_input(input_path, runtime_config, run_id, processed_at)
        for input_path in input_paths
    ]
    unit_results = [
        unit_result
        for input_result in input_results
        for unit_result in input_result["unit_results"]
    ]
    route_summary = _runtime_route_summary(unit_results)
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
    )
    output_workbook = str(output_workbook_path)
    ready_rows = sum(1 for row in ledger_rows if row.recognition_status == RecognitionStatus.READY)
    review_required_rows = sum(
        1 for row in ledger_rows if row.recognition_status == RecognitionStatus.REVIEW_REQUIRED
    )
    review_unit_ids = {
        row.invoice_unit_id
        for row in ledger_rows
        if row.recognition_status == RecognitionStatus.REVIEW_REQUIRED
    }
    ready_unit_ids = {
        row.invoice_unit_id
        for row in ledger_rows
        if row.recognition_status == RecognitionStatus.READY and row.invoice_unit_id not in review_unit_ids
    }
    run_summary = RunSummary(
        run_id=run_id,
        input_count=len(input_paths),
        invoice_units=len(unit_results),
        ready_units=len(ready_unit_ids),
        review_required_units=len(review_unit_ids),
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

    _write_json_artifact(
        output_dir,
        "run_summary.json",
        {**run_summary.model_dump(mode="json"), **route_summary},
    )
    _write_json_artifact(output_dir, "write_result.json", write_result)

    if args.save_evidence == "failed":
        from .contracts import TextUnits

        for index, result in enumerate(unit_results, start=1):
            if not _unit_needs_evidence(result):
                continue
            evidence_text_units = result["text_units"]
            if evidence_text_units is None:
                evidence_text_units = TextUnits(
                    invoice_unit_id=result["invoice_unit"].invoice_unit_id,
                    source="none",
                    units=[],
                )
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
        "route_summary": route_summary,
        "results": [
            {
                "input": result["input"],
                "status": result["invoice_record"].quality.status.value,
                "selected_source": result.get("selected_source"),
                "fallback_reason": result.get("fallback_reason"),
                "direct_status": result.get("direct_status"),
                "ocr_status": result.get("ocr_status"),
                "selection_reason": result.get("selection_reason"),
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
        "recognized_invoices": run_summary.ready_units + run_summary.review_required_units,
        "ready_units": run_summary.ready_units,
        "review_required_units": run_summary.review_required_units,
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
        "write_message_count": len(write_result.messages),
        **route_summary,
    }
    payload = full_payload if args.json_output == "full" else summary_payload
    print(json.dumps(payload, ensure_ascii=False))
    print(user_message, file=sys.stderr)
    return 0
