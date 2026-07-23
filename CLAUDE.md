# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

`invoice-ledger-skill` 是一个**本地离线运行**的发票采集 Skill：把 PDF / 图片 / 扫描件发票识别、结构化后写入可复核的 Excel 台账。核心约束贯穿全部设计——财务数据不出本机，原始票据不被 AI 直接读取（OCR / 解析全部由本地脚本完成，只有结构化字段写入台账）。

- 技术栈：Python 3.11+、PyMuPDF（PDF 文本层）、openpyxl（Excel 读写）、PyYAML（配置 + 票种 Schema）、pydantic（数据校验）、PaddleOCR/PaddlePaddle（图片 / 扫描件 OCR）
- 运行平台：Windows PowerShell 为主，依赖封装在项目自带 `.venv`，不污染系统 Python
- 本仓库内文件名保持 ASCII；Excel 工作表名 / 表头 / 用户可见文案为中文

## 常用命令

```powershell
# 首次安装（克隆 / 换机 / 删 .venv / 改 OCR 后执行一次）：建 .venv 并按 GPU/CPU 装 OCR 依赖
python scripts\install_skill_env.py --ocr auto

# 环境自检（仅在首次安装或环境/模板/OCR 异常时跑，不要每批发票都跑）
.\.venv\Scripts\python.exe scripts\fp_doctor.py

# 查看 CLI 签名
.\.venv\Scripts\python.exe scripts\fp_ledger.py --help

# 兼容性预检（不跑 OCR、不改 Excel）：首次正式写入工作台账前先跑一次
.\.venv\Scripts\python.exe scripts\fp_ledger.py --check-only --input-dir <发票文件夹> `
  --draft-ledger <工作台账.xlsx> --config config\runtime_ocr_auto.yaml --output-dir output

# 处理单个文件 / 文件夹
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input <发票> --draft-ledger <工作台账.xlsx> `
  --config config\runtime_ocr_auto.yaml --output-dir output --json-output summary
.\.venv\Scripts\python.exe scripts\fp_ledger.py --input-dir <发票文件夹> --draft-ledger <工作台账.xlsx> `
  --config config\runtime_ocr_auto.yaml --output-dir output --json-output summary
```

命令要点：

- 入口脚本 `scripts/fp_ledger.py` 会自动 `chdir` 到仓库根目录并把 `src/` 插入 `sys.path`，因此用绝对路径调用也始终以仓库根为工作目录。
- 配置文件三选一：`config/runtime_ocr_auto.yaml`（占位符，运行时按 `nvidia-smi` 自动选 GPU/CPU，**OCR 任务用它**）；`config/runtime_ocr_gpu.yaml` / `runtime_ocr_cpu.yaml`（显式）；`config/runtime.yaml`（仅文本层 PDF，不跑 OCR）。
- `--json-output summary` 是 Agent 运行的默认且推荐档；`full` 仅用于调试（会打印整条记录，对 Agent 上下文昂贵）。
- 写入默认追加到 `--draft-ledger` 指向的工作台账并跳过疑似重复；`--copy-output` 仅在用户明确要一份当次副本时使用。证据默认只对失败 / 未建模 / 需复核单元保存（`--save-evidence failed`）。
- 工作台账纪律：`templates/invoice-information-collection.xlsx` 只是**空白母版**，正式采集要先复制成一份工作台账，多批发票始终指向同一份（追加 + 去重），不要直接写 `templates/`。

### 当前工作树的 venv 说明

本仓库（`E:\maple192600-...`）是**源仓库 / 单一事实源**，可能没有自带 `.venv`。当前活跃 venv 在已安装副本 `C:\Users\Administrator\.workbuddy\skills\invoice-ledger\.venv\Scripts\python.exe`，可用来直接跑本仓库脚本（脚本会自动切根目录、加载本仓库 `src`），不必在源仓库再建 venv。验证环境前先确认实际 venv 位置。

## 核心架构（处理管线）

一条发票从文件到台账行，固定经过以下阶段（入口 `cli.run_cli` → 每个 input 调 `pipeline.unit_processor.process_invoice_input` → 每个发票单元调 `process_invoice_unit`）：

```
文件 ─ file_profile（判类型/文本层质量）─ invoice_units（按页拆单元）
     │
     ├─ 文本层 PDF ─ text_extraction.extract_text_units
     ├─ 图片/OCR页 ─ ocr_adapter（PaddleOCR，批量+缓存）→ text_units_from_ocr_result
     │
     ▼ TextUnits
schema_router.decide_schema        ← 按 schemas/*.yaml 的 match_rules 关键词路由票种
field_candidates.generate_field_candidates   ← 按 schema fields.aliases 生成候选值
field_resolver.resolve_invoice_record        ← 候选值决策 → InvoiceRecord
deductible_vat.apply_deductible_vat_rules    ← 进项税抵扣规则（config/deductible_vat_rules.yaml）
record_validator.validate_invoice_record      ← 金额勾稽校验，给出 quality/confidence
ledger_rows.build_ledger_rows                 ← 每个明细行 → 一条 LedgerRow
template_writer.write_with_template_profile   ← 按 current.yaml profile 写 Excel，三层去重
```

关键设计点：

- **三态识别**（`RecognitionStatus`，定义在 `contracts.py`）：`READY`（写入）、`REVIEW_REQUIRED`（写入 + 在「识别提示」页标注）、`UNMODELED`/`FAILED`（不写入，保存证据）。低置信与异常一律走「识别提示」页供人工复核，绝不静默写错。
- **数据契约全在 `src/invoice_ledger/contracts.py`**：所有 pydantic 模型。金额走 `normalize_amount`（`Decimal`、2 位、`ROUND_HALF_UP`，自动剥千分位 / ￥ / 全角％）；日期走 `normalize_date`（认 `年/月/-` 分隔）。`LedgerRow` 是唯一映射到 Excel 列的模型。
- **Schema 系统**：`schemas/catalog.yaml` 注册专用票种（`standard-invoice` 是特殊兜底，不进 catalog 匹配）。每个 schema 定义 `match_rules`（路由用）、`variants`+`variant_rules`（票面形态子类）、`fields`（字段别名）、`amount_checks`（勾稽项）。`standard-invoice.yaml` 的 `digital-invoice-form`/`traditional-vat-form` 双变体是"新旧并存票种"的正面范式，新增双轨票种照此扩展。
- **新增票种**：复制 `schemas/templates/new-schema-template.yaml` → 改 → 注册到 `catalog.yaml`；专用解析逻辑放 `parsing/scheme_extractors/`。
- **模板 profile**：`config/template_profiles/current.yaml` 定义 `detail`（发票信息采集）/`summary`（发票基础信息）/`issues`（识别提示）三个 sheet 的字段→列映射与必填位。**改了模板 xlsx 的列名 / 表头 / 必填，必须同步改这个 profile**；`output/template_profile.py` 会校验 xlsx 与 profile 是否漂移，漂移则阻断写入。
- **去重三层**（`output/duplicate_rows.py` + `template_writer.py`）：`draft_row_id` / `invoice_line_key`（`hash(seller_tax_id, invoice_no, invoice_date, total_with_tax)`）/ 行指纹。

## 文档地图（动工前按需通读）

仓库不是只有代码，有一批中文背景文档是事实基准，**改涉及税务口径或票种逻辑前必须先读**：

- `INDEX.md`：维护用文件索引（按修改目标定位文件），比本文件更细。
- `SKILL.md`：Skill 使用规则（Agent 运行入口），命令格式以此为准。
- `README.md`：项目说明（GitHub 首页）。
- `REVIEW.md`：对抗性审查报告，列出已知问题 P1~P12（按严重度分级）。
- `P1-修复方案.md`：航空 / 铁路票税额处理的实测修订（P1a/P1b）。
- `数电票扩展总体方案.md`：双轨制架构决策与票种覆盖矩阵。
- `实施计划.md`：**当前权威任务书**（Phase 1 清旧账 / Phase 2 数电票结构化通道 / Phase 3 验证同步 + 验收门 G1/G2/G3 + 七条铁律）。承接开发任务时以此为准。

## 双轨制（进行中的扩展方向）

新增的数电票结构化数据通道（详见 `数电票扩展总体方案.md` / `实施计划.md`）：

- **A 轨（结构化直读，零识别误差）**：A1 = PDF 内嵌 XBRL 直读（铁路票实测内嵌官方 XBRL，字段字典在 `config/xbrl/*.xml`）；A2 = 独立 `.xml` 文件（总局 EInvoice 格式，如高德交付，标准库 `xml.etree` 即可解析）。两路产出统一写入现有 `InvoiceRecord`，模板与写入/去重逻辑不动。
- **B 轨（PDF 文本层 / OCR 识别，保留）**：有官方标准的票种按官方字段语义校准；无官方标准的旧票种 schema 原样保留。
- 双轨一致性铁律：同一张发票无论走 A 还是 B，台账身份（号码、去重键）必须唯一，否则重复入账。

## 项目铁律（违反即返工）

1. **零新增第三方依赖**：只用 venv 已有包（PyMuPDF/openpyxl/PyYAML/pydantic/PaddleOCR）+ 标准库。想"装个包"时先找替代做法。
2. **不过度设计**：不做验签、云查验、OFD 解析、银行/国库凭证、会计入账字段、引入 Java/jar。
3. **没有样本不宣称**：无真实样本验证的票种/格式，只留接口并在文档注明"未验证"，不写"支持"。
4. **单一事实源**：改动只在源仓库做；已安装副本（`.workbuddy\skills\invoice-ledger\`）仅同步。
5. **不静默改税务口径**：凡涉及税率、抵扣判断，只呈现票面/官方数据与来源，抵扣建议一律写"与主管税务机关确认"。公式算出的税额要在备注标注来源。
6. **完成 = 代码 + 实测验证**：看代码存在 ≠ 功能正确。涉及字段变更后必须重跑真实样本对拍（回归基线见 `回归基线/v0/`），差异要能说清。

## 证据纪律 / 输出

- 正常运行只看 stdout 的 JSON 摘要和结尾中文 stderr 消息；不要把完整 `run_summary.json`、evidence JSON、pip/OCR 进度日志贴进对话，除非排查具体失败需要。
- 输出与证据可能含发票文本，默认落在仓库内 `output/`、`outputs/`、`runs/`、`debug-output/` 下（已在 `.gitignore`）。
- 不应提交：`.venv/`、`.ocr_cache/`、`output*/`、`units/`、`回归基线/`、`数电发票/`、用户发票与台账等私有数据。

## Claude Code 开发配置

仓库根 `.claude/` 下有本项目专用的 Claude Code 配置，让开发时权限顺、高频操作一键触发：

- **`.claude/settings.json`**（项目共享）：`env.PYTHONUTF8=1`（Windows 中文输出保险）；允许系统 `python` 跑安装器与查版本。刻意不设阻断性 `deny`，避免妨碍 T1.4 等需要改 `templates` 模板的合法开发操作。
- **`.claude/settings.local.json`**（本机特定）：允许项目 venv 解释器跑任意脚本，消除 `fp_ledger` / `fp_doctor` / `install_skill_env` 的高频权限弹窗。venv 路径写死在文件内——换机或在源仓库建了独立 `.venv` 后需更新此文件。
- **斜杠命令**（`.claude/commands/`）：
  - `/inv-run <发票文件或目录>`：采集发票，自动选 auto OCR 配置，默认复制工作台账、低 token 输出。
  - `/inv-check <发票文件或目录>`：`--check-only` 写入前预检，不跑 OCR、不改 Excel。
  - `/inv-doctor`：环境自检，仅首次安装或排查环境/模板/OCR 问题时用。

venv 解释器当前固定为已安装副本 `C:/Users/Administrator/.workbuddy/skills/invoice-ledger/.venv/Scripts/python.exe`（系统 Python 3.12 未装项目依赖，不可直接用来跑脚本）。
