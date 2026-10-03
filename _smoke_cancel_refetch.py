# -*- coding: utf-8 -*-
"""烟测：①账号获取取消后可立即重新获取（暂停→取消→重取）
②有采集任务卡死时退出，进程也能立即结束（CLI 窗口残留根治）

- 打桩 platforms.get_parser / URLRecognizer.recognize：第一个账号模拟
  「卡死的在飞请求」（sleep 600s），其余账号正常返回
- 流程：开始获取 → 暂停 → 继续 → 取消 → 断言 UI 已释放 → 立即重新获取
- 断言全过后关闭窗口走 main.py 同款退出兜底（atexit + logging + os._exit），
  卡死的 executor 工作线程被一并带走，进程 60s 内必须退出

注意：stdout 被 app_logger 接管，断言输出会进 logs/app_*.log 最新一份。
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as _main
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

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication
QApplication.setAttribute(Qt.AA_ShareOpenGLContexts, True)
_main._install_qt_noise_filter()

app = QApplication(sys.argv)
app.setStyle("Fusion")
from gui_qt import theme
theme.init_theme()
theme.install_auto_remap()
theme.apply_theme(app)

from gui_qt.main_window import MainWindow

# ==================== 打桩：模拟卡死的在飞请求 ====================
import platforms

class _StubAcc:
    def __init__(self, url, name):
        self.url = url
        self.name = name
        self.kind = "video"
        self.group_ids = []

class _FakeParser:
    def fetch_user_posts(self, url, count=10, **kw):
        if "HANG" in url:
            time.sleep(600)   # 模拟卡死的网络请求（在飞账号）
        return []

_fake = _FakeParser()
_real_recognize = platforms.URLRecognizer.recognize
platforms.URLRecognizer.recognize = staticmethod(
    lambda url: "douyin" if "douyin.com" in url else _real_recognize(url))
platforms.get_parser = lambda plat: _fake
import gui_qt.pages.info_accounts as _ia
# _collect_accounts_worker 函数体内 from platforms import ... 取的是模块属性，
# 打桩 platforms 即可；_ia 里另 import 的 infer_kind_from_url 保持原样

# ==================== 启动 ====================
win = MainWindow()
win.show()
page = win.pages.get("info_accounts")
if page is None:
    win.show_page("info_accounts")
    page = win.pages["info_accounts"]

STUB = [_StubAcc("https://www.douyin.com/user/HANG", "卡死账号"),
        _StubAcc("https://www.douyin.com/user/ok1", "正常1"),
        _StubAcc("https://www.douyin.com/user/ok2", "正常2")]
page._resolve_target_accounts = lambda: (STUB, None)

def _log(msg):
    print(f"SMOKE: {msg}", flush=True)

assert page._fetch_round == 0

# ---------- 轮 1：开始获取 → 暂停 → 继续 → 取消 ----------
page._fetch()
assert page._fetching is True, "获取应处于进行中"
assert page._fetch_round == 1
_log("round1 started")

page._toggle_pause()          # 暂停（门闩 clear）
assert not page._pause_evt.is_set()
page._toggle_pause()          # 继续
assert page._pause_evt.is_set()
_log("pause/resume OK")

page._cancel_fetch()           # 取消 —— 应立即释放 UI
assert page._fetching is False, "BUG2：取消后 _fetching 必须立即复位"
assert page.fetch_btn.isEnabled(), "BUG2：取消后获取按钮必须立即可用"
assert page._cancel_evt.is_set()
_log("cancel released UI immediately")

# ---------- 轮 2：立即重新获取（此时轮 1 的卡死账号还在后台 sleep）----------
page._fetch()
assert page._fetching is True, "BUG2：取消后必须能立即开始新一轮"
assert page._fetch_round == 2
assert not page._cancel_evt.is_set(), "新轮取消标记必须是干净的"
assert page._pause_evt.is_set(), "新轮暂停门闩必须是放行态"
_log("round2 started while round1 worker still stuck in background")

# 旧轮（轮 1）的取消标记保持 set：排队中的旧账号照样跳过
# （轮 1 用旧事件对象，新轮 clear 不会复活旧轮队列）

# ---------- 轮 2 也取消 → 退出 ----------
QTimer.singleShot(500, lambda: (page._cancel_fetch(), _log("round2 cancelled")))
QTimer.singleShot(1500, lambda: (_log("closing window"), win.close()))
QTimer.singleShot(3500, lambda: (_log("force quit"), app.quit()))

code = app.exec()
_log(f"exec returned {code}")

# 此时轮 1/轮 2 各有一个卡死的 executor 工作线程（非 daemon）仍在 sleep(600)。
# 走 main.py 同款退出兜底：atexit 收尾 + 强制结束，进程必须立即退出。
try:
    import atexit as _atexit
    _atexit._run_exitfuncs()
except Exception:
    pass
try:
    import logging
    logging.shutdown()
except Exception:
    pass
sys.stdout.flush()
sys.stderr.flush()
os._exit(0)
