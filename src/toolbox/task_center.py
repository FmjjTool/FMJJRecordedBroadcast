"""本软件注册的 Windows 计划任务总览（供「⏰ 定时任务」管理页使用）。

三类任务各自独立，互不影响：
- FengMingTCDocMonitor         腾讯文档监控（每天 / 按间隔）
- FengMingTrackerMonitor       网媒账号追踪（每天 / 按间隔）
- FengMingMusicTrackerMonitor  音乐账号追踪（每天 / 按间隔）
计划文本取自各页保存的配置，安装状态 / 下次运行取自 schtasks。
"""

from __future__ import annotations

from toolbox import account_tracker_task as _acc
from toolbox.config_manager import load_convert_config
from toolbox.tcdoc import task as _tcdoc

# 任务 id → (配置键前缀, 模式键, 时间键, 间隔键)
_CFG_KEYS = {
    "tcdoc": ("tcdoc_schedule_mode", "tcdoc_collect_time", "tcdoc_interval_minutes"),
    "tracker": ("tracker_schedule_mode", "tracker_collect_time", "tracker_interval_minutes"),
    "music": ("music_schedule_mode", "music_collect_time", "music_interval_minutes"),
}


def _interval_text(value) -> str:
    try:
        minutes = max(1, int(value))
    except (TypeError, ValueError):
        return "按间隔"
    return f"每 {minutes // 60} 小时" if minutes % 60 == 0 else f"每 {minutes} 分钟"


def _schedule_text(cfg: dict, mode_key: str, time_key: str, interval_key: str) -> str:
    if str(cfg.get(mode_key) or "daily") == "interval":
        return _interval_text(cfg.get(interval_key))
    return f"每天 {cfg.get(time_key) or '09:00'}"


def _next_run(data: dict) -> str:
    return str(data.get("Next Run Time") or data.get("下次运行时间") or "-").strip()


def list_tasks() -> list[dict]:
    """返回本软件注册的全部计划任务（含未安装项），供管理页展示。"""
    cfg = load_convert_config()
    rows = [
        ("tcdoc", "腾讯文档监控", "文档浏览量 / 成员数定时采集", "tcdoc",
         _tcdoc.TASK_NAME, _tcdoc.info()),
        ("tracker", "网媒账号追踪", "粉丝 / 关注 / 获赞等指标定时采集", "tracker",
         _acc.TASK_NAMES["tracker"], _acc.info("tracker")),
        ("music", "音乐账号追踪", "网易云 / QQ音乐 / 酷狗等指标定时采集", "music",
         _acc.TASK_NAMES["music"], _acc.info("music")),
    ]
    out = []
    for tid, label, usage, page, task_name, data in rows:
        mode_key, time_key, interval_key = _CFG_KEYS[tid]
        out.append({
            "id": tid,
            "label": label,
            "usage": usage,
            "page": page,
            "task_name": task_name,
            "schedule": _schedule_text(cfg, mode_key, time_key, interval_key),
            "installed": bool(data),
            "next_run": _next_run(data) if data else "-",
        })
    return out


def uninstall(task_id: str) -> tuple[bool, str]:
    if task_id == "tcdoc":
        return _tcdoc.uninstall()
    if task_id in _acc.TASK_NAMES:
        return _acc.uninstall(task_id)
    return False, f"未知的定时任务：{task_id}"
