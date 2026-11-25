"""WebUI 系统设置（L3）：工作目录 / 导出目录 / 外观主题。

与桌面端共用同一份配置；依赖组件由桌面端管理，这里只提供目录与主题的读写。
"""

from pathlib import Path

from toolbox.config_manager import (get_export_dir, load_convert_config,
                                   save_convert_config, set_export_dir)

_THEMES = ("light", "dark", "auto")


def _dir_text(value) -> str:
    return str(value or "").strip()


def _check_dir(text: str, label: str) -> Path:
    path = Path(text)
    if not path.is_absolute():
        raise ValueError(f"{label}请填写完整路径")
    if path.parent == path:
        raise ValueError(f"{label}不要使用磁盘根目录，请选择具体文件夹")
    if not path.is_dir():
        raise ValueError(f"{label}不存在或不是文件夹")
    return path


def system_settings() -> dict:
    """当前目录与主题设置（只读）。"""
    cfg = load_convert_config()
    return {
        "work_dir": _dir_text(cfg.get("work_dir")),
        "export_dir": str(get_export_dir()),
        "work_dir_locked": bool(cfg.get("work_dir_locked")),
        "export_dir_locked": bool(cfg.get("export_dir_locked")),
        "theme": str(cfg.get("theme") or "light"),
        "theme_follow_system": bool(cfg.get("theme_follow_system")),
        "note": "目录与主题与桌面端共用同一配置；依赖组件由桌面端管理，"
                "Web 端不提供安装或卸载。",
    }


def save_system_dirs(work_dir=None, export_dir=None,
                     confirmed: bool = False) -> dict:
    """保存工作目录 / 导出目录（空值表示不修改）。"""
    if not confirmed:
        raise ValueError("请确认后再保存目录设置")
    cfg = load_convert_config()
    work = _dir_text(work_dir)
    export = _dir_text(export_dir)
    if not work and not export:
        raise ValueError("请至少填写一个目录")
    changed = {}
    if work:
        if cfg.get("work_dir_locked"):
            raise ValueError("工作目录已锁定，请在桌面端解锁后再修改")
        path = _check_dir(work, "工作目录")
        cfg["work_dir"] = str(path)
        changed["work_dir"] = str(path)
    if export:
        if cfg.get("export_dir_locked"):
            raise ValueError("导出目录已锁定，请在桌面端解锁后再修改")
        path = _check_dir(export, "导出目录")
        changed["export_dir"] = str(path)
    if "work_dir" in changed:
        save_convert_config(cfg)
    if "export_dir" in changed:
        set_export_dir(changed["export_dir"])
    data = system_settings()
    data["changed"] = changed
    data["ok"] = True
    return data


def save_system_theme(theme) -> dict:
    """保存外观主题：light / dark / auto（跟随系统）。"""
    value = str(theme or "").strip()
    if value not in _THEMES:
        raise ValueError("主题只支持 light / dark / auto")
    cfg = load_convert_config()
    if value == "auto":
        cfg["theme_follow_system"] = True
    else:
        cfg["theme_follow_system"] = False
        cfg["theme"] = value
    save_convert_config(cfg)
    data = system_settings()
    data["changed"] = {"theme": value}
    data["ok"] = True
    return data
