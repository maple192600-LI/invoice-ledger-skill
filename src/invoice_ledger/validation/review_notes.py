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

SUMMARY_LABELS = {
    "汇总金额": "票面金额",
    "汇总税额": "票面税额",
    "汇总价税合计": "票面价税合计",
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


def user_review_issue(remark: str | None) -> str:
    """Return one short problem label for the recognition notice sheet."""
    text = str(remark or "")
    if "missing invoice_no" in text or "发票号码" in text and any(word in text for word in ("缺", "补充")):
        return "缺少发票号码"
    if "missing items" in text or "明细缺失" in text or "补充发票明细" in text:
        return "缺少发票明细"
    if any(
        marker in text
        for marker in (
            "amount_total + tax_total",
            "sum line_amount",
            "sum line_tax_amount",
            "line_amount + line_tax_amount",
            "quantity * unit_price",
            "item amount comparison failed",
            "金额不一致",
            "税额不一致",
            "金额、税额和价税合计，三者对不上",
            "明细金额和票面金额，合计数对不上",
            "明细税额和票面税额，合计数对不上",
            "三者对不上",
            "二者相差",
            "其中一项未识别",
        )
    ):
        return "金额不一致"
    if (
        "conflict " in text
        or "uncertain party role" in text
        or "购买方和销售方" in text
        or "可能填反" in text
        or "有冲突" in text
    ):
        return "信息有冲突"
    if "low confidence " in text or "识别不清" in text:
        return "识别不清"
    if "负数项目" in text or "负数金额" in text:
        return "负数金额需确认"
    if "未验收变体" in text:
        return "票据版式需确认"
    if "missing evidence " in text:
        return "信息需确认"
    return "识别结果需确认"


def user_review_remark(remark: str | None) -> str:
    """Translate internal validation notes into actionable Chinese review text."""
    text = str(remark or "").strip()
    if not text:
        return ""
    if text.startswith("请"):
        return text if text.endswith(("。", "！", "？")) else text + "。"

    missing_fields: list[str] = []
    evidence_fields: list[str] = []
    conflict_fields: list[str] = []
    low_confidence_fields: list[str] = []
    messages: list[str] = []

    for issue in _split_issues(text):
        issue = re.sub(r"^(待复核|需复核)：", "", issue).strip()
        if issue == "missing items" or issue == "明细缺失":
            missing_fields.append("发票明细")
        elif issue.startswith("missing evidence "):
            evidence_fields.append(_label(issue.removeprefix("missing evidence ").strip()))
        elif issue.startswith("missing "):
            missing_fields.append(_label(issue.removeprefix("missing ").strip()))
        elif issue.startswith("conflict "):
            conflict_fields.append(_label(issue.removeprefix("conflict ").strip()))
        elif issue.startswith("low confidence "):
            low_confidence_fields.append(_review_target(issue.removeprefix("low confidence ").strip()))
        elif issue == "amount_total + tax_total != total_with_tax":
            messages.append("请核对票面金额、税额和价税合计，三者对不上")
        elif issue == "sum line_amount != amount_total":
            messages.append("请核对明细金额和票面金额，合计数对不上")
        elif issue == "sum line_tax_amount != tax_total":
            messages.append("请核对明细税额和票面税额，合计数对不上")
        elif match := re.match(r"line (\d+): line_amount \+ line_tax_amount != line_total_with_tax", issue):
            messages.append(f"请核对第 {match.group(1)} 行的金额、税额和价税合计，三者对不上")
        elif match := re.match(r"line (\d+): quantity \* unit_price != line_amount", issue):
            messages.append(f"请核对第 {match.group(1)} 行的数量、单价和金额，三者对不上")
        elif issue == "item amount comparison failed":
            messages.append("请核对发票明细金额，票面不同位置的金额对不上")
        elif issue.startswith("uncertain party role "):
            messages.append("请核对购买方和销售方识别号，可能填反")
        elif issue == "buyer_name equals seller_name with different tax ids":
            messages.append("请核对购买方和销售方名称，当前识别结果相同")
        elif issue.startswith("incomplete amount breakdown: missing "):
            fields = issue.removeprefix("incomplete amount breakdown: missing ").split(",")
            missing_fields.extend(_label(field.strip()) for field in fields)
        elif issue == "digital invoice has invoice_code":
            messages.append("请核对发票代码和数电发票号码，当前可能填错列")
        elif issue == "digital invoice number invalid":
            messages.append("请核对票面上的数电发票号码，当前号码格式不对")
        elif match := re.match(r"可抵扣税额规则遇到负数项目\s*(.*)", issue):
            target = match.group(1).strip() or "负数项目"
            messages.append(f"请确认“{target}”的负数金额是否与原票一致")
        elif match := re.match(r"可抵扣税额规则未覆盖项目\s*(.*)", issue):
            target = match.group(1).strip() or "该项目"
            messages.append(f"请确认“{target}”是否可以抵扣进项税")
        elif issue.startswith("未验收变体 "):
            messages.append("请确认票据版式和票面信息是否识别正确")
        elif match := re.match(
            r"(汇总金额|汇总税额|汇总价税合计)与(明细金额合计|明细税额合计|明细价税合计)"
            r"不一致，.*差额\s*(-?\d+(?:\.\d+)?)",
            issue,
        ):
            messages.append(
                f"请核对{SUMMARY_LABELS[match.group(1)]}和{match.group(2)}，"
                f"二者相差 {match.group(3).lstrip('-')} 元"
            )
        elif match := re.match(
            r"(汇总金额|汇总税额|汇总价税合计)或(明细金额合计|明细税额合计|明细价税合计)缺失",
            issue,
        ):
            messages.append(
                f"请核对{SUMMARY_LABELS[match.group(1)]}和{match.group(2)}，其中一项未识别"
            )

    if missing_fields:
        messages.insert(0, "请对照原票补充" + _join_cn(missing_fields))
    if conflict_fields:
        messages.append("请对照原票确认" + _join_cn(conflict_fields) + "，当前识别结果有冲突")
    if low_confidence_fields:
        messages.append("请对照原票核对" + _join_cn(low_confidence_fields) + "，图片可能识别不清")
    if evidence_fields:
        messages.append("请对照原票确认" + _join_cn(evidence_fields))

    if not messages:
        return "请对照原票核对识别结果。"
    # ponytail: 最多展示 3 项；只有实际漏掉关键问题时才拆成多条提示。
    return "；".join(list(dict.fromkeys(messages))[:3]) + "。"
