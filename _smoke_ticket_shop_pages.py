# -*- coding: utf-8 -*-
"""烟测：真实 MainWindow 下跑「票务解析」+「购物解析」两个页面的端到端解析。

覆盖本轮改动：
1. 秀动关键词搜索改走 Web 客户端接口（Playwright 复用 $nuxt.$axios
   补 crpsign 签名，cityCode 全国）——状态栏应出现 showstart 的真实结果
   或「秀动全国无在售演出」，不再是死通道的 0 结果；
2. 淘宝店铺解析（mtop token 过期自动刷新 + Playwright 候选顺序改为
   PC+hotsell 优先）——应能拿到商品行；
3. 整窗截图，检查页面懒加载与窗口正常关闭。

跑法：python _smoke_ticket_shop_pages.py
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


def wait_for(fn, timeout, desc=""):
    """轮询 processEvents 等 fn() 为真。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        app.processEvents()
        if fn():
            return True
        time.sleep(0.05)
    print(f"  等待超时：{desc}", flush=True)
    return False


win = MainWindow()
win.show()
print("SMOKE: window shown", flush=True)

state = {}


def _step_tickets():
    try:
        win.show_page("tickets")
    except Exception as e:
        fails.append(f"加载票务页失败: {e}")
        return _finish()
    QTimer.singleShot(900, _run_tickets)


def _run_tickets():
    from gui_qt.pages.tickets_page import TicketsPage

    pages = win.findChildren(TicketsPage)
    check("票务页已实例化", len(pages) == 1, f"{len(pages)} 个")
    if not pages:
        return _finish()
    page = pages[0]
    check("统计标签存在", hasattr(page, "status_label"))
    # 留空 → 默认关键词（封茗囧菌）三平台搜索
    page.url_entry.setText("")
    page._parse()

    def _done():
        t = page.status_label.text()
        return t.startswith("✅") or ("失败" in t and t)

    wait_for(_done, 240, "票务解析完成")
    txt = page.status_label.text()
    rows = page.table.rowCount()
    print(f"  状态：{txt}", flush=True)
    check("票务解析有结果", rows > 0, f"{rows} 行")
    check("bilibili 通道参与", "bilibili" in txt, txt[:120])
    check("showstart 通道给出明确诊断",
          "showstart" in txt, txt[:160])
    check("秀动不再是死通道空结果",
          "秀动全国无在售演出" in txt or "showstart=" in txt, txt[:160])
    state["ticket_rows"] = rows
    QTimer.singleShot(600, _step_shopping)


def _step_shopping():
    try:
        win.show_page("shopping")
    except Exception as e:
        fails.append(f"加载购物页失败: {e}")
        return _finish()
    QTimer.singleShot(900, _run_shopping)


def _run_shopping():
    from gui_qt.pages.shopping_page import ShoppingPage

    pages = win.findChildren(ShoppingPage)
    check("购物页已实例化", len(pages) == 1, f"{len(pages)} 个")
    if not pages:
        return _finish()
    page = pages[0]
    page.url_entry.setText("")
    page._parse()

    wait_for(lambda: page.status_label.text().startswith(("✅", "⚠️", "❌")), 300,
             "淘宝店铺解析完成")
    txt = page.status_label.text()
    rows = page.table.rowCount()
    print(f"  状态：{txt}", flush=True)
    check("淘宝店铺解析有商品", rows > 0, f"{rows} 行")
    check("状态栏无 Cookie 误导文案",
          not (rows == 0 and "未配置淘宝 Cookie" in txt), txt[:100])
    if rows:
        state["shop_rows"] = rows

    img = win.grab().toImage()
    out = os.path.join(ROOT, "_smoke_ticket_shop_pages.png")
    img.save(out)
    check("整窗截图已保存", os.path.exists(out), out)
    QTimer.singleShot(400, _finish)


def _finish():
    print(f"SMOKE: done, {len(fails)} fail(s) {state}", flush=True)
    win.close()
    QTimer.singleShot(600, app.quit)


QTimer.singleShot(1200, _step_tickets)
code = app.exec()
print(f"SMOKE: exec returned {code}", flush=True)
if fails:
    print("失败项：" + "; ".join(fails), flush=True)
    sys.exit(1)
print("全部通过", flush=True)
