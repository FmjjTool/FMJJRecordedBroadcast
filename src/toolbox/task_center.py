"""本软件注册的 Windows 计划任务总览（供「定时任务」管理页使用）。

九类任务各自独立，互不影响：
- FengMingTCDocMonitor         腾讯文档监控（每天 / 按间隔）
- FengMingTrackerMonitor       网媒账号追踪（每天 / 按间隔）
- FengMingMusicTrackerMonitor  音乐账号追踪（每天 / 按间隔）
- FengMingQuarkBackup          夸克网盘备份（每天 / 按间隔）
- FengMingWebwikiAnalytics     本地资料站访问统计（每天）
- FengMingFanGroupsMonitor     粉丝群 / QQ群人数快照（每天 / 按间隔）
- FengMingInfoCollect          信息获取作品增量（每天 / 按间隔）
- FengMingChaohuaCollect       微博超话采集（每天 / 按间隔）
- FengMingDouyinIndexCollect   抖音指数快照（每天 / 按间隔）
计划文本取自各页保存的配置，安装状态 / 下次运行取自 schtasks；计划设置统一由
「定时任务」页读写，功能页不再单独提供定时配置。
"""

from __future__ import annotations

from toolbox import account_tracker_task as _acc
from toolbox import content_collect_task as _content
from toolbox import fan_groups_task as _fan
from toolbox import info_collect_task as _info
from toolbox import quark_backup_task as _quark
from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.tcdoc import task as _tcdoc
from toolbox.webwiki import analytics_task as _webwiki

# 任务 id → (模式键, 时间键, 间隔键)；模式键为 None 表示只支持每天
_CFG_KEYS = {
    "tcdoc": ("tcdoc_schedule_mode", "tcdoc_collect_time", "tcdoc_interval_minutes"),
    "tracker": ("tracker_schedule_mode", "tracker_collect_time", "tracker_interval_minutes"),
    "music": ("music_schedule_mode", "music_collect_time", "music_interval_minutes"),
    "quark": ("quark_schedule_mode", "quark_backup_time", "quark_interval_minutes"),
    "webwiki": (None, "webwiki_analytics_time", None),
    "fangroups": ("fangroups_schedule_mode", "fangroups_snapshot_time",
                  "fangroups_interval_minutes"),
    "info": ("info_schedule_mode", "info_collect_time", "info_interval_minutes"),
    "chaohua": ("chaohua_schedule_mode", "chaohua_collect_time", "chaohua_interval_minutes"),
    "douyin_index": ("douyin_index_schedule_mode", "douyin_index_collect_time",
                     "douyin_index_interval_minutes"),
}

# 任务 id → 页面 key（仅用于展示来源，不再作为设置入口）
_PAGE_KEYS = {
    "tcdoc": "tcdoc", "tracker": "tracker", "music": "music_tracker",
    "quark": "global_settings", "webwiki": "localwiki", "fangroups": "fan_groups",
    "info": "info_accounts",
    "chaohua": "weibo_chaohua", "douyin_index": "douyin_index",
}
_CONTENT_DEFAULT_TIMES = {"chaohua": "09:30", "douyin_index": "09:45"}
# 站点访问统计的两个补采时间点（空 = 不启用）
_WEBWIKI_EXTRA_TIME_KEYS = ("webwiki_analytics_time2", "webwiki_analytics_time3")


def _webwiki_times(cfg: dict) -> list[str]:
    times = [str(cfg.get("webwiki_analytics_time") or "").strip()]
    times.extend(str(cfg.get(key) or "").strip() for key in _WEBWIKI_EXTRA_TIME_KEYS)
    return [value for value in dict.fromkeys(times) if value]


def _interval_text(value) -> str:
    try:
        minutes = max(1, int(value))
    except (TypeError, ValueError):
        return "按间隔"
    return f"每 {minutes // 60} 小时" if minutes % 60 == 0 else f"每 {minutes} 分钟"


def _schedule_text(cfg: dict, mode_key: str, time_key: str, interval_key: str,
                   default_time: str = "09:00") -> str:
    if mode_key and str(cfg.get(mode_key) or "daily") == "interval":
        return _interval_text(cfg.get(interval_key))
    return f"每天 {cfg.get(time_key) or default_time}"


def _next_run(data: dict) -> str:
    return str(data.get("Next Run Time") or data.get("下次运行时间") or "-").strip()


def list_tasks() -> list[dict]:
    """返回本软件注册的全部计划任务（含未安装项），供管理页展示。"""
    cfg = load_convert_config()
    rows = [
        ("tcdoc", "腾讯文档监控", "文档浏览量 / 成员数定时采集",
         _tcdoc.TASK_NAME, _tcdoc.info()),
        ("tracker", "网媒账号追踪", "粉丝 / 关注 / 获赞等指标定时采集",
         _acc.TASK_NAMES["tracker"], _acc.info("tracker")),
        ("music", "音乐账号追踪", "网易云 / QQ音乐 / 酷狗等指标定时采集",
         _acc.TASK_NAMES["music"], _acc.info("music")),
        ("quark", "夸克网盘备份", "本地目录增量备份到网盘指定文件夹",
         _quark.TASK_NAME, _quark.info()),
        ("webwiki", "站点访问统计", "mandisa.wiki 后台访问统计每日采集",
         _webwiki.TASK_NAME, _webwiki.info()),
        ("fangroups", "粉丝群快照", "已订阅粉丝群 / QQ群的人数定时快照",
         _fan.TASK_NAME, _fan.info()),
        ("info", "作品增量", "信息获取账号的最新作品定时入库（只入库不下媒体）",
         _info.TASK_NAME, _info.info()),
        ("chaohua", "微博超话采集", "已订阅超话的最新帖子与互动指标定时入库",
         _content.TASK_NAMES["chaohua"], _content.info("chaohua")),
        ("douyin_index", "抖音指数快照", "已订阅达人的趋势与粉丝画像定时保存快照",
         _content.TASK_NAMES["douyin_index"], _content.info("douyin_index")),
    ]
    out = []
    for tid, label, usage, task_name, data in rows:
        mode_key, time_key, interval_key = _CFG_KEYS[tid]
        if tid == "webwiki":
            schedule = "每天 " + " / ".join(sorted(_webwiki_times(cfg)))
        else:
            schedule = _schedule_text(cfg, mode_key, time_key, interval_key,
                                      _CONTENT_DEFAULT_TIMES.get(tid, "09:00"))
        out.append({
            "id": tid,
            "label": label,
            "usage": usage,
            "page": _PAGE_KEYS[tid],
            "task_name": task_name,
            "schedule": schedule,
            "installed": bool(data),
            "next_run": _next_run(data) if data else "-",
        })
    return out


def get_schedule(task_id: str) -> dict:
    """读取某任务的计划设置：{mode: daily/interval, time: HH:MM, interval: 分钟}。"""
    mode_key, time_key, interval_key = _CFG_KEYS[task_id]
    cfg = load_convert_config()
    mode = str(cfg.get(mode_key) or "daily") if mode_key else "daily"
    if mode not in ("daily", "interval"):
        mode = "daily"
    try:
        interval = max(1, min(1439, int(cfg.get(interval_key) or 30)))
    except (TypeError, ValueError):
        interval = 30
    result = {
        "mode": mode if interval_key else "daily",
        "time": str(cfg.get(time_key) or _CONTENT_DEFAULT_TIMES.get(task_id, "09:00")),
        "interval": interval,
        "interval_supported": bool(interval_key),
        "extra_times": ([str(cfg.get(key) or "") for key in _WEBWIKI_EXTRA_TIME_KEYS]
                        if task_id == "webwiki" else []),
    }
    if task_id in _content.TASK_NAMES:
        from toolbox.content_collect import get_settings
        result["collection"] = get_settings(task_id)
    return result


def save_schedule(task_id: str, mode: str, time_text: str, interval: int,
                  extra_times=None, collection=None) -> None:
    """保存计划设置到全局配置（是否安装由 install() 负责）。"""
    mode_key, time_key, interval_key = _CFG_KEYS[task_id]
    if task_id in _content.TASK_NAMES:
        from toolbox.content_collect import get_settings, save_settings, validate_settings
        values = get_settings(task_id)["values"] if collection is None else collection
        # 在触碰任一设置前验证对象与参数；不把空订阅登记成可执行计划。
        values = validate_settings(task_id, values)
        save_settings(task_id, values)
    cfg = load_convert_config()
    if mode_key:
        cfg[mode_key] = "interval" if mode == "interval" and interval_key else "daily"
    cfg[time_key] = str(time_text or "09:00").strip()
    if interval_key:
        try:
            cfg[interval_key] = max(1, min(1439, int(interval)))
        except (TypeError, ValueError):
            cfg[interval_key] = 30
    if task_id == "webwiki":
        values = list(extra_times or [])
        for index, key in enumerate(_WEBWIKI_EXTRA_TIME_KEYS):
            cfg[key] = str(values[index] or "").strip() if index < len(values) else ""
    save_convert_config(cfg)


def install(task_id: str, mode: str, time_text: str, interval: int,
            extra_times=None, collection=None) -> tuple[bool, str]:
    """保存并注册 / 更新指定任务的 Windows 计划任务。"""
    save_schedule(task_id, mode, time_text, interval, extra_times, collection)
    if task_id == "webwiki":
        return _webwiki.install_daily(time_text, extra_times or [])
    use_interval = mode == "interval" and _CFG_KEYS[task_id][2] is not None
    if task_id in _content.TASK_NAMES:
        return (_content.install_interval(task_id, interval) if use_interval
                else _content.install_daily(task_id, time_text))
    if task_id == "tcdoc":
        return (_tcdoc.install_interval(interval) if use_interval
                else _tcdoc.install_daily(time_text))
    if task_id == "quark":
        return (_quark.install_interval(interval) if use_interval
                else _quark.install_daily(time_text))
    if task_id == "fangroups":
        return (_fan.install_interval(interval) if use_interval
                else _fan.install_daily(time_text))
    if task_id == "info":
        return (_info.install_interval(interval) if use_interval
                else _info.install_daily(time_text))
    if task_id in _acc.TASK_NAMES:
        return (_acc.install_interval(task_id, interval) if use_interval
                else _acc.install_daily(task_id, time_text))
    return False, f"未知的定时任务：{task_id}"


def uninstall(task_id: str) -> tuple[bool, str]:
    if task_id in _content.TASK_NAMES:
        return _content.uninstall(task_id)
    if task_id == "tcdoc":
        return _tcdoc.uninstall()
    if task_id == "quark":
        return _quark.uninstall()
    if task_id == "webwiki":
        return _webwiki.uninstall()
    if task_id == "fangroups":
        return _fan.uninstall()
    if task_id == "info":
        return _info.uninstall()
    if task_id in _acc.TASK_NAMES:
        return _acc.uninstall(task_id)
    return False, f"未知的定时任务：{task_id}"
