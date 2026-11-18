"""音乐账号追踪：把「当前两次采集的变化」导出为 PNG 图片。

- 单账号：export_diff_png(account, history) → 一张账号数据卡
- 按组：export_group_png(group_id, group_name) → 组内账号卡片 + 合计卡

画面统一由 `toolbox.image_render` 生成（1080 px、深色平台卡片仪表盘）。
"""

from pathlib import Path

from toolbox import image_render as render
from toolbox.platform_names import MUSIC_PLATFORM_NAMES as PLATFORM_NAMES

# 卡片指标（第一项用大号字）：(显示名, 字段名...)
NUM_METRICS = [
    ("粉丝量", "fans"),
    ("播放/听众", "listens"),
    ("歌曲数", "songs"),
]


def _num(rec, fields):
    """从记录里取第一个可解析为整数的数字字段，失败返回 None。"""
    if isinstance(fields, str):
        fields = (fields,)
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


def _plat_key(account, rec):
    return (account.get("platform") or rec.get("platform") or "").strip()


def _resolve_output_dir(output_dir):
    if output_dir is None:
        from toolbox.config_manager import get_export_subdir
        output_dir = get_export_subdir("music_tracker")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def _card(account, curr, prev):
    metrics = []
    for label, *fields in NUM_METRICS:
        p_val = _num(prev, fields)
        c_val = _num(curr, fields)
        delta = None if p_val is None or c_val is None else c_val - p_val
        delta_text, delta_key = render.format_delta(delta)
        metrics.append((label, render.format_count(c_val), delta_text, delta_key))
    return {
        "platform": _plat_of(account, curr),
        "account": account.get("name") or curr.get("name") or "账号",
        "accent": render.platform_accent(_plat_key(account, curr)),
        "metrics": metrics,
        "note": f"{(prev.get('_time') or '—')[:16]} → {(curr.get('_time') or '—')[:16]}",
    }


def _total(entries):
    """粉丝量合计与变化；无任何粉丝数据时返回 None。"""
    current = []
    previous = []
    for acc, hist in entries:
        curr, prev = hist[0], hist[1]
        c_val = _num(curr, ("fans",))
        p_val = _num(prev, ("fans",))
        if c_val is not None:
            current.append(c_val)
        if p_val is not None:
            previous.append(p_val)
    if not current:
        return None
    c_total = sum(current)
    p_total = sum(previous) if len(previous) == len(current) else None
    delta = None if p_total is None else c_total - p_total
    delta_text, delta_key = render.format_delta(delta)
    platforms = len({_plat_key(acc, hist[0]) for acc, hist in entries})
    return {
        "label": f"粉丝量合计（{platforms} 平台）",
        "value": render.format_count(c_total),
        "delta": delta_text,
        "direction": delta_key,
    }


def export_diff_png(account, history, output_dir=None, theme="dark"):
    """单账号导出：最新两次采集的变化对比 PNG，返回文件路径。"""
    if not history or len(history) < 2:
        raise ValueError("该账号至少需要两次采集数据才能导出变化对比")

    curr, prev = history[0], history[1]
    name = account.get("name") or curr.get("name") or "账号"
    plat_name = _plat_of(account, curr)
    output_dir = _resolve_output_dir(output_dir)
    out_path = render.build_output_path(output_dir, "music_tracker", name)

    card = _card(account, curr, prev)
    subtitle = (f"{plat_name} · 数据时间：{(curr.get('_time') or '—')[:16]}"
                f"　较上次（{(prev.get('_time') or '—')[:16]}）")
    return render.render_account_dashboard(
        f"{name} · 音乐数据", subtitle, [card], out_path,
        theme=theme, source="音乐账号追踪 · 本地采集记录")


def export_group_png(group_id, group_name, output_dir=None, theme="dark"):
    """按组导出：组内所有账号的卡片 + 粉丝量合计卡。"""
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
    out_path = render.build_output_path(output_dir, "music_tracker", group_label)

    cards = [_card(acc, hist[0], hist[1]) for acc, hist in entries]
    latest = max((hist[0].get("_time") or "") for _acc, hist in entries)
    subtitle = f"{len(entries)} 个账号 · 最新采集：{latest[:16] or '—'}"
    return render.render_account_dashboard(
        f"{group_label} · 音乐平台数据", subtitle, cards, out_path,
        theme=theme, total=_total(entries), source="音乐账号追踪 · 本地采集记录")