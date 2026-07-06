# invoice-ledger-skill

**把本地发票 PDF、图片、扫描件识别成可复核的 Excel 发票采集表。**

<img src="assets/banner.png" alt="invoice-ledger-skill banner" width="100%">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

## 这是什么

`invoice-ledger-skill` 是一个本地运行的 Codex Skill，用来把发票文件识别、解析并写入 Excel 采集模板。它适合处理文本层 PDF、图片型发票、扫描件和多页发票合集。

它的目标很明确：让 Agent 在用户本机完成发票信息采集。

## 项目依赖

运行环境：

- Python 3.11+
- Windows PowerShell
- 本项目目录内的 `.venv`

基础依赖：

- PyMuPDF：读取 PDF 文本层和页面内容
- openpyxl：读取和写入 Excel 模板
- PyYAML：读取配置和票种 Schema
- Pydantic：结构化数据校验

OCR 依赖：

- PaddleOCR
- 有 NVIDIA GPU 时使用 `paddlepaddle-gpu`
- 没有 NVIDIA GPU 时使用 `paddlepaddle`
- GPU 检测依赖 `nvidia-smi`

项目内置安装器会按电脑环境选择 OCR 版本，依赖安装在项目 `.venv`，不写入系统 Python 环境。

## 安装这个 Skill

这是一个按 Codex Skill 结构组织的 Skill。Claude Code、OpenClaw、Hermes、WorkBuddy 等 Agent 环境只要支持 Skill 或类似的本地能力包机制，通常也可以使用本项目；下载后按对应 Agent 的 Skill 规范调整 `SKILL.md` 入口说明即可。

把下面这条指令交给 Agent 执行即可：

```text
使用 skill-installer 从 GitHub 安装这个公开仓库：maple192600-LI/invoice-ledger-skill。
```

安装完成后重启 Codex，让 Codex 重新加载 Skill 列表。

首次真正处理发票时，Agent 会按 `SKILL.md` 的规则检查本机环境，并把 Python、OCR 和 Excel 相关依赖安装到 Skill 自己的 `.venv` 里。

## 工作方式

1. Agent 读取用户提供的发票文件或文件夹。
2. Skill 判断输入类型，能读文本层就直接解析，需要 OCR 时调用 PaddleOCR。
3. 识别结果按 `schemas/` 里的票种规则结构化。
4. 用户从空白模板复制出一个工作台账，结果追加写入这个工作台账。
5. 低置信度、未支持票种和异常字段写入复核提示。

## 模板

默认空白模板：

```text
templates/invoice-information-collection.xlsx
```

用户可以替换这个模板。只要改了工作表名、列名、必填字段或字段映射，就同步更新：

```text
config/template_profiles/current.yaml
```

正式处理发票时，不要直接写入 `templates/` 里的空白模板。先复制一份作为工作台账，后续多批发票继续指向同一个工作台账，系统会追加写入并跳过疑似重复记录。

## 新增发票种类

票种规则在：

```text
schemas/
```

新增自己的发票场景时，复制 `schemas/templates/new-schema-template.yaml`，改成新的票种 YAML，再注册到 `schemas/catalog.yaml`。

## 仓库内容

- `SKILL.md`：给 Codex 看的 Skill 使用规则
- `scripts/`：安装器、环境检查、发票处理入口
- `src/`：识别、解析、校验、写入逻辑
- `schemas/`：票种 Schema
- `config/`：运行配置、OCR 配置、模板 profile
- `templates/invoice-information-collection.xlsx`：可替换的空白采集模板

## 边界

这个项目只做本地发票信息采集和 Excel 写入。识别结果需要按实际业务要求复核。

## 工具声明与感谢

本 Skill 使用并感谢这些工具和开源项目：

- PaddleOCR / PaddlePaddle：图片型发票和扫描件 OCR
- PyMuPDF：PDF 文本层和页面内容读取
- openpyxl：Excel 采集模板读写

## License

[MIT](./LICENSE)
