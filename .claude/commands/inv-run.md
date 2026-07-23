---
description: 用项目 venv 跑发票采集(fp_ledger.py)，自动选 auto OCR 配置，低 token 输出
argument-hint: <发票文件或目录> [--draft-ledger 工作台账.xlsx]
---

用项目 venv 处理发票，严格遵守 SKILL.md 纪律。项目 venv 解释器固定为（下称 $PY，路径无空格不加引号；若用户已在本仓库建独立 .venv，改用 ./.venv/Scripts/python.exe）：

`C:/Users/Administrator/.workbuddy/skills/invoice-ledger/.venv/Scripts/python.exe`

执行步骤：

1. **解析输入**：`$ARGUMENTS` 中识别发票路径——是目录用 `--input-dir`，是单个文件用 `--input`。支持后缀见 `cli.py` 的 `SUPPORTED_INPUT_SUFFIXES`（.pdf/.png/.jpg/.txt 等；.xml 待 A2 通道落地）。
2. **工作台账**：若 `$ARGUMENTS` 未含 `--draft-ledger`，从 `templates/invoice-information-collection.xlsx` **复制**一份到 `output/ledger.xlsx` 作为工作台账并指向它（**禁止直接写 templates 母版**）；若用户已指定工作台账，沿用同一份（追加 + 去重）。
3. **运行**（默认 auto OCR，按 GPU/CPU 自动选择）：
   ```
   "$PY" scripts/fp_ledger.py <input> --draft-ledger <台账> --config config/runtime_ocr_auto.yaml --output-dir output --json-output summary
   ```
4. **输出纪律**：只读 stdout 的 summary JSON 和结尾中文 stderr 消息，转述给用户（处理张数、写入/待复核/未写入/疑似重复计数、目标 Excel）。**不要**把完整 `run_summary.json`、evidence JSON、OCR 进度日志贴进对话。仅当某张票失败需要排查时，再单独读对应 `output/units/` 证据文件。
5. 若疑似重复计数 > 0 或有待复核/未写入项，提示用户查看 Excel 的「识别提示」页。
