"""腾讯文档本地表格的只读预览用例，桌面与 Web 共用展示排序。

不修改原始行序、快照或云端数据；存储仍由 tables 模块负责。
"""

from __future__ import annotations

from toolbox.table_sort_keys import sort_key, sort_records

from . import config, tables

CHANGES_DESCRIPTION = (
    "本地同步检测记录：同步时对比云端当前数据与上次本地快照，记录新增、修改和删除。"
    "检测时间不是云端编辑时间；首次同步的记录也会显示为新增。"
    "云端版本和编辑人请查看「修订记录」。"
)


def date_column(sheet: dict, rows: list[dict]) -> str:
    """优先使用已知时间列，未命中时仅从非空日期样本推断。"""
    names = [column["name"] for column in sheet.get("columns", [])]
    for name in ("开始时间", "发布时间", "直播时间", "日期", "时间", "创建时间"):
        if name in names:
            return name
    for name in names:
        sample = [str(row["values"].get(name) or "").strip() for row in rows[:20]]
        nonempty = [value for value in sample if value]
        hits = sum(sort_key(value)[0] == 1 for value in nonempty)
        if nonempty and hits >= min(5, len(nonempty)):
            return name
    return ""


def sort_newest_first(rows: list[dict], sheet: dict) -> list[dict]:
    column = date_column(sheet, rows)
    if not column:
        return list(reversed(rows))
    return sort_records(rows, lambda row: sort_key(row["values"].get(column)),
                        ascending=False)


def read_page(doc_key: str, sheet: dict, offset: int = 0, page_size: int = 200,
              newest_first: bool = True, sort_column: str = "",
              descending: bool = False, path=None):
    """整表展示排序后分页，返回 (rows, total, effective_offset)。"""
    tab_id = sheet.get("tab_id")
    if not doc_key or not tab_id:
        return [], 0, 0
    names = [column["name"] for column in sheet.get("columns", [])]
    if sort_column and sort_column not in names:
        raise ValueError("排序列不存在，请刷新表格后重试")
    page_size = max(1, int(page_size))
    offset = max(0, int(offset))
    if newest_first or sort_column:
        # SQLite 的 LIMIT -1 读取完整快照，避免固定上限截断后再分页。
        rows, _ = tables.rows_page(doc_key, tab_id, 0, -1, path=path)
        if newest_first:
            rows = sort_newest_first(rows, sheet)
        if sort_column:
            rows = sort_records(rows, lambda row: sort_key(row["values"].get(sort_column)),
                                ascending=not descending)
        total = len(rows)
        offset = min(offset, max(0, (total - 1) // page_size * page_size))
        return rows[offset:offset + page_size], total, offset
    rows, total = tables.rows_page(doc_key, tab_id, offset, page_size, path=path)
    if not rows and total and offset:
        offset = max(0, (total - 1) // page_size * page_size)
        rows, total = tables.rows_page(doc_key, tab_id, offset, page_size, path=path)
    return rows, total, offset


def recent_changes(doc_key: str, limit: int = 60, path=None) -> list[dict]:
    """当前文档全部子表的本地检测记录，新检测在前，不请求云端。"""
    if not doc_key:
        return []
    path = path if path is not None else config.tables_db_path()
    from pathlib import Path
    if not Path(path).exists():
        return []
    return tables.recent_changes(doc_key, limit=max(1, min(60, int(limit))), path=path)
