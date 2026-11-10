"""腾讯文档监控 · Excel 报表（每个文档一份）。

工作表：
- 每日记录：日期 / 采集时间 / 浏览量 / 浏览增量 / 成员数 / 成员增量 / 已查看人数
- 采集明细：采集时间 / 浏览量 / 成员数 / 状态 / 说明
- 浏览人  ：昵称 / 最近浏览时间 / 来源 / UID / 首次记录（仅手动采集的已查看明细）

增量列用绿色粗体标出正增长（与项目里其它报表的视觉口径一致）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import storage

HEADER_BG = "2F5597"
CST = timezone(timedelta(hours=8))
UID_SOURCE = {1: "QQ", 2: "微信"}


def _ts_text(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), CST).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return ""


def _style_header(ws, columns: list[tuple[str, int]]) -> None:
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    thin = Side(style="thin", color="D0D7E5")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")
    fill = PatternFill("solid", fgColor=HEADER_BG)
    font = Font(color="FFFFFF", bold=True, size=11)
    for idx, (title, width) in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=idx, value=title)
        cell.fill = fill
        cell.font = font
        cell.alignment = center
        cell.border = border
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.row_dimensions[1].height = 22
    ws.freeze_panes = "A2"


def build_report(db_path: Path, xlsx_path: Path, doc_key: str, doc_name: str = "") -> Path:
    """按数据库内容重建报表（保证与库里一致，可反复调用）。"""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side

    xlsx_path = Path(xlsx_path)
    daily = storage.all_daily(db_path, doc_key)
    snaps = storage.all_snapshots(db_path, doc_key)

    thin = Side(style="thin", color="D0D7E5")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center")

    wb = Workbook()
    if doc_name:
        wb.properties.title = doc_name

    ws = wb.active
    ws.title = "每日记录"
    _style_header(ws, [("日期", 14), ("采集时间", 20), ("浏览量", 12), ("浏览增量", 12),
                       ("成员数", 12), ("成员增量", 12), ("已查看人数", 12)])
    for r, row in enumerate(daily, start=2):
        values = [row["date"], row["ts"], row["views"], row["views_delta"],
                  row["members"], row["members_delta"], row["readers"]]
        for c, value in enumerate(values, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.alignment = center
            cell.border = border
            cell.number_format = "0" if c >= 3 and value is not None else "General"
            if c in (4, 6) and value is not None and value > 0:
                cell.font = Font(color="1B7F3B", bold=True)
    ws.auto_filter.ref = f"A1:G{max(len(daily) + 1, 2)}"

    ws2 = wb.create_sheet("采集明细")
    _style_header(ws2, [("采集时间", 20), ("浏览量", 12), ("成员数", 12),
                        ("状态", 10), ("说明", 46)])
    for r, row in enumerate(snaps, start=2):
        values = [row["ts"], row["views"], row["members"],
                  "成功" if row["ok"] else "失败", row["message"] or ""]
        for c, value in enumerate(values, start=1):
            cell = ws2.cell(row=r, column=c, value=value)
            cell.border = border
            cell.alignment = center if c <= 4 else Alignment(horizontal="left", vertical="center")

    browsers = storage.all_browsers(db_path, doc_key)
    if browsers:
        ws3 = wb.create_sheet("浏览人")
        _style_header(ws3, [("昵称", 24), ("最近浏览时间", 21), ("来源", 8),
                            ("UID", 24), ("首次记录", 21)])
        for r, row in enumerate(browsers, start=2):
            values = [row["nick"] or "", _ts_text(row["last_ts"]),
                      UID_SOURCE.get(row["uid_source"], ""), row["uid"],
                      row["first_seen"] or ""]
            for c, value in enumerate(values, start=1):
                cell = ws3.cell(row=r, column=c, value=value)
                cell.border = border
                cell.alignment = center
        ws3.auto_filter.ref = f"A1:E{max(len(browsers) + 1, 2)}"

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
    return xlsx_path
