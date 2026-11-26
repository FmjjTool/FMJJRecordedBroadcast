"""本地封面套版渲染（Pillow，无 Qt）。

使用封面制作的 v1 比例模板；自动化只消费模板，不修改原模板或底图。
颜色、字体回退、换行使用 image_render，共享导出规范的用户自定义封面例外。
"""

from __future__ import annotations

import math
import os
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont, ImageOps

from toolbox import image_render

RESOLUTIONS = {"1280x720", "1920x1080", "2560x1440", "3840x2160"}
FIELD_KEYS = ("title", "date", "remark")
_DEFAULTS = {"title": (88 / 1080, .38), "date": (44 / 1080, .55),
             "remark": (32 / 1080, .68)}


def _number(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("模板数值无效，请重新保存模板") from exc
    if not math.isfinite(number) or not low <= number <= high:
        raise ValueError("模板数值超出范围，请重新保存模板")
    return number


def validate_template(template: dict) -> dict:
    """复制并校验模板；不补写任何配置。旧 v1 模板采用原默认导出参数。"""
    if not isinstance(template, dict) or not isinstance(template.get("fields"), dict):
        raise ValueError("模板缺少文字样式，请先保存封面模板")
    if template.get("version", 1) != 1:
        raise ValueError("模板版本不支持，请重新保存模板")
    resolution = str(template.get("export_resolution") or "1920x1080").lower()
    if resolution not in RESOLUTIONS:
        raise ValueError("封面分辨率不支持")
    fit_mode = str(template.get("fit_mode") or "crop")
    if fit_mode not in {"crop", "contain"}:
        raise ValueError("底图适配方式不支持")
    result = {"version": 1, "bg": str(template.get("bg") or ""),
              "export_resolution": resolution, "fit_mode": fit_mode,
              "y_shift": _number(template.get("y_shift", 0), 0, -.3, .3),
              "jpg_quality": int(_number(template.get("jpg_quality", 95), 95, 60, 100)),
              "remark": str(template.get("remark") or "")[:4000], "fields": {}}
    for key in FIELD_KEYS:
        data = template["fields"].get(key, {})
        if not isinstance(data, dict):
            raise ValueError("模板文字样式无效")
        size, y = _DEFAULTS[key]
        color = str(data.get("color") or image_render.tokens("dark")["text"])
        # QColor 的 #AARRGGBB 转换为 Pillow 的 #RRGGBBAA。
        if len(color) == 9 and color.startswith("#"):
            color = "#" + color[3:] + color[1:3]
        try:
            ImageColor.getrgb(color)
        except (ValueError, TypeError) as exc:
            raise ValueError("模板文字颜色无效") from exc
        alignment = str(data.get("alignment", "center"))
        if alignment not in {"left", "center", "right"}:
            raise ValueError("模板文字对齐方式无效")
        result["fields"][key] = {
            "font_family": str(data.get("font_family") or "Microsoft YaHei")[:200],
            "font_size_ratio": _number(data.get("font_size_ratio", size), size, .001, 1),
            "x_ratio": _number(data.get("x_ratio", .5), .5, 0, 1),
            "y_ratio": _number(data.get("y_ratio", y), y, 0, 1),
            "alignment": alignment, "color": color,
            "bold": bool(data.get("bold")), "italic": bool(data.get("italic")),
        }
    return result


@lru_cache(maxsize=1)
def _system_fonts() -> tuple[tuple[str, str], ...]:
    """只读系统字体注册表，避免把用户字体路径当作文件输入。"""
    if os.name != "nt":
        return ()
    import winreg
    found = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts") as key:
                for index in range(winreg.QueryInfoKey(key)[1]):
                    name, value, _ = winreg.EnumValue(key, index)
                    if isinstance(value, str):
                        path = Path(value)
                        if not path.is_absolute():
                            path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / path
                        found.append((name.casefold(), str(path)))
        except OSError:
            continue
    return tuple(found)


@lru_cache(maxsize=128)
def _font(family: str, size: int, bold: bool, italic: bool):
    wanted = family.casefold().strip()
    candidates = []
    for name, path in _system_fonts():
        base = name.removesuffix(" (truetype)").removesuffix(" (opentype)")
        names = base.split(" & ")
        for item in names:
            clean = item.replace(" bold", "").replace(" italic", "").strip()
            if clean == wanted:
                score = int(("bold" in item) == bold) + int(("italic" in item) == italic)
                candidates.append((-score, path))
                break
    for _, path in sorted(candidates):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return image_render.font(size, bold)


def render_cover_image(template: dict, title: str, date: str = "", remark: str = "") -> Image.Image:
    """套用当前/已存模板生成 RGB 图片；底图缺失时失败，避免批量产生空白底图。"""
    data = validate_template(template)
    width, height = map(int, data["export_resolution"].split("x"))
    bg = Path(data["bg"])
    if not data["bg"] or not bg.is_file():
        raise ValueError("模板底图不存在，请在封面制作中选择底图后重试")
    try:
        with Image.open(bg) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("底图超过 4000 万像素，请缩小后重试")
            background = ImageOps.exif_transpose(source).convert("RGBA")
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("底图无法解码，请重新选择图片") from exc
    fill = image_render.tokens("dark")["bg"]
    canvas = Image.new("RGB", (width, height), fill)
    fitted = (ImageOps.fit(background, (width, height), Image.Resampling.LANCZOS)
              if data["fit_mode"] == "crop" else
              ImageOps.contain(background, (width, height), Image.Resampling.LANCZOS))
    canvas.paste(fitted, ((width - fitted.width) // 2, (height - fitted.height) // 2), fitted)
    draw = ImageDraw.Draw(canvas)
    for key, text in zip(FIELD_KEYS, (title, date, remark or data["remark"])):
        text = str(text or "").strip()
        if not text:
            continue
        if len(text) > 4000:
            raise ValueError("封面文字过长，请缩短后重试")
        state = data["fields"][key]
        size = max(12, round(state["font_size_ratio"] * height))
        while True:
            font = _font(state["font_family"], size, state["bold"], state["italic"])
            lines = image_render.wrap_rich_text(text, font, None, width * .7)
            metrics = [font.getbbox(line or " ") for line in lines]
            line_height = max(max(b[3] - b[1] for b in metrics), size) * 1.2
            block_height = len(lines) * line_height
            if block_height <= height * .88 or size <= 12:
                break
            size = max(12, int(size * .9))
        block_width = max(draw.textlength(line, font=font) for line in lines)
        if block_height > height - 8 or block_width > width - 8:
            raise ValueError("封面文字超出画布，请缩短后重试")
        alignment = state["alignment"]
        anchor = state["x_ratio"] * width
        x = anchor if alignment == "left" else anchor - (block_width if alignment == "right" else block_width / 2)
        x = max(4, min(x, width - block_width - 4))
        y = max(4, min((state["y_ratio"] + data["y_shift"]) * height - block_height / 2,
                       height - block_height - 4))
        for index, line in enumerate(lines):
            line_width = draw.textlength(line, font=font)
            dx = 0 if alignment == "left" else block_width - line_width if alignment == "right" else (block_width - line_width) / 2
            # bbox 原点补偿，保持文字块按模板中心定位。
            draw.text((x + dx, y + index * line_height - metrics[index][1]), line,
                      font=font, fill=state["color"])
    return canvas
