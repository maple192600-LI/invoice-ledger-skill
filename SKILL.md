---
name: invoice-ledger-skill
description: 本地识别 PDF、图片、扫描件和 XML 发票，持续追加写入同一份 Excel 发票采集台账。适用于批量整理发票、多页多票 PDF 拆分入账、续写已有台账、识别图片或扫描发票。Local OCR-based extraction of PDF/image/scanned/XML invoices into one persistent Excel ledger.
license: MIT
compatibility: Windows、Python 3.11+、本地 OCR（PaddleOCR，可选 GPU），无需联网
metadata:
  version: "1.1.0"
  author: maple192600-LI
---

# 发票台账本地化采集

本 skill 在本机识别发票文件，把结果持续追加写入用户选择的同一份 Excel 发票采集台账。所有处理本地完成，不依赖在线 OCR 服务。

详细安装步骤见 [references/installation.md](references/installation.md)。

## 适用场景

- 一个文件夹里有很多发票 → 批量识别写入台账
- 一个 PDF 合并了多张发票 → 逐页拆分，各自入账
- 同一发票跨多页明细 → 确认身份后合并明细
- 图片或扫描件发票 → 本地 OCR 识别

## 前置要求

- Windows
- Python 3.11+
- 首次使用需搭建本地环境（见下文“首次安装”）

## 首次安装

1. 询问用户台账保存位置：

   > 发票采集台账希望保存到哪个文件夹？也可以直接提供完整的 .xlsx 路径。

2. 搭建本地环境并初始化台账（Windows PowerShell）：

   ```powershell
   python scripts\install_skill_env.py --ocr auto --ledger <文件夹或.xlsx路径>
   ```

   安装器创建项目 `.venv`，按电脑环境安装 GPU 或 CPU OCR 依赖，并把空白母版 `发票采集台账.xlsx` 复制到用户选择的位置。目标台账已存在时直接沿用，禁止覆盖。

3. 环境、模板或 OCR 异常时运行修复：

   ```powershell
   .\.venv\Scripts\python.exe scripts\fp_doctor.py
   ```

## 台账规则

- 先读取 `config/user_settings.local.yaml` 的 `draft_ledger`。
- 已保存的台账存在时持续使用该文件。
- 配置缺失或路径失效时重新询问用户，禁止自行选择默认位置。
- 根目录 `发票采集台账.xlsx` 只作为空白母版；新目标不存在时复制母版，目标已存在时先做兼容性检查。
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

整个文件夹：

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input-dir <发票目录> --draft-ledger <台账.xlsx> --config config\runtime_ocr_auto.yaml --output-dir output --json-output summary
```

OCR 任务使用 `config\runtime_ocr_auto.yaml`；仅处理文本层 PDF 时可以使用 `config\runtime.yaml`。

## 输出结果

- 正常识别结果写入“发票信息采集”和“发票基础信息”工作表。
- 待复核、未支持和未写入项写入“识别提示”工作表。
- 正常回复只读取命令摘要和最终中文消息。
- 仅在具体票据失败时读取对应证据文件。

## 使用边界

- 暂不支持 OFD，要求用户提供 PDF 版式文件。
- OCR 结果受图片清晰度、倾斜、遮挡和票面版式影响，低置信度内容会进入“识别提示”。
- 本 skill 负责发票信息采集和台账整理，不代替税务申报、抵扣判断或票据真伪查验。
