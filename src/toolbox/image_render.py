"""导出图片共享渲染（Pillow + 标准库）。

规范见 `src/skills/image-output-spec/SKILL.md`：
- 1080 px 基准宽度、固定页头/页脚、卡片/表格 token；
- 只依赖 Pillow 与标准库，不导入 PySide6；
- 页面/业务模块只传数据，不新写第二套色板、字号与页脚。
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
MARGIN = 32
CARD_PADDING = 24
SECTION_GAP = 24
CARD_RADIUS = 16
TABLE_HEADER_H = 48
TABLE_ROW_H = 56
HEADER_H = 168
FOOTER_H = 56
BOTTOM_GAP = 16
MAX_HEIGHT = 20000
DPI = 144
APP_NAME = "封茗囧菌数据汇总库"

THEMES: dict[str, dict[str, str]] = {
    "light": {
        "bg": "#f3f5f9",
        "card": "#ffffff",
        "surface_alt": "#f0f3f8",
        "text": "#263449",
        "muted": "#5f7089",
        "border": "#dce3ed",
        "accent": "#356bd6",
        "success": "#18754e",
        "danger": "#b93842",
        "warning": "#99600c",
        "flat": "#5f7089",
    },
    "dark": {
        "bg": "#151c29",
        "card": "#1d2737",
        "surface_alt": "#243044",
        "text": "#dce5f3",
        "muted": "#9caec5",
        "border": "#303e52",
        "accent": "#3f76c9",
        "success": "#7cd6af",
        "danger": "#ff969f",
        "warning": "#edc478",
        "flat": "#9caec5",
    },
}

_FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
)
_FONT_BOLD_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsunb.ttf",
)
_EMOJI_FONT_CANDIDATES = (
    "C:/Windows/Fonts/seguiemj.ttf",
    "C:/Windows/Fonts/segui.ttf",
    "C:/Windows/Fonts/seguisym.ttf",
)

_EMOJI_RE = re.compile(
    r"[\U0001F000-\U0001FAFF"
    r"\U00002190-\U000021FF"
    r"\U00002300-\U000023FF"
    r"\U000025A0-\U000025FF"
    r"\U00002600-\U000027BF"
    r"\U00002B00-\U00002BFF"
    r"\U0000FE0E\U0000FE0F"
    r"\U0000200D\U000020E3]"
)

_font_cache: dict[tuple[int, bool, bool], ImageFont.ImageFont] = {}


def tokens(theme: str = "light") -> dict[str, str]:
    """取导出图颜色 token；未知主题回退 brand-light。"""
    return dict(THEMES.get(theme, THEMES["light"]))


def font(size: int, bold: bool = False, emoji: bool = False) -> ImageFont.ImageFont:
    """按 (字号, 字重, emoji) 缓存字体；加载失败回退系统默认字体。"""
    key = (max(1, int(size)), bool(bold), bool(emoji))
    cached = _font_cache.get(key)
    if cached is not None:
        return cached
    candidates = (_EMOJI_FONT_CANDIDATES if emoji else
                  _FONT_BOLD_CANDIDATES if bold else _FONT_CANDIDATES)
    loaded = None
    for path in candidates:
        try:
            loaded = ImageFont.truetype(path, key[0])
            break
        except Exception:
            continue
    if loaded is None:
        try:
            loaded = ImageFont.load_default(size=key[0])
        except Exception:
            loaded = ImageFont.load_default()
    _font_cache[key] = loaded
    return loaded


def _font_length(font_obj: ImageFont.ImageFont, text: str) -> float:
    try:
        return float(font_obj.getlength(text))
    except Exception:
        try:
            return float(font_obj.getbbox(text)[2])
        except Exception:
            return len(text) * 8.0


def _is_emoji(ch: str) -> bool:
    return bool(_EMOJI_RE.match(ch))


def rich_text_width(text: str, main: ImageFont.ImageFont,
                    emoji_font: ImageFont.ImageFont | None = None) -> float:
    """含 emoji 字符串的排版宽度。"""
    if not text:
        return 0.0
    total = 0.0
    segment = ""
    current_emoji = None
    for ch in text:
        use_emoji = _is_emoji(ch) and emoji_font is not None
        if current_emoji is None:
            current_emoji = use_emoji
        if use_emoji == current_emoji:
            segment += ch
        else:
            total += _font_length(emoji_font if current_emoji else main, segment)
            segment, current_emoji = ch, use_emoji
    if segment:
        total += _font_length(emoji_font if current_emoji else main, segment)
    return total


def draw_rich_text(draw: ImageDraw.ImageDraw, x: float, baseline: float, text: str,
                   main: ImageFont.ImageFont, fill: str,
                   emoji_font: ImageFont.ImageFont | None = None) -> float:
    """按基线绘制混排文本，返回结束 x。"""
    if not text:
        return x
    kwargs = {}
    if isinstance(main, ImageFont.FreeTypeFont):
        kwargs["anchor"] = "ls"
    cx = x
    segment = ""
    current_emoji = None
    for ch in text:
        use_emoji = _is_emoji(ch) and emoji_font is not None
        if current_emoji is None:
            current_emoji = use_emoji
        if use_emoji == current_emoji:
            segment += ch
        else:
            current_font = emoji_font if current_emoji else main
            draw.text((cx, baseline), segment, font=current_font, fill=fill, **kwargs)
            cx += _font_length(current_font, segment)
            segment, current_emoji = ch, use_emoji
    if segment:
        current_font = emoji_font if current_emoji else main
        draw.text((cx, baseline), segment, font=current_font, fill=fill, **kwargs)
        cx += _font_length(current_font, segment)
    return cx


def _line_height(font_obj: ImageFont.ImageFont) -> int:
    try:
        ascent, descent = font_obj.getmetrics()
        return int(ascent + descent)
    except Exception:
        return 16


def _ascent(font_obj: ImageFont.ImageFont) -> int:
    try:
        return int(font_obj.getmetrics()[0])
    except Exception:
        return 0


def wrap_rich_text(text: str, main: ImageFont.ImageFont,
                   emoji_font: ImageFont.ImageFont | None, max_width: float) -> list[str]:
    """按排版宽度换行，保留显式 \\n；每行不裁半个字。"""
    if not text:
        return []
    lines: list[str] = []
    current, current_w = "", 0.0
    for ch in str(text).replace("\r", ""):
        if ch == "\n":
            lines.append(current)
            current, current_w = "", 0.0
            continue
        use_emoji = _is_emoji(ch) and emoji_font is not None
        width = _font_length(emoji_font if use_emoji else main, ch)
        if current and current_w + width > max_width:
            lines.append(current)
            current, current_w = ch, width
        else:
            current += ch
            current_w += width
    if current:
        lines.append(current)
    return lines


def ellipsize_rich(text: str, main: ImageFont.ImageFont,
                   emoji_font: ImageFont.ImageFont | None, max_width: float) -> str:
    """超宽时截断并加省略号；不改变换行文本。"""
    if not text or rich_text_width(text, main, emoji_font) <= max_width:
        return text
    suffix = "…"
    suffix_w = rich_text_width(suffix, main, emoji_font)
    kept = ""
    width = 0.0
    for ch in str(text):
        use_emoji = _is_emoji(ch) and emoji_font is not None
        ch_w = _font_length(emoji_font if use_emoji else main, ch)
        if width + ch_w + suffix_w > max_width:
            break
        kept += ch
        width += ch_w
    return kept + suffix


def format_count(value) -> str:
    """数字格式：千分位；万/亿用一位小数。缺失返回 —。"""
    if value is None or value == "":
        return "—"
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return str(value)
    absolute = abs(number)
    if absolute >= 100_000_000:
        return f"{number / 100_000_000:.1f}亿"
    if absolute >= 10_000:
        return f"{number / 10_000:.1f}万"
    return f"{number:,}"


def format_delta(delta) -> tuple[str, str]:
    """返回 (变化文本, token 名)：上涨/下跌/持平均有文字与符号。"""
    if delta is None:
        return "—", "flat"
    try:
        number = int(delta)
    except (TypeError, ValueError):
        return "—", "flat"
    if number > 0:
        return f"↑ +{format_count(number)}", "success"
    if number < 0:
        return f"↓ -{format_count(abs(number))}", "danger"
    return "—", "flat"


def safe_filename_part(text, limit: int = 60) -> str:
    """文件名清洗：去掉非法字符与控制字符，限制长度。"""
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "_", str(text or ""))
    cleaned = re.sub(r"\s+", "_", cleaned).strip(" ._")
    return (cleaned[:limit].strip(" ._") or "未命名")


def build_output_path(output_dir, module: str, object_name: str,
                      suffix: str = ".png", when: datetime | None = None) -> Path:
    """`{module}_{object}_{YYYYMMDD_HHMMSS}.ext`，重名追加 `_2`。"""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
    stem = f"{safe_filename_part(module, 24)}_{safe_filename_part(object_name, 72)}_{stamp}"
    stem = stem[:116]
    path = out_dir / f"{stem}{suffix}"
    index = 2
    while path.exists():
        path = out_dir / f"{stem}_{index}{suffix}"
        index += 1
    return path


def save_png(image: Image.Image, path) -> str:
    """sRGB、不透明、144 DPI；先写临时文件再原子替换。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        prefix=".image_render_", suffix=".png", dir=path.parent, delete=False)
    tmp = Path(handle.name)
    handle.close()
    try:
        image.convert("RGB").save(tmp, "PNG", dpi=(DPI, DPI), optimize=True)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
    return str(path)


def footer_text(source: str, page: int | None = None,
                total: int | None = None, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y-%m-%d %H:%M")
    text = f"由 {APP_NAME} 生成 · {stamp} · 数据来源：{source or '本地采集记录'}"
    if page is not None and total is not None:
        text += f" · 第 {page} / {total} 页"
    return text


def draw_header(draw: ImageDraw.ImageDraw, title: str, subtitle: str = "",
                theme: str = "light", brand: str = APP_NAME) -> int:
    """固定高度页头；返回内容起始 y（HEADER_H）。"""
    palette = tokens(theme)
    brand_font = font(13)
    draw.text((MARGIN, 24), brand, font=brand_font, fill=palette["muted"])
    title_font = font(32, bold=True)
    title_emoji = font(32, emoji=True)
    title_lines = wrap_rich_text(
        ellipsize_rich(title or "未命名", title_font, title_emoji, WIDTH - 2 * MARGIN),
        title_font, title_emoji, WIDTH - 2 * MARGIN)[:2] or ["未命名"]
    y = 50
    for line in title_lines:
        draw_rich_text(draw, MARGIN, y + _ascent(title_font), line,
                       title_font, palette["text"], title_emoji)
        y += int(32 * 1.45)
    if subtitle:
        sub_font = font(13)
        sub_emoji = font(13, emoji=True)
        sub = ellipsize_rich(subtitle, sub_font, sub_emoji, WIDTH - 2 * MARGIN)
        draw_rich_text(draw, MARGIN, 148, sub, sub_font, palette["muted"], sub_emoji)
    return HEADER_H


def draw_footer(draw: ImageDraw.ImageDraw, y: int, source: str,
                theme: str = "light", page: int | None = None,
                total: int | None = None) -> int:
    palette = tokens(theme)
    draw.line([(MARGIN, y), (WIDTH - MARGIN, y)], fill=palette["border"], width=1)
    text = footer_text(source, page=page, total=total)
    footer_font = font(13)
    width = _font_length(footer_font, text)
    draw.text(((WIDTH - width) / 2, y + 20), text, font=footer_font, fill=palette["muted"])
    return y + FOOTER_H


def draw_card(draw: ImageDraw.ImageDraw, box: Sequence[float], theme: str = "light",
              radius: int = CARD_RADIUS) -> None:
    palette = tokens(theme)
    draw.rounded_rectangle(tuple(box), radius=radius, fill=palette["card"],
                           outline=palette["border"], width=1)


def _cell(value, palette: Mapping[str, str]):
    if isinstance(value, tuple):
        text = str(value[0]) if value else ""
        color = str(value[1]) if len(value) > 1 and value[1] else palette["text"]
        bold = bool(value[2]) if len(value) > 2 else False
    else:
        text, color, bold = str(value), palette["text"], False
    return text, color, bold


def draw_metric_table(draw: ImageDraw.ImageDraw, x: int, y: int, width: int,
                      headers: Sequence[str], rows: Sequence[Sequence],
                      theme: str = "light",
                      weights: Sequence[float] = (0.4, 0.2, 0.2, 0.2)) -> int:
    """表头 48 px、行高 56 px；首列左对齐，其余右对齐。"""
    palette = tokens(theme)
    weights = list(weights or ())
    if len(weights) != len(headers):
        weights = [1 / len(headers)] * len(headers)
    total_weight = sum(weights) or 1
    edges = [x]
    for weight in weights:
        edges.append(edges[-1] + int(width * weight / total_weight))
    edges[-1] = x + width

    header_font = font(15, bold=True)
    header_emoji = font(15, emoji=True)
    draw.rectangle([x, y, x + width, y + TABLE_HEADER_H], fill=palette["surface_alt"])
    for index, header in enumerate(headers):
        text = str(header)
        text_w = rich_text_width(text, header_font, header_emoji)
        tx = (edges[index] + 16 if index == 0
              else edges[index + 1] - 16 - text_w)
        draw_rich_text(draw, tx, y + (TABLE_HEADER_H - _line_height(header_font)) // 2
                       + _ascent(header_font), text, header_font,
                       palette["muted"], header_emoji)

    cell_font = font(16)
    cell_bold = font(16, bold=True)
    cell_emoji = font(16, emoji=True)
    row_y = y + TABLE_HEADER_H
    for row_index, row in enumerate(rows):
        if row_index % 2 == 0:
            draw.rectangle([x, row_y, x + width, row_y + TABLE_ROW_H],
                           fill=palette["surface_alt"])
        line_y = row_y + (TABLE_ROW_H - _line_height(cell_font)) // 2
        for column, value in enumerate(row[:len(headers)]):
            text, color, bold = _cell(value, palette)
            text_font = cell_bold if bold else cell_font
            text_w = rich_text_width(text, text_font, cell_emoji)
            tx = (edges[column] + 16 if column == 0
                  else edges[column + 1] - 16 - text_w)
            draw_rich_text(draw, tx, line_y + _ascent(text_font), text,
                           text_font, color, cell_emoji)
        row_y += TABLE_ROW_H
    return row_y


def _section_height(section: Mapping) -> int:
    title = str(section.get("title") or "")
    subtitle = str(section.get("subtitle") or "")
    rows = list(section.get("rows") or [])
    notes = list(section.get("notes") or [])
    height = CARD_PADDING * 2
    if title:
        height += 32 + 4
    if subtitle:
        height += 20 + 4
    height += TABLE_HEADER_H + TABLE_ROW_H * max(1, len(rows))
    if not rows:
        height += 0
    height += len(notes) * 22
    return height


def _split_section(section: Mapping, max_rows: int) -> list[dict]:
    rows = list(section.get("rows") or [])
    if len(rows) <= max_rows:
        return [dict(section)]
    chunks = []
    title = str(section.get("title") or "")
    for start in range(0, len(rows), max_rows):
        part = dict(section)
        part["rows"] = rows[start:start + max_rows]
        part["title"] = f"{title}（续）" if start else title
        chunks.append(part)
    return chunks


def _paginate(sections: Sequence[Mapping]) -> list[list[dict]]:
    usable = MAX_HEIGHT - HEADER_H - FOOTER_H - BOTTOM_GAP - MARGIN
    overhead = _section_height({"rows": []})
    max_rows = max(1, (usable - overhead - TABLE_HEADER_H) // TABLE_ROW_H)
    chunks = []
    for section in sections:
        chunks.extend(_split_section(section, max_rows))
    pages: list[list[dict]] = []
    current: list[dict] = []
    current_h = 0
    for chunk in chunks:
        chunk_h = _section_height(chunk)
        extra = chunk_h + (SECTION_GAP if current else 0)
        if current and current_h + extra > usable:
            pages.append(current)
            current, current_h = [chunk], chunk_h
        else:
            current.append(chunk)
            current_h += extra
    if current:
        pages.append(current)
    return pages or [[]]


def _draw_section(draw: ImageDraw.ImageDraw, section: Mapping, top: int,
                  theme: str) -> int:
    palette = tokens(theme)
    card_w = WIDTH - 2 * MARGIN
    card_h = _section_height(section)
    draw_card(draw, (MARGIN, top, MARGIN + card_w, top + card_h), theme)
    x = MARGIN + CARD_PADDING
    inner_w = card_w - 2 * CARD_PADDING
    y = top + CARD_PADDING
    title_font = font(22, bold=True)
    title_emoji = font(22, emoji=True)
    if section.get("title"):
        title = ellipsize_rich(str(section["title"]), title_font, title_emoji, inner_w)
        draw_rich_text(draw, x, y + _ascent(title_font), title,
                       title_font, palette["text"], title_emoji)
        y += 36
    if section.get("subtitle"):
        sub_font = font(13)
        sub_emoji = font(13, emoji=True)
        subtitle = ellipsize_rich(str(section["subtitle"]), sub_font, sub_emoji, inner_w)
        draw_rich_text(draw, x, y + _ascent(sub_font), subtitle,
                       sub_font, palette["muted"], sub_emoji)
        y += 24
    rows = list(section.get("rows") or [])
    if rows:
        y = draw_metric_table(draw, x, y, inner_w,
                              section.get("headers") or ("指标", "上次", "当前", "变化"),
                              rows, theme, section.get("weights") or (0.4, 0.2, 0.2, 0.2))
    else:
        draw_rich_text(draw, x, y + 24, "暂无数据", font(16), palette["muted"],
                       font(16, emoji=True))
        y += TABLE_HEADER_H
    notes = list(section.get("notes") or [])
    if notes:
        note_font = font(13)
        note_emoji = font(13, emoji=True)
        y += 4
        for note in notes:
            text = ellipsize_rich(str(note), note_font, note_emoji, inner_w)
            draw_rich_text(draw, x, y + _ascent(note_font), text,
                           note_font, palette["muted"], note_emoji)
            y += 22
    return top + card_h


def render_report(title: str, subtitle: str, sections: Sequence[Mapping],
                  output_path, theme: str = "light", source: str = "本地采集记录") -> str:
    """通用报表图：页头 → 若干卡片（标题/表格/备注）→ 页脚；超长自动分页。"""
    pages = _paginate(list(sections))
    total = len(pages)
    first_path = Path(output_path)
    for page_index, page_sections in enumerate(pages, 1):
        content_h = sum(_section_height(s) for s in page_sections)
        content_h += SECTION_GAP * max(0, len(page_sections) - 1)
        height = HEADER_H + content_h + FOOTER_H + BOTTOM_GAP
        image = Image.new("RGB", (WIDTH, height), tokens(theme)["bg"])
        draw = ImageDraw.Draw(image)
        draw_header(draw, title, subtitle, theme)
        y = HEADER_H
        for section in page_sections:
            y = _draw_section(draw, section, y, theme)
            y += SECTION_GAP
        draw_footer(draw, height - FOOTER_H - BOTTOM_GAP, source, theme,
                    page=page_index if total > 1 else None,
                    total=total if total > 1 else None)
        path = first_path
        if page_index > 1:
            path = first_path.with_name(
                f"{first_path.stem}_{page_index}{first_path.suffix}")
        save_png(image, path)
    return str(first_path)


# ==================== 账号追踪仪表盘 ====================
# 参考样式：深色底 + 双列平台卡片 + 平台色左边条 + 合计卡。
# 平台色只用于平台身份识别，不参与涨跌语义；涨跌仍用 success/danger。

PLATFORM_ACCENTS: dict[str, str] = {
    "bilibili": "#fb7299",
    "douyin": "#fe2c55",
    "weibo": "#ff8200",
    "kuaishou": "#ff5000",
    "xiaohongshu": "#ff2442",
    "netease": "#e23a3a",
    "qq": "#31c27c",
    "kugou": "#2ea7ff",
    "kuwo": "#ffb020",
    "fivesing": "#ff7a45",
}
ACCENT_FALLBACK = "#3f76c9"

DASH_HEADER_H = 150
DASH_CARD_H = 196
DASH_WIDE_H = 224
DASH_CARD_GAP = 20
DASH_TOTAL_H = 132


def platform_accent(platform_key: str) -> str:
    """平台英文标识 → 固定身份色；未知平台用中性强调色。"""
    return PLATFORM_ACCENTS.get(str(platform_key or "").lower(), ACCENT_FALLBACK)


def _center_text(draw: ImageDraw.ImageDraw, y: int, text: str,
                 font_obj: ImageFont.ImageFont, fill: str,
                 emoji_font: ImageFont.ImageFont | None = None) -> None:
    width = rich_text_width(text, font_obj, emoji_font)
    draw_rich_text(draw, (WIDTH - width) / 2, y + _ascent(font_obj), text,
                   font_obj, fill, emoji_font)


def _draw_dash_card(draw: ImageDraw.ImageDraw, box: Sequence[float],
                    card: Mapping, theme: str, wide: bool = False) -> None:
    palette = tokens(theme)
    x1, y1, x2, y2 = box
    draw_card(draw, box, theme)
    accent = str(card.get("accent") or ACCENT_FALLBACK)
    draw.rounded_rectangle((x1, y1, x1 + 6, y2), radius=3, fill=accent)
    inner_w = (x2 - x1) - 2 * CARD_PADDING
    title = f"{card.get('platform') or '未知'} · {card.get('account') or '账号'}"
    title_font = font(18, bold=True)
    title_emoji = font(18, emoji=True)
    title = ellipsize_rich(title, title_font, title_emoji, inner_w)
    draw_rich_text(draw, x1 + CARD_PADDING, y1 + 24 + _ascent(title_font),
                   title, title_font, accent, title_emoji)
    metrics = list(card.get("metrics") or [])
    if wide and metrics:
        # 单账号：指标横向分栏，避免半宽卡片旁边大块留白。
        count = len(metrics)
        gap = 24
        col_w = (inner_w - gap * (count - 1)) / count
        for index, metric in enumerate(metrics):
            label, value, delta_text, direction = metric
            cx = x1 + CARD_PADDING + index * (col_w + gap)
            label_font = font(13)
            value_font = font(30, bold=True)
            delta_font = font(14, bold=True)
            draw_rich_text(draw, cx, y1 + 78, str(label), label_font,
                           palette["muted"])
            draw_rich_text(draw, cx, y1 + 104 + _ascent(value_font),
                           str(value), value_font, palette["text"])
            if delta_text:
                draw_rich_text(draw, cx, y1 + 154 + _ascent(delta_font),
                               str(delta_text), delta_font,
                               palette.get(str(direction or "flat"),
                                           palette["flat"]))
    else:
        y = y1 + 58
        for index, metric in enumerate(metrics):
            label, value, delta_text, direction = metric
            label_font = font(13)
            value_font = font(22 if index == 0 else 17, bold=True)
            delta_font = font(13, bold=True)
            draw_rich_text(draw, x1 + CARD_PADDING, y + _ascent(label_font),
                           str(label), label_font, palette["muted"])
            draw_rich_text(draw, x1 + CARD_PADDING + 96, y + _ascent(value_font),
                           str(value), value_font, palette["text"])
            if delta_text:
                delta_w = _font_length(delta_font, str(delta_text))
                draw_rich_text(draw, x2 - CARD_PADDING - delta_w,
                               y + _ascent(value_font), str(delta_text),
                               delta_font,
                               palette.get(str(direction or "flat"),
                                           palette["flat"]))
            y += 36
    note = str(card.get("note") or "")
    if note:
        note_font = font(11)
        note = ellipsize_rich(note, note_font, None, inner_w)
        draw_rich_text(draw, x1 + CARD_PADDING, y2 - 22,
                       note, note_font, palette["muted"])


def render_account_dashboard(title: str, subtitle: str, cards: Sequence[Mapping],
                             output_path, theme: str = "dark",
                             total: Mapping | None = None,
                             source: str = "本地采集记录") -> str:
    """账号追踪仪表盘：深色双列平台卡片 + 合计卡 + 固定页脚。

    cards: [{
        "platform": "B站", "account": "账号名", "accent": "#fb7299",
        "metrics": [("粉丝量", "1.2万", "↑ +345", "success"), ...],
        "note": "上次 2026-11-16 → 本次 2026-11-17",
    }]
    total: {"label": "粉丝量合计（8 平台）", "value": "1857.76万",
            "delta": "↓ -1,093", "direction": "danger"}
    """
    palette = tokens(theme)
    cards = list(cards or [])
    if not cards:
        raise ValueError("没有可导出的账号数据")
    single = len(cards) == 1
    card_w = WIDTH - 2 * MARGIN if single else \
        (WIDTH - 2 * MARGIN - DASH_CARD_GAP) // 2
    card_h = DASH_WIDE_H if single else DASH_CARD_H
    columns = 1 if single else 2
    rows = (len(cards) + columns - 1) // columns
    content_h = rows * card_h + max(0, rows - 1) * DASH_CARD_GAP
    if total:
        content_h += DASH_CARD_GAP + DASH_TOTAL_H
    height = DASH_HEADER_H + content_h + FOOTER_H + BOTTOM_GAP
    image = Image.new("RGB", (WIDTH, height), palette["bg"])
    draw = ImageDraw.Draw(image)

    brand_font = font(12)
    _center_text(draw, 22, APP_NAME, brand_font, palette["muted"])
    title_font = font(32, bold=True)
    title_emoji = font(32, emoji=True)
    title_text = ellipsize_rich(title or "账号数据", title_font, title_emoji,
                                WIDTH - 2 * MARGIN)
    _center_text(draw, 52, title_text, title_font, palette["text"], title_emoji)
    if subtitle:
        sub_font = font(13)
        sub_emoji = font(13, emoji=True)
        sub_text = ellipsize_rich(subtitle, sub_font, sub_emoji, WIDTH - 2 * MARGIN)
        _center_text(draw, 108, sub_text, sub_font, palette["muted"], sub_emoji)

    y = DASH_HEADER_H
    for index, card in enumerate(cards):
        column = index % columns
        row = index // columns
        x = MARGIN + column * (card_w + DASH_CARD_GAP)
        top = y + row * (card_h + DASH_CARD_GAP)
        _draw_dash_card(draw, (x, top, x + card_w, top + card_h), card, theme,
                        wide=single)
    y += content_h

    if total:
        top = DASH_HEADER_H + rows * card_h + (rows - 1) * DASH_CARD_GAP \
            + DASH_CARD_GAP
        draw_card(draw, (MARGIN, top, WIDTH - MARGIN, top + DASH_TOTAL_H), theme)
        label_font = font(15, bold=True)
        label = str(total.get("label") or "粉丝量合计")
        label = ellipsize_rich(label, label_font, font(15, emoji=True),
                               WIDTH - 2 * MARGIN - 2 * CARD_PADDING)
        _center_text(draw, top + 18, label, label_font, palette["warning"])
        value_font = font(40, bold=True)
        value = str(total.get("value") or "—")
        delta = str(total.get("delta") or "")
        delta_font = font(16, bold=True)
        value_w = _font_length(value_font, value)
        delta_w = _font_length(delta_font, delta) if delta else 0
        gap = 14 if delta else 0
        start = (WIDTH - value_w - gap - delta_w) / 2
        baseline = top + 52 + _ascent(value_font)
        draw_rich_text(draw, start, baseline, value, value_font, palette["text"])
        if delta:
            draw_rich_text(draw, start + value_w + gap, baseline, delta,
                           delta_font,
                           palette.get(str(total.get("direction") or "flat"),
                                       palette["flat"]))

    draw_footer(draw, height - FOOTER_H - BOTTOM_GAP, source, theme)
    save_png(image, output_path)
    return str(output_path)


def shift_color(delta: int, theme: str = "light") -> str:
    """涨跌颜色 token，供业务模块组装单元格时使用。"""
    palette = tokens(theme)
    if delta > 0:
        return palette["success"]
    if delta < 0:
        return palette["danger"]
    return palette["flat"]
