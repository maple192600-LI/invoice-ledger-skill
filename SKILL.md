---
name: invoice-ledger-skill
description: Local Codex skill for extracting invoice information from PDF, image, and scanned invoice files into an Excel invoice ledger. Use when the user needs local invoice OCR, invoice field extraction, ledger row generation, evidence output, template-based Excel writing, first-install environment setup, or adding invoice schema YAML files.
---

# Invoice Ledger Skill

Use this skill to process local invoice files into an Excel collection workbook. Keep user invoice files local. Do not design SaaS, account, payment, cloud storage, multi-tenant, or permission systems for this skill.

## First Install

Run the installer once after cloning the skill or moving it to a new computer:

```powershell
python scripts\install_skill_env.py --ocr auto
```

The installer creates `.venv` inside the skill folder, installs base dependencies, detects NVIDIA GPU with `nvidia-smi`, then installs GPU OCR dependencies when GPU is available and CPU OCR dependencies when GPU is not available.

Run doctor only for first install, machine changes, `.venv` rebuilds, OCR/Paddle errors, Python errors, template changes, or OCR config changes:

```powershell
.\.venv\Scripts\python.exe scripts\fp_doctor.py
```

Normal invoice processing should not run doctor every time.

## Run

Use the project virtual environment:

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input <invoice-file-or-folder> --draft-ledger templates\invoice-information-collection.xlsx --config config\runtime_ocr_gpu.yaml --target-sheet 发票信息采集 --output-dir output --save-evidence
```

If the machine has no working GPU OCR runtime, use `config\runtime_ocr_cpu.yaml`.

The command writes recognized rows into the Excel workbook and writes JSON/Markdown evidence under `output`.

## Template

The default blank workbook is:

```text
templates/invoice-information-collection.xlsx
```

Users may replace this workbook with their own collection template. When column names, sheet names, or required fields change, update:

```text
config/template_profiles/current.yaml
```

Do not hardcode a user's private workbook layout into source code.

## Invoice Types

Supported invoice schemas live in:

```text
schemas/
```

To add a user-specific invoice type, add a YAML schema in `schemas/` and register it in `schemas/catalog.yaml`. Use `schemas/templates/new-schema-template.yaml` as the starting point.

## Boundaries

This skill does local extraction and Excel writing only. It does not file taxes, approve reimbursements, provide legal/accounting advice, upload invoices, or guarantee recognition accuracy. Low-confidence and unsupported results must be surfaced as review items.
