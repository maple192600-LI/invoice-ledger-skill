# 多 Agent 安装说明

本 skill 是一个 Python 项目加标准 `SKILL.md`，遵循 [Agent Skills 开放标准](https://agentskills.io/specification)。任何兼容该标准的 agent 都能识别和使用，包括 Claude Code、OpenAI Codex、Cursor、Gemini CLI、GitHub Copilot、VS Code 等（完整列表见 [agentskills.io](https://agentskills.io)）。

## 通用前置

- Windows
- Python 3.11+
- Git

## 第一步：获取 skill 源码

```bash
git clone https://github.com/maple192600-LI/invoice-ledger-skill.git
```

## 第二步：挂载到你的 agent

不同 agent 的 skill 目录不同。把仓库（或其软链接）放到对应位置：

| Agent | 用户级目录 | 项目级目录 |
|---|---|---|
| Claude Code | `~/.claude/skills/invoice-ledger-skill/` | `<项目>/.claude/skills/invoice-ledger-skill/` |
| OpenAI Codex | `~/.codex/skills/invoice-ledger-skill/` | `<项目>/.codex/skills/invoice-ledger-skill/` |
| Cursor | 参考 Cursor 官方文档的 skills 目录 | 同 |
| Gemini CLI | 参考 Gemini CLI 官方文档 | 同 |
| 其他兼容 agent | 见 [agentskills.io 采用列表](https://agentskills.io) | — |

> Codex 用户也可在 Codex 终端用 `$skill-installer` 输入仓库 URL 安装；`agents/openai.yaml` 是 Codex 专有界面配置，其他 agent 会忽略，不影响使用。

Claude Code 还支持把整个仓库打包为 plugin，通过 `/plugin install` 或 marketplace 分发，详见 [Claude Code Plugins 文档](https://code.claude.com/docs/en/plugins.md)。

## 第三步：搭建 Python 环境

在 skill 目录下运行（Windows PowerShell）：

```powershell
python scripts\install_skill_env.py --ocr auto --ledger <台账文件夹或.xlsx路径>
```

安装器会：

- 创建项目 `.venv`
- 按 GPU/CPU 自动安装 OCR 依赖（PaddleOCR，可选 GPU 加速）
- 把空白台账母版 `发票采集台账.xlsx` 复制到指定位置（已存在则沿用，不覆盖）

仅修复环境（不重新选台账）：

```powershell
python scripts\install_skill_env.py --ocr auto
```

## 第四步：验证

向你的 agent 说：

> 把 `D:\待报销发票` 里的发票识别并写入台账。

agent 会按 `SKILL.md` 指引调用 `scripts\fp_ledger.py` 完成识别和写入。

## 环境修复

OCR、模板或路径异常时：

```powershell
.\.venv\Scripts\python.exe scripts\fp_doctor.py
```

## 关于 npm

本 skill 是 Python 项目，核心安装痛点是 Python 环境和本地 OCR 模型，而非分发 skill 文件本身。npm（JavaScript 生态）不适合作为本 skill 的安装方式，请按上述步骤用 Git 获取源码并运行 Python 安装器。
