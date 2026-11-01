# -*- coding: utf-8 -*-
"""烟测：真实 MainWindow 下加载「资产管理」页，验证本地资源逐级读取 + 目录锁定。

与 main.py 相同的初始化路径（日志/主题/离屏），然后：
1. show_page("asset") —— 走真实懒加载 + on_show；
2. 切到「本地资源」Tab，锁定 D:/封茗囧菌/_重要文件_ 并扫描；
3. 断言：深度下拉存在且为「全部层级」、锁定后路径只读、扫描结果 > 100 个资源、
   「文件夹」下拉有子目录、分组标题出现；
4. 整窗截图 _smoke_asset_main_window.png；
5. 关闭窗口，检查进程能正常退出。

跑法：python _smoke_asset_main_window.py
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as _main  # noqa: F401

_main._headless_io()
_main._suppress_child_consoles()

from toolbox.config_manager import init_all_configs  # noqa: E402

init_all_configs()

from PySide6.QtCore import QLoggingCategory, Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
QLoggingCategory.setFilterRules("qt.text.font.db.info=false\nqt.text.font.db.warning=false")
_main._install_qt_noise_filter()

app = QApplication(sys.argv)
app.setStyle("Fusion")
from gui_qt import theme  # noqa: E402

theme.init_theme()
theme.install_auto_remap()
theme.apply_theme(app)

from gui_qt.main_window import MainWindow  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (f"  {extra}" if extra else ""), flush=True)
    if not cond:
        fails.append(name)


win = MainWindow()
win.show()
print("SMOKE: window shown", flush=True)

TARGET = r"D:/封茗囧菌/_重要文件_"


def _step_pages():
    try:
        win.show_page("asset")
    except Exception as e:
        print(f"SMOKE: page load error: {e}", flush=True)
        fails.append(f"加载资产管理页失败: {e}")
        return _finish()
    QTimer.singleShot(1200, _step_local)


def _step_local():
    from gui_qt.pages.asset import AssetPage

    pages = win.findChildren(AssetPage)
    check("资产管理页已实例化", len(pages) == 1, f"{len(pages)} 个")
    if not pages:
        return _finish()
    page = pages[0]
    check("深度下拉存在（默认全部层级）",
          hasattr(page, "depth_sel") and page.depth_sel.currentData() == -1,
          str(page.depth_sel.currentData()) if hasattr(page, "depth_sel") else "-")
    check("文件夹下拉存在", hasattr(page, "local_dir_sel"))
    check("锁定按钮存在", hasattr(page, "dir_lock_btn") and hasattr(page, "pin_dir_btn"))

    page.tabs.setCurrentIndex(2)
    if not os.path.isdir(TARGET):
        check(f"测试目录不存在：{TARGET}", False)
        return _finish()
    page.dir_entry.setText(TARGET)
    page._folder = TARGET
    page._set_dir_locked(True)
    page._scan()

    deadline = time.time() + 60
    while time.time() < deadline:
        app.processEvents()
        if page._all_files and "⏳" not in page.local_status.text():
            break
        time.sleep(0.03)
    getattr(app, "processEvents")()
    check("锁定后路径只读", page.dir_entry.isReadOnly())
    check("扫描逐级读到子文件夹", len(page._all_files) > 100, f"{len(page._all_files)} 个资源")
    check("文件夹下拉含子目录", page.local_dir_sel.count() > 10,
          f"{page.local_dir_sel.count()} 项")
    headers = [page.local_layout.itemAt(i).widget().text()
               for i in range(page.local_layout.count())
               if page.local_layout.itemAt(i).widget() is not None
               and hasattr(page.local_layout.itemAt(i).widget(), "text")
               and page.local_layout.itemAt(i).widget().text().startswith("📁")]
    check("列表出现文件夹分组标题", len(headers) >= 1, str(headers[:3]))
    print("  状态行：" + page.local_status.text(), flush=True)

    # download 分类目录 → 文件清单视图（用户要的「按 download 下这些文件夹筛选」）
    page.tabs.setCurrentIndex(0)
    deadline = time.time() + 60     # 首次列表扫描（骨架 → 真实数据）跑完下拉才有目录项
    while time.time() < deadline:
        app.processEvents()
        if page.folder_sel.count() > 1:
            break
        time.sleep(0.03)
    idx = page.folder_sel.findData("dir:弹幕文件")
    check("文件夹下拉含 download 分类目录",
          page.folder_sel.findData("dir:弹幕文件") > 0,
          str([page.folder_sel.itemData(i) for i in range(min(6, page.folder_sel.count()))]))
    if idx > 0:
        page.folder_sel.setCurrentIndex(idx)
        deadline = time.time() + 30
        while time.time() < deadline:
            app.processEvents()
            if page._view_mode == "files" and page._plain_files:
                break
            time.sleep(0.03)
        check("切到文件清单视图", page._view_mode == "files", page._view_mode)
        check("列出弹幕文件目录内容", len(page._plain_files) > 0, f"{len(page._plain_files)} 个")
        check("批量下载按钮在文件视图置灰", not page.batch_dl_btn.isEnabled())
        page.folder_sel.setCurrentIndex(0)
        deadline = time.time() + 30
        while time.time() < deadline:
            app.processEvents()
            if page._view_mode == "entries":
                break
            time.sleep(0.03)
        check("切回条目视图", page._view_mode == "entries", page._view_mode)

    img = win.grab().toImage()
    out = os.path.join(ROOT, "_smoke_asset_main_window.png")
    img.save(out)
    check("整窗截图已保存", os.path.exists(out), out)
    QTimer.singleShot(300, _finish)


def _finish():
    print(f"SMOKE: done, {len(fails)} fail(s)", flush=True)
    win.close()
    QTimer.singleShot(600, app.quit)


QTimer.singleShot(1200, _step_pages)
code = app.exec()
print(f"SMOKE: exec returned {code}", flush=True)
if fails:
    print("失败项：" + "; ".join(fails), flush=True)
    sys.exit(1)
print("全部通过", flush=True)
