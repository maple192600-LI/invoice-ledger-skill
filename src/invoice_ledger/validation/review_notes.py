"""User-facing Chinese review notes for invoice recognition risks."""

from __future__ import annotations

import re


FIELD_LABELS = {
    "invoice_code": "发票代码",
    "invoice_no": "发票号码或数电发票号码",
    "invoice_date": "开票日期",
    "buyer_name": "购买方名称",
    "buyer_tax_id": "购买方纳税人识别号",
    "seller_name": "销售方名称",
    "seller_tax_id": "销售方纳税人识别号",
    "invoice_type": "发票票种",
    "amount_total": "金额",
    "tax_total": "税额",
    "total_with_tax": "价税合计",
    "line_amount": "明细金额",
    "line_tax_amount": "明细税额",
    "line_total_with_tax": "明细价税合计",
    "items": "明细行",
}

REVIEW_TARGETS = {
    "items": "明细行的货物或服务名称、规格型号、数量、单价、金额、税额、价税合计",
    "amount_total": "金额",
    "tax_total": "税额",
    "total_with_tax": "价税合计",
    "line_amount": "明细金额",
    "line_tax_amount": "明细税额",
    "line_total_with_tax": "明细价税合计",
}


def _split_issues(remark: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;；]", remark) if part.strip()]


def _label(field_name: str) -> str:
    return FIELD_LABELS.get(field_name, field_name)


def _review_target(field_name: str) -> str:
    return REVIEW_TARGETS.get(field_name, _label(field_name))


def _join_cn(parts: list[str]) -> str:
    unique = list(dict.fromkeys(part for part in parts if part))
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    return "、".join(unique)


def user_review_remark(remark: str | None) -> str:
    """Translate internal validation notes into actionable Chinese review text."""
    text = str(remark or "").strip()
    if not text:
        return ""
    if text.startswith(("待复核：", "需复核：")):
        return text

    missing_fields: list[str] = []
    missing_evidence_fields: list[str] = []
    conflict_fields: list[str] = []
    low_confidence_targets: list[str] = []
    messages: list[str] = []
    unknown: list[str] = []
    amount_formula_failed = False

    for issue in _split_issues(text):
        if issue.startswith("missing evidence "):
            missing_evidence_fields.append(_label(issue.removeprefix("missing evidence ").strip()))
        elif issue.startswith("missing "):
            missing_fields.append(_label(issue.removeprefix("missing ").strip()))
        elif issue.startswith("conflict "):
            conflict_fields.append(_label(issue.removeprefix("conflict ").strip()))
        elif issue.startswith("low confidence "):
            low_confidence_targets.append(_review_target(issue.removeprefix("low confidence ").strip()))
        elif issue == "amount_total + tax_total != total_with_tax":
            amount_formula_failed = True
            messages.append("金额、税额、价税合计之间的勾稽关系不一致，请核对票面合计区。")
        elif issue == "sum line_amount != amount_total":
            messages.append("明细金额合计与发票金额不一致，请核对每一行明细金额和合计金额。")
        elif issue == "sum line_tax_amount != tax_total":
            messages.append("明细税额合计与发票税额不一致，请核对每一行税额和合计税额。")
        elif "line_amount + line_tax_amount != line_total_with_tax" in issue:
            messages.append("明细行的金额、税额、价税合计不一致，请核对对应明细行。")
        elif issue == "missing items":
            messages.append("缺少发票明细行，请核对货物或服务名称、规格型号、数量、单价、金额、税额、价税合计。")
        elif issue == "digital invoice has invoice_code":
            messages.append("数电发票不应写入发票代码，请核对发票代码和数电发票号码是否放在正确列。")
        elif issue == "digital invoice number invalid":
            messages.append("数电发票号码格式异常，请核对票面上的数电发票号码。")
        elif re.search(r"[\u4e00-\u9fff]", issue):
            messages.append(issue)
        else:
            unknown.append(issue)

    if low_confidence_targets:
        messages.insert(
            0,
            "OCR 对"
            + _join_cn(low_confidence_targets)
            + "识别置信度偏低，请对照票面核对这些字段是否识别正确。",
        )
    if missing_fields:
        messages.append("缺少" + _join_cn(missing_fields) + "，请从票面补充或确认该字段是否确实不存在。")
    if missing_evidence_fields:
        messages.append(
            _join_cn(missing_evidence_fields)
            + "缺少可靠证据来源，请对照票面确认字段值是否正确。"
        )
    if conflict_fields:
        messages.append(_join_cn(conflict_fields) + "存在多个候选值冲突，请以票面主字段为准复核。")
    if low_confidence_targets and not amount_formula_failed:
        messages.append("金额勾稽关系已通过，复核重点是票面数字和明细内容是否被 OCR 看错。")
    if unknown:
        messages.append("系统发现未分类风险：" + _join_cn(unknown) + "，请人工核对。")

    if not messages:
        return text
    return "需复核：" + "；".join(messages)
