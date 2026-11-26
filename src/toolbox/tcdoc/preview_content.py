"""正文图文展示与导出 DTO 格式化；不执行网络请求或修改凭据。

图片只引用 body_images 验证后的本地派生文件；远程网页 HTML 不进入预览。
正文、标题及图片说明始终转义，桌面与导出共用同一图片清单。
"""

from __future__ import annotations

import os
from html import escape
from pathlib import Path
from urllib.parse import quote


def image_counts(pages: list[dict]) -> dict:
    images = [image for page in pages for image in page.get("images", [])]
    return {"total": len(images),
            "downloaded": sum(image.get("status") == "downloaded" for image in images),
            "failed": sum(image.get("status") != "downloaded" for image in images),
            "uncaptured": sum(not page.get("images_captured", "images" in page)
                              for page in pages)}


def image_summary(pages: list[dict]) -> str:
    counts = image_counts(pages)
    if counts["uncaptured"]:
        return "旧存档尚未采图，点「重新抓取」补齐"
    if not counts["total"]:
        return "未发现正文图片"
    text = f"图片 {counts['downloaded']}/{counts['total']} 张"
    if counts["failed"]:
        text += f" · {counts['failed']} 张下载失败，可重新抓取"
    return text


def local_image_path(image: dict) -> Path:
    from .body_images import safe_image_path
    if image.get("status") != "downloaded" or not image.get("path"):
        raise ValueError("图片尚未下载")
    return safe_image_path(str(image["path"]))


def html_for_pages(pages: list[dict], max_width: int = 780) -> str:
    """仅产生受控本地图片与转义文字，图片按原顺序附于所属篇正文之后。"""
    max_width = max(80, int(max_width))
    out = []
    if image_counts(pages)["uncaptured"]:
        out.append("<p>旧存档尚未采集图片，点「重新抓取」补齐。</p>")
    for index, page in enumerate(pages):
        if index:
            out.append("<hr>")
        if len(pages) > 1:
            out.append(f"<p><b>{escape(str(page.get('title') or f'第 {index + 1} 篇'))}</b></p>")
        text = str(page.get("error") or page.get("text") or "")
        if text:
            out.append("<p>" + escape(text).replace("\n", "<br>") + "</p>")
        elif not page.get("images"):
            out.append("<p>（空白页）</p>")
        for image_index, image in enumerate(page.get("images") or [], 1):
            name = escape(str(image.get("name") or f"图片 {image_index}"))
            try:
                uri = local_image_path(image).as_uri()
            except (OSError, ValueError):
                reason = escape(str(image.get("error") or "本地图片不可用，请重新抓取"))
                out.append(f"<p>{name}：{reason}</p>")
                continue
            width = max(1, int(image.get("width") or max_width))
            height = max(1, int(image.get("height") or width))
            shown_width = min(width, max_width)
            shown_height = max(1, round(height * shown_width / width))
            src = escape(uri, quote=True)
            out.append(f'<p><a href="{src}"><img src="{src}" '
                       f'width="{shown_width}" height="{shown_height}" alt="{name}"></a></p>')
            note = " · 点击查看原图"
            if image.get("error"):
                note += " · " + escape(str(image["error"]))
            out.append(f"<p>{name}{note}</p>")
    return "<html><body>" + "".join(out) + "</body></html>"


def markdown_for_pages(pages: list[dict], relative_to: Path | None = None) -> str:
    """Markdown 带图片引用；自动快照用相对路径，独立导出可用本地文件 URL。"""
    chunks = []
    for index, page in enumerate(pages, 1):
        text = str(page.get("error") or page.get("text") or "")
        if not text and not page.get("images"):
            text = "（空白页）"
        if len(pages) > 1:
            title = str(page.get("title") or f"第 {index} 篇")
            text = f"━━━━━━━━ {index}. {title} ━━━━━━━━\n{text}"
        for image_index, image in enumerate(page.get("images") or [], 1):
            label = str(image.get("name") or f"图片 {image_index}").replace("\n", " ")
            label = label.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
            try:
                target = local_image_path(image)
                uri = (quote(Path(os.path.relpath(target, relative_to)).as_posix(), safe="/.")
                       if relative_to is not None else target.as_uri())
                text += f"\n\n![{label}]({uri})"
            except (OSError, ValueError):
                text += f"\n\n[{label}：图片未下载，可重新抓取]"
        chunks.append(text)
    return "\n\n".join(chunks)
