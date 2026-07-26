---
name: invoice-ledger-skill
description: Local Codex skill for extracting invoice information from PDF, image, and scanned invoice files into an Excel invoice ledger. Use when the user needs local invoice OCR, invoice field extraction, ledger row generation, evidence output, template-based Excel writing, first-install environment setup, or adding invoice schema YAML files.
slug: maple192600-li-invoice-ledger
displayName: 发票台账本地化采集
version: 1.0.1
summary: 本地运行的开票采集 Skill，把 PDF / 图片 / 扫描件发票识别成可复核的 Excel 台账——全程离线、财务数据不出本机、AI 不直读原始票据。
license: MIT
---

# Invoice Ledger Skill

Process local invoice files into a working Excel collection workbook. Keep normal runs low-token: run deterministic scripts, read only summaries, and open evidence files only when debugging a specific failure.

## First Install

On first install, ask the user: `发票采集台账希望保存到哪个文件夹？也可以直接提供完整的 .xlsx 路径。`

Pass that answer to the installer:

```powershell
python scripts\install_skill_env.py --ocr auto --ledger <用户选择的文件夹或.xlsx路径>
```

The installer copies the root workbook only when the selected target does not exist, saves the selected path locally, creates `.venv`, and installs OCR dependencies. It never overwrites an existing workbook. It selects `requirements-ocr-gpu.txt` when `nvidia-smi` reports an NVIDIA GPU, otherwise `requirements-ocr-cpu.txt`. Use `--verbose` only when installation fails.

After first install, rerun without `--ledger` only when repairing the environment:

```powershell
python scripts\install_skill_env.py --ocr auto
```

Run install from the skill root. Runtime scripts switch to the skill root automatically when invoked by absolute path.

Run doctor only for first install or environment/template/OCR problems:

```powershell
.\.venv\Scripts\python.exe scripts\fp_doctor.py
```

Do not run doctor before every invoice batch.

## Before Running

For unfamiliar versions, check the CLI signature once:

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --help
```

Use `--input` only for one file. Use `--input-dir` for a folder.

## Workbook Rule

Keep the blank workbook as a source template only:

```text
发票采集台账.xlsx
```

### First-use ledger location

Before the first real collection run, check `config/user_settings.local.yaml`.

- If it contains an existing `draft_ledger` path, reuse that workbook.
- If the setting is missing, invalid, or the user already provided another workbook, ask exactly one question before copying or processing anything: `发票采集台账希望保存到哪个文件夹？也可以直接提供完整的 .xlsx 路径。`
- Do not silently choose `output/ledger.xlsx` or any other default location.
- For a new path, create its parent folder when needed, copy the root `发票采集台账.xlsx` there once, and save the absolute path as `draft_ledger` in `config/user_settings.local.yaml`.
- If the selected workbook already exists, use it after compatibility validation. Never copy over, clear, replace, or rewrite its existing rows.
- Later batches reuse that selected workbook. If the saved file was moved or deleted, ask for its new location instead of creating another ledger.

For real work, keep pointing `--draft-ledger` or `--workbook` to that same user-selected ledger. The writer only appends new rows and skips likely duplicates. `--replace-existing` and `--update-existing` are forbidden.

Do not write directly into the root blank template. Do not create a fresh workbook for each invoice batch unless the user explicitly wants a separate ledger.

Before the first formal run against a working ledger, run the cheap compatibility check:

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --check-only --input-dir <invoice-folder> --draft-ledger <working-ledger.xlsx> --config config\runtime_ocr_auto.yaml --output-dir output
```

`--check-only` validates arguments, input paths, config, and workbook/template compatibility. It does not run OCR and does not modify Excel.

## Run

Single file:

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input <invoice-file> --draft-ledger <working-ledger.xlsx> --config config\runtime_ocr_auto.yaml --output-dir output --json-output summary
```

Folder:

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input-dir <invoice-folder> --draft-ledger <working-ledger.xlsx> --config config\runtime_ocr_auto.yaml --output-dir output --json-output summary
```

Use `config\runtime_ocr_auto.yaml` for OCR jobs. It selects GPU when `nvidia-smi` reports an NVIDIA GPU, otherwise CPU. Use `config\runtime.yaml` only for text-layer PDFs where OCR is not needed.

Default writing appends to the workbook and skips likely duplicates. Use `--copy-output` only when the user explicitly wants a copied output workbook for that run.

Default evidence behavior saves unit evidence only for failed, unmodeled, or review-required invoice units. Use `--save-evidence none` only when the user wants no unit evidence files.

Use `--json-output full` only for debugging; it prints full records and can be expensive for Agent contexts.

Keep `--output-dir` under `output/`, `outputs/`, `runs/`, or `debug-output/` inside the skill folder unless the user explicitly chooses another location. Output summaries and evidence can contain invoice text.

## Output Discipline

Treat stdout summary and the final Chinese stderr message as the normal result. Do not paste full `run_summary.json`, evidence JSON, pip logs, or OCR progress logs into the conversation unless a failure requires that file.

When a long OCR run is still executing, wait for completion and inspect the final summary instead of repeatedly pulling full task logs.

## Template

Default blank workbook:

```text
发票采集台账.xlsx
```

Users may replace this workbook. If sheet names, column names, required fields, or mappings change, update:

```text
config/template_profiles/current.yaml
```

Keep code and configuration file names portable. The user-facing root workbook, sheet names, and headers stay in Chinese.

## Invoice Types

Schemas live in:

```text
schemas/
```

To add a user-specific invoice type, add a YAML schema in `schemas/` and register it in `schemas/catalog.yaml`. Use `schemas/templates/new-schema-template.yaml` as the starting point.

## Boundaries

This skill does local extraction and Excel writing. Low-confidence and unsupported results must be surfaced as review items.
