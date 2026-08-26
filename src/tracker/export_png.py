"""账号追踪：把「当前两次采集的变化」导出为 PNG 图片。

支持两种导出维度（不按平台）：
- 单账号：export_diff_png(account, history) → 一张账号对比图
- 按组：export_group_png(group_id, group_name) → 组内所有账号堆叠成一张长图

用 PIL 绘制深色主题对比图，涨绿跌红，保存到统一导出目录（账号追踪数据）。
"""

import re
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# 配色（与 GUI 深色主题一致）
BG = "#1a1d27"          # 页面背景
CARD = "#23262f"        # 数据行斑马纹背景
HEADER_BG = "#2a2e3a"   # 表头背景
TEXT = "#e1e4ed"        # 正文
MUTED = "#9aa0b4"       # 次要文字
ACCENT = "#64b5f6"      # 强调蓝
UP = "#4caf50"          # 涨（绿）
DOWN = "#ff5252"        # 跌（红）
FLAT = "#8a8f9e"        # 无变化

# 数字指标（可计算涨跌）：(显示名, 字段名...)
NUM_METRICS = [
    ("粉丝数", "fans"),
    ("关注数", "following"),
    ("获赞数", "likes"),
    ("内容数", "video_count", "weibo_count"),
]

PLATFORM_NAMES = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博",
                  "kuaishou": "快手", "xiaohongshu": "小红书"}

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
]
_FONT_BOLD_CANDIDATES = [
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsunb.ttf",
]

# 布局参数
W = 860
MARGIN = 40
COL_WIDTHS = [220, 180, 180, 200]  # 指标 / 上次 / 当前 / 变化
TABLE_X = MARGIN
TABLE_RIGHT = W - MARGIN
HEADER_H = 46
ROW_H = 52

_font_cache = {}


def _font(size, bold=False):
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    candidates = _FONT_BOLD_CANDIDATES if bold else _FONT_CANDIDATES
    font = None
    for path in candidates:
        try:
            font = ImageFont.truetype(path, size)
            break
        except Exception:
            continue
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    _font_cache[key] = font
    return font


def _num(rec, fields):
    """从记录里取第一个可解析为整数的数字字段，失败返回 None。"""
    for f in fields:
        v = rec.get(f)
        if v is None or v == "":
            continue
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None
    return None


def _fmt(n):
    return f"{n:,}"


def _diff_text(prev, curr, fields):
    """返回 (变化文本, 颜色)。两边都缺返回 (None, None)。"""
    p = _num(prev, fields)
    c = _num(curr, fields)
    if p is None or c is None:
        return "—", FLAT
    delta = c - p
    if delta > 0:
        return f"↑ +{_fmt(delta)}", UP
    if delta < 0:
        return f"↓ {_fmt(delta)}", DOWN
    return "持平", FLAT


def _draw_right(d, text, x_right, y, font, fill):
    """右对齐绘制文本（用 textlength 手动定位）。"""
    try:
        w = d.textlength(text, font=font)
    except Exception:
        w = len(text) * 8
    d.text((x_right - w, y), text, font=font, fill=fill)


def _draw_table(d, top_y, prev, curr):
    """绘制表头 + 4 数据行，返回表格底部 y 坐标。"""
    f_header = _font(17, bold=True)
    f_cell = _font(18)
    f_cell_bold = _font(18, bold=True)

    d.rectangle([TABLE_X, top_y, TABLE_RIGHT, top_y + HEADER_H], fill=HEADER_BG)
    headers = ["指标", "上次", "当前", "变化"]
    x = TABLE_X
    for i, (h, cw) in enumerate(zip(headers, COL_WIDTHS)):
        if i == 0:
            d.text((x + 16, top_y + (HEADER_H - 17) / 2), h, font=f_header, fill=MUTED)
        else:
            _draw_right(d, h, x + cw - 16, top_y + (HEADER_H - 17) / 2, f_header, MUTED)
        x += cw

    y = top_y + HEADER_H
    for ri, (label, *fields) in enumerate(NUM_METRICS):
        row_y = y + ri * ROW_H
        if ri % 2 == 0:
            d.rectangle([TABLE_X, row_y, TABLE_RIGHT, row_y + ROW_H], fill=CARD)
        cy = row_y + (ROW_H - 18) / 2

        p_val = _num(prev, fields)
        c_val = _num(curr, fields)
        delta_text, delta_color = _diff_text(prev, curr, fields)

        x = TABLE_X
        d.text((x + 16, cy), label, font=f_cell, fill=TEXT)
        x += COL_WIDTHS[0]
        p_text = _fmt(p_val) if p_val is not None else "—"
        _draw_right(d, p_text, x + COL_WIDTHS[1] - 16, cy, f_cell, TEXT)
        x += COL_WIDTHS[1]
        c_text = _fmt(c_val) if c_val is not None else "—"
        _draw_right(d, c_text, x + COL_WIDTHS[2] - 16, cy, f_cell_bold, TEXT)
        x += COL_WIDTHS[2]
        _draw_right(d, delta_text, x + COL_WIDTHS[3] - 16, cy, f_cell_bold, delta_color)

    return y + len(NUM_METRICS) * ROW_H


def _draw_times(d, top_y, prev, curr, show_export=False):
    """绘制底部时间信息，返回底部 y。"""
    f_footer = _font(15)
    t_prev = (prev.get("_time") or "—")[:16]
    t_curr = (curr.get("_time") or "—")[:16]
    d.text((MARGIN, top_y), f"上次采集：{t_prev}", font=f_footer, fill=MUTED)
    d.text((MARGIN, top_y + 26), f"当前采集：{t_curr}", font=f_footer, fill=MUTED)
    y = top_y + 52
    if show_export:
        t_export = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        d.text((MARGIN, y), f"导出时间：{t_export}", font=f_footer, fill=MUTED)
        y += 26
    if curr.get("name") and prev.get("name") and curr["name"] != prev["name"]:
        d.text((MARGIN, y), f"昵称变更：{prev['name']} → {curr['name']}", font=f_footer, fill=ACCENT)
        y += 26
    return y


def _plat_of(account, rec):
    platform = (account.get("platform") or rec.get("platform") or "").strip()
    return PLATFORM_NAMES.get(platform, platform or "未知")


def _safe_name(name):
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def _resolve_output_dir(output_dir):
    if output_dir is None:
        from toolbox.config_manager import get_export_subdir
        output_dir = get_export_subdir("tracker")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def export_diff_png(account, history, output_dir=None):
    """单账号导出：最新两次采集的变化对比 PNG，返回文件路径。

    account: {name, url, platform, group_id}
    history: load_history 返回的记录列表（最新在前）
    """
    if not history or len(history) < 2:
        raise ValueError("该账号至少需要两次采集数据才能导出变化对比")

    curr, prev = history[0], history[1]
    name = account.get("name") or curr.get("name") or "账号"
    plat_name = _plat_of(account, curr)

    output_dir = _resolve_output_dir(output_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = output_dir / f"{_safe_name(name)}_变化对比_{ts}.png"

    title_h = 104
    footer_h = 150
    table_bottom = title_h + HEADER_H + ROW_H * len(NUM_METRICS)
    H = table_bottom + footer_h

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = _font(34, bold=True)
    f_sub = _font(18)
    d.text((MARGIN, 26), name, font=f_title, fill=TEXT)
    d.text((MARGIN, 70), f"{plat_name} · 最近两次采集变化对比", font=f_sub, fill=ACCENT)

    _draw_table(d, title_h, prev, curr)
    _draw_times(d, table_bottom + 24, prev, curr, show_export=True)

    img.save(str(out_path))
    return str(out_path)


def export_group_png(group_id, group_name, output_dir=None):
    """按组导出：把组内所有账号（各自最新两次变化）堆叠成一张长图。

    自动读取组内账号及其历史数据，跳过采集不足两次的账号。
    若组内没有可导出的账号，抛 ValueError。
    返回文件路径。
    """
    from .accounts import get_accounts_by_group, load_history

    accounts = get_accounts_by_group(group_id)
    entries = []
    for acc in accounts:
        hist = load_history(acc.get("name", ""))
        if len(hist) >= 2:
            entries.append((acc, hist))
    if not entries:
        raise ValueError("该组没有可导出的账号（需每个账号至少两次采集）")

    group_label = f"{group_name}（组{group_id}）" if group_name else f"组{group_id}"
    output_dir = _resolve_output_dir(output_dir)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe = _safe_name(group_label)
    out_path = output_dir / f"{safe}_变化对比_{ts}.png"

    # 布局：组标题区 + 每个账号一个卡片
    group_h = 116
    account_title_h = 66
    account_footer_h = 110
    gap = 26
    block_h = account_title_h + HEADER_H + ROW_H * len(NUM_METRICS) + account_footer_h
    H = group_h + (block_h + gap) * len(entries) - gap + 20

    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # 组标题
    f_group = _font(34, bold=True)
    f_group_sub = _font(18)
    d.text((MARGIN, 26), group_label, font=f_group, fill=TEXT)
    d.text((MARGIN, 72),
           f"组号 {group_id} · {len(entries)} 个账号 · 最近两次采集变化对比",
           font=f_group_sub, fill=ACCENT)

    f_name = _font(22, bold=True)
    f_plat = _font(15)

    y = group_h
    for acc, hist in entries:
        curr, prev = hist[0], hist[1]
        name = acc.get("name") or curr.get("name") or "账号"
        plat_name = _plat_of(acc, curr)
        # 账号标题
        d.text((MARGIN, y + 18), name, font=f_name, fill=TEXT)
        d.text((MARGIN, y + 48), f"{plat_name}", font=f_plat, fill=ACCENT)
        # 表格
        table_bottom = _draw_table(d, y + account_title_h, prev, curr)
        # 底部时间
        _draw_times(d, table_bottom + 22, prev, curr, show_export=False)
        y = table_bottom + 22 + account_footer_h + gap

    img.save(str(out_path))
    return str(out_path)
