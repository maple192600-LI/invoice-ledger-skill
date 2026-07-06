---
name: invoice-ledger-skill
description: Local Codex skill for extracting invoice information from PDF, image, and scanned invoice files into an Excel invoice ledger. Use when the user needs local invoice OCR, invoice field extraction, ledger row generation, evidence output, template-based Excel writing, first-install environment setup, or adding invoice schema YAML files.
---

# Invoice Ledger Skill

Process local invoice files into an Excel collection workbook. Keep normal runs low-token: run deterministic scripts, read only summaries, and open evidence files only when debugging a specific failure.

## First Install

Run once after cloning, moving machines, deleting `.venv`, or changing OCR/Paddle/Python setup:

```powershell
python scripts\install_skill_env.py --ocr auto
```

The installer creates `.venv` in the skill folder and installs OCR dependencies there. It selects `requirements-ocr-gpu.txt` when `nvidia-smi` reports an NVIDIA GPU, otherwise `requirements-ocr-cpu.txt`. Use `--verbose` only when installation fails.

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

Use `--input` only for one file. Use `--input-dir` for a folder. `--save-evidence` must be `auto`, `always`, or `never`.

## Run

Single file:

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input <invoice-file> --draft-ledger templates\invoice-information-collection.xlsx --config config\runtime_ocr_gpu.yaml --target-sheet 发票信息采集 --output-dir output --save-evidence always --json-output summary
```

Folder:

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input-dir <invoice-folder> --draft-ledger templates\invoice-information-collection.xlsx --config config\runtime_ocr_gpu.yaml --target-sheet 发票信息采集 --output-dir output --save-evidence always --json-output summary
```

Use `config\runtime_ocr_cpu.yaml` when GPU OCR is unavailable. Use `config\runtime.yaml` only for text-layer PDFs where OCR is not needed.

Default writing appends to the workbook and skips likely duplicates. Use `--copy-output` only when the user explicitly wants a copied output workbook.

Use `--json-output full` only for debugging; it prints full records and can be expensive for Agent contexts.

## Output Discipline

Treat stdout summary and the final Chinese stderr message as the normal result. Do not paste full `run_summary.json`, evidence JSON, pip logs, or OCR progress logs into the conversation unless a failure requires that file.

When a long OCR run is still executing, wait for completion and inspect the final summary instead of repeatedly pulling full task logs.

## Template

Default blank workbook:

```text
templates/invoice-information-collection.xlsx
```

Users may replace this workbook. If sheet names, column names, required fields, or mappings change, update:

```text
config/template_profiles/current.yaml
```

Keep repository file names ASCII for cross-agent compatibility. Keep workbook sheet names and headers in Chinese when needed.

## Invoice Types

Schemas live in:

```text
schemas/
```

To add a user-specific invoice type, add a YAML schema in `schemas/` and register it in `schemas/catalog.yaml`. Use `schemas/templates/new-schema-template.yaml` as the starting point.

## Boundaries

This skill does local extraction and Excel writing. Low-confidence and unsupported results must be surfaced as review items.
