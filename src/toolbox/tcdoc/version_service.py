"""采集版本只读用例，供桌面与 Web 共用。

版本文件的唯一写入方是 versions；本模块只校验清单身份、组装展示 DTO。
选择历史仅改变调用方的预览状态，不恢复/覆盖当前数据库、导出文件或云端。
"""

from __future__ import annotations

from io import BytesIO


def _document(doc_key: str) -> dict:
    from . import config

    key = str(doc_key or "").strip()
    if not key or len(key) > 200:
        raise ValueError("请选择文档")
    doc = next((item for item in config.load_docs() if item.get("key") == key), None)
    if doc is None:
        raise ValueError("文档不在当前清单中，请刷新后重试")
    return doc


def list_versions(doc_key: str, source: str | None = None) -> list[dict]:
    from . import versions

    doc = _document(doc_key)
    if source not in (None, "tables", "body"):
        raise ValueError("采集版本类型无效，请刷新后重试")
    return versions.list_versions(doc["key"], source=source)


def read_version(doc_key: str, version_id: str) -> dict:
    from . import versions

    doc = _document(doc_key)
    return versions.read_version(doc["key"], version_id)


def table_page(doc_key: str, version_id: str, tab_id: str,
               page: int = 1, page_size: int = 200, *,
               sort_column: str = "", descending: bool = False) -> dict:
    """先按共享显示键排整个历史子表，再分页；不改历史原始顺序。"""
    from toolbox.table_sort_keys import sort_key, sort_records
    content = read_version(doc_key, version_id)
    sheet = next((item for item in content.get("sheets", [])
                  if item.get("tab_id") == tab_id), None)
    if sheet is None:
        raise ValueError("该采集版本没有此工作表，请重新选择")
    rows = sheet.get("rows") or []
    if sort_column != "":
        columns = sheet.get("columns") or []
        try:
            index = int(sort_column)
            if not 0 <= index <= len(columns):
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError("排序列无效，请重新选择") from exc
        column = columns[index - 1] if index else None
        name = column.get("name") if isinstance(column, dict) else str(column or "")
        rows = sort_records(rows, lambda row: sort_key(
            (row.get("values") or {}).get(name) if index else row.get("row_no")),
            ascending=not descending)
    try:
        page_size = max(1, min(200, int(page_size)))
        pages = max(1, (len(rows) + page_size - 1) // page_size)
        page = max(1, min(pages, int(page)))
    except (TypeError, ValueError) as exc:
        raise ValueError("页码无效，请重新选择") from exc
    offset = (page - 1) * page_size
    return {"sheet": {key: value for key, value in sheet.items() if key != "rows"},
            "rows": rows[offset:offset + page_size], "total": len(rows),
            "page": page, "pages": pages, "page_size": page_size}


def read_asset(doc_key: str, version_id: str, asset_id: str) -> tuple[bytes, str]:
    """只提供校验通过的内置位图，绝不从历史记录里的 URL 或路径补读。"""
    from PIL import Image
    from . import versions

    doc = _document(doc_key)
    data, _mime = versions.read_asset(doc["key"], version_id, asset_id)
    try:
        if not data or len(data) > 32 * 1024 * 1024:
            raise ValueError("图片大小超出预览限制")
        with Image.open(BytesIO(data)) as picture:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "GIF": "image/gif",
                    "WEBP": "image/webp", "BMP": "image/bmp", "AVIF": "image/avif"}.get(
                        str(picture.format or "").upper())
            if not mime or min(picture.size) < 1 or picture.width * picture.height > 50_000_000:
                raise ValueError("图片格式或尺寸不支持预览")
            picture.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ValueError("历史图片不可用，请选择其他采集版本") from exc
    return data, mime
