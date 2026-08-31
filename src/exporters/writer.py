"""
导出模块 - 支持 JSON / XLSX / MD 格式
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Optional

def _sanitize_filename(name: str, max_len: int = 60) -> str:
    """安全文件名（统一实现见 toolbox.utils.sanitize_filename）"""
    from toolbox.utils import sanitize_filename
    return sanitize_filename(name, max_len, "untitled")


def _fmt_duration(sec: int) -> str:
    if not sec: return ""
    m, s = divmod(sec, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"


def _build_stem(info) -> str:
    date_str = ""
    raw_date = info.publish_time or info.fetched_at or ""
    if raw_date: date_str = re.sub(r'[^0-9]', '', raw_date[:10])
    if not date_str or len(date_str) < 8: date_str = datetime.now().strftime("%Y%m%d")
    title_part = _sanitize_filename(info.title) if info.title else "untitled"
    platform_part = info.platform or "unknown"
    return f"{date_str}_{platform_part}_{title_part}"


def _prepare_data(info_list: list) -> list[dict]:
    return [info.to_dict() for info in info_list]


# ========== JSON ==========

def export_json(info_list: list, output_path: Optional[str] = None, pretty: bool = True) -> str:
    data = _prepare_data(info_list)
    if not output_path:
        output_path = f"video_info_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2 if pretty else None)
    return os.path.abspath(output_path)


# ========== XLSX ==========

def export_xlsx(info_list: list, output_path: Optional[str] = None) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook(); ws = wb.active; ws.title = "视频信息"

    headers = ["序号","平台","视频ID","标题","正文","作者","作者ID",
               "播放/阅览","点赞","评论","分享","收藏",
               "时长","封面URL","链接","发布时间","标签","原始URL","获取时间"]

    # 样式定义
    hf = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
    hfill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    ha = Alignment(horizontal="center", vertical="center", wrap_text=True)
    tb = Border(left=Side(style="thin", color="D9D9D9"), right=Side(style="thin", color="D9D9D9"),
                top=Side(style="thin", color="D9D9D9"), bottom=Side(style="thin", color="D9D9D9"))
    even_fill = PatternFill(start_color="F2F7FC", end_color="F2F7FC", fill_type="solid")
    num_fmt = '#,##0'
    center_al = Alignment(horizontal="center", vertical="center")
    left_al = Alignment(horizontal="left", vertical="center", wrap_text=True)
    right_al = Alignment(horizontal="right", vertical="center")

    # 数字列（右对齐+千分位）和链接列
    num_cols = {8, 9, 10, 11, 12}  # 播放/点赞/评论/分享/收藏
    center_cols = {1, 2, 13}  # 序号/平台/时长
    link_cols = {14, 15, 18}  # 封面URL/链接/原始URL

    # 写表头
    for ci, h in enumerate(headers, 1):
        c = ws.cell(row=1, column=ci, value=h)
        c.font = hf; c.fill = hfill; c.alignment = ha; c.border = tb
    ws.row_dimensions[1].height = 28

    # 写数据
    for ri, info in enumerate(info_list, 2):
        vals = [ri-1, info.platform, info.video_id, info.title or "",
                (info.description or "")[:500],
                info.author or "", info.author_id or "",
                info.play_count, info.like_count,
                info.comment_count, info.share_count, info.favorite_count,
                _fmt_duration(info.duration), info.cover_url, info.video_url,
                info.publish_time or "", ", ".join(info.tags) if info.tags else "",
                info.raw_url or "", info.fetched_at or ""]
        row_fill = even_fill if ri % 2 == 0 else PatternFill()
        for ci, v in enumerate(vals, 1):
            c = ws.cell(row=ri, column=ci, value=v)
            c.border = tb; c.fill = row_fill
            if ci in num_cols:
                c.alignment = right_al
                if isinstance(v, (int, float)) and v > 0:
                    c.number_format = num_fmt
            elif ci in center_cols:
                c.alignment = center_al
            elif ci in link_cols and v and str(v).startswith("http"):
                c.alignment = left_al
                c.hyperlink = str(v)
                c.font = Font(color="0563C1", underline="single", name="微软雅黑")
            else:
                c.alignment = left_al
        ws.row_dimensions[ri].height = 22

    # 列宽
    widths = [5, 8, 16, 36, 30, 14, 14, 12, 10, 10, 10, 10, 7, 30, 30, 16, 18, 30, 16]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    if not output_path:
        output_path = f"video_info_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    wb.save(output_path)
    return os.path.abspath(output_path)


# ========== Markdown ==========

def export_md(info_list: list, output_path: Optional[str] = None) -> str:
    if not output_path:
        output_path = f"video_info_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    lines = ["# 📊 视频信息报告",
             f"\n> 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
             f"> 总计: {len(info_list)} 条\n"]
    groups: dict[str, list] = {}
    for info in info_list: groups.setdefault(info.platform, []).append(info)
    lines.append("## 📑 目录\n")
    for plat, items in groups.items():
        emoji = {"bilibili":"📺","douyin":"🎵","weibo":"📢"}.get(plat,"📄")
        lines.append(f"- {emoji} **{plat.upper()}** ({len(items)} 条)")
    lines.append("")
    for plat, items in groups.items():
        emoji = {"bilibili":"📺","douyin":"🎵","weibo":"📢"}.get(plat,"📄")
        lines.append("---\n"); lines.append(f"## {emoji} {plat.upper()} ({len(items)} 条)\n")
        for i, info in enumerate(items, 1):
            view_label = "阅览量" if info.platform == "weibo" else "播放量"
            lines.append(f"### {i}. {info.title or '无标题'}\n")
            lines.append("| 属性 | 值 |"); lines.append("|------|-----|")
            rows = [("平台", info.platform), ("视频ID", info.video_id),
                    ("作者", f"@{info.author} (ID: {info.author_id})")]
            if info.description: rows.append(("正文", info.description[:500]))
            rows.extend([(view_label, _fmt(info.play_count)),
                         ("点赞数", _fmt(info.like_count)),
                         ("评论数", _fmt(info.comment_count)),
                         ("分享数", _fmt(info.share_count)),
                         ("收藏数", _fmt(info.favorite_count))])
            if info.duration: rows.append(("时长", _fmt_duration(info.duration)))
            rows.append(("发布时间", info.publish_time))
            if info.tags: rows.append(("标签", ", ".join(info.tags)))
            if info.category: rows.append(("分类/地区", info.category))
            if info.music: rows.append(("音乐", info.music))
            rows.extend([("封面URL", info.cover_url), ("链接", info.video_url), ("原始URL", info.raw_url)])
            for k, v in rows: lines.append(f"| {k} | {v} |")
            lines.append("")
    content = "\n".join(lines)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f: f.write(content)
    return os.path.abspath(output_path)


def _fmt(n: int) -> str:
    if n >= 10000: return f"{n/10000:.1f}万 ({n:,})"
    return f"{n:,}"


# ========== 批量导出 ==========



# ========== 按视频独立导出 ==========

EXPORT_DIR = "video_data"

def export_per_video(info_list: list, output_dir: Optional[str] = None, formats: Optional[list[str]] = None, skip_existing: bool = True) -> tuple[dict[str, list[str]], list[str]]:
    """按视频独立导出。skip_existing=True 时已存在文件跳过不覆盖。

    并行导出（ThreadPoolExecutor，上限 8 线程）：单条导出是 xlsx 渲染/磁盘 IO 密集型，
    条目多时串行极慢，并行后总耗时 ≈ 最慢单条。
    返回 (results, skipped)：results 为新导出的路径分组，skipped 为已存在被跳过的路径。
    文件被移走/删除/改名后（路径不存在），会重新保存。
    """
    if formats is None: formats = ["json","xlsx","md"]
    base_dir = output_dir or EXPORT_DIR
    os.makedirs(base_dir, exist_ok=True)
    from toolbox.utils import platform_cn, DATA_DIR_SUFFIX

    def _export_one(info):
        paths: list[str] = []
        skipped_local: list[str] = []
        stem = _build_stem(info)
        # 按平台分文件夹: video_data/抖音_文件/, video_data/B站_文件/ ...（媒体在对应 {平台}_媒体/）
        plat_dir = os.path.join(base_dir, platform_cn(info.platform) + DATA_DIR_SUFFIX)
        os.makedirs(plat_dir, exist_ok=True)
        for fmt in formats:
            path = os.path.join(plat_dir, f"{stem}.{fmt}")
            # 已存在则跳过（用户移走/删除/改名后路径不存在，会重新保存）
            if skip_existing and os.path.exists(path):
                skipped_local.append(path)
                continue
            if fmt == "json":
                export_json([info], path)
                raw_path = os.path.join(plat_dir, f"{stem}_raw.json")
                _export_raw_json(info, raw_path)
                paths.append(raw_path)
            elif fmt == "xlsx": export_xlsx([info], path)
            elif fmt == "md": export_md([info], path)
            paths.append(path)
        return info.platform, paths, skipped_local

    if len(info_list) > 4:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(8, len(info_list))) as ex:
            outcomes = list(ex.map(_export_one, info_list))
    else:
        outcomes = [_export_one(info) for info in info_list]

    results: dict[str, list[str]] = {}
    skipped: list[str] = []
    for plat, paths, skipped_local in outcomes:
        results.setdefault(plat, []).extend(paths)
        skipped.extend(skipped_local)
    return results, skipped


def _export_raw_json(info, path: str):
    raw = info.raw_data if isinstance(info.raw_data, dict) else {}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)


def export_per_video_flat(info_list: list, output_dir: Optional[str] = None, formats: Optional[list[str]] = None, skip_existing: bool = True) -> tuple[list[str], list[str]]:
    """按视频独立导出并展平。返回 (paths, skipped_paths)。"""
    grouped, skipped = export_per_video(info_list, output_dir, formats, skip_existing)
    flat: list[str] = []
    for paths in grouped.values(): flat.extend(paths)
    return flat, skipped
