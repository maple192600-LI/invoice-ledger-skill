---
description: --check-only 兼容性预检（不跑 OCR、不改 Excel），首次正式写入工作台账前必跑
argument-hint: <发票文件或目录> [--draft-ledger 工作台账.xlsx]
---

用项目 venv 做写入前兼容性预检——校验参数、输入路径、配置、工作台账与模板 profile 的兼容性，**不跑 OCR、不改 Excel**：

```
C:/Users/Administrator/.workbuddy/skills/invoice-ledger/.venv/Scripts/python.exe scripts/fp_ledger.py <input> --check-only --draft-ledger <台账> --config config/runtime_ocr_auto.yaml --output-dir output
```

输入解析与工作台账规则同 `/inv-run`（`$ARGUMENTS`）。

报告检查是否通过；不通过则给出具体原因（如 `template_drift_report` 指出工作台账与 profile 的列名/表头漂移）。这是 SKILL.md 要求的「首次正式写入工作台账前」廉价预检。
