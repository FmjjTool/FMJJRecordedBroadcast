"""资产预览拼贴图（L3）：Pillow + 共享 image_render，不依赖 Qt。"""

from __future__ import annotations

import os
import re
import subprocess
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageOps

from toolbox import image_render as render
from toolbox.utils import platform_cn

_FFMPEG_CACHE = [""]


def _video_cover(path: str, cell: int) -> Image.Image | None:
    """用 ffmpeg 抽视频第 1 秒帧；失败返回 None。"""
    args_base = ["-y", "-ss", "1", "-i", path, "-frames:v", "1",
                 "-vf", f"scale={cell}:{cell}:force_original_aspect_ratio=increase,"
                        f"crop={cell}:{cell}",
                 "-f", "image2", "-c:v", "mjpeg", "-"]
    candidates = []
    if _FFMPEG_CACHE[0]:
        candidates.append(_FFMPEG_CACHE[0])
    else:
        from toolbox.utils import find_ffmpeg_exe
        found = find_ffmpeg_exe()
        if found:
            candidates.append(found)
        candidates.append("D:/Programs/ffmpeg/bin/ffmpeg.exe")
    for exe in candidates:
        if not exe or not os.path.exists(exe):
            continue
        try:
            result = subprocess.run([exe] + args_base, capture_output=True, timeout=20)
            if result.returncode == 0 and result.stdout:
                _FFMPEG_CACHE[0] = exe
                return Image.open(BytesIO(result.stdout)).convert("RGB")
        except Exception:
            continue
    return None


def _placeholder(cell: int, palette: dict) -> Image.Image:
    image = Image.new("RGB", (cell, cell), palette["surface_alt"])
    draw = ImageDraw.Draw(image)
    font = render.font(15)
    text = "视频"
    width = render.rich_text_width(text, font)
    draw.text(((cell - width) / 2, cell / 2 - 10), text, font=font,
              fill=palette["muted"])
    return image


def _media_count_label(images: int, videos: int) -> str:
    if images and videos:
        return f"媒体文件（{images} 图 · {videos} 视频）"
    if videos:
        return f"媒体文件（{videos} 视频）"
    return f"媒体文件（{images} 图）"


def _draw_card_title(draw: ImageDraw.ImageDraw, x: int, y: int, text: str,
                     palette: dict, font_size: int = 22) -> int:
    title_font = render.font(font_size, bold=True)
    emoji_font = render.font(font_size, emoji=True)
    render.draw_rich_text(draw, x, y + render._ascent(title_font), text,
                          title_font, palette["text"], emoji_font)
    return y + int(font_size * 1.45)


def _draw_stats_card(draw: ImageDraw.ImageDraw, item: dict, top: int,
                     palette: dict, theme: str) -> int:
    x = render.MARGIN
    width = render.WIDTH - 2 * render.MARGIN
    height = 190
    render.draw_card(draw, (x, top, x + width, top + height), theme)
    inner_x = x + render.CARD_PADDING
    inner_w = width - 2 * render.CARD_PADDING
    y = top + render.CARD_PADDING

    platform = platform_cn(item.get("platform") or "")
    plat_en = {"douyin": "Douyin", "weibo": "Weibo", "bilibili": "bilibili"}.get(
        item.get("platform"), (item.get("platform") or "").title())
    y = _draw_card_title(draw, inner_x, y, platform or "未知平台", palette)

    tag_font = render.font(13)
    tag_w = render.rich_text_width(plat_en, tag_font) + 24
    draw.rounded_rectangle(
        (x + width - render.CARD_PADDING - tag_w, top + 22,
         x + width - render.CARD_PADDING, top + 46),
        radius=10, fill=palette["accent"])
    draw.text((x + width - render.CARD_PADDING - tag_w + 12, top + 27),
              plat_en, font=tag_font, fill="#ffffff")

    draw.line([(inner_x, y + 4), (inner_x + inner_w, y + 4)],
              fill=palette["border"], width=1)
    y += 20

    stats = [
        ("播放", render.format_count(item.get("play_count", 0))),
        ("点赞", render.format_count(item.get("like_count", 0))),
        ("评论", render.format_count(item.get("comment_count", 0))),
    ]
    label_font = render.font(13)
    value_font = render.font(28, bold=True)
    column_w = inner_w // len(stats)
    for index, (label, value) in enumerate(stats):
        column_x = inner_x + index * column_w
        draw.text((column_x, y), label, font=label_font, fill=palette["muted"])
        draw.text((column_x, y + 24), value, font=value_font, fill=palette["text"])

    time_font = render.font(13)
    time_text = (f"发布：{item.get('publish_time') or '—'}"
                 f"  ·  采集：{item.get('fetched_at') or '—'}")
    draw.text((inner_x, top + height - 32), time_text,
              font=time_font, fill=palette["muted"])
    return top + height


def _media_card(item: dict, palette: dict) -> Image.Image | None:
    media = [m for m in (item.get("media_files") or [])
             if m.get("type") in ("image", "video")]
    if not media:
        return None
    inner_w = render.WIDTH - 2 * render.MARGIN - 2 * render.CARD_PADDING
    columns = 3
    gap = 12
    cell = (inner_w - gap * (columns - 1)) // columns
    rows = (len(media) + columns - 1) // columns
    title_h = 44
    height = render.CARD_PADDING * 2 + title_h + rows * cell + (rows - 1) * gap
    card = Image.new("RGB", (render.WIDTH - 2 * render.MARGIN, height), palette["card"])
    draw = ImageDraw.Draw(card)
    draw.rounded_rectangle((0, 0, card.width - 1, card.height - 1),
                           radius=render.CARD_RADIUS, fill=palette["card"],
                           outline=palette["border"], width=1)
    inner_x = render.CARD_PADDING
    images = sum(1 for m in media if m.get("type") == "image")
    videos = sum(1 for m in media if m.get("type") == "video")
    _draw_card_title(draw, inner_x, render.CARD_PADDING - 6,
                     _media_count_label(images, videos), palette)
    total_w = columns * cell + (columns - 1) * gap
    start_x = (card.width - total_w) // 2
    y0 = render.CARD_PADDING + title_h
    for index, item_media in enumerate(media):
        try:
            if item_media.get("type") == "video":
                image = _video_cover(item_media.get("path", ""), cell) or _placeholder(cell, palette)
            else:
                image = Image.open(item_media.get("path", "")).convert("RGB")
            image = ImageOps.fit(image, (cell, cell), Image.LANCZOS,
                                 centering=(0.5, 0.5))
            mask = Image.new("L", image.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, image.size[0], image.size[1]), radius=12, fill=255)
            row, column = divmod(index, columns)
            x = start_x + column * (cell + gap)
            y = y0 + row * (cell + gap)
            card.paste(image, (x, y), mask)
        except Exception:
            row, column = divmod(index, columns)
            x = start_x + column * (cell + gap)
            y = y0 + row * (cell + gap)
            card.paste(_placeholder(cell, palette), (x, y))
    return card


def _draw_desc_card(draw: ImageDraw.ImageDraw, description: str, top: int,
                    palette: dict, theme: str) -> int:
    x = render.MARGIN
    width = render.WIDTH - 2 * render.MARGIN
    inner_w = width - 2 * render.CARD_PADDING
    body_font = render.font(16)
    body_emoji = render.font(16, emoji=True)
    lines = render.wrap_rich_text(description, body_font, body_emoji, inner_w)
    line_h = int(16 * 1.5)
    height = render.CARD_PADDING * 2 + 34 + max(1, len(lines)) * line_h
    render.draw_card(draw, (x, top, x + width, top + height), theme)
    inner_x = x + render.CARD_PADDING
    y = _draw_card_title(draw, inner_x, top + render.CARD_PADDING - 6, "正文", palette)
    y += 6
    for line in lines:
        render.draw_rich_text(draw, inner_x, y + render._ascent(body_font),
                              line, body_font, palette["text"], body_emoji)
        y += line_h
    return top + height


def render_asset_preview(item: dict, out_path=None, theme: str = "light") -> str:
    """渲染单条资产预览图；out_path 为空时写入 download/导出的图片。"""
    palette = render.tokens(theme)
    title = re.sub(r"[\r\n]+", " ", item.get("title") or "(无标题)")[:60]
    author = item.get("author") or "-"
    subtitle = (f"{platform_cn(item.get('platform') or '')} · @{author}"
                f" · 发布 {item.get('publish_time') or '—'}"
                f" · 采集 {item.get('fetched_at') or '—'}")
    stats_h = 190
    media_image = _media_card(item, palette)
    desc = (item.get("description") or "").strip()[:300]
    desc_lines = []
    if desc:
        body_font = render.font(16)
        body_emoji = render.font(16, emoji=True)
        desc_lines = render.wrap_rich_text(
            desc, body_font, body_emoji,
            render.WIDTH - 2 * render.MARGIN - 2 * render.CARD_PADDING)
    height = render.HEADER_H + stats_h + render.FOOTER_H + render.BOTTOM_GAP
    if media_image is not None:
        height += render.SECTION_GAP + media_image.height
    if desc_lines:
        height += render.SECTION_GAP + (
            render.CARD_PADDING * 2 + 34 + len(desc_lines) * int(16 * 1.5))
    image = Image.new("RGB", (render.WIDTH, height), palette["bg"])
    draw = ImageDraw.Draw(image)
    render.draw_header(draw, title, subtitle, theme)
    y = render.HEADER_H
    y = _draw_stats_card(draw, item, y, palette, theme) + render.SECTION_GAP
    if media_image is not None:
        image.paste(media_image, (render.MARGIN, y))
        y += media_image.height + render.SECTION_GAP
    if desc_lines:
        y = _draw_desc_card(draw, desc, y, palette, theme)
    render.draw_footer(draw, height - render.FOOTER_H - render.BOTTOM_GAP,
                       "资产数据 · 本地媒体", theme)
    if out_path is None:
        from toolbox.config_manager import get_export_dir
        out_dir = get_export_dir() / "导出的图片"
        object_name = f"{item.get('date_part', '')}_{author}_{title}"
        out_path = render.build_output_path(out_dir, "asset", object_name)
    return render.save_png(image, out_path)
