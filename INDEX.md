# 仓库索引

这个索引用来快速定位仓库文件。`SKILL.md` 是 Agent 运行本 Skill 的入口，`README.md` 是 GitHub 项目首页；本文件只做维护索引。

## 入口文件

| 路径 | 用途 |
| --- | --- |
| `SKILL.md` | Agent 使用本 Skill 时读取的核心规则 |
| `README.md` | GitHub 项目说明 |
| `agents/openai.yaml` | Codex Skill 列表展示信息 |
| `scripts/install_skill_env.py` | 首次安装环境，创建 `.venv`，按 GPU/CPU 安装 OCR 依赖 |
| `scripts/fp_doctor.py` | 首次安装后或环境异常时检查依赖、OCR、配置和模板 |
| `scripts/fp_ledger.py` | 发票处理 CLI 入口 |

## 运行配置

| 路径 | 用途 |
| --- | --- |
| `config/runtime_ocr_auto.yaml` | OCR 自动模式，按机器环境选择 GPU 或 CPU 配置 |
| `config/runtime_ocr_gpu.yaml` | GPU OCR 配置 |
| `config/runtime_ocr_cpu.yaml` | CPU OCR 配置 |
| `config/runtime.yaml` | 仅处理文本层 PDF 的配置 |
| `config/template_profiles/current.yaml` | Excel 模板、工作表、列名和字段映射 |
| `config/status_messages.yaml` | 状态码中文显示 |
| `config/deductible_vat_rules.yaml` | 可抵扣进项税规则 |

## 模板和票种

| 路径 | 用途 |
| --- | --- |
| `templates/invoice-information-collection.xlsx` | 默认空白采集模板 |
| `schemas/catalog.yaml` | 票种 Schema 注册表 |
| `schemas/*.yaml` | 已支持票种的结构化规则 |
| `schemas/templates/new-schema-template.yaml` | 新增票种时复制的空白 Schema 模板 |

## 核心代码

| 路径 | 用途 |
| --- | --- |
| `src/invoice_ledger/cli.py` | CLI 参数、运行流程、摘要输出 |
| `src/invoice_ledger/doctor.py` | 环境和模板检查 |
| `src/invoice_ledger/contracts.py` | 全局数据模型 |
| `src/invoice_ledger/_paths.py` | 项目根目录定位 |
| `src/invoice_ledger/errors.py` | 错误类型 |

## 输入识别

| 路径 | 用途 |
| --- | --- |
| `src/invoice_ledger/input_profile/file_profile.py` | 判断输入文件类型和文本层质量 |
| `src/invoice_ledger/input_profile/invoice_units.py` | 把文件拆成可处理的发票单元 |
| `src/invoice_ledger/input_profile/text_extraction.py` | 提取 PDF 文本层 |
| `src/invoice_ledger/input_profile/ocr_adapter.py` | 调用 OCR |
| `src/invoice_ledger/input_profile/ocr_cache.py` | OCR 缓存 |
| `src/invoice_ledger/input_profile/pdf_context.py` | PDF 页面上下文 |
| `src/invoice_ledger/input_profile/text_units.py` | 文本块结构 |

## 票种匹配和解析

| 路径 | 用途 |
| --- | --- |
| `src/invoice_ledger/schema/schema_loader.py` | 加载 Schema |
| `src/invoice_ledger/schema/schema_router.py` | 根据文本匹配票种 |
| `src/invoice_ledger/schema/schema_bootstrapper.py` | Schema 初始化辅助 |
| `src/invoice_ledger/parsing/field_candidates.py` | 字段候选值生成 |
| `src/invoice_ledger/parsing/field_resolver.py` | 字段候选值决策 |
| `src/invoice_ledger/parsing/invoice_identity.py` | 发票身份和号码判断 |
| `src/invoice_ledger/parsing/scheme_extractors/` | 各票种专用解析器 |
| `src/invoice_ledger/parsing/_*.py` | 通用字段、明细、金额、购销方解析辅助 |

## 校验和复核

| 路径 | 用途 |
| --- | --- |
| `src/invoice_ledger/validation/record_validator.py` | 识别记录质量校验 |
| `src/invoice_ledger/validation/validation_policy.py` | 校验策略 |
| `src/invoice_ledger/validation/review_notes.py` | 面向用户的中文复核提示 |
| `src/invoice_ledger/validation/deductible_vat.py` | 进项税抵扣判断 |

## Excel 写入

| 路径 | 用途 |
| --- | --- |
| `src/invoice_ledger/output/ledger_rows.py` | 把识别记录转换为台账行 |
| `src/invoice_ledger/output/template_profile.py` | 校验 Excel 模板和 profile 是否匹配 |
| `src/invoice_ledger/output/template_writer.py` | 按模板 profile 写入 Excel |
| `src/invoice_ledger/output/duplicate_rows.py` | 已写入记录和重复行定位 |
| `src/invoice_ledger/output/recognition_notices.py` | 生成“识别提示”页内容 |
| `src/invoice_ledger/output/evidence.py` | 保存失败、未建模或需复核证据 |
| `src/invoice_ledger/output/ledger_writer.py` | 旧写入辅助逻辑 |

## 常见修改入口

| 目标 | 优先查看 |
| --- | --- |
| 改安装逻辑 | `scripts/install_skill_env.py`, `requirements*.txt`, `SKILL.md` |
| 改运行命令或输出摘要 | `src/invoice_ledger/cli.py`, `SKILL.md` |
| 改 Excel 列名或模板 | `templates/invoice-information-collection.xlsx`, `config/template_profiles/current.yaml` |
| 改“识别提示”页 | `src/invoice_ledger/output/recognition_notices.py`, `src/invoice_ledger/output/template_writer.py`, `config/template_profiles/current.yaml` |
| 改重复判断 | `src/invoice_ledger/output/duplicate_rows.py`, `src/invoice_ledger/output/template_writer.py` |
| 新增票种 | `schemas/templates/new-schema-template.yaml`, `schemas/catalog.yaml`, `src/invoice_ledger/parsing/scheme_extractors/` |
| 改 OCR 行为 | `config/runtime_ocr_auto.yaml`, `config/runtime_ocr_gpu.yaml`, `config/runtime_ocr_cpu.yaml`, `src/invoice_ledger/input_profile/ocr_adapter.py` |
| 改复核规则 | `src/invoice_ledger/validation/`, `src/invoice_ledger/output/recognition_notices.py` |

## 不应提交的内容

| 内容 | 原因 |
| --- | --- |
| `.venv/` | 本机虚拟环境 |
| `.ocr_cache/` | 本机 OCR 缓存 |
| `output/`, `outputs/`, `runs/`, `debug-output/` | 运行结果可能包含发票文本 |
| `units/`, `evidence.md`, `run_summary.json`, `write_result.json` | 运行证据和摘要可能包含发票信息 |
| 用户发票、测试发票、客户 Excel 台账 | 私有数据 |
