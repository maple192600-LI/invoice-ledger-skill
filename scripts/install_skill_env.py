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
OcrMode = Literal["auto", "gpu", "cpu", "none"]


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
    result = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode == 0:
        return
    if result.stdout:
        print(result.stdout[-4000:], file=sys.stderr)
    if result.stderr:
        print(result.stderr[-4000:], file=sys.stderr)
    raise subprocess.CalledProcessError(result.returncode, command)


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
    parser.add_argument("--dry-run", action="store_true", help="Print the install plan without running it.")
    parser.add_argument("--verbose", action="store_true", help="Stream installer subprocess output.")
    args = parser.parse_args(argv)

    if sys.version_info < (3, 11):
        print("Python 3.11+ is required to create this skill environment.", file=sys.stderr)
        return 2
    try:
        plan = build_install_plan(args.ocr)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    for command in plan["commands"]:
        _run(command, PROJECT_ROOT, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
