---
description: 跑 fp_doctor.py 环境自检（Python / OCR / 模板 / profile），仅在首次安装或环境异常时用
---

用项目 venv 跑环境自检：

```
C:/Users/Administrator/.workbuddy/skills/invoice-ledger/.venv/Scripts/python.exe scripts/fp_doctor.py
```

逐项报告检查结果（Python 版本、OCR 环境可用性、空白模板与 `config/template_profiles/current.yaml` 的一致性），有失败项给出原因。

**使用边界**（来自 SKILL.md）：doctor **不是**每批发票都要跑，仅在首次安装、或排查环境/模板/OCR 问题时使用。日常发票采集直接用 `/inv-run`。
