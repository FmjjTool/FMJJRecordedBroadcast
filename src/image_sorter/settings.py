"""图片筛选分类工具配置：config.json 顶层「image_sorter」节读写封装。

替代源项目的 config.ini + AppConfig + runtime_paths：
- 全部配置键存 config.json「image_sorter」节（独立于 convert/rename 节，
  不受 convert 节默认字典过滤影响），load 时逐键合并默认值，缺键自动补齐
- 路径统一基于 config_manager.get_config_dir() / "image_sorter"
  （源项目 cache/ 目录迁移至此：scan/ + thumbnails/ + cache.db）
"""

import json
from pathlib import Path
from typing import Any, Dict, List

from toolbox.config_manager import get_config_dir

from image_sorter._log import logger


# 配置默认值（源项目 DEFAULT_CONFIG 迁移：删 dark_mode——跟随工具箱全局主题）
DEFAULT_SETTINGS: Dict[str, Any] = {
    "work_dir": "",
    "sort_order": "path",           # path/create_time/random
    "view_mode": "date",            # default/date/random（日期分组为默认模式）
    "random_sample_size": 50,
    "autoplay_video": False,
    "autoplay_live_photo": False,
    "duplicate_keep": "latest",     # latest/manual
    "filter_screened_files": True,
    "file_operation_mode": "copy",  # copy/move
    "auto_rename_enabled": False,
    "auto_rename_pattern": "{source_name}",
    "auto_rename_fields": [],
    "compress_preset": "均衡(推荐)",
    "compress_options": {},
    "compress_image_preset": "均衡(推荐)",
    "compress_image_options": {},
    "compress_video_preset": "均衡(推荐)",
    "compress_video_options": {},
    "compress_cpu_limit_percent": 55,
    "source_folders": [],
    "classified_target_folders": [],
    "rename_templates": {},          # 重命名模板 {名称: 模板内容}
    "rename_tags": [],               # 分类词列表（_前缀格式）
    "right_panel": 1,               # 右栏（信息面板）默认标签页
    "media_viewer": 2,              # 中栏预览默认标签页
    "folder_panel": 1,              # 左栏文件夹面板默认标签页
    "shortcuts": {},                # 快捷键映射覆盖（空 = 全默认，见 shortcut_manager）
}


def _cfg_file() -> Path:
    return get_config_dir() / "config.json"


def _read_full_config() -> Dict[str, Any]:
    try:
        with open(_cfg_file(), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_full_config(data: Dict[str, Any]):
    try:
        with open(_cfg_file(), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.log_exception(e, "保存图片筛选配置失败")


def load_settings() -> Dict[str, Any]:
    """读取配置（合并默认值；「image_sorter」节缺失时返回全默认，不回写文件）。"""
    section = _read_full_config().get("image_sorter")
    merged = DEFAULT_SETTINGS.copy()
    if isinstance(section, dict):
        for k, v in section.items():
            if k in merged:
                merged[k] = v
    return merged


def save_settings(settings: Dict[str, Any]):
    """整节保存（只认默认字典里的键，未知键丢弃防污染）。"""
    payload = {k: v for k, v in settings.items() if k in DEFAULT_SETTINGS}
    data = _read_full_config()
    data["image_sorter"] = payload
    _write_full_config(data)


def get_setting(key: str, default: Any = None) -> Any:
    value = load_settings().get(key, DEFAULT_SETTINGS.get(key))
    if default is not None and value is None:
        return default
    return value


def set_setting(key: str, value: Any):
    if key not in DEFAULT_SETTINGS:
        return
    settings = load_settings()
    settings[key] = value
    save_settings(settings)
    logger.log_operation("图片筛选配置更新", f"{key} = {value}")


# ==================== 列表 / 字典便捷读写 ====================

def get_str(key: str, default: str = "") -> str:
    value = get_setting(key, default)
    return value if isinstance(value, str) else default


def get_bool(key: str, default: bool = False) -> bool:
    value = get_setting(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes")
    return default


def get_int(key: str, default: int = 0) -> int:
    try:
        return int(get_setting(key, default))
    except (TypeError, ValueError):
        return default


def get_list(key: str) -> List[str]:
    value = get_setting(key)
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def set_list(key: str, value: List[str]):
    set_setting(key, [str(item).strip() for item in value if str(item).strip()])


def get_dict(key: str) -> Dict[str, Any]:
    value = get_setting(key)
    return value if isinstance(value, dict) else {}


def set_dict(key: str, value: Dict[str, Any]):
    set_setting(key, {str(k): v for k, v in (value or {}).items()})


# ==================== 路径（替代源项目 runtime_paths） ====================

def get_data_root_dir() -> Path:
    """可持久化数据根目录：config/image_sorter/（跟随工具箱 config 落点，
    exe 旁不可写时自动落到 AppData，由 config_manager 统一处理）。"""
    root = get_config_dir() / "image_sorter"
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_cache_root_dir() -> Path:
    """缓存根目录（scan / thumbnails / cache.db 全在此目录下）。"""
    return get_data_root_dir()


def is_directory_writable(path: Path) -> bool:
    """检测目录是否可写（实际写入探针，Windows 上 os.access 对 W_OK 不可靠）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_probe.tmp"
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        try:
            probe.unlink()
        except Exception:
            pass
        return True
    except Exception:
        return False
