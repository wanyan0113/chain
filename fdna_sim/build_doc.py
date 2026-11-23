# -*- coding: utf-8 -*-
"""把设计方案 Markdown 渲染为可打印 HTML，并调用 Edge/Chrome 无头模式导出 PDF。"""
import os
import subprocess
import sys

import markdown

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "数据模拟器设计方案.md")
HTML = os.path.join(HERE, "out", "数据模拟器设计方案.html")
PDF = os.path.join(HERE, "out", "数据模拟器设计方案.pdf")

CSS = """
@page { size: A4; margin: 18mm 16mm 16mm 16mm; }
body { font-family: "Microsoft YaHei","PingFang SC","Source Han Sans SC",sans-serif;
       font-size: 10.5pt; line-height: 1.72; color:#1a1a1a; max-width: 100%; }
h1 { font-size: 20pt; border-bottom: 3px solid #2F5597; padding-bottom: 8px; color:#1F3864; }
h2 { font-size: 14pt; margin-top: 22px; color:#1F3864; border-left: 5px solid #2F5597;
     padding-left: 9px; background:#F2F5FA; padding-top:3px; padding-bottom:3px; }
h3 { font-size: 11.5pt; margin-top: 16px; color:#2F5597; }
table { border-collapse: collapse; width: 100%; margin: 10px 0 14px; font-size: 9pt; }
tr { page-break-inside: avoid; }
thead { display: table-header-group; }
th:first-child, td:first-child { white-space: nowrap; }
th { background:#2F5597; color:#fff; font-weight:600; padding:5px 7px; text-align:left;
     border:1px solid #2F5597; }
td { border:1px solid #C9D3E6; padding:5px 7px; vertical-align: top; }
tr:nth-child(even) td { background:#F7F9FC; }
code { background:#F0F2F5; padding:1px 4px; border-radius:3px;
       font-family: Consolas,"Courier New",monospace; font-size:9pt; color:#B03A2E; }
pre { background:#282C34; color:#E6E6E6; padding:12px 14px; border-radius:6px;
      overflow-x:auto; font-size:8.6pt; line-height:1.5; page-break-inside: avoid; }
pre code { background:none; color:#E6E6E6; }
blockquote { border-left: 4px solid #9DC3E6; background:#F4F8FD; margin:10px 0;
             padding:8px 12px; color:#33475B; font-size:9.6pt; }
hr { border:none; border-top:1px solid #D6DCE4; margin:20px 0; }
strong { color:#1F3864; }
ul,ol { padding-left: 22px; }
li { margin: 3px 0; }
"""


def main():
    with open(SRC, encoding="utf-8") as fh:
        text = fh.read()
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "toc", "sane_lists"])
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>数据模拟器设计方案</title><style>{CSS}</style></head><body>{body}</body></html>"""
    os.makedirs(os.path.dirname(HTML), exist_ok=True)
    with open(HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    print("[ok] html ->", HTML)

    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    exe = next((c for c in candidates if os.path.exists(c)), None)
    if not exe:
        print("[warn] 未找到 Edge/Chrome，跳过 PDF")
        return
    url = "file:///" + HTML.replace("\\", "/")
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-sandbox",
           f"--print-to-pdf={PDF}", "--no-pdf-header-footer",
           f"--user-data-dir={os.path.join(HERE, 'out', '_chrome_tmp')}", url]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if os.path.exists(PDF):
        print("[ok] pdf ->", PDF, os.path.getsize(PDF), "bytes")
    else:
        print("[warn] pdf 未生成", r.returncode, r.stderr[-400:])


if __name__ == "__main__":
    sys.exit(main())
