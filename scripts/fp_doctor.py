from __future__ import annotations

from pathlib import Path
import sys


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


_configure_stdio()

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = PROJECT_ROOT / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

from invoice_ledger.doctor import run_doctor  # noqa: E402


def main() -> int:
    return run_doctor()


if __name__ == "__main__":
    raise SystemExit(main())
