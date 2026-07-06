"""Environment checks for running the invoice ledger skill."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import sys
from typing import Any

import yaml

from .output.template_profile import load_template_profile, validate_template_workbook


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_IMPORTS = {
    "pymupdf": "fitz",
    "openpyxl": "openpyxl",
    "pyyaml": "yaml",
    "pydantic": "pydantic",
}
REQUIRED_FILES = [
    "SKILL.md",
    "scripts/fp_ledger.py",
    "config/runtime.yaml",
    "config/runtime_ocr_auto.yaml",
    "config/runtime_ocr_cpu.yaml",
    "config/runtime_ocr_gpu.yaml",
    "config/template_profiles/current.yaml",
    "templates/invoice-information-collection.xlsx",
]


def _check(name: str, status: str, message: str, fix: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "status": status, "message": message}
    if fix and status != "passed":
        payload["fix"] = fix
    return payload


def _has_module(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _version(package_name: str) -> str | None:
    try:
        return importlib.metadata.version(package_name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _load_yaml(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, str(exc)
    except yaml.YAMLError as exc:
        return None, str(exc)
    if not isinstance(loaded, dict):
        return None, "YAML root is not an object."
    return loaded, None


def _python_checks(project_root: Path) -> list[dict[str, Any]]:
    checks = []
    version_ok = sys.version_info >= (3, 11)
    checks.append(
        _check(
            "python_version",
            "passed" if version_ok else "failed",
            f"Python {platform.python_version()} at {sys.executable}",
            "Use Python 3.11+ inside the project .venv.",
        )
    )
    exe = Path(sys.executable).resolve()
    expected_part = (project_root / ".venv").resolve()
    in_project_venv = expected_part in exe.parents
    checks.append(
        _check(
            "project_venv",
            "passed" if in_project_venv else "warning",
            f"Current interpreter: {exe}",
            r"Run commands with .\.venv\Scripts\python.exe from the skill root.",
        )
    )
    return checks


def _required_file_checks(project_root: Path) -> list[dict[str, Any]]:
    checks = []
    for relative in REQUIRED_FILES:
        path = project_root / relative
        checks.append(
            _check(
                f"required_file:{relative}",
                "passed" if path.exists() else "failed",
                f"{relative} {'exists' if path.exists() else 'is missing'}",
            )
        )
    return checks


def _dependency_checks() -> list[dict[str, Any]]:
    checks = []
    for package_name, module_name in BASE_IMPORTS.items():
        installed = _has_module(module_name)
        version = _version(package_name)
        checks.append(
            _check(
                f"dependency:{package_name}",
                "passed" if installed else "failed",
                f"{package_name} {version or 'not installed'}",
                r".\.venv\Scripts\python.exe -m pip install -r requirements.txt",
            )
        )
    return checks


def _config_checks(project_root: Path) -> list[dict[str, Any]]:
    checks = []
    for relative in ["config/runtime.yaml", "config/runtime_ocr_auto.yaml", "config/runtime_ocr_cpu.yaml", "config/runtime_ocr_gpu.yaml"]:
        config, error = _load_yaml(project_root / relative)
        checks.append(
            _check(
                f"config:{relative}",
                "passed" if config is not None else "failed",
                f"{relative} loaded" if config is not None else f"{relative}: {error}",
            )
        )
    return checks


def _template_check(project_root: Path) -> list[dict[str, Any]]:
    profile_path = project_root / "config/template_profiles/current.yaml"
    workbook_path = project_root / "templates/invoice-information-collection.xlsx"
    if not profile_path.exists() or not workbook_path.exists():
        return [
            _check(
                "template_profile",
                "failed",
                "Template profile or template workbook is missing.",
            )
        ]
    profile = load_template_profile(profile_path)
    report = validate_template_workbook(workbook_path, profile)
    return [
        _check(
            "template_profile",
            "passed" if report.get("status") == "passed" else "failed",
            f"Template validation status: {report.get('status')}",
        )
    ]


def _ocr_checks() -> list[dict[str, Any]]:
    checks = []
    paddle_installed = _has_module("paddle")
    paddleocr_installed = _has_module("paddleocr")
    checks.append(
        _check(
            "ocr_dependency:paddlepaddle-gpu",
            "passed" if paddle_installed else "warning",
            f"paddlepaddle-gpu {_version('paddlepaddle-gpu') or _version('paddlepaddle') or 'not installed'}",
            r".\.venv\Scripts\python.exe -m pip install -r requirements-ocr-gpu.txt",
        )
    )
    checks.append(
        _check(
            "ocr_dependency:paddleocr",
            "passed" if paddleocr_installed else "warning",
            f"paddleocr {_version('paddleocr') or 'not installed'}",
            r".\.venv\Scripts\python.exe -m pip install -r requirements-ocr-gpu.txt",
        )
    )
    if not paddle_installed or not paddleocr_installed:
        return checks
    try:
        import paddle  # type: ignore

        cuda_ready = bool(paddle.device.is_compiled_with_cuda())
        device = str(paddle.device.get_device())
        if cuda_ready and device.startswith("gpu"):
            message = f"CUDA compiled={cuda_ready}; current device={device}; use config/runtime_ocr_gpu.yaml"
        else:
            message = f"CUDA compiled={cuda_ready}; current device={device}; use config/runtime_ocr_cpu.yaml"
        checks.append(
            _check(
                "ocr_gpu",
                "passed",
                message,
            )
        )
    except Exception as exc:  # pragma: no cover - depends on optional OCR runtime
        checks.append(
            _check(
                "ocr_gpu",
                "warning",
                f"Could not inspect Paddle GPU runtime: {exc}",
            )
        )
    return checks


def _output_check(project_root: Path) -> list[dict[str, Any]]:
    output_dir = project_root / "output"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / f".doctor_write_probe_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return [_check("output_dir", "failed", f"output is not writable: {exc}")]
    return [_check("output_dir", "passed", "output is writable")]


def _overall_status(checks: list[dict[str, Any]]) -> str:
    failed = [item for item in checks if item["status"] == "failed"]
    if failed:
        return "blocked"
    ocr_warnings = [
        item for item in checks if item["name"].startswith("ocr_") and item["status"] != "passed"
    ]
    if ocr_warnings:
        return "text_only_ready"
    warnings = [item for item in checks if item["status"] == "warning"]
    return "partial" if warnings else "ready"


def build_doctor_report(project_root: Path | None = None, include_ocr: bool = True) -> dict[str, Any]:
    root = (project_root or PROJECT_ROOT).resolve()
    checks: list[dict[str, Any]] = []
    checks.extend(_python_checks(root))
    checks.extend(_required_file_checks(root))
    checks.extend(_dependency_checks())
    checks.extend(_config_checks(root))
    checks.extend(_template_check(root))
    checks.extend(_output_check(root))
    if include_ocr:
        checks.extend(_ocr_checks())
    status = _overall_status(checks)
    return {
        "status": status,
        "project_root": str(root),
        "python": sys.executable,
        "checks": checks,
    }


def render_human_summary(report: dict[str, Any]) -> str:
    failed = [item for item in report["checks"] if item["status"] == "failed"]
    warnings = [item for item in report["checks"] if item["status"] == "warning"]
    lines = [f"环境检查结果：{report['status']}"]
    if failed:
        lines.append("阻断项：")
        lines.extend(f"- {item['message']}" for item in failed)
    if warnings:
        lines.append("注意项：")
        lines.extend(f"- {item['message']}" for item in warnings)
    fixes = [item["fix"] for item in failed + warnings if item.get("fix")]
    if fixes:
        lines.append("建议：")
        for fix in dict.fromkeys(fixes):
            lines.append(f"- {fix}")
    if not failed and not warnings:
        lines.append("基础依赖、配置、模板和 OCR 环境均可用。")
    return "\n".join(lines)


def run_doctor(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Check invoice ledger skill environment.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument("--no-ocr", action="store_true", help="Skip optional OCR runtime checks.")
    args = parser.parse_args(argv)

    report = build_doctor_report(include_ocr=not args.no_ocr)
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        print(render_human_summary(report))
        print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] in {"ready", "text_only_ready", "partial"} else 2
