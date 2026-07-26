"""通用明细行/单品抽取：识别、规格剥离、数量/单价/税率拆分。"""

from __future__ import annotations

# ruff: noqa: F401

from ._line_item_traditional import (
    _remove_spec_from_name,
    _traditional_item_from_group,
    _traditional_vat_items,
)
from ._line_item_ocr_table import (
    _extract_ocr_table_items,
)
from ._line_item_sequence_helpers import (
    _split_unit_price_quantity,
    _unit_tokens,
    _textual_spec_tokens,
    _is_common_unit_token,
    _is_unit_before_numeric_values,
    _is_textual_spec_before_unit,
    _is_textual_spec_before_rate_and_unit,
    _is_trailing_item_name_fragment,
    _finalize_sequence_item_name,
    _is_blocked_building_project_fragment,
    _building_sequence_parts,
)
from ._line_item_sequence import (
    _numeric_line_item,
    _extract_sequence_item,
    _extract_items,
    _extract_items_from_text_units,
    _item_tax_rates,
)
from ._line_item_receipts import (
    _add_json_item,
    _quantity_between,
    _extract_simple_receipt_items,
    _extract_machine_invoice_item,
)
