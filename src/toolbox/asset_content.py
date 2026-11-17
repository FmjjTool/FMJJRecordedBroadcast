"""资产预览内容转换（L3，纯函数，不依赖 Qt）。

Markdown / HTML / DOCX / 纯文本 → 可嵌入的 HTML 片段。
"""

from __future__ import annotations

import html as html_lib
import os
import re
import zipfile
from pathlib import Path


def fmt_size(n) -> str:
    n = int(n or 0)
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n/1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n/1024/1024:.1f} MB"
    return f"{n/1024/1024/1024:.2f} GB"


def read_text_file(path: str) -> str:
    """读文本文件（UTF-8 优先，GBK/GB18030 兜底）。"""
    data = Path(path).read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def md_to_html(text: str, base_dir: str) -> str:
    """公众号 MD 导出 → 可预览 HTML（标题/引用/加粗/图片）。"""
    lines = text.splitlines()
    out, para, quote = [], [], []

    def flush_para():
        if para:
            out.append("<p>" + "<br>".join(para) + "</p>")
            para.clear()

    def flush_quote():
        if quote:
            out.append("<blockquote>" + "".join(f"<p>{x}</p>" for x in quote)
                       + "</blockquote>")
            quote.clear()

    def inline(s: str) -> str:
        s = html_lib.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return s

    for raw in lines:
        line = raw.strip()
        m_img = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", line)
        if m_img:
            flush_para()
            flush_quote()
            alt, src = m_img.group(1), m_img.group(2)
            if not re.match(r"^(https?:|data:|file:)", src, re.I):
                local = os.path.abspath(os.path.normpath(
                    os.path.join(base_dir, src)))
                src = Path(local).as_uri()
            out.append(f'<p><img src="{html_lib.escape(src, quote=True)}" '
                       f'alt="{html_lib.escape(alt, quote=True)}"></p>')
            continue
        m_h = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m_h:
            flush_para()
            flush_quote()
            level = min(len(m_h.group(1)) + 1, 6)
            out.append(f"<h{level}>{inline(m_h.group(2))}</h{level}>")
            continue
        if line in ("---", "***", "___"):
            flush_para()
            flush_quote()
            out.append("<hr>")
            continue
        if line.startswith(">"):
            flush_para()
            text_q = line.lstrip(">").strip()
            if text_q:
                quote.append(inline(text_q))
            continue
        if not line:
            flush_para()
            flush_quote()
            continue
        para.append(inline(line))
    flush_para()
    flush_quote()
    return "".join(out)


def html_to_content(html_text: str):
    """公众号 HTML 导出 → (标题, 正文 HTML)。"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_text, "html.parser")
    title = soup.title.get_text(strip=True) if soup.title else ""
    node = soup.find("div", class_="content") or soup.body or soup
    for tag in node.find_all(["script", "style"]):
        tag.decompose()
    return title, node.decode_contents()


def docx_to_html(path: str) -> str:
    """docx 正文提取（最小 OOXML 读取，失败返回空串）。"""
    from xml.sax.saxutils import unescape as xml_unescape
    try:
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return ""
    out = []
    for p in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
        texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p, re.S)
        line = xml_unescape("".join(texts)).strip()
        if line:
            out.append(f"<p>{html_lib.escape(line)}</p>")
    return "".join(out)


def text_preview_html(text: str, limit: int = 200000) -> str:
    text = text[:limit]
    style = ("white-space:pre-wrap; font-family:Consolas,'Microsoft YaHei';"
             " font-size:12px; color:#333;")
    return f'<pre style="{style}">' + html_lib.escape(text) + "</pre>"


def files_summary_html(files: list, empty_tip: str = "（没有可预览的文档）") -> str:
    if not files:
        return f'<p style="color:#888;">{empty_tip}</p>'
    rows = []
    for f in files[:300]:
        name = html_lib.escape(f.get("name") or "")
        size = fmt_size(f.get("size", 0))
        rows.append(f"<li>{name} <span style='color:#888;'>{size}</span></li>")
    more = "" if len(files) <= 300 else f"<p style='color:#888;'>… 其余 {len(files) - 300} 个文件</p>"
    return ("<p style='color:#666;'>该条目没有可内嵌预览的正文，包含以下文件：</p><ul>"
            + "".join(rows) + "</ul>" + more)
