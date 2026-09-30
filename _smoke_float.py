"""离屏冒烟测试：模块导入 + 下载浮窗多线程事件流渲染。"""
import importlib
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

app = QApplication([])

mods = [
    "gui_qt.download_float", "gui_qt.download_panel", "gui_qt.download_ops",
    "gui_qt.main_window",
    "gui_qt.pages.info_results", "gui_qt.pages.info_batch",
    "gui_qt.pages.info_accounts", "gui_qt.pages.info_parse",
    "gui_qt.pages.asset", "gui_qt.pages.netease", "gui_qt.pages.shopping_page",
    "gui_qt.pages.wechat_article", "gui_qt.pages.video_comments",
    "gui_qt.pages.bili_emoji", "gui_qt.pages.bili_garb",
    "media_downloader", "shopping", "toolbox.comments_common",
    "platforms.wechat_article", "bili.download",
]
for m in mods:
    importlib.import_module(m)
print("IMPORTS OK (%d modules)" % len(mods))

from gui_qt.download_float import get_float

win = get_float()
card = win.add_batch("测试批次", total=6, threads=3)


def worker():
    for i in range(5):
        card.handle_event("start", f"file_{i}.mp4")
        for pct in (30, 70, 100):
            card.handle_event("bytes", f"file_{i}.mp4", pct * 1000, 100000)
        card.handle_event("done", f"file_{i}.mp4", True)
    card.handle_event("skip", "skipped.jpg")
    card.finish("✅ 完成 6 条")


t = threading.Thread(target=worker)
t.start()
t.join()
for _ in range(30):
    app.processEvents()
    time.sleep(0.005)

assert len(card.panel._rows) == 3, f"expected 3 slots, got {len(card.panel._rows)}"
head = card.panel._batch_head.text()
summary = card.summary_label.text()
print("batch head:", head)
print("summary:", summary)
assert "6/6" in head, head
assert "完成" in summary, summary
assert card.cancel_btn is None or not card.cancel_btn.isVisibleTo(win), "finish() should hide cancel btn"

card2 = win.add_batch("第二批次")
card2.handle_event("plan", "segments", 1, 4)
card2.handle_event("start", "single.mp4")
card2.handle_event("seg", 0, 50, 100)
card2.handle_event("seg", 1, 100, 100)
card2.handle_event("bytes", "single.mp4", 64000, 128000)
card2.handle_event("done", "single.mp4", True)
for _ in range(20):
    app.processEvents()
    time.sleep(0.005)
stats = card2.panel._single_stats.text()
print("second card stats:", stats)
assert "线程" in stats, stats
assert win.isVisible(), "float should stay visible with cards"

win.remove_card(card)
win.remove_card(card2)
for _ in range(10):
    app.processEvents()
    time.sleep(0.005)
assert not win.isVisible(), "float should hide when all cards removed"

print("FLOAT SMOKE OK")
