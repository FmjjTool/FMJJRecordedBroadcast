"""账号指标变化对比（纯函数，不依赖 Qt）。

页面只负责把 direction 映射为主题色；文本与增减量在这里统一生成。
"""

from __future__ import annotations

_APPROX_TRUE = {"1", "true", "yes", "是"}


def fans_meta(rec: dict) -> tuple[str | None, bool]:
    """返回 (粉丝来源标记, 是否为约数)。旧记录没有这两个字段时返回 (None, False)。"""
    source = rec.get("fans_source")
    source = str(source).strip() if source not in (None, "") else None
    approximate = rec.get("fans_approximate")
    if isinstance(approximate, str):
        approximate = approximate.strip().lower() in _APPROX_TRUE
    return source, bool(approximate)


def fans_comparable(prev: dict, curr: dict) -> bool:
    """粉丝增减只允许在统计口径一致时计算。

    两侧都声明了来源且来源/约数标记不同（如「m端万化」vs「PC端精确」），
    数值差是口径差而不是真实变化，必须放弃比较。旧记录缺字段时按可比较处理。
    """
    p_source, p_approx = fans_meta(prev)
    c_source, c_approx = fans_meta(curr)
    if p_source and c_source:
        return p_source == c_source and p_approx == c_approx
    return True


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
    fans_ok = fans_comparable(prev, curr)
    for label, field in fields:
        if field == "fans" and not fans_ok:
            continue
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
