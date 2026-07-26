"""Schema loading helpers."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml


from .._paths import PROJECT_ROOT  # noqa: F401  (re-exported for downstream use)


@lru_cache(maxsize=16)
def load_schema(schema_id: str = "standard-invoice") -> dict[str, Any]:
    schema_path = PROJECT_ROOT / "schemas" / f"{schema_id}.yaml"
    with schema_path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)
    if not isinstance(loaded, dict):
        raise ValueError(f"Invalid schema: {schema_id}")
    return loaded


@lru_cache(maxsize=1)
def load_schema_catalog() -> list[dict[str, Any]]:
    catalog_path = PROJECT_ROOT / "schemas" / "catalog.yaml"
    with catalog_path.open("r", encoding="utf-8") as file:
        loaded = yaml.safe_load(file)
    schemas = loaded.get("schemas", []) if isinstance(loaded, dict) else []
    return [schema for schema in schemas if isinstance(schema, dict)]


def field_aliases(schema: dict[str, Any], field_name: str) -> list[str]:
    field = schema.get("fields", {}).get(field_name, {})
    aliases = field.get("aliases", [])
    if not isinstance(aliases, list):
        return []
    normalized: list[str] = []
    for alias in aliases:
        if isinstance(alias, str):
            normalized.append(alias)
        elif isinstance(alias, dict):
            normalized.extend(str(key) for key in alias)
    return normalized


def field_exclude_terms(schema: dict[str, Any], field_name: str) -> list[str]:
    field = schema.get("fields", {}).get(field_name, {})
    terms = field.get("exclude_terms", [])
    return terms if isinstance(terms, list) else []
