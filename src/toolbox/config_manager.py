"""配置管理器 — 合并为 config.json + cache.json + rename_log.log 三个文件"""
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field


# ==================== 路径工具 ====================

def get_app_root() -> Path:
    # PyInstaller 打包后：以 exe 所在目录为根（config/download 都落在 exe 旁）
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent

def _writable_dir(d: Path) -> bool:
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.write_text("1", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False

def get_config_dir() -> Path:
    cfg_dir = get_app_root() / "config"
    if not _writable_dir(cfg_dir):
        # exe 旁不可写（如安装在 Program Files）→ 落到用户 AppData，保证 Cookie/配置可保存
        base = Path(os.environ.get("APPDATA", "")) or (Path.home() / "AppData" / "Roaming")
        cfg_dir = base / "FengMingToolbox" / "config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir

# 合并后的文件路径
def _cfg_path() -> Path:
    return get_config_dir() / "config.json"

def _cache_path() -> Path:
    return get_config_dir() / "cache.json"


# ==================== 统一读写 ====================

def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _write_json(path: Path, data: Dict[str, Any]):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


# ==================== 旧文件迁移 ====================

def _migrate_if_needed():
    """首次运行时，从旧文件迁移数据到 config.json / cache.json"""
    root = get_app_root()
    cfg_dir = get_config_dir()

    # 检查是否需要迁移（旧文件存在且新文件不存在）
    need_migrate = not _cfg_path().exists() and any(
        (root / f).exists() or (cfg_dir / f).exists()
        for f in ["convert_config.json", "rename_config.json"]
    )

    if not need_migrate:
        return

    # --- 收集旧数据 ---
    # convert_config
    old_convert = {}
    for p in [root / "convert_config.json", cfg_dir / "convert_config.json"]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old_convert = json.load(f)
                break
            except Exception:
                pass

    # rename_config
    old_rename = {}
    for p in [root / "rename_config.json", cfg_dir / "rename_config.json"]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old_rename = json.load(f)
                break
            except Exception:
                pass

    # video_convert_cache
    old_vc_cache = {}
    for p in [root / ".video_convert_cache.json", cfg_dir / ".video_convert_cache.json"]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old_vc_cache = json.load(f)
                break
            except Exception:
                pass

    # rename_cache
    old_r_cache = {}
    for p in [root / "rename_cache.json", cfg_dir / "rename_cache.json"]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old_r_cache = json.load(f)
                break
            except Exception:
                pass

    # rename_history
    old_r_history = []
    for p in [root / "rename_history.json", cfg_dir / "rename_history.json"]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old_r_history = json.load(f)
                break
            except Exception:
                pass

    # rename_log
    old_log = ""
    for p in [root / "rename_log.log", cfg_dir / "rename_log.log"]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old_log = f.read()
                break
            except Exception:
                pass

    # app_settings
    old_app = {}
    for p in [root / "app_settings.json", cfg_dir / "app_settings.json"]:
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    old_app = json.load(f)
                break
            except Exception:
                pass

    # --- 写入合并文件 ---
    config_data = {
        "convert": _merge_config(DEFAULT_CONVERT_CONFIG_DICT, old_convert),
        "rename": _merge_config(DEFAULT_RENAME_CONFIG_DICT, old_rename),
        "work_dir": old_app.get("work_dir", old_convert.get("work_dir", "")),
        "subtitle_formats": old_app.get("subtitle_formats", old_convert.get("subtitle_formats", ["text", "timed_text", "lrc"])),
    }
    _write_json(_cfg_path(), config_data)

    cache_data = {
        "convert_cache": old_vc_cache if isinstance(old_vc_cache, dict) else {},
        "rename_cache": old_r_cache if isinstance(old_r_cache, dict) else {"processed_files": [], "finished_folders": []},
        "rename_history": old_r_history if isinstance(old_r_history, list) else [],
    }
    _write_json(_cache_path(), cache_data)

    # 保留日志
    log_path = get_config_dir() / "rename_log.log"
    if old_log and not log_path.exists():
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(old_log)
        except Exception:
            pass


def _merge_config(default: dict, user: dict) -> dict:
    merged = default.copy()
    for k, v in user.items():
        if k in merged:
            merged[k] = v
    return merged


# ==================== 转换配置 ====================

DEFAULT_CONVERT_CONFIG_DICT = {
    "input_extensions": [".flv", ".mkv", ".ts", ".mov", ".avi", ".wmv", ".rmvb"],
    "output_extension": ".mp4",
    "output_dir": "",
    "delete_source_after_success": False,
    "retry_count": 2,
    "retry_delay_seconds": 1,
    "enable_encode_fallback": False,
    "encode_video_codec": "libx264",
    "encode_crf": 18,
    "encode_preset": "fast",
    "encode_audio_copy": True,
    "concurrent_count": 1,
    "preview_before_start": True,
    "preserve_metadata": True,
    "preserve_timestamps": True,
    "enable_cache": True,
    "cache_filename": ".video_convert_cache.json",
    "work_dir": "",
    "subtitle_formats": ["text", "timed_text", "lrc"],
    # 字幕页开关（未注册默认键的键会在 load 时被过滤丢弃，必须登记）
    "subtitle_use_date_folder": False,
    "subtitle_use_same_dir": True,
    # 资产页本地资源「增量模式(写入索引)」
    "local_inc_enabled": False,
    # 音乐下载页「自动换源」
    "music_fallback_enabled": True,
    # 已获取页导出格式
    "export_formats": ["json", "xlsx", "md"],
    "launcher_items": [],
    "migrate_target_dir": "",
    "max_log_files": 50,
    "theme": "light",
    "theme_schedule_enabled": False,
    "theme_follow_system": False,
    "theme_light_time": "07:00",
    "theme_dark_time": "19:00",
    # 弹幕姬主播信息卡底部备注（全局设置可改，每行一条）
    "remark_info": "抖音：歆慕\n微博：歆慕XINMU\nb站：歆慕XINMU\n使用信息请标明来源",
    # 账号获取「增量模式(写入数据库)」默认勾选状态（切换即保存）
    "inc_db_checked": True,
    # FFmpeg 自定义位置（目录或 ffmpeg.exe 完整路径；空 = 使用系统 PATH）
    "ffmpeg_path": "",
    # 全局设置：文件夹锁定（锁定后拖入文件夹不改变，直到解锁）
    "work_dir_locked": False,
    "export_dir_locked": False,
}


def load_convert_config() -> Dict[str, Any]:
    data = _read_json(_cfg_path())
    user = data.get("convert", {})
    merged = DEFAULT_CONVERT_CONFIG_DICT.copy()
    for k, v in user.items():
        if k in merged:
            merged[k] = v
    # work_dir / subtitle_formats 可能在顶层
    for k in ("work_dir", "subtitle_formats"):
        if k in data:
            merged[k] = data[k]
    return merged


def save_convert_config(config: Dict[str, Any]):
    data = _read_json(_cfg_path())
    data["convert"] = {k: v for k, v in config.items()
                       if k not in ("work_dir", "subtitle_formats")}
    if "work_dir" in config:
        data["work_dir"] = config["work_dir"]
    if "subtitle_formats" in config:
        data["subtitle_formats"] = config["subtitle_formats"]
    _write_json(_cfg_path(), data)


# ==================== 转换缓存 ====================

def load_convert_cache() -> Dict[str, Any]:
    return _read_json(_cache_path()).get("convert_cache", {})


def save_convert_cache(cache: Dict[str, Any]):
    data = _read_json(_cache_path())
    data["convert_cache"] = cache
    _write_json(_cache_path(), data)


# ==================== 重命名配置 ====================

@dataclass
class RenameConfig:
    root_path: str = ""
    account_list: List[str] = field(default_factory=lambda: [
        "封茗囧菌", "封茗囧菌的啊团", "封茗囧菌咨讯站", "琉璃若水"
    ])
    live_types: List[str] = field(default_factory=lambda: [
        "聊天", "游戏", "巡演", "签售", "线下", "生日", "调试", "跨年"
    ])
    video_suffix: str = ".mp4"
    subtitle_suffixes: List[str] = field(default_factory=lambda: [
        ".srt", ".lrc", ".txt", ".ass", ".vtt"
    ])
    skip_subfolders: List[str] = field(default_factory=lambda: [
        "修复", "合并", "线下", "琉璃直播间", "temp_video_fix"
    ])
    cache_file: str = "rename_cache.json"
    log_file: str = "rename_log.log"
    history_file: str = "rename_history.json"
    subtitle_match_threshold: float = 0.6
    extract_audio: bool = False
    audio_output_mode: str = "same"
    audio_output_dir: str = ""
    extra_info: str = "【腾讯文档】封茗囧菌数据汇总库 https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr"
    append_extra_info: bool = True
    platform_list: List[str] = field(default_factory=lambda: [
        "抖音", "B站", "微博", "小红书", "快手"
    ])
    default_platform: str = "抖音"


DEFAULT_RENAME_CONFIG_DICT = {
    "account_list": ["封茗囧菌", "封茗囧菌的啊团", "封茗囧菌咨讯站", "琉璃若水"],
    "live_types": ["聊天", "游戏", "巡演", "签售", "线下", "生日", "调试", "跨年"],
    "video_suffix": ".mp4",
    "subtitle_suffixes": [".srt", ".lrc", ".txt", ".ass", ".vtt"],
    "skip_subfolders": ["修复", "合并", "线下", "琉璃直播间", "temp_video_fix"],
    "cache_file": "rename_cache.json",
    "log_file": "rename_log.log",
    "history_file": "rename_history.json",
    "subtitle_match_threshold": 0.6,
    "extract_audio": False,
    "audio_output_mode": "same",
    "audio_output_dir": "",
    "extra_info": "【腾讯文档】封茗囧菌数据汇总库 https://docs.qq.com/sheet/DRnd3bkhkQUR1aEJr",
    "append_extra_info": True,
    "platform_list": ["抖音", "B站", "微博", "小红书", "快手"],
    "default_platform": "抖音",
    "launcher_items": [],
}

_rename_config: Optional[RenameConfig] = None


def load_rename_config() -> RenameConfig:
    global _rename_config
    data = _read_json(_cfg_path())
    user = data.get("rename", {})
    if not user:
        _rename_config = RenameConfig()
        return _rename_config
    cfg = RenameConfig()
    for key, value in user.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    _rename_config = cfg
    return cfg


def save_rename_config(config: RenameConfig):
    global _rename_config
    _rename_config = config
    data = _read_json(_cfg_path())
    data["rename"] = {
        "account_list": config.account_list,
        "live_types": config.live_types,
        "video_suffix": config.video_suffix,
        "subtitle_suffixes": config.subtitle_suffixes,
        "skip_subfolders": config.skip_subfolders,
        "cache_file": config.cache_file,
        "log_file": config.log_file,
        "history_file": config.history_file,
        "subtitle_match_threshold": config.subtitle_match_threshold,
        "extract_audio": config.extract_audio,
        "audio_output_mode": config.audio_output_mode,
        "audio_output_dir": config.audio_output_dir,
        "extra_info": config.extra_info,
        "append_extra_info": config.append_extra_info,
        "platform_list": config.platform_list,
        "default_platform": config.default_platform,
    }
    _write_json(_cfg_path(), data)


def get_rename_config() -> RenameConfig:
    global _rename_config
    if _rename_config is None:
        _rename_config = load_rename_config()
    return _rename_config


# ==================== 重命名缓存 / 历史 ====================





# 保留兼容路径引用


# ==================== 路径兼容（返回None表示不再使用独立文件） ====================







# ==================== 统一导出目录 ====================

def get_export_dir() -> Path:
    """统一导出目录（默认 软件根/download，不存在时自动创建）。"""
    data = _read_json(_cfg_path())
    d = data.get("export_dir", "")
    if d:
        p = Path(d)
        try:
            p.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            pass
    return get_app_root() / "download"


def set_export_dir(path: str):
    data = _read_json(_cfg_path())
    data["export_dir"] = path
    _write_json(_cfg_path(), data)


def set_ffmpeg_path(path: str):
    """全局设置「FFmpeg 位置」：目录或 ffmpeg.exe 完整路径，空串 = 使用系统 PATH。"""
    data = _read_json(_cfg_path())
    data.setdefault("convert", {})["ffmpeg_path"] = path
    _write_json(_cfg_path(), data)


# 导出分类子文件夹名（按文件类型路由）
EXPORT_CATEGORIES = {
    "subtitle": "字幕文件",
    "music": "歌曲文件",
    "music_info": "音乐信息",
    "tracker": "账号追踪数据",
    "music_tracker": "音乐账号数据",
    "video_data": "已获取数据",
    "danmaku": "弹幕文件",
    "danmaku_bili": "弹幕文件B站",
}


def get_export_subdir(category: str) -> Path:
    """返回指定分类的导出子文件夹（自动创建）。category 见 EXPORT_CATEGORIES。"""
    root = get_export_dir()
    sub = EXPORT_CATEGORIES.get(category, "")
    path = root / sub if sub else root
    path.mkdir(parents=True, exist_ok=True)
    if category == "video_data":
        _migrate_video_data_layout(path)
    return path


_VD_LAYOUT_MIGRATED = False
_VD_LAYOUT_LOCK = threading.Lock()


def _migrate_video_data_layout(video_dir: Path):
    """旧目录结构 → 「平台_文件 / 平台_媒体」新结构（首次访问时一次性迁移）。

    旧：已获取数据/douyin（数据导出）+ 已获取数据/抖音（媒体，内含作者子目录）
    新：已获取数据/抖音_文件 + 已获取数据/抖音_媒体
    目标已存在时逐项合并，重名冲突加 `_旧` 后缀；线程安全，失败静默不阻塞主流程。
    """
    global _VD_LAYOUT_MIGRATED
    if _VD_LAYOUT_MIGRATED:
        return
    with _VD_LAYOUT_LOCK:
        if _VD_LAYOUT_MIGRATED:
            return
        try:
            import shutil
            from toolbox.utils import PLATFORM_CN, DATA_DIR_SUFFIX, MEDIA_DIR_SUFFIX
            for en, cn in PLATFORM_CN.items():
                for suffix, old in ((DATA_DIR_SUFFIX, en), (MEDIA_DIR_SUFFIX, cn)):
                    src = video_dir / old
                    dst = video_dir / f"{cn}{suffix}"
                    if not src.is_dir() or src == dst:
                        continue
                    if not dst.exists():
                        src.rename(dst)
                        continue
                    if src.resolve() == dst.resolve():
                        continue
                    for item in src.iterdir():
                        target = dst / item.name
                        if target.exists():
                            stem, ext = os.path.splitext(item.name)
                            target = dst / f"{stem}_旧{ext}"
                        shutil.move(str(item), str(target))
                    shutil.rmtree(src, ignore_errors=True)
        except Exception:
            pass  # 迁移失败不影响导出/下载主流程
        _VD_LAYOUT_MIGRATED = True


def ensure_export_structure():
    """确保导出根目录下所有分类子文件夹存在。"""
    for sub in EXPORT_CATEGORIES.values():
        (get_export_dir() / sub).mkdir(parents=True, exist_ok=True)


# ==================== 工作目录 ====================

def set_work_dir(path: str) -> bool:
    """设置默认工作目录并保存。"""
    cfg = load_convert_config()
    cfg["work_dir"] = path
    save_convert_config(cfg)
    return True


def has_date_subfolders(directory: str) -> bool:
    """检查目录下是否存在日期格式（20XXXXXX）的子文件夹。"""
    import re
    pat = re.compile(r"^(20\d{6})")
    base = Path(directory)
    if not base.is_dir():
        return False
    for p in base.iterdir():
        if p.is_dir() and pat.match(p.name):
            return True
    return False


# ==================== 初始化 ====================

def init_all_configs():
    # 迁移旧文件
    _migrate_if_needed()

    # 确保 config.json 存在
    if not _cfg_path().exists():
        _write_json(_cfg_path(), {
            "convert": DEFAULT_CONVERT_CONFIG_DICT.copy(),
            "rename": DEFAULT_RENAME_CONFIG_DICT.copy(),
            "work_dir": "",
            "subtitle_formats": ["text", "timed_text", "lrc"],
        })

    # 确保 cache.json 存在
    if not _cache_path().exists():
        _write_json(_cache_path(), {
            "convert_cache": {},
            "rename_cache": {"processed_files": [], "finished_folders": []},
            "rename_history": [],
        })

    # 确保日志文件存在
    log_path = get_config_dir() / "rename_log.log"
    if not log_path.exists():
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
        except Exception:
            pass

    # 确保导出目录 + 分类子文件夹存在
    ensure_export_structure()

    # 加载配置
    load_convert_config()
    load_rename_config()


