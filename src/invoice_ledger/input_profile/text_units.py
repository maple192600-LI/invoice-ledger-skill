"""Text unit normalization for the invoice draft ledger pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable

from ..contracts import TextUnit, TextUnits


@dataclass(frozen=True)
class LogicalTextLine:
    page: int
    text: str
    units: tuple[TextUnit, ...]
    bbox: list[float] | None
    confidence: float | None


def _bbox(unit: TextUnit) -> list[float]:
    return unit.bbox or [0.0, 0.0, 0.0, 0.0]


def _has_real_bbox(unit: TextUnit) -> bool:
    return len(_bbox(unit)) == 4 and any(float(value) != 0.0 for value in _bbox(unit))


def _center_y(unit: TextUnit) -> float:
    bbox = _bbox(unit)
    return (float(bbox[1]) + float(bbox[3])) / 2


def _x0(unit: TextUnit) -> float:
    return float(_bbox(unit)[0])


def _height(unit: TextUnit) -> float:
    bbox = _bbox(unit)
    return max(0.0, float(bbox[3]) - float(bbox[1]))


def _line_bbox(units: list[TextUnit]) -> list[float] | None:
    real_boxes = [_bbox(unit) for unit in units if _has_real_bbox(unit)]
    if not real_boxes:
        return None
    return [
        min(float(box[0]) for box in real_boxes),
        min(float(box[1]) for box in real_boxes),
        max(float(box[2]) for box in real_boxes),
        max(float(box[3]) for box in real_boxes),
    ]


def _line_confidence(units: list[TextUnit]) -> float | None:
    confidences = [float(unit.confidence) for unit in units if unit.confidence is not None]
    if not confidences:
        return None
    return min(confidences)


def logical_text_lines(text_units: TextUnits, y_tolerance: float | None = None) -> list[LogicalTextLine]:
    real_units = [unit for unit in text_units.units if unit.text.strip() and _has_real_bbox(unit)]
    heights = [_height(unit) for unit in real_units if _height(unit) > 0]
    tolerance = y_tolerance if y_tolerance is not None else max(8.0, (median(heights) * 0.6 if heights else 8.0))
    grouped: list[list[TextUnit]] = []
    without_bbox: list[TextUnit] = []

    for unit in sorted(text_units.units, key=lambda item: (item.page, _center_y(item), _x0(item), item.order)):
        if not unit.text.strip():
            continue
        if not _has_real_bbox(unit):
            without_bbox.append(unit)
            continue
        for group in grouped:
            if group[0].page == unit.page and abs(_center_y(group[0]) - _center_y(unit)) <= tolerance:
                group.append(unit)
                break
        else:
            grouped.append([unit])

    lines: list[LogicalTextLine] = []
    for group in grouped:
        ordered = sorted(group, key=lambda item: (_x0(item), item.order))
        lines.append(
            LogicalTextLine(
                page=ordered[0].page,
                text=" ".join(unit.text for unit in ordered),
                units=tuple(ordered),
                bbox=_line_bbox(ordered),
                confidence=_line_confidence(ordered),
            )
        )

    for unit in without_bbox:
        lines.append(
            LogicalTextLine(
                page=unit.page,
                text=unit.text,
                units=(unit,),
                bbox=_line_bbox([unit]),
                confidence=unit.confidence,
            )
        )

    return sorted(
        lines,
        key=lambda line: (
            line.page,
            float(line.bbox[1]) if line.bbox else min(unit.order for unit in line.units),
            float(line.bbox[0]) if line.bbox else min(unit.order for unit in line.units),
        ),
    )


def normalize_text_blocks(
    invoice_unit_id: str,
    source: str,
    blocks: Iterable[dict[str, Any]],
) -> TextUnits:
    units: list[TextUnit] = []
    for index, block in enumerate(blocks, start=1):
        text = str(block.get("text", "")).strip()
        if not text:
            continue
        bbox = block.get("bbox")
        units.append(
            TextUnit(
                text=text,
                page=int(block.get("page", 1)),
                bbox=bbox if bbox is not None else [0, 0, 0, 0],
                confidence=block.get("confidence"),
                order=int(block.get("order", index)),
                source=str(block.get("source", source)),
            )
        )

    units.sort(key=lambda unit: (unit.page, unit.order))
    return TextUnits(invoice_unit_id=invoice_unit_id, source=source, units=units)
