"""WebUI 桌面服务框架（L3）：模块概览、任务列表与系统信息。

任务页支持安装 / 更新 / 删除 Windows 计划任务（与桌面端共用 task_center
配置）；其余模块概览只读，具体业务动作由各自的 WebUI 用例提供。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from version import APP_VERSION
from toolbox import task_center
from toolbox.config_manager import (get_app_root, get_config_dir,
                                    get_export_dir, load_convert_config)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")

MODULES = [
    {"key": "organize", "name": "自动整理", "group": "录播工具箱",
     "desc": "按日期、分类整理录播与媒体文件",
     "actions": ["开始整理", "预览整理结果"]},
    {"key": "editor", "name": "视频操作", "group": "录播工具箱",
     "desc": "转换、合并、切割、压缩与截图",
     "actions": ["转换", "合并", "切割", "压缩", "截图"]},
    {"key": "rename", "name": "命名投稿", "group": "录播工具箱",
     "desc": "按模板批量重命名并整理投稿文件",
     "actions": ["批量重命名", "投稿整理"]},
    {"key": "audio", "name": "音频提取", "group": "录播工具箱",
     "desc": "从视频提取音频，可选自动转字幕",
     "actions": ["提取音频", "自动转写"]},
    {"key": "subtitle", "name": "字幕转换", "group": "录播工具箱",
     "desc": "字幕与歌词格式转换",
     "actions": ["转换字幕", "转换歌词"]},
    {"key": "launcher", "name": "继续操作", "group": "录播工具箱",
     "desc": "从最近文件或任务继续处理",
     "actions": ["继续上次操作", "打开快捷项"]},
    {"key": "calendar", "name": "日历浏览", "group": "录播工具箱",
     "desc": "按日期浏览录播与作品目录",
     "actions": ["打开日期目录"]},
    {"key": "migrate", "name": "文件迁移", "group": "录播工具箱",
     "desc": "把文件迁移到目标目录并校验",
     "actions": ["开始迁移", "校验目标目录"]},
]


def list_modules() -> dict:
    return {"modules": MODULES}


def overview(key: str) -> dict:
    definition = next((item for item in MODULES if item["key"] == key), None)
    if definition is None:
        raise ValueError("模块不存在")
    cfg = load_convert_config()
    settings = []
    extra = {}
    if key == "organize":
        settings = [
            {"label": "工作目录", "value": _set_text(cfg.get("work_dir"))},
            {"label": "统一导出目录", "value": get_export_dir().name},
            {"label": "本地扫描根", "value": _set_text(cfg.get("local_folder"))},
        ]
    elif key == "editor":
        settings = [
            {"label": "默认输出格式", "value": cfg.get("output_extension") or "-"},
            {"label": "合并方式", "value": cfg.get("merge_mode") or "-"},
            {"label": "压缩预设", "value": cfg.get("editor_compress_preset") or "-"},
        ]
    elif key == "rename":
        settings = [
            {"label": "工作目录", "value": _set_text(cfg.get("work_dir"))},
            {"label": "统一导出目录", "value": get_export_dir().name},
        ]
    elif key == "audio":
        settings = [
            {"label": "提取范围",
             "value": "全部音轨" if cfg.get("audio_extract_mode") == "all"
             else "第一条音轨"},
            {"label": "自动转写", "value": "开启" if cfg.get("asr_enabled") else "关闭"},
            {"label": "转写语言", "value": cfg.get("asr_language") or "-"},
        ]
    elif key == "subtitle":
        formats = cfg.get("subtitle_formats") or []
        settings = [
            {"label": "目标格式", "value": " / ".join(formats) or "-"},
            {"label": "输出到原目录",
             "value": "是" if cfg.get("subtitle_use_same_dir") else "否"},
        ]
    elif key == "launcher":
        items = cfg.get("launcher_items") or []
        settings = [
            {"label": "快捷项", "value": f"{len(items)} 个"},
            {"label": "工作目录", "value": _set_text(cfg.get("work_dir"))},
        ]
    elif key == "calendar":
        days = _recent_days(cfg.get("work_dir"))
        extra["days"] = days
        settings = [
            {"label": "工作目录", "value": _set_text(cfg.get("work_dir"))},
            {"label": "日期目录", "value": f"{len(days)} 个"},
        ]
    elif key == "migrate":
        settings = [
            {"label": "迁移目标", "value": _set_text(cfg.get("migrate_target_dir"))},
            {"label": "工作目录", "value": _set_text(cfg.get("work_dir"))},
        ]
    return {**definition, "settings": settings, **extra,
            "note": "Web 端当前提供只读概览；整理、转换、投稿等操作请在桌面端完成。"}


def task_rows() -> dict:
    try:
        rows = task_center.list_tasks()
    except Exception:
        rows = []
    return {"tasks": rows}


def _task_ids() -> set[str]:
    return {str(row.get("id") or "") for row in task_center.list_tasks()}


def task_schedule(task_id: str) -> dict:
    task_id = str(task_id or "")
    if task_id not in _task_ids():
        raise ValueError("未知的定时任务")
    return task_center.get_schedule(task_id)


def install_task(task_id: str, mode: str, time_text: str, interval,
                 confirmed: bool = False, extra_times=None) -> dict:
    """安装 / 更新 Windows 计划任务（保存计划设置并注册）。"""
    if not confirmed:
        raise ValueError("缺少操作确认")
    task_id = str(task_id or "")
    if task_id not in _task_ids():
        raise ValueError("未知的定时任务")
    mode = str(mode or "daily")
    if mode not in ("daily", "interval"):
        raise ValueError("计划模式只支持每天或按间隔")
    time_text = str(time_text or "09:00").strip()
    if not _TIME_RE.fullmatch(time_text):
        raise ValueError("时间格式应为 HH:MM")
    if interval in (None, ""):
        interval = 30
    try:
        interval = int(interval)
    except (TypeError, ValueError) as exc:
        raise ValueError("间隔必须是分钟数") from exc
    if not 1 <= interval <= 1439:
        raise ValueError("间隔需在 1–1439 分钟之间")
    if task_id == "webwiki":
        extra = list(extra_times or
                     task_center.get_schedule(task_id).get("extra_times") or [])
    else:
        extra = None
    ok, message = task_center.install(task_id, mode, time_text, interval, extra)
    if not ok:
        raise ValueError(str(message or "安装失败"))
    return {"ok": True, "message": str(message or "已安装 / 更新")}


def uninstall_task(task_id: str, confirmed: bool = False) -> dict:
    """删除 Windows 计划任务（与桌面端共用同一任务注册）。"""
    if not confirmed:
        raise ValueError("缺少操作确认")
    task_id = str(task_id or "")
    if task_id not in _task_ids():
        raise ValueError("未知的定时任务")
    ok, message = task_center.uninstall(task_id)
    if not ok:
        raise ValueError(str(message or "删除失败"))
    return {"ok": True, "message": str(message or "已删除")}


def system_info() -> dict:
    from toolbox import webui_tunnel

    cfg = load_convert_config()
    tunnel_cfg = webui_tunnel.get_tunnel().config()
    mode = tunnel_cfg["mode"]
    clients = []
    for key, path in webui_tunnel.get_tunnel().detect().items():
        meta = webui_tunnel.TUNNELS.get(key, {})
        clients.append({"key": key, "name": meta.get("name") or key,
                        "ready": bool(path)})
    return {
        "version": APP_VERSION,
        "app_root": get_app_root().name,
        "config_dir": get_config_dir().name,
        "export_dir": get_export_dir().name,
        "work_dir": _set_text(cfg.get("work_dir")),
        "webui": {
            "port": int(cfg.get("webui_port") or 8765),
            "lan": bool(cfg.get("webui_lan")),
            "tunnel": mode,
            "tunnel_name": (webui_tunnel.TUNNELS.get(mode, {})
                            .get("name") or "未启用"),
            "tunnel_public_url": tunnel_cfg.get("frp_public_url") or "",
            "tunnel_frp_config": bool(tunnel_cfg.get("frp_config")),
            "tunnel_clients": clients,
        },
        "note": "目录与主题可在本页修改，与桌面端共用配置；"
                "依赖组件由桌面端管理，Web 端不提供安装或卸载。"
                "公网隧道由桌面端启动 / 停止，本轮只读展示。",
    }


def _set_text(value) -> str:
    return "已设置" if str(value or "").strip() else "未设置"


def _recent_days(work_dir, limit: int = 12) -> list[str]:
    text = str(work_dir or "").strip()
    if not text:
        return []
    root = Path(text)
    if not root.is_dir():
        return []
    days = []
    try:
        for entry in os.scandir(root):
            if len(days) >= 10000:
                break
            if entry.is_dir() and _DATE_RE.match(entry.name):
                days.append(entry.name)
    except OSError:
        return []
    days.sort(reverse=True)
    return days[:limit]