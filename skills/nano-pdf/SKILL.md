---
name: nano-pdf
description: Edit PDFs with natural-language instructions using the nano-pdf CLI.
homepage: https://pypi.org/project/nano-pdf/
metadata:
  {
    "openclaw":
      {
        "emoji": "📄",
        "requires": { "bins": ["nano-pdf"] },
        "install":
          [
            {
              "id": "uv",
              "kind": "uv",
              "package": "nano-pdf",
              "bins": ["nano-pdf"],
              "label": "Install nano-pdf (uv)",
            },
          ],
      },
  }
---

# nano-pdf

Use `nano-pdf` to apply edits to a specific page in a PDF using a natural-language instruction.

## Quick start

```bash
nano-pdf edit deck.pdf 1 "Change the title to 'Q3 Results' and fix the typo in the subtitle"
```

Notes:

- Page numbers are 0-based or 1-based depending on the tool’s version/config; if the result looks off by one, retry with the other.
- Always sanity-check the output PDF before sending it out.

## 用 nano-pdf 重新生成简历（本项目）

对项目根目录下的 `resume.pdf` 做**视觉优化**（版式、颜色、层次），由 Gemini 按自然语言指令重排，效果优于纯代码生成。

**前置条件：**

1. **系统依赖**（二选一）  
   - Windows: `choco install poppler tesseract`  
   - macOS: `brew install poppler tesseract`
2. **Gemini API**：在 [Google AI Studio](https://aistudio.google.com/api-keys) 创建 API Key 并**开通计费**（图像生成需付费），设置环境变量：  
   `GEMINI_API_KEY=你的key`

**运行方式：**

- Windows（PowerShell，项目根目录）：  
  `.\scripts\regenerate_resume_nano_pdf.ps1`
- macOS/Linux：  
  `bash scripts/regenerate_resume_nano_pdf.sh`

脚本会先确保存在 `resume.pdf`（若无则用 `scripts/generate_resume_qmjianli.py` 生成），再调用 `nano-pdf edit` 生成优化版并覆盖 `resume.pdf`（原文件备份为 `resume_backup.pdf`）。设计指令要求：左侧浅灰栏（姓名/联系方式/求职意向/技能）、右侧白底与蓝色竖条标题、一页内排完、保留全部中文内容仅优化版式与配色。
