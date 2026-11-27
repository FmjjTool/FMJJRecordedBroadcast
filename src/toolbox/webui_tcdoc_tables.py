"""腾讯文档本地表格预览用例（Qt-free、只读）。

与桌面复用 table_preview 的整表排序和快照差分；不请求云端，不持有
桌面页面状态，不建立 Web 专用配置或数据库。
"""

from __future__ import annotations

from toolbox.tcdoc import config, table_preview, tables


def _document(doc_key: str) -> dict:
    key = str(doc_key or "").strip()
    if not key:
        raise ValueError("请选择文档")
    doc = next((item for item in config.load_docs() if item.get("key") == key), None)
    if doc is None:
        raise ValueError("文档不存在，请重新选择")
    return doc


def table_rows(doc_key: str, tab_id: str, page: int = 1, page_size: int = 200,
               newest_first: bool = True, sort_column: str = "",
               direction: str = "") -> dict:
    """整表排序后再分页，默认按日期从新到旧；不改变原始行序。"""
    if direction not in ("", "asc", "desc"):
        raise ValueError("排序方向无效，请重新选择")
    doc = _document(doc_key)
    key = doc["key"]
    tab_id = str(tab_id or "").strip()
    if not tab_id:
        raise ValueError("请选择工作表")
    path = config.tables_db_path()
    if not path.exists():
        raise ValueError("还没有本地表格数据，请先同步表格数据")
    sheet = next((item for item in tables.sheets_of(key)
                  if item.get("tab_id") == tab_id and not item.get("skipped")), None)
    if sheet is None:
        raise ValueError("工作表不存在，请重新选择")
    page = max(1, int(page))
    page_size = max(1, min(200, int(page_size)))
    column = str(sort_column or "") if direction else ""
    if direction and not column:
        raise ValueError("请选择排序列")
    rows, total, offset = table_preview.read_page(
        key, sheet, offset=(page - 1) * page_size, page_size=page_size,
        newest_first=bool(newest_first), sort_column=column,
        descending=direction == "desc")
    pages = max(1, (int(total) + page_size - 1) // page_size)
    return {"doc_key": key, "tab_id": tab_id, "rows": rows,
            "total": int(total), "page": offset // page_size + 1,
            "page_size": page_size, "pages": pages,
            "newest": bool(newest_first), "sort_column": column,
            "direction": direction}


def recent_changes(doc_key: str, limit: int = 60) -> dict:
    """只读本地同步差分，时间为检测时间，不冒充云端编辑时间。"""
    doc = _document(doc_key)
    limit = max(1, min(60, int(limit)))
    path = config.tables_db_path()
    rows = table_preview.recent_changes(doc["key"], limit=limit) if path.exists() else []
    return {"doc_key": doc["key"], "doc_name": str(doc.get("name") or doc["key"]),
            "rows": rows, "total": len(rows), "limit": limit,
            "source": "local_snapshot_diff",
            "description": table_preview.CHANGES_DESCRIPTION}
