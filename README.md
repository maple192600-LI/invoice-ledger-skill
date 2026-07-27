# invoice-ledger-skill

**把本地发票 PDF、图片和扫描件识别成 Excel 发票采集台账。**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

## 项目介绍

`invoice-ledger-skill` 是一个本地运行的 Codex Skill。它读取发票文件，提取发票号码、开票日期、购销双方、金额、税额和明细，并持续写入用户首次选择的同一份 Excel 台账。

## 功能范围

- 读取文本层 PDF、图片型 PDF、PNG、JPG、TIFF、BMP、WebP、XML、TXT 和 Markdown。
- 文本层票据直接解析，图片和扫描件使用 PaddleOCR。
- 多页 PDF 逐页识别；不同发票分别生成发票单元，同一张跨页发票确认身份一致后合并明细。
- 识别结果按票种 Schema 校验后写入 `发票采集台账.xlsx`。
- 已有台账只追加新行，不覆盖、不清空；疑似重复记录不重复写入。
- 低置信度、未支持票种和未写入原因集中写入“识别提示”工作表。
- 暂不支持 OFD，需先取得 PDF 版式文件。

## 支持票种

- 电子发票（普通发票）
- 电子发票（增值税专用发票）
- 传统增值税发票
- 数电机动车销售统一发票
- 航空运输电子客票行程单
- 铁路电子客票
- 公路汽车客票
- 水路旅客运输客票
- 出租车机打发票
- 地铁定额发票
- 通用机打发票
- 医疗收费票据
- 税收完税证明

## 安装

把下面的指令交给 Codex：

```text
使用 skill-installer 从 GitHub 安装这个公开仓库：maple192600-LI/invoice-ledger-skill。
```

安装完成后重启 Codex。

首次启用时，Skill 会询问发票采集台账的保存位置。用户可以选择文件夹，也可以提供已有 `.xlsx` 文件。选择结果保存到本机配置，后续批次持续写入该台账。

## 运行环境

- Python 3.11+
- Windows PowerShell
- PyMuPDF
- openpyxl
- PyYAML
- Pydantic
- PaddleOCR
- NVIDIA GPU 可用时自动使用 `paddlepaddle-gpu`，否则使用 CPU 版本

## 台账规则

根目录 `发票采集台账.xlsx` 是空白母版，不直接写入。目标台账不存在时复制一次；目标文件已存在时先校验再续写。`--replace-existing` 和 `--update-existing` 均被禁止。

## 作者

[maple192600-LI](https://github.com/maple192600-LI)

## License

[MIT](./LICENSE)
