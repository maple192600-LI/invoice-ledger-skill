---
name: invoice-ledger-skill
description: Extract local PDF, image, scanned, and XML invoice files into one persistent Excel invoice ledger.
slug: maple192600-li-invoice-ledger
displayName: 发票台账本地化采集
version: 1.0.1
summary: 本地识别发票文件并持续写入用户选择的 Excel 台账。
license: MIT
---

# Invoice Ledger Skill

本 Skill 在本机识别发票文件，并把结果持续写入用户选择的同一份 Excel 台账。

## 首次安装

首次安装时询问：

`发票采集台账希望保存到哪个文件夹？也可以直接提供完整的 .xlsx 路径。`

取得用户选择后运行：

```powershell
python scripts\install_skill_env.py --ocr auto --ledger <文件夹或.xlsx路径>
```

安装器创建项目 `.venv`，按电脑环境安装 GPU 或 CPU OCR 依赖，并把根目录 `发票采集台账.xlsx` 复制到用户选择的位置。目标台账已存在时直接沿用，禁止覆盖。

环境修复时运行：

```powershell
python scripts\install_skill_env.py --ocr auto
```

环境、模板或 OCR 异常时运行：

```powershell
.\.venv\Scripts\python.exe scripts\fp_doctor.py
```

## 台账规则

- 先读取 `config/user_settings.local.yaml` 的 `draft_ledger`。
- 已保存的台账存在时持续使用该文件。
- 配置缺失或路径失效时重新询问用户，禁止自行选择默认位置。
- 根目录 `发票采集台账.xlsx` 只作为空白母版。
- 新目标不存在时复制母版；目标已存在时先做兼容性检查。
- 每次运行只追加新行并跳过疑似重复，禁止覆盖、清空或替换已有数据。
- 禁止使用 `--replace-existing` 和 `--update-existing`。

首次写入前执行兼容性检查：

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --check-only --input-dir <发票目录> --draft-ledger <台账.xlsx> --config config\runtime_ocr_auto.yaml --output-dir output
```

## 运行

单个文件：

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input <发票文件> --draft-ledger <台账.xlsx> --config config\runtime_ocr_auto.yaml --output-dir output --json-output summary
```

文件夹：

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input-dir <发票目录> --draft-ledger <台账.xlsx> --config config\runtime_ocr_auto.yaml --output-dir output --json-output summary
```

OCR 任务使用 `config\runtime_ocr_auto.yaml`。仅处理文本层 PDF 时可以使用 `config\runtime.yaml`。

## 结果

- 正常识别结果写入“发票信息采集”和“发票基础信息”工作表。
- 待复核、未支持和未写入项写入“识别提示”工作表。
- 正常回复只读取命令摘要和最终中文消息。
- 仅在具体票据失败时读取对应证据文件。
- OFD 暂不支持，要求用户提供 PDF 版式文件。
