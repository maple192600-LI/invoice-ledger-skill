"""Validation policy helpers for eval reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_validation_basis(config_path: str | Path, sample_role_key: str) -> dict[str, Any]:
    path = Path(config_path)
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    sample_roles = payload.get("sample_roles", {})
    quality_gates = payload.get("primary_quality_gates", [])
    sample_role = sample_roles.get(sample_role_key)
    if not sample_role:
        raise ValueError(f"Missing validation sample role: {sample_role_key}")
    if not isinstance(quality_gates, list) or not quality_gates:
        raise ValueError("Missing validation primary_quality_gates")
    return {
        "sample_role": sample_role,
        "primary_quality_gates": [str(item) for item in quality_gates],
    }
