"""WebUI 桌面服务只读框架（L3）：模块概览、任务列表与系统信息。

只读，不执行整理/转换/投稿等业务动作；页面按钮只做入口说明，实际操作
仍在桌面端。WebUI 通过这些用例了解桌面服务状态，不直接访问页面控件。
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


def system_info() -> dict:
    cfg = load_convert_config()
    return {
        "version": APP_VERSION,
        "app_root": get_app_root().name,
        "config_dir": get_config_dir().name,
        "export_dir": get_export_dir().name,
        "work_dir": _set_text(cfg.get("work_dir")),
        "webui": {
            "port": int(cfg.get("webui_port") or 8765),
            "lan": bool(cfg.get("webui_lan")),
            "tunnel": str(cfg.get("webui_tunnel") or "none"),
        },
        "note": "Web 端只读展示；目录、依赖与主题等设置在桌面端「全局设置」修改。",
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