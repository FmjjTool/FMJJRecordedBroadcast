"""collected.db → increment.db 投影（derived）。

collected.db 是「已获取」主数据；本模块是 increment.db 的唯一写入入口。
页面只触发投影，不再直接调用 increment_db。
"""

from __future__ import annotations


def project_items(items) -> dict:
    """把已获取条目投影到分平台增量库，返回 {平台: 新增条数}。"""
    from toolbox import increment_db
    return increment_db.save_incremental(items)


def increment_path():
    from toolbox import increment_db
    return increment_db.db_path()
