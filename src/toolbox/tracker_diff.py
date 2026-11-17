"""账号指标变化对比（纯函数，不依赖 Qt）。

页面只负责把 direction 映射为主题色；文本与增减量在这里统一生成。
"""

from __future__ import annotations


def diff_items(prev: dict, curr: dict, fields: list[tuple[str, str]]) -> list[dict]:
    """对比前后两次采集，返回变化项 DTO。

    fields: [(显示名, 字段名), ...]；direction: note / up / down。
    """
    items = []
    if curr.get("name") and prev.get("name") and curr["name"] != prev["name"]:
        items.append({
            "text": f"更名 {prev['name']}→{curr['name']}",
            "direction": "note",
        })
    for label, field in fields:
        try:
            c = curr.get(field)
            p = prev.get(field)
            if c is None or c == "" or p is None or p == "":
                continue
            c, p = int(float(c)), int(float(p))
            if c != p:
                delta = c - p
                arrow = "↑" if delta > 0 else "↓"
                items.append({
                    "text": f"{label}{arrow}{abs(delta)}",
                    "direction": "up" if delta > 0 else "down",
                    "field": field,
                    "delta": delta,
                })
        except (ValueError, TypeError):
            continue
    return items


def num_delta(prev: dict, curr: dict, fields: list[str]):
    """数值指标对比：返回 curr - prev；任一侧缺失/非数值返回 None。"""
    def _val(rec):
        for field in fields:
            value = rec.get(field)
            if value is None or value == "":
                continue
            try:
                return int(float(value))
            except (ValueError, TypeError):
                return None
        return None

    c, p = _val(curr), _val(prev)
    if c is None or p is None:
        return None
    return c - p
