from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Literal


PROJECT_ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = PROJECT_ROOT / ".venv"
LEDGER_TEMPLATE = PROJECT_ROOT / "发票采集台账.xlsx"
USER_SETTINGS = PROJECT_ROOT / "config" / "user_settings.local.yaml"
OcrMode = Literal["auto", "gpu", "cpu", "none"]


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_configure_stdio()


def _venv_python(root: Path = PROJECT_ROOT) -> Path:
    if sys.platform == "win32":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def detect_nvidia_gpu() -> dict:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return {"available": False, "tool": None, "gpus": []}
    result = subprocess.run(
        [nvidia_smi, "--query-gpu=name", "--format=csv,noheader"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return {"available": False, "tool": nvidia_smi, "gpus": []}
    gpus = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return {"available": bool(gpus), "tool": nvidia_smi, "gpus": gpus}


def select_ocr_requirement(ocr: OcrMode, gpu_available: bool) -> str | None:
    if ocr == "none":
        return None
    if ocr == "gpu":
        if not gpu_available:
            raise ValueError("Requested GPU OCR install, but nvidia-smi did not report a GPU.")
        return "requirements-ocr-gpu.txt"
    if ocr == "cpu":
        return "requirements-ocr-cpu.txt"
    return "requirements-ocr-gpu.txt" if gpu_available else "requirements-ocr-cpu.txt"


def build_install_plan(ocr: OcrMode, project_root: Path = PROJECT_ROOT) -> dict:
    gpu = detect_nvidia_gpu()
    ocr_requirements = select_ocr_requirement(ocr, gpu["available"])
    venv_python = _venv_python(project_root)
    commands = [
        [sys.executable, "-m", "venv", str(project_root / ".venv")],
        [str(venv_python), "-m", "pip", "install", "-q", "-r", "requirements.txt"],
    ]
    if ocr_requirements:
        commands.append([str(venv_python), "-m", "pip", "install", "-q", "-r", ocr_requirements])
    commands.append([str(venv_python), "scripts/fp_doctor.py"])
    return {
        "project_root": str(project_root),
        "venv_python": str(venv_python),
        "gpu": gpu,
        "ocr_mode": ocr,
        "ocr_requirements": ocr_requirements,
        "commands": commands,
    }


def _run(command: list[str], cwd: Path, verbose: bool = False) -> None:
    print(" ".join(command), flush=True)
    if verbose:
        subprocess.run(command, cwd=cwd, check=True)
        return
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return
    if result.stdout:
        print(result.stdout[-4000:], file=sys.stderr)
    if result.stderr:
        print(result.stderr[-4000:], file=sys.stderr)
    raise subprocess.CalledProcessError(result.returncode, command)


def _saved_ledger(settings_path: Path = USER_SETTINGS) -> Path | None:
    if not settings_path.exists():
        return None
    try:
        text = settings_path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        value = json.loads(text).get("draft_ledger")
    except (json.JSONDecodeError, AttributeError):
        value = next(
            (
                line.split(":", 1)[1].strip().strip("'\"")
                for line in text.splitlines()
                if line.strip().startswith("draft_ledger:")
            ),
            None,
        )
    if not value:
        return None
    path = Path(value)
    return path if path.is_file() else None


def configure_ledger(
    location: str,
    project_root: Path = PROJECT_ROOT,
) -> tuple[Path, bool]:
    template = project_root / LEDGER_TEMPLATE.name
    settings_path = project_root / "config" / USER_SETTINGS.name
    selected = Path(location).expanduser()
    if selected.is_dir():
        destination = selected / template.name
    elif selected.suffix.lower() == ".xlsx":
        destination = selected
    elif not selected.suffix:
        destination = selected / template.name
    else:
        raise ValueError("台账位置必须是文件夹或 .xlsx 文件。")
    destination = destination.resolve()
    if destination == template.resolve():
        raise ValueError("请选择 Skill 根目录以外的位置，根目录台账必须保留为空白母版。")
    if destination.exists() and not destination.is_file():
        raise ValueError("所选台账路径不是 Excel 文件。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    created = not destination.exists()
    if created:
        shutil.copy2(template, destination)
    settings_path.write_text(
        json.dumps({"draft_ledger": str(destination)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return destination, created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create the project .venv and install invoice skill dependencies into it."
    )
    parser.add_argument(
        "--ocr",
        choices=["auto", "gpu", "cpu", "none"],
        default="auto",
        help="OCR dependency mode. auto installs GPU OCR when NVIDIA GPU is detected, otherwise CPU OCR.",
    )
    parser.add_argument(
        "--ledger",
        help="User-selected folder or .xlsx path for the persistent invoice ledger.",
    )
    parser.add_argument("--verbose", action="store_true", help="Stream installer subprocess output.")
    args = parser.parse_args(argv)

    if sys.version_info < (3, 11):
        print("Python 3.11+ is required to create this skill environment.", file=sys.stderr)
        return 2
    saved_ledger = _saved_ledger()
    ledger_location = args.ledger
    if saved_ledger is None and not ledger_location:
        if not sys.stdin.isatty():
            print(
                "首次安装必须选择发票采集台账保存位置，请使用 --ledger <文件夹或.xlsx路径>。",
                file=sys.stderr,
            )
            return 2
        ledger_location = input(
            "发票采集台账希望保存到哪个文件夹？也可以直接提供完整的 .xlsx 路径："
        ).strip()
    if ledger_location:
        try:
            saved_ledger, created = configure_ledger(ledger_location)
        except (OSError, ValueError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        action = "已创建" if created else "已沿用"
        print(f"{action}发票采集台账：{saved_ledger}")
    try:
        plan = build_install_plan(args.ocr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for command in plan["commands"]:
        _run(command, PROJECT_ROOT, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
