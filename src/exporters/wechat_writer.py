"""公众号文章多格式导出器：HTML / PDF / Word(docx) / 图片文本(Markdown)。

- HTML：自包含单文件（图片 base64 内嵌，双击即可看）
- PDF：QTextDocument + QPdfWriter 渲染（须在 Qt 主线程调用 render_pdf）
- Word：内置最小 docx 生成器（zip + OOXML，无第三方依赖）
- 图片文本：Markdown + 本地图片文件夹（正文文字与图片分离存放）
"""
import base64
import io
import re
import zipfile
from pathlib import Path
from typing import List, Optional
from xml.sax.saxutils import escape as xml_escape

from bs4 import BeautifulSoup, NavigableString, Tag

MIME = {"jpeg": "image/jpeg", "jpg": "image/jpeg", "png": "image/png", "gif": "image/gif"}


# ==================== 公共 ====================

def build_stem(art) -> str:
    from toolbox.utils import sanitize_filename
    digits = re.sub(r"\D", "", (art.publish_time or "")[:10])[:8]
    if len(digits) < 8:
        from datetime import datetime
        digits = datetime.now().strftime("%Y%m%d")
    acct = sanitize_filename(art.account or "公众号", 24)
    title = sanitize_filename(art.title, 48)
    return f"{digits}_{acct}_{title}"


def _meta_lines(art) -> str:
    parts = []
    if art.account:
        parts.append(f"公众号：{art.account}")
    if art.author:
        parts.append(f"作者：{art.author}")
    if art.publish_time:
        parts.append(f"发布时间：{art.publish_time}")
    return "　|　".join(parts)


def _content_soup(art) -> Tag:
    """正文的 soup（img.src = data URI，便于 HTML/PDF 内嵌）。"""
    soup = BeautifulSoup(art.content_html or "", "html.parser")
    for img in soup.find_all("img"):
        w = art.image_by_url(img.get("src") or "")
        if w and w.data:
            img["src"] = f"data:{MIME.get(w.fmt, 'image/jpeg')};base64,{base64.b64encode(w.data).decode()}"
            img["style"] = "max-width:100%;height:auto;"
        else:
            img.decompose()
    return soup


# ==================== HTML ====================

_HTML_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ margin:0; background:#f2f3f5; font-family:"Microsoft YaHei","PingFang SC",sans-serif; }}
.page {{ max-width:714px; margin:0 auto; background:#fff; padding:36px 28px 48px; }}
h1.title {{ font-size:22px; line-height:1.5; margin:0 0 14px; color:#222; }}
.meta {{ font-size:13px; color:#888; border-bottom:1px solid #eee; padding-bottom:14px; margin-bottom:24px; }}
.meta .src {{ display:block; margin-top:6px; word-break:break-all; }}
.content {{ font-size:16px; line-height:1.75; color:#3f3f3f; word-wrap:break-word; }}
.content img {{ max-width:100% !important; height:auto; }}
</style>
</head>
<body><div class="page">
<h1 class="title">{title}</h1>
<div class="meta">{meta}<span class="src">原文链接：<a href="{url}">{url}</a></span></div>
<div class="content">{content}</div>
</div></body></html>"""


def export_html(art, out_path: str) -> str:
    content = _content_soup(art).decode_contents()
    html = _HTML_TMPL.format(
        title=escape_text(art.title), meta=escape_text(_meta_lines(art)),
        url=art.url, content=content)
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return str(p)


def build_full_html(art) -> str:
    """PDF 渲染用完整 HTML（与 HTML 导出同款，仅标题区紧凑些）。"""
    content = _content_soup(art).decode_contents()
    return _HTML_TMPL.format(
        title=escape_text(art.title), meta=escape_text(_meta_lines(art)),
        url=art.url, content=content)


def escape_text(s: str) -> str:
    return xml_escape(s or "")


# ==================== PDF（Qt 主线程调用） ====================

def render_pdf(html: str, out_path: str) -> str:
    from PySide6.QtCore import QMarginsF, QSizeF
    from PySide6.QtGui import QFont, QPageLayout, QPageSize, QPdfWriter, QTextDocument

    doc = QTextDocument()
    doc.setDefaultFont(QFont("Microsoft YaHei", 11))
    doc.setHtml(html)

    writer = QPdfWriter(out_path)
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(16, 14, 14, 14), QPageLayout.Unit.Millimeter)
    writer.setResolution(96)
    doc.setPageSize(QSizeF(writer.width(), writer.height()))
    doc.print_(writer)
    return out_path


# ==================== Markdown（图片文本） ====================

def export_markdown(art, out_dir: str) -> str:
    """导出 Markdown + 图片文件夹：out_dir/{stem}/{stem}.md + out_dir/{stem}/图片/N.ext"""
    stem = build_stem(art)
    folder = Path(out_dir) / stem
    img_dir = folder / "图片"
    img_dir.mkdir(parents=True, exist_ok=True)

    soup = BeautifulSoup(art.content_html or "", "html.parser")
    # 先存图片，img 节点替换为占位再转 md（同一 URL 复用同一编号）
    index = {w.url: i + 1 for i, w in enumerate(art.images)}
    for img in soup.find_all("img"):
        w = art.image_by_url(img.get("src") or "")
        if not (w and w.data):
            img.replace_with("[图片下载失败]")
            continue
        idx = index.get(w.url, 0)
        name = f"{idx:03d}.{w.fmt if w.fmt != 'jpeg' else 'jpg'}"
        target = img_dir / name
        if not target.exists():
            target.write_bytes(w.data)
        img.replace_with(f"![图片{name}](图片/{name})")

    lines = [f"# {art.title}", "", f"> {_meta_lines(art)}  ", f"> 原文链接：{art.url}", "", "---", ""]
    lines.extend(_soup_to_markdown(soup))
    md_path = folder / f"{stem}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return str(md_path)


def _md_inline(node) -> str:
    """节点内文本 → markdown 行内语法（加粗/斜体）。"""
    out = []
    for child in node.children:
        if isinstance(child, NavigableString):
            out.append(str(child))
        elif child.name in ("b", "strong"):
            t = _md_inline(child).strip()
            if t:
                out.append(f"**{t}**")
        elif child.name in ("i", "em"):
            t = _md_inline(child).strip()
            if t:
                out.append(f"*{t}*")
        elif child.name == "br":
            out.append("  \n")
        elif child.name == "img":
            out.append(str(child))  # 已替换为 ![..](..) 字符串
        else:
            out.append(_md_inline(child))
    return "".join(out)


def _soup_to_markdown(soup) -> List[str]:
    lines = []

    def emit(text, prefix=""):
        t = re.sub(r"[ \t\u00a0]+", " ", text).strip()
        if t:
            lines.append(prefix + t)
            lines.append("")

    def walk(node):
        for child in node.children:
            if isinstance(child, NavigableString):
                emit(str(child))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name == "img":
                emit(str(child))
            elif name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                level = min(int(name[1]) + 1, 6)
                t = _md_inline(child).strip()
                if t:
                    lines.append("#" * level + " " + t)
                    lines.append("")
            elif name == "blockquote":
                for sub in child.find_all(["p", "section"]) or [child]:
                    emit(_md_inline(sub), "> ")
            elif name in ("p", "section", "div", "span", "li", "td", "th", "font", "a"):
                # 叶子容器 → 段落；含块级子节点 → 递归
                has_block = child.find(["p", "section", "div", "img", "blockquote", "h1", "h2", "h3", "h4", "ul", "ol", "table"])
                if has_block or name == "li":
                    walk(child)
                else:
                    emit(_md_inline(child))
            elif name in ("ul", "ol", "table", "tbody", "tr"):
                walk(child)
            elif name == "br":
                continue
            else:
                walk(child)

    walk(soup)
    return lines


# ==================== Word（内置最小 docx 生成器） ====================

_MAX_IMG_EMU = 5486400  # 6.0 英寸（96dpi 576px），A4 减页边距后的安全宽度


def _run(text, **kw):
    r = {"text": text}
    r.update(kw)
    return r


def export_docx(art, out_path: str) -> str:
    soup = BeautifulSoup(art.content_html or "", "html.parser")
    blocks = []
    _docx_walk(soup, blocks, art)

    paras = [_p_title(art.title)]
    meta = _meta_lines(art)
    if meta:
        paras.append(_p_runs([_run(meta, size=18, color="808080")], align="left"))
    paras.append(_p_runs([_run(f"原文链接：{art.url}", size=18, color="808080", underline=True)],
                         align="left"))
    paras.append(_p_runs([_run("", size=6)]))

    images = []
    for b in blocks:
        if b["kind"] == "image":
            w = b.get("image")
            if not (w and w.data):
                continue
            no = len(images) + 1
            images.append((no, w))
            paras.append(_p_image(no, w.width, w.height))
            paras.append(_p_runs([_run("", size=6)]))
        else:
            runs = [r for r in b["runs"] if r.get("text") or r.get("br")]
            if runs:
                paras.append(_p_runs(runs, align=b.get("align"), indent=b.get("indent")))

    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _write_docx(p, paras, images)
    return str(p)


def _docx_walk(root, blocks, art):
    """正文树 → 块序列 [{kind: text/image, runs, align, indent, image}]。"""
    ctx = {}

    def new_run(text, **kw):
        r = {"text": text}
        r.update(ctx)
        r.update(kw)
        return r

    def get_runs():
        return ctx.setdefault("_runs", [])

    def emit_runs_as_para(align=None, indent=None):
        runs = [r for r in get_runs() if r.get("text", "").strip() or r.get("br")]
        ctx["_runs"] = []
        if runs and any(r.get("text", "").strip() for r in runs):
            blocks.append({"kind": "text", "runs": runs,
                           "align": align, "indent": indent})

    def walk(node, bold=False, italic=False, color=None, size=None):
        for child in node.children:
            if isinstance(child, NavigableString):
                text = re.sub(r"\s+", " ", str(child))
                if text and text != " ":
                    get_runs().append(new_run(text, bold=bold, italic=italic, color=color, size=size))
                elif text == " " and get_runs():
                    get_runs().append(new_run(" ", bold=bold, italic=italic, color=color, size=size))
                continue
            if not isinstance(child, Tag):
                continue
            name = child.name
            if name in ("script", "style"):
                continue
            if name == "img":
                emit_runs_as_para()
                blocks.append({"kind": "image", "image": art.image_by_url(child.get("src") or "")})
                continue
            if name == "br":
                get_runs().append({"br": True})
                continue
            if name in ("b", "strong"):
                walk(child, bold=True, italic=italic, color=color, size=size)
            elif name in ("i", "em"):
                walk(child, bold=bold, italic=True, color=color, size=size)
            elif name == "a":
                walk(child, bold=bold, italic=italic, color="0563C1", size=size)
            elif name in ("span", "font", "u", "s", "sub", "sup", "code"):
                c2, s2 = color, size
                style = (child.get("style") or "").replace(" ", "").lower()
                m = re.search(r"color[:#](?:rgb\((\d+),(\d+),(\d+)\)|#?([0-9a-f]{3,6}))", style)
                if m:
                    if m.group(1):
                        c2 = "{:02X}{:02X}{:02X}".format(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                    else:
                        hx = m.group(4)
                        if len(hx) == 3:
                            hx = "".join(ch * 2 for ch in hx)
                        c2 = hx.upper()
                m = re.search(r"font-size:(\d+)px", style)
                if m:
                    pt = round(int(m.group(1)) * 0.75)
                    s2 = max(16, min(pt * 2, 36))
                walk(child, bold=bold, italic=italic, color=c2, size=s2)
            elif name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                emit_runs_as_para()
                walk(child, bold=True, size=int(name[1]) <= 2 and 30 or 26)
                emit_runs_as_para(align=None)
            elif name == "blockquote":
                emit_runs_as_para()
                walk(child, bold=bold, italic=italic, color="666666", size=size)
                emit_runs_as_para(indent=480)
            elif name == "li":
                emit_runs_as_para()
                walk(child)
                emit_runs_as_para(indent=360)
            else:  # p / section / div / table 等：块级
                emit_runs_as_para()
                align = None
                style = (child.get("style") or "").replace(" ", "").lower()
                if "text-align:center" in style:
                    align = "center"
                elif "text-align:right" in style:
                    align = "right"
                walk(child)
                emit_runs_as_para(align=align)

    walk(root)
    return blocks


def _p_runs(runs, align=None, indent=None) -> str:
    pr = ""
    if align:
        pr += f'<w:jc w:val="{align}"/>'
    if indent:
        pr += f'<w:ind w:leftChars="{indent // 100}"/>'
    pr = f"<w:pPr>{pr}</w:pPr>" if pr else ""
    body = ""
    for r in runs:
        if r.get("br"):
            body += "<w:r><w:br/></w:r>"
            continue
        rpr = ""
        if r.get("bold"):
            rpr += "<w:b/>"
        if r.get("italic"):
            rpr += "<w:i/>"
        if r.get("color"):
            rpr += f'<w:color w:val="{r["color"]}"/>'
        if r.get("underline"):
            rpr += "<w:u w:val=\"single\"/>"
        if r.get("size"):
            rpr += f'<w:sz w:val="{r["size"]}"/><w:szCs w:val="{r["size"]}"/>'
        rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
        body += f'<w:r>{rpr}<w:t xml:space="preserve">{escape_text(r.get("text", ""))}</w:t></w:r>'
    return f"<w:p>{pr}{body}</w:p>"


def _p_title(text: str) -> str:
    return ('<w:p><w:pPr><w:jc w:val="center"/></w:pPr>'
            f'<w:r><w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr>'
            f'<w:t xml:space="preserve">{escape_text(text)}</w:t></w:r></w:p>')


def _p_image(no: int, width_px: int, height_px: int) -> str:
    cx = min(int(width_px or 576) * 9525, _MAX_IMG_EMU)
    scale = cx / (int(width_px or 576) * 9525) if width_px else 1.0
    cy = int((height_px or 324) * 9525 * scale)
    return (
        '<w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
        '<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/>'
        f'<wp:docPr id="{no}" name="Picture {no}"/>'
        '<wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr>'
        '<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
        '<pic:pic><pic:nvPicPr>'
        f'<pic:cNvPr id="{no}" name="image{no}"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="rId{no}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        '</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>')


def _write_docx(path: Path, paras: List[str], images: List[tuple]):
    """images: [(rel_no, WechatImage), ...]"""
    ext_map = {"jpeg": "jpeg", "png": "png", "gif": "gif"}
    defaults = {v: MIME[v] for v in set(ext_map.get(w.fmt, "jpeg") for _, w in images)}
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>']
    for ext, mime in defaults.items():
        ct.append(f'<Default Extension="{ext}" ContentType="{mime}"/>')
    ct.append('</Types>')

    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
            '<Relationship Id="rIdDoc" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>',
            '</Relationships>']

    doc_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    for no, w in images:
        ext = ext_map.get(w.fmt, "jpeg")
        doc_rels.append(
            f'<Relationship Id="rId{no}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" '
            f'Target="media/image{no}.{ext}"/>')
    doc_rels.append('</Relationships>')

    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
                '<w:body>' + "".join(paras) +
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr>'
                '</w:body></w:document>')

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", "".join(rels))
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", "".join(doc_rels))
        for no, w in images:
            ext = ext_map.get(w.fmt, "jpeg")
            z.writestr(f"word/media/image{no}.{ext}", w.data)
