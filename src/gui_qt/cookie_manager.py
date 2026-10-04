"""Cookie 管理对话框（PySide6 版）：平台列表 + 扫码/浏览器/输入/清除。

二维码轮询在后台线程执行，所有 UI 更新通过 post_to_main 调度回主线程。
"""

import io
import os
import re
import sqlite3
import sys
import threading
import time

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

import auth
from gui_qt.common import post_to_main

# (key, 中文名, 支持扫码, 支持浏览器登录)
# 扫码：网易云/B站 原生接口，QQ音乐 走微信扫码换 musickey，
# 酷狗概念版 走概念版登录接口（会话存 config/kugou_lite_session.json，非 Cookie）；
# 酷狗/酷我/5sing/小红书/快手/淘宝 无公开扫码 API，登录页内扫码由「浏览器」入口承载
COOKIE_PLATFORMS = [
    ("bilibili", "B站", True, True),
    ("douyin", "抖音", False, True),
    ("weibo", "微博", False, True),
    ("netease", "网易云", True, False),
    ("qq", "QQ音乐", True, True),
    ("kugou", "酷狗", False, True),
    ("kugou_lite", "酷狗概念版", True, False),
    ("kuwo", "酷我", False, True),
    ("5sing", "5sing", False, True),
    ("xiaohongshu", "小红书", False, True),
    ("kuaishou", "快手", False, True),
    ("taobao", "淘宝", False, True),
]


def _kugou_lite_state() -> tuple[bool, str]:
    """酷狗概念版登录态：会话文件判断 + userid 展示。返回 (已登录, userid文本)。"""
    try:
        from music.scrapers.kugou_lite import load_session
        d = load_session()
        if d.get("token"):
            return True, str(d.get("userid") or "")
    except Exception:
        pass
    return False, ""


def _kugou_lite_age() -> str:
    """酷狗概念版会话文件保存时长描述（按文件修改时间）。"""
    try:
        from music.scrapers.kugou_lite import session_file
        f = session_file()
        if not f.exists():
            return ""
        delta = time.time() - f.stat().st_mtime
        if delta >= 86400:
            return f"{int(delta // 86400)}天前"
        if delta >= 3600:
            return f"{int(delta // 3600)}小时前"
        if delta >= 60:
            return f"{int(delta // 60)}分钟前"
        return "刚刚"
    except Exception:
        return ""


def _legacy_profile_dir(plat: str) -> str:
    """旧持久 profile 的目录（AppData\\<应用>\\QtWebEngine\\login_<plat>）。"""
    from PySide6.QtCore import QStandardPaths
    base = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    return os.path.join(base, "QtWebEngine", f"login_{plat}")


def _legacy_profile_cookies(plat: str) -> dict:
    """读旧持久 profile 的 Chromium Cookies 库（Windows 上 QtWebEngine 明文存值）。

    只在 profile 未被本应用创建时调用（库不被 Chromium 锁住才能读到）。
    """
    out: dict = {}
    try:
        db = os.path.join(_legacy_profile_dir(plat), "Cookies")
        if not os.path.exists(db):
            return out
        uri = "file:" + db.replace("?", "%3F").replace("#", "%23") + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            rows = con.execute(
                "select name, value from cookies where value != '' "
                "order by last_update_utc desc").fetchall()
        finally:
            con.close()
    except Exception:
        return out
    for name, value in rows:
        if name and name not in out:  # 同名多域取最近更新的
            out[name] = value
    return out


def _import_legacy_profile(plat: str, auth_keys: tuple) -> bool:
    """一次性迁移旧持久 profile 里留下的登录 Cookie 到应用存储。

    修复前（持久 profile 时代）用户在登录窗完成了登录但没能捕获保存，
    登录态只留在 Chromium 的 Cookies 库里 —— 这里把它救回 cookies.json，
    成功后删掉旧库防止重复迁移。库被占用（旧版应用还在运行）时静默跳过，
    下次再试。
    """
    try:
        if auth.load_cookies().get(plat, ""):
            return False  # 应用已有存储，无需迁移
        cks = _legacy_profile_cookies(plat)
        if not any(k in cks for k in auth_keys):
            return False  # 旧库里没有登录特征（游客态），不迁
        cookie_str = "; ".join(f"{k}={v}" for k, v in cks.items())
        auth.set_cookie(plat, cookie_str)
        for f in ("Cookies", "Cookies-journal"):
            try:
                os.remove(os.path.join(_legacy_profile_dir(plat), f))
            except OSError:
                pass
        return True
    except Exception:
        return False


class CookieManagerDialog(QDialog):
    def __init__(self, parent, on_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Cookie 管理")
        self.resize(620, 640)
        self.on_changed = on_changed

        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.container)
        layout.addWidget(scroll, 1)

        # 登录用户名探测：缓存 {plat: {"ok":..,"username":..} | "probing"}
        self._whoami_cache: dict = {}
        self._whoami_probing: set = set()

        # 底部：浏览器组件安装（Playwright + Chromium，游客态采集兜底用）
        bottom = QHBoxLayout()
        self.pw_status = QLabel()
        bottom.addWidget(self.pw_status, 1)
        self.pw_btn = self._btn("📦 下载浏览器组件", self._install_playwright, True)
        bottom.addWidget(self.pw_btn)
        layout.addLayout(bottom)
        self._refresh_pw_status()
        self._reload()

    def _refresh_pw_status(self):
        """浏览器环境状态：Playwright 库 + 系统 Edge/Chrome + 本地采集引擎。"""
        try:
            import playwright  # noqa: F401
        except ImportError:
            self.pw_status.setText("浏览器组件：❌ Playwright 未安装（采集需要）")
            self.pw_status.setStyleSheet("color: #ff5252;")
            self.pw_btn.setEnabled(True)
            self.pw_btn.setText("📦 下载浏览器组件")
            return
        try:
            from toolbox.browser import detect
            env = detect()
        except Exception:
            env = {"edge": None, "chrome": None, "shell": False}
        edge, chrome, shell = env["edge"], env["chrome"], env["shell"]
        if edge or chrome:
            which = f"Edge {edge}" if edge else f"Chrome {chrome}"
            self.pw_status.setText(f"浏览器环境：✅ 系统 {which}（采集走系统浏览器，无需下载）")
            self.pw_status.setStyleSheet("color: #4caf50;")
            self.pw_btn.setText("🔄 检测浏览器环境")
        elif shell:
            self.pw_status.setText("浏览器环境：✅ 本地采集引擎已就绪")
            self.pw_status.setStyleSheet("color: #4caf50;")
            self.pw_btn.setText("🔄 检测浏览器环境")
        else:
            self.pw_status.setText("浏览器环境：⚠️ 无系统 Edge/Chrome，采集需要下载引擎（91 MB）")
            self.pw_status.setStyleSheet("color: #eab308;")
            self.pw_btn.setText("⬇️ 下载采集引擎")
        self.pw_btn.setEnabled(True)

    def _pip_install_dialog(self, title, tip, cmds, success_msg, on_ok=None):
        """通用后台安装：逐条执行 cmds（(显示名, 参数列表)），弹实时日志窗口。"""
        from gui_qt.common import post_to_main

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(560, 320)
        v = QVBoxLayout(dlg)
        tip_lbl = QLabel(tip)
        v.addWidget(tip_lbl)
        from PySide6.QtWidgets import QPlainTextEdit
        log_view = QPlainTextEdit()
        log_view.setReadOnly(True)
        v.addWidget(log_view, 1)
        close_btn = QPushButton("后台继续，关闭窗口")
        close_btn.clicked.connect(dlg.close)
        v.addWidget(close_btn)
        dlg.show()

        def append_log(line):
            def _do():
                log_view.appendPlainText(line)
            post_to_main(_do)

        def run():
            import subprocess
            ok = True
            try:
                for disp, args in cmds:
                    append_log(f"$ {disp}")
                    r = subprocess.run(
                        [sys.executable] + args,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding="utf-8", errors="replace", timeout=3600,
                    )
                    for line in (r.stdout or "").splitlines():
                        append_log(line)
                    ok = ok and r.returncode == 0
            except Exception as e:
                append_log(f"安装失败：{e}")
                ok = False

            def _finish():
                append_log("\n完成 ✅" if ok else "\n失败 ❌（可重试或手动执行上面的命令）")
                tip_lbl.setText("安装完成，可关闭本窗口" if ok else "安装失败，可重试")
                if on_ok:
                    on_ok(ok)
                if ok:
                    QMessageBox.information(self, "成功", success_msg)
            post_to_main(_finish)

        threading.Thread(target=run, daemon=True).start()

    def _install_playwright(self):
        """手动检测浏览器环境；缺采集引擎时按需下载 headless shell（用户目录）。"""
        try:
            import playwright  # noqa: F401
        except ImportError:
            if getattr(sys, "frozen", False):
                QMessageBox.information(
                    self, "提示",
                    "安装版已内置浏览器组件，无需额外下载。\n"
                    "若采集异常请检查系统是否装有 Edge/Chrome。")
                return
            self._pip_install_dialog(
                "下载浏览器组件",
                "正在安装 Playwright（约 40 MB，取决于网速）…",
                [("pip install playwright", ["-m", "pip", "install", "playwright"])],
                "浏览器组件安装完成！采集将自动使用系统 Edge/Chrome。",
                on_ok=lambda ok: self._refresh_pw_status(),
            )
            return

        from toolbox.browser import detect, download_headless_shell, headless_shell_installed
        env = detect()
        edge, chrome = env["edge"], env["chrome"]
        status = (f"系统 Edge：{edge or '未安装'}\n"
                  f"系统 Chrome：{chrome or '未安装'}\n"
                  f"本地采集引擎：{'已就绪' if headless_shell_installed() else '未下载'}\n\n")
        if edge or chrome:
            QMessageBox.information(
                self, "浏览器环境", status + "✅ 采集将自动使用系统浏览器，无需下载。")
            self._refresh_pw_status()
            return
        if headless_shell_installed():
            QMessageBox.information(self, "浏览器环境", status + "✅ 采集引擎已就绪。")
            self._refresh_pw_status()
            return
        ret = QMessageBox.question(
            self, "下载采集引擎",
            status + "未检测到系统 Edge/Chrome，采集功能需要浏览器内核。\n\n"
                     "是否下载 Playwright 采集引擎（约 91 MB，下载到用户目录，仅需一次）？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
        if ret != QMessageBox.Yes:
            return

        dlg = QDialog(self)
        dlg.setWindowTitle("下载采集引擎")
        dlg.resize(560, 320)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("正在下载 Playwright 采集引擎（约 91 MB，国内镜像优先）…"))
        from PySide6.QtWidgets import QPlainTextEdit
        log_view = QPlainTextEdit()
        log_view.setReadOnly(True)
        v.addWidget(log_view, 1)
        close_btn = QPushButton("后台继续，关闭窗口")
        close_btn.clicked.connect(dlg.close)
        v.addWidget(close_btn)
        dlg.show()

        def append_log(line):
            def _do():
                log_view.appendPlainText(line)
            post_to_main(_do)

        def run():
            ok = download_headless_shell(log=append_log)

            def _finish():
                append_log("\n完成 ✅ 采集引擎已就绪" if ok else "\n失败 ❌（可重试；也可安装 Edge/Chrome 后使用系统浏览器）")
                self._refresh_pw_status()
            post_to_main(_finish)

        threading.Thread(target=run, daemon=True).start()

    def _reload(self):
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cookies = auth.load_cookies()
        netease_has = bool(auth.get_netease_cookie_stored().strip())
        for plat, name, can_qr, can_browser in COOKIE_PLATFORMS:
            if plat == "netease":
                has = netease_has
                age = auth.get_cookie_age("netease") if has else ""
            elif plat == "kugou_lite":
                has, _uid = _kugou_lite_state()
                age = _kugou_lite_age() if has else ""
            else:
                has = bool(cookies.get(plat, "") and len(cookies.get(plat, "")) > 10)
                age = auth.get_cookie_age(plat)
            row = QHBoxLayout()
            row.addWidget(QLabel(name, styleSheet="font-weight: bold;"))
            sl = QLabel(self._status_text(plat, has, age))
            sl.setStyleSheet(self._status_color(plat, has))
            row.addWidget(sl)
            row.addStretch(1)
            if has:
                row.addWidget(self._btn("验证", lambda p=plat: self._verify(p)))
            if plat == "kugou_lite":
                row.addWidget(self._btn("🎁 领VIP", lambda: self._kugou_lite_claim_vip(), True))
            if can_qr:
                row.addWidget(self._btn("扫码登录", lambda p=plat: self._qr_login(p), True))
            if can_browser:
                row.addWidget(self._btn("浏览器", lambda p=plat: self._browser_login(p)))
            if plat != "kugou_lite":
                row.addWidget(self._btn("输入", lambda p=plat: self._input(p)))
            row.addWidget(self._btn("清除", lambda p=plat: self._clear(p), danger=True))
            w = QWidget()
            w.setLayout(row)
            w.setStyleSheet("QWidget { background: #ffffff; border-radius: 6px; }")
            self.container_layout.addWidget(w)

        # 有 Cookie 且未探测过的平台 → 后台自动拉取登录用户名
        # （管理弹窗读"已存储"Cookie：总开关只管采集，不影响这里的状态展示/验证）
        stored = auth.load_cookies()
        for plat, _n, _q, _b in COOKIE_PLATFORMS:
            cookie = (auth.get_netease_cookie_stored() if plat == "netease" else stored.get(plat, "")) or ""
            if len(cookie) > 10 and plat not in self._whoami_cache and plat not in self._whoami_probing:
                self._start_probe(plat)

    @staticmethod
    def _short(s: str, limit: int = 20) -> str:
        """超长文本截断（如 QQ 音乐微信登录的 19 位 uin），避免撑破行布局。"""
        s = s or ""
        return s if len(s) <= limit else s[:limit - 3] + "..."

    def _status_text(self, plat, has, age):
        if not has:
            return "🔴 未登录"
        info = self._whoami_cache.get(plat)
        if info is None:
            return "🟢 已登录" + (f" · {age}" if age else "")
        if info == "probing":
            return "🟢 已登录 · ⏳ 验证中…" + (f" · {age}" if age else "")
        base = "🟢 已登录"
        if info["ok"] is False:
            base = "🟠 已保存 · 验证失败"
        elif info.get("username"):
            base = f"🟢 已登录 · {self._short(info['username'])}"
        return base + (f" · {age}" if age else "")

    def _status_color(self, plat, has):
        if not has:
            return "color: #ff5252;"
        info = self._whoami_cache.get(plat)
        if info and info != "probing" and info["ok"] is False:
            return "color: #ef6c00;"
        return "color: #4caf50;"

    def _start_probe(self, plat):
        from gui_qt.common import post_to_main
        from tracker import whoami

        self._whoami_probing.add(plat)
        self._whoami_cache[plat] = "probing"
        self._render_status(plat)

        def run():
            try:
                info = whoami.probe(plat)
            except Exception:
                info = {"ok": None, "username": ""}

            def done():
                self._whoami_cache[plat] = info
                self._whoami_probing.discard(plat)
                self._render_status(plat)
            post_to_main(done)

        threading.Thread(target=run, daemon=True).start()

    def _render_status(self, plat):
        """只更新对应行的状态文字（避免整表重建闪烁）。"""
        for i in range(self.container_layout.count()):
            w = self.container_layout.itemAt(i).widget()
            if not w:
                continue
            labels = w.findChildren(QLabel)
            if labels and labels[0].text() == auth.PLATFORM_NAMES.get(plat):
                if plat == "netease":
                    has = bool(auth.get_netease_cookie_stored().strip())
                    age = auth.get_cookie_age(plat) if has else ""
                elif plat == "kugou_lite":
                    has, _uid = _kugou_lite_state()
                    age = _kugou_lite_age() if has else ""
                else:
                    has = bool((auth.load_cookies().get(plat, "") or "").strip())
                    age = auth.get_cookie_age(plat) if has else ""
                labels[1].setText(self._status_text(plat, has, age))
                labels[1].setStyleSheet(self._status_color(plat, has))
                return

    def _verify(self, plat):
        """手动验证：清缓存重新拉取登录用户名。"""
        self._whoami_cache.pop(plat, None)
        if plat == "kugou_lite":
            self._kugou_lite_verify()
            return
        self._start_probe(plat)

    def _kugou_lite_verify(self):
        """酷狗概念版验证登录：轻量搜索请求校验 token（会话文件，不走 whoami）。"""
        self._whoami_cache["kugou_lite"] = "probing"
        self._render_status("kugou_lite")

        def run():
            info = {"ok": None, "username": ""}
            msg = ""
            try:
                from music.scrapers.kugou_lite import KuGouLiteClient
                ok, msg = KuGouLiteClient().verify_session()
                info = {"ok": ok, "username": ""}
            except Exception as e:  # noqa: BLE001 - 网络异常按验证失败展示
                info = {"ok": False, "username": ""}
                msg = str(e)

            def done():
                self._whoami_cache["kugou_lite"] = info
                self._render_status("kugou_lite")
                if info.get("ok") is False:
                    QMessageBox.warning(self, "酷狗概念版验证", f"❌ {msg}")
            post_to_main(done)

        threading.Thread(target=run, daemon=True).start()

    def _btn(self, text, slot, primary=False, danger=False):
        b = QPushButton(text)
        # clicked 信号自带 checked 参数，会覆盖 lambda 的默认参数（曾导致所有按钮
        # 收到 plat=False 而静默无效）——这里显式丢弃信号参数再调 slot
        b.clicked.connect(lambda *args: slot())
        b.setFixedHeight(28)
        # :disabled 态必须显式给出：自定义 background 会盖掉 Qt 默认的禁用变灰，
        # 按钮看起来可点实则失效（「下载浏览器组件」已安装时点了没反应的根因）
        if primary:
            b.setStyleSheet(
                "QPushButton { background: #2e7d32; color: white; border-radius: 4px; padding: 2px 8px; }"
                "QPushButton:disabled { background: #b9c4bc; color: #eef1ee; }")
        elif danger:
            b.setStyleSheet(
                "QPushButton { background: #c62828; color: white; border-radius: 4px; padding: 2px 8px; }"
                "QPushButton:disabled { background: #d8b9b9; color: #f3e9e9; }")
        return b

    def _input(self, plat):
        from auth import PLATFORM_NAMES
        name = PLATFORM_NAMES.get(plat, plat)
        val, ok = QInputDialog.getText(self, "输入 Cookie", f"请输入 {name} 的 Cookie:")
        if not ok or not val.strip():
            return
        try:
            auth.set_cookie(plat, val.strip())
            if plat == "netease":
                auth.sync_netease_to_legacy()
            self._whoami_cache.pop(plat, None)
            self._reload()
            self._notify()
            QMessageBox.information(self, "成功", f"{name} Cookie 已保存")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"{name} Cookie 写入失败：\n{e}")

    def _clear(self, plat):
        from auth import PLATFORM_NAMES
        name = PLATFORM_NAMES.get(plat, plat)
        try:
            auth.clear_cookie(plat)
            if plat == "kugou_lite":
                # 概念版登录态在会话文件（非 Cookie 统一存储），需一并清除
                from music.scrapers.kugou_lite import clear_session
                clear_session()
            self._whoami_cache.pop(plat, None)
            self._reload()
            self._notify()
        except Exception as e:
            QMessageBox.critical(self, "清除失败", f"{name} 清除失败：\n{e}")

    def _browser_login(self, plat):
        """内嵌登录：系统浏览器（Edge→Chrome）经 Playwright 有头启动，去边框后
        顶层贴合对话框页签区（视觉内嵌、键盘原生可用），轮询 context.cookies()
        捕获全量 Cookie（含 HttpOnly）。"""
        try:
            from playwright.sync_api import sync_playwright
            from toolbox.browser import launch as pw_launch
        except ImportError:
            QMessageBox.warning(self, "不可用", "浏览器组件（Playwright）未安装，请改用「输入」手动粘贴 Cookie")
            return

        # (url, 标题, 登录特征 Cookie, 登录操作提示)
        configs = {
            "weibo": ("https://m.weibo.cn/", "微博登录（软件内）", ("SUB",), ""),
            "douyin": ("https://www.douyin.com/", "抖音登录（软件内）", ("sessionid",), ""),
            "bilibili": ("https://www.bilibili.com/", "B站登录（软件内）", ("SESSDATA",), ""),
            "qq": ("https://y.qq.com/", "QQ音乐登录（软件内）",
                   ("uin", "qm_keyst"), "点击右上角「登录」→ 扫码或账号登录"),
            "kugou": ("https://www.kugou.com/", "酷狗登录（软件内）",
                      ("KugouID", "userid", "token"), "点击右上角「登录」→ 扫码或账号登录"),
            "kuwo": ("https://www.kuwo.cn/", "酷我登录（软件内）",
                     ("userid",), "点击顶部「登录」→ 手机号验证码登录"),
            "5sing": ("https://5sing.kugou.com/login", "5sing登录（软件内）",
                      ("userid",), "账号密码登录（支持酷狗账号）"),
            "xiaohongshu": ("https://www.xiaohongshu.com/", "小红书登录（软件内）",
                            ("web_session",), "点击「登录」→ 扫码或短信验证"),
            "kuaishou": ("https://www.kuaishou.com/account/login", "快手登录（软件内）",
                         # web_st=30天票；webday7_st=7天票（新版常见，只认前者会误报未登录）
                         ("kuaishou.server.web_st", "kuaishou.server.webday7_st"),
                         "扫码 / 手机号验证码登录（直达登录页，避开首页登录按钮被备案号遮挡）"),
            "taobao": ("https://www.taobao.com/", "淘宝登录（软件内）",
                       ("cookie2", "unb", "sg"),
                       "点击顶部「登录」→ 扫码或手机号登录（商品列表采集用）"),
        }
        # 快手登录浏览器回种白名单：只保留设备/环境字段。
        # 登录态票（*_st/passToken/userId）过期后回种会同名覆盖新票；
        # pt_*/p_*/qrsig 等是 QQ 互联残留，会干扰快手登录流程。
        _KS_LOGIN_KEEP = {
            "did", "didv", "clientid", "kpf", "kpn",
            "_bl_uid", "__aegis_uid", "kwfv1", "ktrace-context",
        }
        if plat not in configs:
            QMessageBox.information(self, "提示", f"{plat} 暂不支持浏览器登录，请使用「输入」")
            return
        url, title, auth_keys, login_hint = configs[plat]

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(1100, 780)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("  在下方页面完成登录后，点击「✅ 已获取」按钮保存 Cookie（页面已自动登录时可直接点击）"
                      + (f"　💡 {login_hint}" if login_hint else ""))
        hint.setStyleSheet("color: #555; padding: 4px; background: #ffffff;")
        v.addWidget(hint)

        # 旧持久 profile 时代留下的登录态一次性迁移（必须在 profile 创建前读库）
        if _import_legacy_profile(plat, auth_keys):
            self._whoami_cache.pop(plat, None)
            self._reload()
            self._notify()

        # 贴合宿主：worker 找到浏览器窗口句柄后，由主线程去边框贴合此区域
        host = QWidget()
        host.setAttribute(Qt.WA_NativeWindow)  # 原生句柄取物理坐标（免 DPI 换算）
        host.setStyleSheet("background: #ffffff;")
        v.addWidget(host, 1)

        # 底部操作栏：只有用户点「已获取」才保存，不自动不超时
        bar = QHBoxLayout()
        self_keys = "/".join(auth_keys)
        cap_lbl = QLabel(f"  已捕获 {0} 个 Cookie（登录特征：{self_keys}）")
        cap_lbl.setStyleSheet("color: #888; padding: 4px;")
        bar.addWidget(cap_lbl, 1)
        reload_btn = QPushButton("🔄 刷新")
        reload_btn.setStyleSheet("color: #1565c0;")
        reload_btn.setToolTip("页面白屏/卡死/加载异常时点此重新加载（Cookie 捕获不受影响）")
        reload_btn.clicked.connect(lambda *args: state["reload"].set())
        bar.addWidget(reload_btn)
        got_btn = QPushButton("✅ 已获取（保存 Cookie）")
        got_btn.setStyleSheet("background: #2e7d32; color: white; border-radius: 4px; padding: 4px 14px;")
        got_btn.clicked.connect(lambda *args: save_now())
        bar.addWidget(got_btn)
        pop_btn = QPushButton("⌨ 改独立窗口")
        pop_btn.setStyleSheet("color: #1565c0;")
        pop_btn.setToolTip("无法在嵌入页面打字时点此：浏览器弹回独立窗口，输入必定可用，Cookie 捕获不受影响")
        pop_btn.setEnabled(False)  # 嵌入成功后才可用
        pop_btn.clicked.connect(lambda *args: _pop_out())
        bar.addWidget(pop_btn)
        cancel_btn = QPushButton("放弃")
        cancel_btn.clicked.connect(dlg.close)
        bar.addWidget(cancel_btn)
        bar_w = QWidget()
        bar_w.setLayout(bar)
        v.addWidget(bar_w)

        jar: dict[str, str] = {}

        def _refresh_cap():
            hit = any(k in jar for k in auth_keys)
            cap_lbl.setText(f"  已捕获 {len(jar)} 个 Cookie" + (f"（✅ 检测到登录特征 {self_keys}）" if hit else f"（登录特征：{self_keys}）"))
            cap_lbl.setStyleSheet("color: #2e7d32; padding: 4px;" if hit else "color: #888; padding: 4px;")

        # ==== Win32 覆盖层辅助（Playwright 启动的 Chromium 系窗口类名）====
        import ctypes
        user32 = ctypes.windll.user32
        GWL_STYLE, GWL_EXSTYLE = -16, -20
        WS_CAPTION, WS_THICKFRAME, WS_EX_TOOLWINDOW = 0x00C00000, 0x00040000, 0x00000080

        def _win_proc_name(hwnd):
            """窗口所属进程名（小写）；Electron 应用（Trae/QQ 等）类名同为
            Chrome_WidgetWin_1，必须按进程名过滤防止误嵌。"""
            from ctypes import wintypes
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED
            if not h:
                return ""
            try:
                buf = ctypes.create_unicode_buffer(260)
                size = wintypes.DWORD(260)
                ctypes.windll.kernel32.QueryFullProcessImageNameW(
                    h, 0, buf, ctypes.byref(size))
                return buf.value.rsplit("\\", 1)[-1].lower()
            finally:
                ctypes.windll.kernel32.CloseHandle(h)

        def _enum_browser_windows():
            out = []
            CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

            def _cb(hwnd, _):
                if user32.IsWindowVisible(hwnd):
                    buf = ctypes.create_unicode_buffer(64)
                    user32.GetClassNameW(hwnd, buf, 64)
                    if buf.value == "Chrome_WidgetWin_1" and \
                            _win_proc_name(hwnd) in ("msedge.exe", "chrome.exe"):
                        out.append(hwnd)
                return True
            user32.EnumWindows(CB(_cb), 0)
            return out

        SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
        SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x0010, 0x0040
        SWP_ASYNCWINDOWPOS = 0x4000  # 关键：跨进程异步改窗口，不等对方窗口

        def _bw_show(hwnd, show=True):
            """跨进程显示/隐藏浏览器窗口。

            必须走 SetWindowPos + SWP_ASYNCWINDOWPOS：同步的 ShowWindow /
            SetWindowPos 要等目标窗口（Chromium）处理窗口消息才返回，
            页面白屏/渲染挂起时它不响应 → 主线程被永久吊死（登录框
            拖不动、按钮全点不动，实测快手白屏时如此）。
            """
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | 0x0004  # NOZORDER
                                | SWP_NOACTIVATE
                                | (SWP_SHOWWINDOW if show else 0x0080)
                                | SWP_ASYNCWINDOWPOS)

        state = {"stop": threading.Event(), "hwnd": 0, "overlay": False,
                 "box_open": False, "reload": threading.Event()}

        def _fg_root():
            """当前前台窗口的根窗口句柄（0=无）。"""
            fg = user32.GetForegroundWindow()
            if not fg:
                return 0
            return user32.GetAncestor(fg, 2) or fg  # GA_ROOT

        def _overlay_should_show():
            """贴合浏览器当前是否应可见。

            登录框激活，或前台就是浏览器本身（用户正在页面里点/打字）→ 可见；
            其余（用户在主窗口干活、切到别的程序）→ 收起：
            - 顶层贴合窗口不被 Qt 的 owned 链约束，主窗口一激活就会把它压到
              下面（「窗口存在却被 Qt 遮住」的根因），收起后再弹出最稳
            - 主窗口里右键菜单/详情框是嵌套事件循环，与跨进程覆盖层共存
              会把主线程冻死（实测），收起期间这组合不可能出现
            """
            hwnd = state.get("hwnd") or 0
            if not hwnd or not state.get("overlay"):
                return False
            try:
                if dlg.isActiveWindow():
                    return True
                return _fg_root() == hwnd
            except Exception:
                return True

        def _resync():
            """按激活状态同步贴合层可见性（显示时同时贴合+抬 z 序）。"""
            hwnd = state.get("hwnd") or 0
            if not hwnd or not state.get("overlay") or state.get("box_open"):
                return
            if _overlay_should_show():
                _bw_show(hwnd, True)
                _reposition()
            else:
                _bw_show(hwnd, False)

        def _reposition(*_a):
            """把无边框浏览器窗口贴到 host 的全局矩形上（含 z 序置顶）。"""
            hwnd = state.get("hwnd") or 0
            if not hwnd or not state.get("overlay"):
                return
            if state.get("box_open"):
                return  # 弹窗期间不动浏览器，不与其抢 z 序
            try:
                if not _overlay_should_show():
                    return  # 已收起状态不贴合（_resync 负责显示时再贴）
                from ctypes import wintypes
                rc = wintypes.RECT()
                if user32.GetWindowRect(int(host.winId()), ctypes.byref(rc)):
                    cur = wintypes.RECT()
                    same = False
                    if user32.GetWindowRect(hwnd, ctypes.byref(cur)):
                        same = (cur.left, cur.top, cur.right, cur.bottom) == \
                               (rc.left, rc.top, rc.right, rc.bottom)
                    if same:
                        # 矩形没变也要抬 z 序：主窗口激活过就会把浏览器压到
                        # 下面（被 Qt 遮住的根因），NOMOVE|NOSIZE 不触发重绘
                        user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                                            SWP_NOSIZE | SWP_NOMOVE
                                            | SWP_NOACTIVATE | SWP_SHOWWINDOW
                                            | SWP_ASYNCWINDOWPOS)
                    else:
                        user32.SetWindowPos(hwnd, 0, rc.left, rc.top,
                                            rc.right - rc.left, rc.bottom - rc.top,
                                            SWP_NOACTIVATE | SWP_SHOWWINDOW
                                            | SWP_ASYNCWINDOWPOS)
            except Exception:
                pass

        def _embed(hwnd):
            """主线程：浏览器改成无边框顶层窗口，贴合 host 区域（视觉等同内嵌）。

            保持顶层独立窗口是关键：作子窗口嵌入时 Chromium 收不到系统激活
            消息（WM_ACTIVATE/WM_ACTIVATEAPP），会按「未激活」丢弃按键，登录框
            完全打不了字（实测）；顶层贴合方案下键盘输入原生可用。"""
            state["hwnd"] = hwnd
            try:
                style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                user32.SetWindowLongW(hwnd, GWL_STYLE,
                                      int(style) & ~(WS_CAPTION | WS_THICKFRAME))
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                      int(ex) | WS_EX_TOOLWINDOW)  # 不进任务栏/Alt-Tab
                state["overlay"] = True
                # 激活一次浏览器窗口：NOACTIVATE 贴入的窗口从未被激活过，
                # Chromium 在非激活状态下会丢弃按钮点击的事件派发（实测快手
                # 验证码登录「获取验证码」点击零请求、无倒计时，激活后恢复）
                try:
                    user32.SetForegroundWindow(hwnd)
                except Exception:
                    pass
                _resync()
                for ms in (150, 400, 1000):
                    QTimer.singleShot(ms, _resync)

                # 对话框激活/失活/移动/缩放/最小化时保持贴合；
                # 失活时收起贴合层（防主窗口把它压到后面 + 规避嵌套循环冻死组合）
                class _FollowFilter(QObject):
                    def eventFilter(self, obj, ev):
                        t = ev.type()
                        if not state.get("overlay"):
                            return False
                        if t == QEvent.WindowStateChange:
                            if dlg.isMinimized():
                                _bw_show(state["hwnd"], False)   # 跟随隐藏
                            else:
                                QTimer.singleShot(0, _resync)
                        elif t == QEvent.WindowActivate:
                            QTimer.singleShot(0, _resync)   # 回到登录框：弹出+贴合+置顶
                        elif t == QEvent.WindowDeactivate:
                            # 焦点去向未知（可能点进浏览器页面打字）→ 延迟
                            # 判断，前台是浏览器本身就保持显示
                            QTimer.singleShot(60, _resync)
                        elif t in (QEvent.Move, QEvent.Resize, QEvent.Show):
                            QTimer.singleShot(0, _reposition)
                        return False
                filt = _FollowFilter(dlg)
                dlg.installEventFilter(filt)
                host.installEventFilter(filt)
                state["focus_filter"] = filt
                pop_btn.setEnabled(True)
            except Exception:
                pass  # 贴合失败时浏览器保持独立窗口，登录功能不受影响
            _refresh_cap()

        def worker():
            """后台线程：Playwright 生命周期必须在创建线程内收尾（sync API 非线程安全）。"""
            try:
                before = set(_enum_browser_windows())  # 快照必须在 launch 前
                with sync_playwright() as p:
                    # 登录浏览器强制直连：全部为国内站点，走系统代理反而会被本地
                    # 代理工具干扰（代理开启时 DNS 解析失败，实测 ERR_NAME_NOT_RESOLVED）；
                    # AutomationControlled：抹掉 navigator.webdriver 自动化标记，
                    # 部分站点（实测快手）检测到会直接返回白屏
                    browser = pw_launch(p, headless=False, args=[
                        "--no-proxy-server",
                        "--disable-blink-features=AutomationControlled"])
                    ctx = browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
                        # 高度 760：快手新版首页在 660 视口下侧栏「立即登录」按钮会被
                        # 备案号盒子叠住（实测 elementFromPoint 命中备案按钮），加高错开
                        viewport={"width": 1080, "height": 760},
                        locale="zh-CN",
                    )
                    # --disable-blink-features=AutomationControlled 抹不掉
                    # navigator.webdriver（实测仍为 true），必须 init script 覆写；
                    # 快手等站点检测到自动化标记会发无效票据（表面登录成功、
                    # 跳主页即掉登录态）
                    ctx.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', "
                        "{get: () => undefined});")
                    # 回种已存 Cookie：页面自动保持登录，用户可直接点「已获取」
                    stored = auth.load_cookies().get(plat, "")
                    if plat == "kuaishou":
                        # 快手特例：过期的登录态票（web*_st/passToken/userId）与
                        # QQ 互联残留（pt_*/p_*/qrsig 等）回种后会同名覆盖新登录
                        # 种下的票，收割拿到假 Cookie（2026-10 实测）。只回种
                        # 设备/环境字段，登录态全靠本次新种。
                        keep = _KS_LOGIN_KEEP
                        stored = "; ".join(
                            p.strip() for p in stored.split(";")
                            if p.strip().split("=")[0].strip() in keep)
                    if stored:
                        from urllib.parse import urlparse
                        h = urlparse(url).hostname or ""
                        dom = "." + h.split(".", 1)[1] if h.count(".") >= 2 else h
                        for pair in stored.split(";"):
                            n, eq, val = pair.strip().partition("=")
                            if n and eq:
                                ctx.add_cookies([{"name": n, "value": val,
                                                  "domain": dom, "path": "/"}])
                                jar[n] = val
                    page = ctx.new_page()
                    page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    # 找到新出现的浏览器窗口句柄 → 主线程嵌入
                    hwnd = None
                    deadline = time.time() + 10
                    while time.time() < deadline and not state["stop"].is_set():
                        new = [h for h in _enum_browser_windows() if h not in before]
                        if new:
                            hwnd = new[0]
                            break
                        time.sleep(0.3)
                    if hwnd is not None:
                        post_to_main(lambda: _embed(hwnd))
                    # 轮询捕获（全量含 HttpOnly；窗口被用户手动关闭时退出）
                    while not state["stop"].is_set():
                        time.sleep(1.5)
                        if state["reload"].is_set():
                            state["reload"].clear()
                            try:  # 白屏/卡死的救急重载，失败保留现状
                                page.reload(wait_until="domcontentloaded",
                                            timeout=45000)
                            except Exception:
                                pass
                            continue
                        try:
                            for c in ctx.cookies():
                                n, val = c.get("name"), c.get("value")
                                if n and val:
                                    jar[n] = val
                            post_to_main(_refresh_cap)
                        except Exception:
                            break
            except Exception as e:
                def _fail(msg=str(e)):
                    if dlg.isVisible():
                        _info_box(QMessageBox.Warning, "打开失败",
                                  f"浏览器启动失败：\n{msg}\n\n可改用「输入」手动粘贴 Cookie",
                                  on_ok=dlg.close)
                post_to_main(_fail)

        def _pop_out():
            """⌨ 保底：浏览器恢复普通独立窗口（不再贴合对话框，可随意拖动缩放）。"""
            hwnd = state.get("hwnd") or 0
            if not hwnd:
                return
            state["overlay"] = False
            pop_btn.setEnabled(False)
            try:
                style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                user32.SetWindowLongW(hwnd, GWL_STYLE,
                                      int(style) | WS_CAPTION | WS_THICKFRAME)
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, int(ex) & ~WS_EX_TOOLWINDOW)
                # 尾部 0x4040 = SWP_SHOWWINDOW|SWP_ASYNCWINDOWPOS
                user32.SetWindowPos(hwnd, 0, 100, 100, 1100, 760, 0x4040)
                user32.SetForegroundWindow(hwnd)
            except Exception:
                pass

        def _cleanup():
            state["stop"].set()
            state["overlay"] = False
            if state["hwnd"]:
                _bw_show(state["hwnd"], False)  # goto 阻塞期间避免独立窗口闪现

        def _info_box(icon, title, text, on_ok=None):
            """非阻塞提示框：show() 代替 exec()。

            模态 exec 的嵌套事件循环在跨进程覆盖层场景下会停摆（主线程冻死、
            后续点击全被吞——实测 faulthandler 定位），非阻塞方式彻底规避。"""
            hwnd = state.get("hwnd") or 0
            hid = False
            if hwnd and state.get("overlay"):
                _bw_show(hwnd, False)  # 弹窗期间隐藏浏览器，防止盖住弹窗
                hid = True

            def _restore():
                if hid and state.get("overlay") and state.get("hwnd"):
                    _resync()  # 弹窗关闭：按当前激活状态决定弹出还是保持收起

            b = QMessageBox(dlg)
            b.setIcon(icon)
            b.setWindowTitle(title)
            b.setText(text)
            b.addButton("知道了", QMessageBox.AcceptRole)
            b.setWindowModality(Qt.WindowModal)
            state["box_open"] = True

            def _done(*_a):
                state["box_open"] = False
                _restore()
                b.deleteLater()
                if on_ok:
                    on_ok()

            b.buttonClicked.connect(lambda _btn: b.close())
            b.finished.connect(_done)
            b.show()

        def _confirm_box(title, text, on_yes):
            """非阻塞确认框：点「保存」才继续；「取消/ESC」不动作。"""
            hwnd = state.get("hwnd") or 0
            hid = False
            if hwnd and state.get("overlay"):
                _bw_show(hwnd, False)
                hid = True

            def _restore():
                if hid and state.get("overlay") and state.get("hwnd"):
                    _resync()

            b = QMessageBox(dlg)
            b.setIcon(QMessageBox.Question)
            b.setWindowTitle(title)
            b.setText(text)
            yes = b.addButton("保存", QMessageBox.YesRole)
            no = b.addButton("取消", QMessageBox.NoRole)
            b.setDefaultButton(no)
            b.setWindowModality(Qt.WindowModal)
            state["box_open"] = True
            ok = {"v": False}

            def _done(*_a):
                state["box_open"] = False
                _restore()
                b.deleteLater()
                if ok["v"]:
                    on_yes()

            def _click(btn):
                ok["v"] = (btn is yes)
                b.close()

            b.buttonClicked.connect(_click)
            b.finished.connect(_done)
            b.show()

        def _do_save():
            cookie_str = "; ".join(f"{k}={v}" for k, v in jar.items())
            try:
                auth.set_cookie(plat, cookie_str)
                self._whoami_cache.pop(plat, None)
                self._reload()
                self._notify()
            except Exception as e:
                _info_box(QMessageBox.Critical, "保存失败", f"Cookie 写入失败：\n{e}")
                return
            dlg.close()

        def save_now():
            if not jar:
                _info_box(QMessageBox.Warning, "无 Cookie",
                          "尚未捕获到任何 Cookie，请先在页面完成登录")
                return
            if not any(k in jar for k in auth_keys):
                # 只有用「无登录特征的捕获」覆盖「已存有效登录」时才确认；
                # 其余直接保存（旧版每次都弹「仍要保存吗」，且模态弹窗会卡死）
                stored = auth.load_cookies().get(plat, "")
                if any(k in stored for k in auth_keys):
                    _confirm_box(
                        "覆盖确认",
                        f"当前捕获 {len(jar)} 个 Cookie，但未检测到登录特征（{self_keys}）。\n"
                        "保存会覆盖已存的有效登录，确定吗？",
                        _do_save)
                    return
            _do_save()

        dlg.closeEvent = lambda ev: _cleanup()

        threading.Thread(target=worker, daemon=True).start()
        dlg.show()

    def _qr_login(self, plat):
        if plat == "netease":
            self._netease_qr()
        elif plat == "bilibili":
            self._bilibili_qr()
        elif plat == "qq":
            self._qq_qr()
        elif plat == "kugou_lite":
            self._kugou_lite_qr()

    def _kugou_lite_qr(self):
        """酷狗概念版扫码登录：v2/qrcode 生成（官方 base64 图）→ 轮询 get_userinfo_qrcode。

        会话（token/userid）存 config/kugou_lite_session.json，登录后免扫码。
        """
        dlg, qr_label, status = self._qr_dialog("酷狗概念版扫码登录")

        def run():
            try:
                from music.scrapers.kugou_lite import KuGouLiteClient
                client = KuGouLiteClient()
                key, png = client.qr_create()
                self._show_qr_bytes(qr_label, png)
                post_to_main(status.setText, "📱 请用酷狗概念版 App 扫码")
                while dlg.isVisible():
                    time.sleep(2)
                    r = client.qr_poll(key)
                    st = r.get("status")
                    if st == 4 and r.get("logged_in"):
                        post_to_main(self._on_qr_success, dlg,
                                    "酷狗概念版登录成功（会话已保存，下次免扫码）")
                        return
                    if st == 4:
                        post_to_main(status.setText, "❌ 已确认但未返回 token，请重试")
                        return
                    if st == 0:
                        post_to_main(status.setText, "⚠️ 二维码已失效，请重新登录")
                        return
                    if st == 2:
                        post_to_main(status.setText, "✅ 扫码成功，请在手机上确认")
                    elif r.get("message") and st != 1:
                        post_to_main(status.setText, f"⏳ {r['message']}")
            except Exception as e:
                post_to_main(status.setText, f"❌ {e}")

        threading.Thread(target=run, daemon=True).start()

    def _kugou_lite_claim_vip(self):
        """酷狗概念版领 VIP：广告播放上报（每次 +3 小时，每日 8 次上限）。

        无登录态时提示先扫码；领取结果弹窗展示。
        """
        try:
            from music.scrapers.kugou_lite import session_logged_in
            if not session_logged_in():
                QMessageBox.information(
                    self, "未登录", "请先在酷狗概念版行点「扫码登录」，登录后才能领取 VIP。")
                return
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取登录态失败：\n{e}")
            return

        def run():
            from music.scrapers.kugou_lite import KuGouLiteClient
            ok, msg = KuGouLiteClient().claim_vip()

            def done():
                QMessageBox.information(
                    self, "领取 VIP",
                    f"{'✅' if ok else '❌'} {msg}\n\n"
                    "概念版每天可领 8 次（每次 +3 小时），领满 24 小时即可当日无损下载。")
                self._reload()
            post_to_main(done)

        threading.Thread(target=run, daemon=True).start()

    def _qr_dialog(self, title):
        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(360, 420)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel("请用手机 APP 扫码登录", styleSheet="font-weight: bold;"))
        qr_label = QLabel()
        qr_label.setFixedSize(280, 280)
        qr_label.setAlignment(Qt.AlignCenter)
        v.addWidget(qr_label, 0, Qt.AlignHCenter)
        status = QLabel("⏳ 生成二维码中...")
        status.setStyleSheet("color: #eab308;")
        v.addWidget(status)
        dlg.show()
        return dlg, qr_label, status

    def _show_qr(self, qr_label, img):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        self._show_qr_bytes(qr_label, buf.getvalue())

    def _show_qr_bytes(self, qr_label, data: bytes):
        qimg = QImage.fromData(data)
        pm = QPixmap.fromImage(qimg).scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        def _set():
            try:
                qr_label.setPixmap(pm)
            except Exception:
                pass
        post_to_main(_set)

    def _qq_qr(self):
        """QQ音乐扫码登录：微信扫码 → wx_code → musicu.fcg LoginServer 换 musicid/musickey。

        ptlogin2 直连路线已被网关按 u1 参数封禁（403），微信扫码路线轮询走
        lp.open.weixin.qq.com（微信开放平台域名，无此限制）。
        """
        import requests
        dlg, qr_label, status = self._qr_dialog("QQ音乐扫码登录")
        UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"}
        CONNECT = "https://open.weixin.qq.com/connect/qrconnect"
        POLL = "https://lp.open.weixin.qq.com/connect/l/qrconnect"

        def run():
            try:
                s = requests.Session()
                s.headers.update(UA)
                r = s.get(CONNECT, params={
                    "appid": "wx48db31d50e334801",
                    "redirect_uri": "https://y.qq.com/portal/wx_redirect.html?login_type=2&surl=https://y.qq.com/",
                    "response_type": "code", "scope": "snsapi_login", "state": "STATE",
                    "href": "https://y.qq.com/mediastyle/music_v17/src/css/popup_wechat.css#wechat_redirect",
                }, timeout=20)
                m = re.search(r"uuid=(.+?)\"", r.text)
                if not m:
                    post_to_main(status.setText, "❌ 生成二维码失败")
                    return
                uuid = m.group(1)
                img = s.get(f"https://open.weixin.qq.com/connect/qrcode/{uuid}",
                            headers={"Referer": CONNECT}, timeout=20)
                if not img.content:
                    post_to_main(status.setText, "❌ 二维码图片获取失败")
                    return
                self._show_qr_bytes(qr_label, img.content)
                post_to_main(status.setText, "📱 请用微信扫码（QQ音乐登录）")
                while dlg.isVisible():
                    p = s.get(POLL, params={"uuid": uuid, "_": int(time.time() * 1000)},
                              headers={"Referer": "https://open.weixin.qq.com/"}, timeout=35)
                    m_err = re.search(r"window\.wx_errcode=(\d+);", p.text)
                    m_code = re.search(r"window\.wx_code='([^']*)';", p.text)
                    err = m_err.group(1) if m_err else ""
                    if err == "405" and m_code and m_code.group(1):
                        cookie_str = self._qq_exchange_wxcode(s, m_code.group(1))
                        if not cookie_str:
                            post_to_main(status.setText, "❌ 获取登录凭据失败，请重试")
                            return
                        auth.set_cookie("qq", cookie_str)
                        self._whoami_cache.pop("qq", None)
                        post_to_main(self._on_qr_success, dlg, "QQ音乐登录成功，Cookie 已保存")
                        return
                    elif err == "404":
                        post_to_main(status.setText, "✅ 扫码成功，请在手机上确认")
                    elif err == "403":
                        post_to_main(status.setText, "⚠️ 二维码已失效，请重新登录")
                        return
                    elif err == "408":
                        post_to_main(status.setText, "⏳ 等待扫码...")
                    time.sleep(2)
            except Exception as e:
                post_to_main(status.setText, f"❌ {e}")

        threading.Thread(target=run, daemon=True).start()

    @staticmethod
    def _qq_exchange_wxcode(session, wx_code: str) -> str:
        """wx_code → musicid/musickey → 拼成 Cookie 串；失败返回空。"""
        body = {
            "comm": {"cv": 13020508, "v": 13020508, "ct": "11", "tmeAppID": "qqmusic",
                     "format": "json", "inCharset": "utf-8", "outCharset": "utf-8",
                     "uid": "0", "tmeLoginType": "1"},
            "music.login.LoginServer.Login": {
                "module": "music.login.LoginServer", "method": "Login",
                "param": {"code": wx_code, "strAppid": "wx48db31d50e334801"},
            },
        }
        r = session.post("https://u.y.qq.com/cgi-bin/musicu.fcg", json=body,
                         headers={"Referer": "https://y.qq.com/"}, timeout=20)
        login = (r.json() or {}).get("music.login.LoginServer.Login") or {}
        if login.get("code") not in (0, None):
            return ""
        user = login.get("data") or {}
        musicid = str(user.get("str_musicid") or user.get("musicid") or user.get("uin") or "")
        musickey = user.get("musickey") or user.get("music_key") or ""
        if not musicid or not musickey:
            return ""
        return (f"uin={musicid}; qqmusic_key={musickey}; qm_keyst={musickey}; "
                f"tmeLoginType=1; wxuin={musicid};")

    def _netease_qr(self):
        from netease.music_api import QRLoginManager
        dlg, qr_label, status = self._qr_dialog("网易云扫码登录")

        def run():
            try:
                manager = QRLoginManager()
                unikey = manager.generate_qr_key()
                if not unikey:
                    post_to_main(status.setText, "❌ 生成二维码失败")
                    return
                import qrcode
                qr = qrcode.QRCode(border=1)
                qr.add_data(f"https://music.163.com/login?codekey={unikey}")
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                self._show_qr(qr_label, img)
                post_to_main(status.setText, "📱 请用网易云音乐 APP 扫码")
                while dlg.isVisible():
                    time.sleep(2)
                    code, cookies = manager.check_qr_login(unikey)
                    if code == 803:
                        cookie_str = f"MUSIC_U={cookies.get('MUSIC_U', '')};os=pc;appver=8.9.70;"
                        auth.set_cookie("netease", cookie_str)
                        auth.sync_netease_to_legacy()
                        self._whoami_cache.pop("netease", None)
                        post_to_main(self._on_qr_success, dlg, "网易云登录成功，Cookie 已保存")
                        return
                    elif code == 801:
                        post_to_main(status.setText, "⏳ 等待扫码...")
                    elif code == 802:
                        post_to_main(status.setText, "✅ 扫码成功，请在手机上确认")
                    else:
                        post_to_main(status.setText, f"登录失败，错误码 {code}")
            except Exception as e:
                post_to_main(status.setText, f"❌ {e}")

        threading.Thread(target=run, daemon=True).start()

    def _bilibili_qr(self):
        import qrcode
        import requests
        dlg, qr_label, status = self._qr_dialog("B站扫码登录")
        UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0.0.0 Safari/537.36"}
        GEN = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
        POLL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={}"

        def run():
            try:
                s = requests.Session()
                d = s.get(GEN, headers=UA, timeout=15).json()
                if d.get("code") != 0:
                    post_to_main(status.setText, "❌ 生成二维码失败")
                    return
                url, key = d["data"]["url"], d["data"]["qrcode_key"]
                qr = qrcode.QRCode(border=1)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                self._show_qr(qr_label, img)
                post_to_main(status.setText, "📱 请用哔哩哔哩 APP 扫码")
                while dlg.isVisible():
                    time.sleep(2)
                    pd = s.get(POLL.format(key), headers=UA, timeout=15).json().get("data", {})
                    code = pd.get("code")
                    if code == 0:
                        cookies = dict(s.cookies)
                        if "SESSDATA" not in cookies:
                            u = pd.get("url") or ""
                            if u:
                                s.get(u, headers=UA, timeout=15)
                                cookies = dict(s.cookies)
                        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
                        auth.set_cookie("bilibili", cookie_str)
                        self._whoami_cache.pop("bilibili", None)
                        post_to_main(self._on_qr_success, dlg, "B站登录成功，Cookie 已保存")
                        return
                    elif code == 86101:
                        post_to_main(status.setText, "⏳ 等待扫码...")
                    elif code == 86090:
                        post_to_main(status.setText, "✅ 扫码成功，请在手机上确认")
                    elif code == 86038:
                        post_to_main(status.setText, "⚠️ 二维码已失效，请重新登录")
                        return
                    else:
                        post_to_main(status.setText, f"登录失败，错误码 {code}")
            except Exception as e:
                post_to_main(status.setText, f"❌ {e}")

        threading.Thread(target=run, daemon=True).start()

    def _on_qr_success(self, dlg, msg):
        try:
            dlg.close()
        except Exception:
            pass
        self._reload()
        self._notify()
        QMessageBox.information(self, "登录成功", msg)

    def _notify(self):
        if self.on_changed:
            self.on_changed()
