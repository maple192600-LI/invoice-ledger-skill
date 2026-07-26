"""OCR table line item extraction."""

from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any

import fitz

from ..contracts import TextUnit, TextUnits
from ._helpers import _center_x, _compact_text, _is_money_text, _ocr_table_item, _x0, _y0
from ._line_item_sequence_helpers import _textual_spec_tokens


def _text_height(units: list[TextUnit]) -> float:
    heights = [unit.bbox[3] - unit.bbox[1] for unit in units if unit.bbox and unit.bbox[3] > unit.bbox[1]]
    return median(heights) if heights else 12.0


def _ocr_table_layout(text_units: TextUnits, schema: dict[str, Any]) -> tuple[dict[str, Any], dict[int, float]]:
    """从 OCR 表头动态推导列边界，并找出每页的合计截止线。"""
    table_config = schema.get("ocr_table", {})
    config = dict(table_config) if isinstance(table_config, dict) else {}
    text_to_col = _header_text_to_col(schema)
    units = [unit for unit in text_units.units if unit.text.strip()]
    text_height = _text_height(units)
    y_bucket = text_height * 5 / 12
    header_skip = text_height / 3
    headers: dict[int, dict[int, list[tuple[str, float]]]] = defaultdict(lambda: defaultdict(list))
    rows: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for unit in units:
        # OCR 表头各列的基线可相差约 1/8 个字高，按字高比例合桶。
        y_key = round(_y0(unit) / y_bucket) * y_bucket
        rows[unit.page][y_key].append(unit.text)
        column = text_to_col.get(_compact_text(unit.text))
        if column:
            headers[unit.page][y_key].append((column, _center_x(unit)))

    centers: dict[str, list[float]] = defaultdict(list)
    header_y: dict[int, float] = {}
    for page, buckets in headers.items():
        y_key, pairs = max(buckets.items(), key=lambda item: len({column for column, _ in item[1]}))
        if len({column for column, _ in pairs}) < 2:
            continue
        header_y[page] = float(y_key)
        for column, center in pairs:
            centers[column].append(center)
    if len(centers) >= 2:
        ordered = sorted(((column, sum(values) / len(values)) for column, values in centers.items()), key=lambda item: item[1])
        config["column_right_edges"] = {
            column: (center + ordered[index + 1][1]) / 2
            for index, (column, center) in enumerate(ordered[:-1])
        }
        config["last_column"] = ordered[-1][0]
        config["use_center_x"] = True

    end_y: dict[int, float] = {}
    for page, y in header_y.items():
        for y_key in sorted(rows[page]):
            if y_key <= y + header_skip:
                continue
            row = _compact_text("".join(rows[page][y_key]))
            if "价税合计" in row or "合计" in row or "备注" in row:
                end_y[page] = float(y_key)
                break
    return config, end_y


def _extract_ocr_table_items(text_units: TextUnits, schema: dict[str, Any]) -> list[dict[str, Any]]:
    table_config, end_y_by_page = _ocr_table_layout(text_units, schema)
    item_start_max_x = float(table_config.get("column_right_edges", {}).get("item_name", table_config.get("item_start_max_x", float("inf"))))
    end_marker_min_x = float(table_config.get("end_marker_min_x", float("inf")))
    end_marker_min_y_delta = float(table_config.get("end_marker_min_y_delta", float("inf")))
    textual_spec_tokens = _textual_spec_tokens(schema)
    units = [unit for unit in text_units.units if unit.text.strip()]
    item_starts = [
        index
        for index, unit in enumerate(units)
        if unit.text.strip().startswith("*")
        and _x0(unit) < item_start_max_x
        and (unit.page not in end_y_by_page or _y0(unit) < end_y_by_page[unit.page])
    ]
    items: list[dict[str, Any]] = []
    for start_position, start_index in enumerate(item_starts):
        end_index = item_starts[start_position + 1] if start_position + 1 < len(item_starts) else len(units)
        group: list[TextUnit] = []
        for unit in units[start_index:end_index]:
            if (
                unit.page in end_y_by_page and _y0(unit) >= end_y_by_page[unit.page]
            ) or unit.text.strip() in {"备注"} or "价税合计" in unit.text:
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


_DIGITAL_NUMERIC_COLUMNS = {"line_amount", "line_tax_amount", "tax_rate", "quantity", "unit_price"}


def _read_pdf_spans(source_file: str, page_range: list[int]) -> list[dict[str, Any]]:
    """读 PDF 各页 dict spans（每个 span 带精确 bbox），用于数电票表格列分桶。

    text_extraction 的 block 级 bbox 缺列精度，明细表格解析需要 span 级坐标，故独立读取。
    """
    spans: list[dict[str, Any]] = []
    document = fitz.open(source_file)
    try:
        pages = page_range or list(range(1, document.page_count + 1))
        order = 0
        for page_number in pages:
            page = document.load_page(page_number - 1)
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = span["text"].strip()
                        if not text:
                            continue
                        x0, y0, x1, y1 = span["bbox"]
                        order += 1
                        spans.append(
                            {
                                "text": text,
                                "x0": float(x0),
                                "y0": float(y0),
                                "x1": float(x1),
                                "y1": float(y1),
                                "page": page_number,
                                "order": order,
                            }
                        )
    finally:
        document.close()
    return spans


def _header_text_to_col(schema: dict[str, Any]) -> dict[str, str]:
    header_aliases = schema.get("line_table", {}).get("header_aliases", {})
    if not isinstance(header_aliases, dict):
        return {}
    mapping: dict[str, str] = {}
    for column, aliases in header_aliases.items():
        for alias in aliases:
            mapping[_compact_text(alias)] = str(column)
    return mapping


def _derive_digital_edges(
    spans: list[dict[str, Any]], text_to_col: dict[str, str]
) -> tuple[dict[str, float], dict[int, float]] | None:
    """推导列右边界 + 各页表头 y。

    表头行 = 该页匹配列名最多的 y 桶；数电票各页列 x 一致，合并各页表头得全局列锚点。
    """
    header_heights = [span["y1"] - span["y0"] for span in spans if span["y1"] > span["y0"]]
    y_bucket = (median(header_heights) * 5 / 12) if header_heights else 5.0
    page_buckets: dict[int, dict[float, list[tuple[str, float]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for span in spans:
        column = text_to_col.get(_compact_text(span["text"]))
        if column:
            # 同一视觉表头的 PDF span 常有约 1/8 个字高的基线误差，按字高比例合桶。
            page_buckets[span["page"]][round(span["y0"] / y_bucket) * y_bucket].append(
                (column, (span["x0"] + span["x1"]) / 2)
            )
    col_center: dict[str, float] = {}
    header_y_by_page: dict[int, float] = {}
    for page, buckets in page_buckets.items():
        best_y, best_pairs = max(buckets.items(), key=lambda item: len(item[1]))
        distinct = {column for column, _ in best_pairs}
        if len(distinct) >= 2:
            header_y_by_page[page] = float(best_y)
            for column, center_x in best_pairs:
                col_center.setdefault(column, center_x)
    if len(_DIGITAL_NUMERIC_COLUMNS & set(col_center)) < 2 or not header_y_by_page:
        return None
    ordered = sorted(col_center.items(), key=lambda item: item[1])
    edges: dict[str, float] = {}
    for index, (column, center_x) in enumerate(ordered):
        if index + 1 < len(ordered):
            edges[column] = (center_x + ordered[index + 1][1]) / 2
    return edges, header_y_by_page


_FREIGHT_TRIGGER_FALLBACK = ("运输工具种类", "起运地", "到达地")
_FREIGHT_INTERFERENCE_KEYWORDS = ("价税合计", "备注", "开户", "下载次数", "开票人", "收款人", "复核")


def _freight_text_to_col(schema: dict[str, Any]) -> dict[str, str]:
    subtable = schema.get("freight_subtable", {})
    if not isinstance(subtable, dict):
        return {}
    mapping: dict[str, str] = {}
    for column, aliases in (subtable.get("header_aliases") or {}).items():
        for alias in aliases:
            mapping[_compact_text(alias)] = str(column)
    return mapping


def _extract_freight_subtable(
    spans: list[dict[str, Any]], schema: dict[str, Any]
) -> list[dict[str, str]]:
    """货物运输服务 5 列子表：表头锚点定位 + 数据行按 x 归列。

    货运子表是明细合计行下方的独立区块（运输工具种类/牌号/起运地/到达地/运输货物名称），
    非 8 列标准表。trigger_keywords 不全命中则返回空——非货运票零触发、零退化。
    """
    subtable = schema.get("freight_subtable", {})
    if not isinstance(subtable, dict):
        return []
    text_to_col = _freight_text_to_col(schema)
    if not text_to_col:
        return []
    triggers = subtable.get("trigger_keywords") or _FREIGHT_TRIGGER_FALLBACK
    compact_all = {_compact_text(s["text"]) for s in spans if s["text"].strip()}
    if any(_compact_text(t) not in compact_all for t in triggers):
        return []

    # 表头行 = 含最多货运列名的 y 桶；列锚点 = 各表头 span 中心 x
    buckets: dict[int, dict[str, float]] = defaultdict(dict)
    for span in spans:
        column = text_to_col.get(_compact_text(span["text"]))
        if column:
            buckets[round(span["y0"])][column] = (span["x0"] + span["x1"]) / 2
    if not buckets:
        return []
    header_y, col_centers = max(buckets.items(), key=lambda item: len(item[1]))
    if len(col_centers) < len(triggers):
        return []
    anchors = sorted(col_centers.items(), key=lambda item: item[1])
    header_spans = [
        span
        for span in spans
        if round(span["y0"]) == header_y and _compact_text(span["text"]) in text_to_col
    ]
    header_bottom = max(span["y1"] for span in header_spans)
    header_height = max(span["y1"] - span["y0"] for span in header_spans)

    # 数据区下界由下一结构锚点决定，避免页面缩放后固定像素带截断数据。
    interference_y = min(
        (
            s["y0"]
            for s in spans
            if s["y0"] > header_y
            and any(k in s["text"] for k in _FREIGHT_INTERFERENCE_KEYWORDS)
        ),
        default=max((s["y1"] for s in spans), default=float(header_y)) + 1,
    )
    lower_bound = interference_y - header_height
    gaps = [anchors[i + 1][1] - anchors[i][1] for i in range(len(anchors) - 1)]
    tol = (min(gaps) / 2) if gaps else 45.0

    rows: dict[int, dict[str, str]] = defaultdict(dict)
    for span in spans:
        if span["y0"] <= header_bottom or span["y0"] >= lower_bound:
            continue
        cx = (span["x0"] + span["x1"]) / 2
        nearest_col, nearest_cx = min(anchors, key=lambda item: abs(item[1] - cx))
        if abs(nearest_cx - cx) <= tol:
            rows[round(span["y0"])][nearest_col] = span["text"].strip()

    ordered: list[dict[str, str]] = []
    for y_key in sorted(rows):
        row = rows[y_key]
        if len(row) >= len(triggers):
            ordered.append(row)
    return ordered


def _merge_freight_into_items(
    items: list[dict[str, Any]], freight_rows: list[dict[str, str]]
) -> None:
    """把货运 5 列 merge 进标准明细 item（按行序对应；多行超出挂末行，单行样本已验证）。"""
    if not freight_rows or not items:
        return
    for idx, row in enumerate(freight_rows):
        target = items[idx] if idx < len(items) else items[-1]
        for col, value in row.items():
            if value:
                target[col] = value


def _extract_digital_text_table_items(
    text_units: TextUnits, schema: dict[str, Any]
) -> list[dict[str, Any]]:
    """文本层数电票明细：独立读 dict spans，按表头锚点坐标分列 + 按 * 分组组装。

    解决 get_text("blocks") 列顺序错乱导致序列解析失败（三一泵路 0 items）或名称残缺
    （成品油换行丢尾）。推导失败返回空，由上层 fallback 到序列解析。
    """
    if not text_units.source_file:
        return []
    spans = _read_pdf_spans(text_units.source_file, list(text_units.page_range))
    text_to_col = _header_text_to_col(schema)
    derived = _derive_digital_edges(spans, text_to_col)
    if not derived:
        return []
    edges, header_y_by_page = derived
    table_config = {
        "column_right_edges": edges,
        "last_column": "line_tax_amount",
        "use_center_x": True,
    }
    item_name_edge = edges.get("item_name", float("inf"))

    by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for span in spans:
        by_page[span["page"]].append(span)

    detail_units: list[TextUnit] = []
    for page in sorted(by_page):
        header_y = header_y_by_page.get(page)
        if header_y is None:
            continue
        page_spans = sorted(by_page[page], key=lambda item: item["y0"])
        y_row: dict[int, list[str]] = defaultdict(list)
        for span in page_spans:
            y_row[round(span["y0"])].append(span["text"])
        total_y: float | None = None
        for y_key in sorted(y_row):
            row_compact = _compact_text("".join(y_row[y_key]))
            if "小计" in row_compact or "合计" in row_compact or "价税合计" in row_compact:
                total_y = float(y_key)
                break
        if total_y is not None:
            preceding_money = [
                span["y0"]
                for span in page_spans
                if total_y - 15.0 <= span["y0"] <= total_y
                and _is_money_text(span["text"])
            ]
            if preceding_money:
                total_y = min(total_y, *preceding_money)
        for span in page_spans:
            if span["y0"] <= header_y + 4:
                continue
            if total_y is not None and span["y0"] >= total_y:
                continue
            detail_units.append(
                TextUnit(
                    text=span["text"],
                    page=span["page"],
                    bbox=[span["x0"], span["y0"], span["x1"], span["y1"]],
                    order=span["order"],
                    source="pdf_text",
                )
            )
    detail_units.sort(key=lambda unit: (unit.page, _y0(unit), _x0(unit)))
    if not detail_units:
        return []

    item_starts = [
        index
        for index, unit in enumerate(detail_units)
        if unit.text.startswith("*") and _center_x(unit) <= item_name_edge
    ]
    textual_spec_tokens = _textual_spec_tokens(schema)
    items: list[dict[str, Any]] = []
    for position, start_index in enumerate(item_starts):
        end_index = item_starts[position + 1] if position + 1 < len(item_starts) else len(detail_units)
        item = _ocr_table_item(detail_units[start_index:end_index], table_config, textual_spec_tokens)
        if item:
            items.append(item)
    _merge_freight_into_items(items, _extract_freight_subtable(spans, schema))
    return items
