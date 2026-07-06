"""OCR table line item extraction."""

from __future__ import annotations

from typing import Any

from ..contracts import TextUnit, TextUnits
from ._helpers import _ocr_table_item, _x0, _y0
from ._line_item_sequence_helpers import _textual_spec_tokens


def _extract_ocr_table_items(text_units: TextUnits, schema: dict[str, Any]) -> list[dict[str, Any]]:
    table_config = schema.get("ocr_table", {})
    if not isinstance(table_config, dict):
        table_config = {}
    item_start_max_x = float(table_config.get("item_start_max_x", float("inf")))
    end_marker_min_x = float(table_config.get("end_marker_min_x", float("inf")))
    end_marker_min_y_delta = float(table_config.get("end_marker_min_y_delta", float("inf")))
    textual_spec_tokens = _textual_spec_tokens(schema)
    units = [unit for unit in text_units.units if unit.text.strip()]
    item_starts = [
        index
        for index, unit in enumerate(units)
        if unit.text.strip().startswith("*") and _x0(unit) < item_start_max_x
    ]
    items: list[dict[str, Any]] = []
    for start_position, start_index in enumerate(item_starts):
        end_index = item_starts[start_position + 1] if start_position + 1 < len(item_starts) else len(units)
        group: list[TextUnit] = []
        for unit in units[start_index:end_index]:
            if unit.text.strip() in {"备注"} or "价税合计" in unit.text:
                break
            if (
                unit.text.strip().startswith("¥")
                and _x0(unit) > end_marker_min_x
                and _y0(unit) > _y0(units[start_index]) + end_marker_min_y_delta
            ):
                break
            group.append(unit)
        item = _ocr_table_item(group, table_config, textual_spec_tokens)
        if item:
            items.append(item)
    return items
