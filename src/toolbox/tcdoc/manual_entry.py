"""手动录入的只读字段模型，桌面与 Web 共用；不保存、不上传任何记录。"""

from __future__ import annotations

from . import field_maps

SKIP_TYPES = frozenset({5, 10, 11, 13, 19, 21})  # 图片 / 人员 / 公式 / 关联


def field_pairs(target: dict) -> list[tuple[str, str]]:
    """按表头顺序返回（展示列名，内部字段），遵守已有字段映射。"""
    doc_key = str(target.get("doc_key") or "")
    tab_id = str(target.get("tab_id") or "")
    pairs = []
    seen = set()
    for column in target.get("columns") or []:
        shown = str(column.get("name") or "")
        if not shown or int(column.get("type") or 1) in SKIP_TYPES:
            continue
        internal = field_maps.internal_of(doc_key, tab_id, shown)
        if (not internal or internal in seen
                or not field_maps.target(doc_key, tab_id, internal)):
            continue
        seen.add(internal)
        pairs.append((shown, internal))
    return pairs


def build_row(target: dict, values: dict) -> dict:
    """只接收当前表单字段，构造待复制行；不接受调用方传入的上传元数据。"""
    if not isinstance(values, dict):
        raise ValueError("录入内容格式不正确，请重新填写")
    row = {internal: str(values.get(internal) or "").strip()
           for _shown, internal in field_pairs(target)}
    if not any(row.values()):
        raise ValueError("至少填写一个字段")
    row.update(_images=[], _source="", _needs_review=True)
    return row
