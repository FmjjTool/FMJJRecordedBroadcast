"""冒烟截图：本地资源页（深度/文件夹/锁定）真实渲染。

跑法：python _smoke_asset_local_ui.py
输出：_smoke_asset_local.png
"""
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
ROOT = os.path.dirname(os.path.abspath(__file__))

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from gui_qt.pages.asset import AssetPage


class _StubApp:
    def toast(self, *a, **k):
        pass

    def __getattr__(self, name):
        return lambda *a, **k: None


page = AssetPage(_StubApp())
page.resize(1000, 640)
page.show()
app.processEvents()

TARGET = r"D:/封茗囧菌/_重要文件_"
if os.path.isdir(TARGET):
    page.dir_entry.setText(TARGET)
    page._folder = TARGET
    page.depth_sel.setCurrentIndex(page.depth_sel.findData(-1))
    page._scan()
    deadline = time.time() + 60
    while time.time() < deadline:
        app.processEvents()
        if page._all_files and "⏳" not in page.local_status.text():
            break
        time.sleep(0.05)

# 切到本地资源 tab（0=已获取资源 1=数据库浏览 2=本地资源）
page.tabs.setCurrentIndex(2)
app.processEvents()
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)

out = os.path.join(ROOT, "_smoke_asset_local.png")
page.grab().toImage().save(out)
print("状态行：", page.local_status.text())
print("文件夹下拉选项数：", page.local_dir_sel.count(),
      [page.local_dir_sel.itemText(i) for i in range(min(5, page.local_dir_sel.count()))])
print("列表布局项：", page.local_layout.count())
print("截图：", out, os.path.exists(out))
page.hide()
