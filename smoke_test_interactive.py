"""扩展冒烟测试：无网络交互方法（模式切换/全选反选/清空/渲染）。"""

import os
import sys
import traceback

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

from PySide6.QtWidgets import QApplication

errors = []


def check(name, fn):
    try:
        fn()
        print(f"  OK  {name}")
    except Exception:
        errors.append((name, traceback.format_exc()))
        print(f"  FAIL {name}")


def main():
    app = QApplication(sys.argv)
    from gui_qt.main_window import MainWindow
    win = MainWindow()

    # netease：四种模式切换 + 全选/反选
    win.show_page("netease")
    np = win.pages["netease"]
    for m in ("song", "playlist", "album", "search"):
        check(f"netease._switch_mode({m})", lambda m=m: np._switch_mode(m))
    # 搜索结果渲染（无网络，直接调渲染）
    check("netease._render_songs", lambda: np._render_songs([
        {"id": 1, "name": "测试歌", "artists": "歌手A", "album": "专辑B", "duration": 210000},
        {"id": 2, "name": "测试歌2", "artists": "歌手B", "album": "", "duration": 0},
    ]))
    check("netease._sel(all)", lambda: np._sel("all"))
    check("netease._sel(invert)", lambda: np._sel("invert"))
    check("netease._sel(none)", lambda: np._sel("none"))
    check("netease._render_albums", lambda: np._render_albums([
        {"id": 10, "name": "专辑X", "artist": {"name": "歌手C"}, "picUrl": ""},
    ]))
    check("netease._render_single_song", lambda: np._render_single_song(
        {"id": 5, "name": "单曲", "artists": "A", "album": "B", "duration": 1, "_lyric": "[00:01] 测试歌词", "_picUrl": ""}))

    # music_info：列选择（先造列表项）
    win.show_page("music_info")
    mp = win.pages["music_info"]
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QListWidgetItem
    lst = mp.columns["kugou"]["list"]
    for i in range(3):
        it = QListWidgetItem(f"歌{i}")
        it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
        it.setCheckState(Qt.Unchecked)
        lst.addItem(it)
    mp.columns["kugou"]["songs"] = [None] * 3
    check("music_info._col_select(all)", lambda: mp._col_select("kugou", "all"))
    check("music_info._col_select(invert)", lambda: mp._col_select("kugou", "invert"))
    check("music_info._col_select(none)", lambda: mp._col_select("kugou", "none"))

    # music_unlock：文件列表
    win.show_page("unlock_music")
    up = win.pages["unlock_music"]
    check("music_unlock._clear_files", up._clear_files)
    up.files = [r"d:\fake\a.ncm", r"d:\fake\b.kgm"]
    check("music_unlock._render_files", up._render_files)
    check("music_unlock.on_return_press(空列表提示)", up.on_return_press)
    up.files = []
    up._render_files()

    # settings：信息刷新
    win.show_page("global_settings")
    sp = win.pages["global_settings"]
    check("settings.on_show", sp.on_show)
    check("settings._refresh_info", sp._refresh_info)
    check("settings._check_ffmpeg", sp._check_ffmpeg)

    # test_page：清空 + 断开（未连接状态）
    win.show_page("test_page")
    tp = win.pages["test_page"]
    check("test_page._clear_all", tp._clear_all)
    check("test_page._disconnect", tp._disconnect)
    check("test_page._tick(未连接直接返回)", tp._tick)

    # dycast：环境检查
    win.show_page("dycast")
    dp = win.pages["dycast"]
    check("dycast._check_env", dp._check_env)

    print()
    if errors:
        print(f"共 {len(errors)} 项失败:")
        for name, tb in errors:
            print(f"\n===== {name} =====\n{tb}")
        return 1
    print("扩展冒烟测试全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
