"""Evidence writer for the invoice draft ledger pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _write_json(output_dir: Path, filename: str, value: Any) -> Path:
    path = output_dir / filename
    path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _render_markdown(
    file_profile: Any,
    schema_decision: Any,
    invoice_record: Any,
    ledger_rows: list[Any],
    text_units: Any,
    ocr_result: Any | None = None,
) -> str:
    record = _jsonable(invoice_record)
    decision = _jsonable(schema_decision)
    rows = _jsonable(ledger_rows)
    text = "\n".join(unit["text"] for unit in _jsonable(text_units)["units"][:80])
    invoice = record.get("invoice", {})
    quality = record.get("quality", {})
    ocr = _jsonable(ocr_result) if ocr_result is not None else None
    ocr_section = []
    if ocr is not None:
        ocr_section = [
            "",
            "## OCR 识别",
            "",
            f"- provider: {ocr.get('provider')}",
            f"- status: {ocr.get('status')}",
            f"- blocks: {len(ocr.get('blocks', []))}",
            f"- messages: {'; '.join(ocr.get('messages', []))}",
        ]

    return "\n".join(
        [
            "# 发票解析证据",
            "",
            "## 发票基础信息",
            "",
            f"- 来源文件: {_jsonable(file_profile).get('input_file')}",
            f"- 发票号码: {invoice.get('invoice_no')}",
            f"- 开票日期: {invoice.get('invoice_date')}",
            f"- 购买方: {invoice.get('buyer_name')}",
            f"- 销售方: {invoice.get('seller_name')}",
            f"- 价税合计: {invoice.get('total_with_tax')}",
            "",
            "## 明细信息",
            "",
            *[
                f"- {row.get('line_no')}: {row.get('item_name')} / {row.get('line_amount')} / {row.get('tax_rate')} / {row.get('line_tax_amount')}"
                for row in rows
            ],
            "",
            "## 草稿行摘要",
            "",
            f"- 行数: {len(rows)}",
            *ocr_section,
            "",
            "## 票种识别",
            "",
            f"- decision: {decision.get('decision')}",
            f"- schema_id: {decision.get('schema_id')}",
            f"- variant_id: {decision.get('variant_id')}",
            "",
            "## 字段校验",
            "",
            f"- status: {quality.get('status')}",
            f"- confidence: {quality.get('confidence')}",
            f"- remark: {quality.get('remark')}",
            "",
            "## 票面文字",
            "",
            "```text",
            text,
            "```",
            "",
        ]
    )


def save_evidence_bundle(
    output_dir: str | Path,
    file_profile: Any,
    invoice_units: list[Any],
    text_units: Any,
    schema_decision: Any,
    field_candidates: Any,
    invoice_record: Any,
    ledger_rows: list[Any],
    ocr_result: Any | None = None,
) -> dict[str, str]:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    written = {
        "file_profile": _write_json(output_path, "file_profile.json", file_profile),
        "invoice_units": _write_json(output_path, "invoice_units.json", invoice_units),
        "text_units": _write_json(output_path, "text_units.json", text_units),
        "schema_decision": _write_json(output_path, "schema_decision.json", schema_decision),
        "field_candidates": _write_json(output_path, "field_candidates.json", field_candidates),
        "invoice_record": _write_json(output_path, "invoice_record.json", invoice_record),
        "ledger_rows": _write_json(output_path, "ledger_rows.json", ledger_rows),
    }
    if ocr_result is not None:
        written["ocr_result"] = _write_json(output_path, "ocr_result.json", ocr_result)

    evidence_md = output_path / "evidence.md"
    evidence_md.write_text(
        _render_markdown(
            file_profile,
            schema_decision,
            invoice_record,
            ledger_rows,
            text_units,
            ocr_result=ocr_result,
        ),
        encoding="utf-8",
    )
    written["evidence_md"] = evidence_md

    return {key: str(path) for key, path in written.items()}
