"""冒烟测试：死代码清理后全量模块可导入 + 已删符号无残留引用。"""
import importlib
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

failures = []


def check(name, cond):
    print(("[OK] " if cond else "[FAIL] ") + name)
    if not cond:
        failures.append(name)


# 1) 已删符号全仓无残留引用（文本级）
import subprocess

DEAD = [
    "douyin_browser_login", "platform_downloadable", "_shop_base",
    "parse_room_id_input", "fetch_profile", "open_in_explorer",
    "probe_duration", "find_ffprobe", "set_speed", "create_cover_module",
    "_date_range_desc", "_browse_icon", "_clear_icon", "clear_selection",
    "get_auto_rename_config", "get_current_file", "refresh_list",
    "clear_all", "clear_classified_md5_cache", "mark_md5_classified",
    "set_base_dir", "get_files_to_keep", "is_duplicate",
    "copy_files_batch", "build_hash_set", "get_history_count",
    "mark_classified_md5", "clear_classified_md5",
]
r = subprocess.run(
    ["git", "grep", "-n", "-E", "\\b(" + "|".join(DEAD) + ")\\b"],
    cwd=os.path.dirname(os.path.abspath(__file__)),
    capture_output=True, text=True)
leftover = [l for l in (r.stdout or "").splitlines() if l.strip()]
check(f"已删符号无残留引用（{len(leftover)} 处）", not leftover)
for l in leftover[:10]:
    print("    残留: " + l)

# 2) 关键模块导入（含被改文件与其依赖方）
from PySide6.QtWidgets import QApplication
app = QApplication([])

MODULES = [
    "auth", "shopping", "bili.api", "toolbox.skills", "toolbox.bili_live",
    "toolbox.live_video", "platforms.xiaohongshu", "music.downloader",
    "image_sorter.core.cache_manager", "image_sorter.core.sqlite_cache",
    "image_sorter.core.duplicate_detector", "image_sorter.core.file_processor",
    "image_sorter.core.media_hash_manager", "image_sorter.core.operation_history",
    "gui_qt.pages.cover_maker", "gui_qt.pages.info_accounts",
    "gui_qt.pages.launcher", "gui_qt.pages.image_sorter_folder_panel",
    "gui_qt.pages.image_sorter_widgets",
]
for m in MODULES:
    try:
        importlib.import_module(m)
        ok = True
    except Exception as e:
        ok = False
        print(f"    {m}: {e}")
    check(f"导入 {m}", ok)

# 3) 类完整性抽查：删除后类仍可用
from image_sorter.core.cache_manager import CacheManager
from image_sorter.core.duplicate_detector import DuplicateDetector
from toolbox.live_video import VideoStreamPlayer
check("CacheManager 可实例化（临时目录）",
      CacheManager() is not None)
check("DuplicateDetector 保留核心方法",
      hasattr(DuplicateDetector, "detect_duplicates") and hasattr(DuplicateDetector, "duplicate_groups"))
check("VideoStreamPlayer 保留 seek/_speed 机制（start 未破坏）",
      hasattr(VideoStreamPlayer, "seek") and hasattr(VideoStreamPlayer, "start"))
from toolbox import skills
check("skills 模块核心函数保留",
      hasattr(skills, "list_skills") and hasattr(skills, "skill_total_size_text"))
from auth import get_cookie, set_cookie, set_cookie_enabled
check("auth 核心函数保留", all(callable(f) for f in (get_cookie, set_cookie, set_cookie_enabled)))

print()
if failures:
    print(f"结果：{len(failures)} 项失败 -> {failures}")
    sys.exit(1)
print("结果：全部通过")
