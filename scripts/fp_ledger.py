from __future__ import annotations

import os
from pathlib import Path
import sys


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_configure_stdio()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.chdir(PROJECT_ROOT)
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from invoice_ledger.cli import build_parser as build_parser, run_cli  # noqa: E402


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
