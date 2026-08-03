---
name: invoice-ledger-skill
description: 批量读取本地 PDF、图片、XML 等发票文件，提取票面和商品明细，拆分多票 PDF、合并已确认属于同一发票的连续页面，并将结果追加到用户指定的 Excel 发票采集台账；重复项跳过，异常集中写入“识别提示”。当用户要求识别、整理、汇总、导入或批量处理发票，续写已有发票台账，或处理数电发票、多页 PDF、扫描件、出租车票、定额发票时使用。优先直接解析原始文本型 PDF；仅在输入没有可用文本层时使用本地 OCR。
---

# 发票识别并追加台账

把用户提供的发票完整处理到 Excel 写入和结果汇报。默认批量处理，复用已经配置的环境和台账。

## 执行原则

- 以本 `SKILL.md` 所在目录为 skill 根目录，所有相对路径和命令都从该目录执行。
- 优先使用数电发票原始文本型 PDF，不要先转成图片。系统先解析已有文本，只有没有文本或直接识别未通过的页面才会自动回退 OCR。
- 系统按文件和页面分流，同一目录只需执行一次。文本识别通过的页面不会调用 OCR。
- 台账只追加新数据并跳过疑似重复。禁止覆盖、清空或替换已有数据，禁止使用 `--replace-existing` 和 `--update-existing`。
- 没有证据的字段保持为空或待复核，不猜测发票号码、购销双方、金额、税额和商品明细。
- 不支持 OFD。要求用户从电子税务局税务数字账户下载 PDF 版式文件。

## 1. 确定输入和台账

从用户请求中取得单个发票文件或发票文件夹。批量目录先盘点 PDF、PNG、JPG、JPEG、BMP、TIF、TIFF、WebP、XML、TXT 和 Markdown 文件，并单独记录 OFD 及其他不支持的文件。

`--input-dir` 只处理当前目录，不递归子目录。请求范围包含子目录时，对每个含支持文件的目录分别执行，持续写入同一台账，最后汇总所有运行结果。不得把子目录或不支持的文件静默算作已处理。

读取 `config/user_settings.local.yaml` 中的 `draft_ledger`。该文件不存在、保存路径失效或用户明确指定另一份台账时，取得用户提供的文件夹或 `.xlsx` 路径，并在下一步把它传给安装器创建或记录。不得把 skill 根目录的 `发票采集台账.xlsx` 当作工作台账，它只用于创建新台账。

## 2. 准备环境

已有 `.venv` 时直接检查，不重复安装：

```powershell
# 只处理原始文本型 PDF、XML、TXT 或 Markdown
.\.venv\Scripts\python.exe scripts\fp_doctor.py --no-ocr

# 发现 OCR 页面后再运行完整 OCR 环境检查
# .\.venv\Scripts\python.exe scripts\fp_doctor.py
```

只处理文本型文件时，检查结果不能是 `blocked`。OCR 环境由预检统计决定，避免为纯文本批次安装 OCR。

`.venv` 不存在或检查结果为 `blocked` 时，先准备基础环境：

```powershell
python scripts\install_skill_env.py --ocr none --ledger <文件夹或台账.xlsx>
```

环境已经可用、只需创建或切换台账时，运行 `python scripts\install_skill_env.py --ocr none --ledger <文件夹或台账.xlsx>`，避免重复安装 OCR。

预检返回码为 `3` 时，运行 `python scripts\install_skill_env.py --ocr auto --ledger <文件夹或台账.xlsx>`；安装器自动选择 GPU 或 CPU 依赖，不由执行者判断数量或设备。安装完成后重新运行预检。只要 `ocr_required_pages` 大于 `0`，就运行完整的 `fp_doctor.py`，确认 `paddle` 和 `paddleocr` 均可用；`text_only_ready` 和 `blocked` 都不能继续执行 OCR。失败时保留真实错误，不继续写入台账。

## 3. 选择识别配置

普通识别统一使用 `config\runtime_ocr_auto.yaml`。它只在实际需要时调用 OCR，并自动选择 GPU 或 CPU。`runtime.yaml`、`runtime_ocr_cpu.yaml`、`runtime_ocr_gpu.yaml` 仅用于明确的调试或设备固定场景。

## 4. 写入前检查

先运行 `--check-only`。单个文件使用 `--input`，文件夹使用 `--input-dir`，两者只选一个。

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py <输入参数> --draft-ledger <台账.xlsx> --config <识别配置> --output-dir output --check-only
```

预检会实际尝试原生文本识别，并输出 `direct_files`、`structured_files`、`ocr_required_files`、`ocr_fallback_files`、`unsupported_files` 和 `ocr_required_pages`。它不运行 OCR，也不修改 Excel。返回码为 `0` 才继续；返回码为 `3` 表示需要先准备 OCR 环境；返回码为 `2` 表示参数、台账、配置或输入存在阻断。

## 5. 执行识别和写入

使用与检查阶段相同的输入、台账和配置，去掉 `--check-only`：

```powershell
.\.venv\Scripts\python.exe scripts\fp_ledger.py <输入参数> --draft-ledger <台账.xlsx> --config <识别配置> --output-dir output --json-output summary
```

不要把单张、单个目录成功当作整批完成。读取每次运行的最终 JSON 摘要并汇总，核对：

- `status`
- `input_count` 和 `recognized_invoices`
- `added_rows` 和 `skipped_duplicate_rows`
- `review_required_units`、`unmodeled_units` 和 `failed_units`
- `output_workbook`

## 6. 汇报结果

向用户说明实际处理文件数、识别发票数、新增明细数、疑似重复数、待复核或失败数，以及目标 Excel 的完整路径。

待复核、未支持和未写入原因以 Excel 的“识别提示”工作表为准。只有具体票据失败或需要定位时，才读取 `output\units` 下对应证据文件。命令失败、结果为 `partial` 或 `uncompleted` 时明确说明，不能声称全部完成。
