"""Golden test：对固定样本断言关键字段（G2 验收基准）。

样本：铁路乘车/退票、吉祥/东航数电航空行程单、高德 XML（总局 EInvoice）。
退出码 0=已运行样本全部通过；1=有断言失败或无样本可测。
样本缺失时该用例 SKIP（不计失败），便于在无样本环境运行。

用法: python scripts/fp_golden_test.py
"""
from __future__ import annotations

import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from invoice_ledger.cli import _user_message  # noqa: E402
from invoice_ledger.contracts import RunSummary, WriteResult  # noqa: E402
from invoice_ledger.output.recognition_notices import build_recognition_notices  # noqa: E402
from invoice_ledger.output.ledger_rows import build_ledger_rows  # noqa: E402
from invoice_ledger.output.template_writer import write_with_template_profile  # noqa: E402
from invoice_ledger.pipeline.unit_processor import process_invoice_input  # noqa: E402

# 高德 XML 为桌面只读引用（不在仓库内）；换机请替换为本机对应路径。
GAODE_XML = Path("C:/Users/Administrator/Desktop/财务相关资料/高德打车电子发票/【小牛快跑-13.49元-1个行程】高德打车电子发票.xml")

CASES = [
    {
        "label": "铁路乘车(新开)",
        "path": ROOT / "数电发票" / "火车票-新开-26149126497000282522.pdf",
        "expect": {
            "invoice_no": "26149126497000282522",
            "amount_total": "102.75", "tax_total": "9.25", "total_with_tax": "112.00",
            "items": [
                {"line_amount": "102.75", "tax_rate": "9%", "line_tax_amount": "9.25",
                 "line_total_with_tax": "112.00", "name_contains": "运城北"},
            ],
        },
    },
    {
        "label": "铁路退票",
        "path": ROOT / "数电发票" / "火车票.pdf",
        "expect": {
            "invoice_no": "25649214846000009802",
            "amount_total": "35.85", "tax_total": "2.15", "total_with_tax": "38.00",
            "items": [
                {"line_amount": "35.85", "tax_rate": "6%", "line_tax_amount": "2.15",
                 "line_total_with_tax": "38.00", "name_contains": "退票费"},
            ],
        },
    },
    {
        "label": "吉祥航空(数电行程单)",
        "path": ROOT / "数电发票" / "dzfp_26318018111050755598_上海吉祥航空股份有限公司_20260713191314.pdf",
        "expect": {
            "invoice_no": "26318018111050755598",
            "amount_total": "1013.30", "tax_total": "86.70", "total_with_tax": "1100.00",
            "items": [
                {"line_amount": "963.30", "tax_rate": "9%", "line_tax_amount": "86.70", "line_total_with_tax": "1050.00"},
                {"line_amount": "50.00", "line_tax_amount": "0.00", "name_contains": "民航发展基金"},
            ],
        },
    },
    {
        "label": "东航(数电行程单)",
        "path": ROOT / "数电发票" / "dzfp_26318781111050920413_中国东方航空股份有限公司_20260713191306.pdf",
        "expect": {
            "invoice_no": "26318781111050920413",
            "amount_total": "925.69", "tax_total": "74.31", "total_with_tax": "1000.00",
            "items": [
                {"line_amount": "825.69", "tax_rate": "9%", "line_tax_amount": "74.31", "line_total_with_tax": "900.00"},
                {"line_amount": "100.00", "line_tax_amount": "0.00", "name_contains": "民航发展基金"},
            ],
        },
    },
    {
        "label": "货物运输(启达物流)",
        "path": ROOT / "数电发票" / "dzfp_26142000000668273041_山西启达物流有限公司_20260713184956.pdf",
        "expect": {
            "invoice_no": "26142000000668273041",
            "amount_total": "35779.82", "tax_total": "3220.18", "total_with_tax": "39000.00",
            "items": [
                {"line_amount": "35779.82", "tax_rate": "9%", "line_tax_amount": "3220.18",
                 "line_total_with_tax": "39000.00", "name_contains": "运输服务",
                 "transport_vehicle_type": "公路运输", "transport_vehicle_no": "晋ADC0693",
                 "origin_place": "阳曲", "destination_place": "景洪",
                 "transport_goods_name": "摊铺机、双钢轮"},
            ],
        },
    },
    {
        "label": "建筑服务(西双版纳)",
        "path": ROOT / "数电发票" / "增值税专用发票-建筑服务.pdf",
        "expect": {
            "invoice_no": "23532000000004844777",
            "special_invoice_type": "建筑服务",
            "amount_total": "5582.52", "tax_total": "167.48", "total_with_tax": "5750.00",
            "row_context_contains": ["建筑服务发生地：", "建筑项目名称："],
            "items": [
                {"line_amount": "5582.52", "tax_rate": "3%", "line_tax_amount": "167.48",
                 "line_total_with_tax": "5750.00", "service_location": "云南省西双版纳傣族自治州景洪市",
                 "project_name": "景洪市大渡岗田园综合体（茶园道路） 建设项目"},
            ],
        },
    },
    {
        "label": "建筑服务(山西磐鼎)",
        "path": ROOT / "数电发票" / "dzfp_26142000000978765226_山西磐鼎建筑工程有限公司_20260713184558.pdf",
        "expect": {
            "invoice_no": "26142000000978765226",
            "special_invoice_type": "建筑服务",
            "amount_total": "103276.13", "tax_total": "3098.28", "total_with_tax": "106374.41",
            "row_context_contains": [
                "建筑服务发生地：山西省临汾市蒲县",
                "建筑项目名称：蒲县养护维修工程项目",
            ],
            "items": [
                {"line_amount": "103276.13", "tax_rate": "3%", "line_tax_amount": "3098.28",
                 "line_total_with_tax": "106374.41", "service_location": "山西省临汾市蒲县",
                 "project_name": "蒲县养护维修工程项目"},
            ],
        },
    },
    {
        "label": "成品油(含折扣)",
        "path": ROOT / "数电发票" / "增值税普通发票-成品油发票-有现金折扣.pdf",
        "expect": {
            "invoice_no": "26147000000209852922",
            "special_invoice_type": "成品油",
            "amount_total": "257.53", "tax_total": "33.47", "total_with_tax": "291.00",
            "items": [
                {"line_amount": "265.49", "tax_rate": "13%", "line_tax_amount": "34.51",
                 "line_total_with_tax": "300.00", "unit": "升", "quantity": "35.72"},
                {"line_amount": "-7.96", "tax_rate": "13%", "line_tax_amount": "-1.04",
                 "line_total_with_tax": "-9.00"},
            ],
        },
    },
    {
        "label": "成品油(无折扣)",
        "path": ROOT / "数电发票" / "增值税普通发票-成品油发票-没有现金折扣.pdf",
        "expect": {
            "invoice_no": "26147000000225867341",
            "special_invoice_type": "成品油",
            "amount_total": "176.99", "tax_total": "23.01", "total_with_tax": "200.00",
            "items": [
                {"line_amount": "176.99", "tax_rate": "13%", "line_tax_amount": "23.01",
                 "line_total_with_tax": "200.00", "unit": "升", "quantity": "22.63"},
            ],
        },
    },
    {
        "label": "成品油(舟山保源)",
        "path": ROOT / "数电发票" / "dzfp_26332000003455897176_舟山保源能源有限公司_20260713190411.pdf",
        "expect": {
            "invoice_no": "26332000003455897176",
            "special_invoice_type": "成品油",
            "amount_total": "218987.61", "tax_total": "28468.39", "total_with_tax": "247456.00",
            "items": [
                {"line_amount": "218987.61", "tax_rate": "13%", "line_tax_amount": "28468.39",
                 "line_total_with_tax": "247456.00", "unit": "吨", "quantity": "33.44"},
            ],
        },
    },
    {
        "label": "成品油(中国石油)",
        "path": ROOT / "数电发票" / "dzfp_26537000000295846219_中国石油天然气股份有限公司云南西双版纳销售分公司_20260713184913.pdf",
        "expect": {
            "invoice_no": "26537000000295846219",
            "special_invoice_type": "成品油",
            "amount_total": "211104.42", "tax_total": "27443.58", "total_with_tax": "238548.00",
            "items": [
                {"line_amount": "211104.42", "tax_rate": "13%", "line_tax_amount": "27443.58",
                 "line_total_with_tax": "238548.00", "unit": "吨", "quantity": "30",
                 "spec_model": "GB19147"},
            ],
        },
    },
    {
        "label": "跨页数电票(喜跃发)",
        "path": ROOT / "数电发票" / "dzfp_26142000001030443346_喜跃发国际环保新材料股份有限公司_20260713184312.pdf",
        "expect": {
            "invoice_no": "26142000001030443346",
            "amount_total": "1718849.19", "tax_total": "223450.39", "total_with_tax": "1942299.58",
            "item_count": 47,
            "items": [
                {"line_amount": "1637.17", "line_tax_amount": "212.83",
                 "quantity": "0.74", "unit_price": "2212.3893805309735"},
            ],
        },
    },
    {
        "label": "机动车销售统一发票(山东东岳)",
        "path": ROOT / "数电发票" / "dzfp_25372000000360862004_山东东岳专用汽车制造有限公司_20260713191424.pdf",
        "expect": {
            "schema_id": "motor-vehicle-unified",
            "invoice_no": "25372000000360862004",
            "amount_total": "527433.63", "tax_total": "68566.37", "total_with_tax": "596000.00",
            "items": [
                {"item_name": "稀浆封层车", "spec_model": "圣岳牌SDZ5317TFCF30",
                 "line_amount": "527433.63", "tax_rate": "13%", "line_tax_amount": "68566.37",
                 "line_total_with_tax": "596000.00",
                 "context_contains": ["合格证号", "发动机号码", "车辆识别代号/车架号码"]},
            ],
        },
    },
    {
        "label": "高德XML(总局EInvoice)",
        "path": GAODE_XML,
        "expect": {
            "invoice_no": "24142000000104454178",
            "amount_total": "13.10", "tax_total": "0.39", "total_with_tax": "13.49",
            "items": [
                {"line_amount": "14.00", "tax_rate": "3%", "line_tax_amount": "0.42", "line_total_with_tax": "14.42"},
                {"line_amount": "-0.90", "tax_rate": "3%", "line_tax_amount": "-0.03", "line_total_with_tax": "-0.93"},
            ],
        },
    },
]


def _eq(a, b) -> bool:
    if a is None:
        return False
    try:
        return Decimal(str(a)) == Decimal(str(b))
    except (InvalidOperation, ValueError):
        return str(a) == str(b)


def _record(path: Path):
    result = process_invoice_input(path, {}, "golden", "2026-07-23T00:00:00")
    return result["unit_results"][0]["invoice_record"]


def _check_case(case) -> list[str]:
    record = _record(case["path"])
    inv = record.invoice
    expect = case["expect"]
    errors: list[str] = []
    if record.quality.status.value != "ready":
        errors.append(f"status: 期望 ready，实际 {record.quality.status.value}")
    if expect.get("schema_id") and record.schema_id != expect["schema_id"]:
        errors.append(f"schema_id: 期望 {expect['schema_id']}，实际 {record.schema_id}")
    if expect.get("special_invoice_type") != inv.special_invoice_type:
        errors.append(
            f"special_invoice_type: 期望 {expect.get('special_invoice_type')}，"
            f"实际 {inv.special_invoice_type}"
        )
    if not _eq(inv.invoice_no, expect["invoice_no"]):
        errors.append(f"invoice_no: 期望 {expect['invoice_no']}，实际 {inv.invoice_no}")
    for field in ("amount_total", "tax_total", "total_with_tax"):
        want = expect[field]
        actual = getattr(inv, field)
        if not _eq(actual, want):
            errors.append(f"{field}: 期望 {want}，实际 {actual}")
    expected_item_count = expect.get("item_count", len(expect["items"]))
    if len(record.items) != expected_item_count:
        errors.append(f"明细行数: 期望 {expected_item_count}，实际 {len(record.items)}")
    for idx, item_expect in enumerate(expect["items"]):
        if idx >= len(record.items):
            break
        item = record.items[idx]
        prefix = f"行{idx + 1}"
        for key, want in item_expect.items():
            if key == "name_contains":
                if want not in (item.item_name or ""):
                    errors.append(f"{prefix} 名称未含 {want}：{item.item_name}")
            elif key == "context_contains":
                for text in want:
                    if text not in (item.context_remark or ""):
                        errors.append(f"{prefix} 备注未含 {text}：{item.context_remark}")
            else:
                actual = getattr(item, key)
                if not _eq(actual, want):
                    errors.append(f"{prefix} {key}: 期望 {want}，实际 {actual}")
    if expect.get("row_context_contains"):
        rows = build_ledger_rows(record, "golden", "2026-07-26T00:00:00")
        context = rows[0].context_remark if rows else ""
        for text in expect["row_context_contains"]:
            if text not in context:
                errors.append(f"台账备注未含 {text}：{context}")
    return errors


def _check_repeat_write(case) -> list[str]:
    result = process_invoice_input(case["path"], {}, "golden-repeat", "2026-07-23T00:00:00")
    unit_results = result["unit_results"]
    rows = [row for unit_result in unit_results for row in unit_result["ledger_rows"]]
    errors: list[str] = []
    with TemporaryDirectory() as temp_dir:
        workbook = Path(temp_dir) / "ledger.xlsx"
        copy2(ROOT / "templates" / "invoice-information-collection.xlsx", workbook)
        for run_number in range(1, 4):
            notices = build_recognition_notices(unit_results, rows)
            write_result = write_with_template_profile(
                workbook,
                ROOT / "config" / "template_profiles" / "current.yaml",
                rows,
                f"golden-repeat-{run_number}",
                recognition_notices=notices,
            )
            expected_added = len(rows) if run_number == 1 else 0
            expected_skipped = 0 if run_number == 1 else len(rows)
            if write_result.added_rows != expected_added:
                errors.append(
                    f"第 {run_number} 次 added_rows: 期望 {expected_added}，实际 {write_result.added_rows}"
                )
            if write_result.skipped_duplicate_rows != expected_skipped:
                errors.append(
                    f"第 {run_number} 次 skipped_duplicate_rows: "
                    f"期望 {expected_skipped}，实际 {write_result.skipped_duplicate_rows}"
                )
            if write_result.review_required_rows != 0:
                errors.append(
                    f"第 {run_number} 次 review_required_rows: 期望 0，实际 {write_result.review_required_rows}"
                )
    return errors


def _check_user_messages() -> list[str]:
    errors: list[str] = []

    def check(label: str, summary: RunSummary, expected: list[str], forbidden: list[str]) -> None:
        message = _user_message(summary, None)
        for text in expected:
            if text not in message:
                errors.append(f"{label}: 缺少文案 {text}")
        for text in forbidden:
            if text in message:
                errors.append(f"{label}: 不应出现文案 {text}")

    check(
        "正常复核",
        RunSummary(
            run_id="message-review",
            input_count=36,
            invoice_units=36,
            ready_units=34,
            review_required_units=2,
            ready_rows=75,
            review_required_rows=37,
            write_result=WriteResult(
                run_id="message-review",
                target_sheet="test",
                added_rows=112,
            ),
            output_dir="",
        ),
        ["识别结果：36 张发票、112 条明细", "待复核：2 张发票，共 37 条明细"],
        ["待复核：37 张发票"],
    )
    check(
        "未建模",
        RunSummary(
            run_id="message-unmodeled",
            input_count=1,
            invoice_units=1,
            unmodeled_units=1,
            write_result=WriteResult(run_id="message-unmodeled", target_sheet="test"),
            output_dir="",
        ),
        ["识别结果：0 张发票、0 条明细", "未形成可写入结果：1 个处理单元"],
        ["识别结果：1 张发票"],
    )
    check(
        "弱身份重复",
        RunSummary(
            run_id="message-weak-duplicate",
            input_count=1,
            invoice_units=1,
            ready_units=1,
            ready_rows=1,
            write_result=WriteResult(
                run_id="message-weak-duplicate",
                target_sheet="test",
                added_rows=1,
                messages=["疑似重复（弱身份票）：本次已写入但需人工确认"],
            ),
            output_dir="",
        ),
        ["弱身份疑似重复已写入：1 张发票"],
        ["疑似重复未写入：1 张发票"],
    )
    return errors


def main() -> int:
    ran = skipped = failed = 0
    failures: dict[str, list[str]] = {}
    for case in CASES:
        if not case["path"].exists():
            print(f"[SKIP] {case['label']}（样本不存在）")
            skipped += 1
            continue
        ran += 1
        errors = _check_case(case)
        if errors:
            failed += 1
            failures[case["label"]] = errors
        else:
            print(f"[PASS] {case['label']}")
    if failures:
        print("\n==== GOLDEN TEST FAILED ====")
        for label, errors in failures.items():
            print(f"[FAIL] {label}")
            for err in errors:
                print(f"    - {err}")
        return 1
    if ran == 0:
        print("\n==== GOLDEN TEST: 无样本可测（全部 SKIP）====")
        return 1
    message_errors = _check_user_messages()
    if message_errors:
        print("[FAIL] 用户摘要统计口径")
        for error in message_errors:
            print(f"    - {error}")
        return 1
    print("[PASS] 用户摘要统计口径")
    repeat_case = next(case for case in CASES if case["path"].exists())
    repeat_errors = _check_repeat_write(repeat_case)
    if repeat_errors:
        print("[FAIL] 重复写入统计口径")
        for error in repeat_errors:
            print(f"    - {error}")
        return 1
    print("[PASS] 重复写入统计口径")
    print(f"\n==== GOLDEN TEST PASSED ({ran}/{ran}，skip {skipped}) ====")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
