# -*- coding: utf-8 -*-
"""烟测：主窗口关闭后进程能否真正退出（复现 CLI 窗口残留问题）。

流程：
1. 离屏启动，与 main.py 相同的初始化（子进程补丁 + 日志 + 主题）
2. 构造 MainWindow，懒加载关键页面（账号获取 / 抖音弹幕姬 / B站弹幕姬 / 继续操作）
3. QTimer 关闭主窗口（走真实 closeEvent → 各页 shutdown）
4. app.exec() 返回后打印标记，进程应立刻结束
5. 外层看门狗：脚本由 _run_exit_smoke.bat 带 timeout 运行，超时 = 进程挂死

判定：日志里出现 "SMOKE: exec returned" 且进程在 60s 内退出码 0。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 与 main.py 一致的初始化（不改被测代码本身）
import main as _main  # noqa: F401  (仅触发 sys.path 注入与模块加载，不执行 main())
_main._headless_io()
_main._suppress_child_consoles()

from toolbox.config_manager import init_all_configs
init_all_configs()

try:
    from app_logger import setup_logging
    from toolbox.config_manager import load_convert_config
    setup_logging(max_files=int(load_convert_config().get("max_log_files", 50) or 50),
                  console=False)
except Exception:
    pass

from PySide6.QtCore import Qt, QTimer, QLoggingCategory
from PySide6.QtWidgets import QApplication

QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
QLoggingCategory.setFilterRules("qt.text.font.db.info=false\nqt.text.font.db.warning=false")
_main._install_qt_noise_filter()

app = QApplication(sys.argv)
app.setStyle("Fusion")
icon_path = os.path.join(ROOT, "icon.ico")
if os.path.exists(icon_path):
    from PySide6.QtGui import QIcon
    app.setWindowIcon(QIcon(icon_path))
from gui_qt import theme
theme.init_theme()
theme.install_auto_remap()
theme.apply_theme(app)

from gui_qt.main_window import MainWindow

win = MainWindow()
win.show()
print("SMOKE: window shown", flush=True)


def _open_pages():
    """懒加载用户常用页（账号获取 / 抖音弹幕姬 / B站弹幕姬 / 继续操作 / 已获取）。"""
    try:
        for key in ("info_accounts", "test_page", "bili_danmaku",
                    "toolbox_launcher", "info_results"):
            win.show_page(key)
            print(f"SMOKE: page {key} loaded", flush=True)
    except Exception as e:
        print(f"SMOKE: page load error: {e}", flush=True)
    QTimer.singleShot(1500, _close)


def _close():
    print("SMOKE: closing main window", flush=True)
    win.close()
    QTimer.singleShot(2000, _really_quit)


def _really_quit():
    print("SMOKE: requesting quit", flush=True)
    app.quit()


QTimer.singleShot(1500, _open_pages)

code = app.exec()
print(f"SMOKE: exec returned {code}", flush=True)
# 不用 os._exit：要复现真实退出路径（atexit / 解释器收尾 / 守护线程清理），
# 若 exec 返回后进程仍不退出，外层 timeout 会捕获到挂死点
sys.stdout.flush()
sys.stderr.flush()
sys.exit(code)
