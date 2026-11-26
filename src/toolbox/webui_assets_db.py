"""WebUI 资产数据库用例（L3）：确认后按稳定记录键删除。

仅调用增量数据库唯一写入方，不维护第二份数据；不依赖桌面页面、Qt
或界面状态。分页与筛选仍由 asset_query 的查询用例处理。
"""

from __future__ import annotations

from toolbox import increment_db

_MAX_DELETE_ROWS = 200


def delete_rows(payload: dict) -> dict:
    """删除当前平台所选记录，返回实际删除数与平台表最新数量。"""
    if not isinstance(payload, dict):
        raise ValueError("删除数据格式不正确，请刷新页面后重试")
    if payload.get("confirmed") is not True:
        raise ValueError("删除前需要确认，请重新选择记录后重试")
    platform = payload.get("platform")
    if not isinstance(platform, str) or not platform:
        raise ValueError("请选择要删除记录的平台")
    keys = payload.get("keys")
    if (not isinstance(keys, list) or not 1 <= len(keys) <= _MAX_DELETE_ROWS
            or any(not isinstance(key, str) or not key.strip() for key in keys)):
        raise ValueError("请选择 1–200 条要删除的记录")
    tables = increment_db.list_tables()
    if platform not in {name for name, _count in tables}:
        raise ValueError("平台表不存在，请刷新列表后重试")
    deleted = sum(1 for key in dict.fromkeys(keys)
                  if increment_db.delete_row(platform, key))
    return {
        "deleted": deleted,
        "tables": [{"name": name, "rows": int(count)}
                   for name, count in increment_db.list_tables()],
    }
