"""账号追踪：把「当前两次采集的变化」导出为 PNG 图片。

- 单账号：export_diff_png(account, history) → 一张账号对比图
- 按组：export_group_png(group_id, group_name) → 组内所有账号堆叠成一张长图

画面统一由 `toolbox.image_render` 生成（1080 px、品牌页头/页脚、浅色默认）。
"""

from pathlib import Path

from toolbox import image_render as render
from toolbox.platform_names import MEDIA_PLATFORM_NAMES as PLATFORM_NAMES

# 数字指标（可计算涨跌）：(显示名, 字段名...)
NUM_METRICS = [
    ("粉丝数", "fans"),
    ("关注数", "following"),
    ("获赞数", "likes"),
    ("内容数", "video_count", "weibo_count"),
]


def _num(rec, fields):
    """从记录里取第一个可解析为整数的数字字段，失败返回 None。"""
    for f in fields:
        v = rec.get(f)
        if v is None or v == "":
            continue
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return None
    return None


def _plat_of(account, rec):
    platform = (account.get("platform") or rec.get("platform") or "").strip()
    return PLATFORM_NAMES.get(platform, platform or "未知")


def _resolve_output_dir(output_dir):
    if output_dir is None:
        from toolbox.config_manager import get_export_subdir
        output_dir = get_export_subdir("tracker")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _rows(prev, curr, theme):
    palette = render.tokens(theme)
    rows = []
    for label, *fields in NUM_METRICS:
        p_val = _num(prev, fields)
        c_val = _num(curr, fields)
        delta = None if p_val is None or c_val is None else c_val - p_val
        delta_text, delta_key = render.format_delta(delta)
        rows.append((
            label,
            (render.format_count(p_val), palette["text"], False),
            (render.format_count(c_val), palette["text"], True),
            (delta_text, palette[delta_key], True),
        ))
    return rows


def _notes(prev, curr):
    notes = [
        f"上次采集：{(prev.get('_time') or '—')[:16]}",
        f"当前采集：{(curr.get('_time') or '—')[:16]}",
    ]
    if curr.get("name") and prev.get("name") and curr["name"] != prev["name"]:
        notes.append(f"昵称变更：{prev['name']} → {curr['name']}")
    return notes


def export_diff_png(account, history, output_dir=None, theme="light"):
    """单账号导出：最新两次采集的变化对比 PNG，返回文件路径。"""
    if not history or len(history) < 2:
        raise ValueError("该账号至少需要两次采集数据才能导出变化对比")

    curr, prev = history[0], history[1]
    name = account.get("name") or curr.get("name") or "账号"
    plat_name = _plat_of(account, curr)
    output_dir = _resolve_output_dir(output_dir)
    out_path = render.build_output_path(output_dir, "tracker", name)

    sections = [{
        "title": "",
        "subtitle": "",
        "rows": _rows(prev, curr, theme),
        "notes": _notes(prev, curr),
    }]
    return render.render_report(
        name, f"{plat_name} · 最近两次采集变化对比",
        sections, out_path, theme=theme, source="账号追踪 · 本地采集记录")


def export_group_png(group_id, group_name, output_dir=None, theme="light"):
    """按组导出：组内所有账号（各自最新两次变化）堆叠成一张长图。"""
    from .accounts import get_accounts_by_group, load_history

    accounts = get_accounts_by_group(group_id)
    entries = []
    for acc in accounts:
        hist = load_history(acc.get("name", ""))
        if len(hist) >= 2:
            entries.append((acc, hist))
    if not entries:
        raise ValueError("该组没有可导出的账号（需每个账号至少两次采集）")

    group_label = f"{group_name}（组{group_id}）" if group_name else f"组{group_id}"
    output_dir = _resolve_output_dir(output_dir)
    out_path = render.build_output_path(output_dir, "tracker", group_label)

    sections = []
    for acc, hist in entries:
        curr, prev = hist[0], hist[1]
        name = acc.get("name") or curr.get("name") or "账号"
        sections.append({
            "title": name,
            "subtitle": _plat_of(acc, curr),
            "rows": _rows(prev, curr, theme),
            "notes": _notes(prev, curr),
        })
    return render.render_report(
        group_label,
        f"组号 {group_id} · {len(entries)} 个账号 · 最近两次采集变化对比",
        sections, out_path, theme=theme, source="账号追踪 · 本地采集记录")
