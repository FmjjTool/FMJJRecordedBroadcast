"""Cookie 管理对话框（PySide6 版）：平台列表 + 扫码/浏览器/输入/清除。

二维码轮询在后台线程执行，所有 UI 更新通过 post_to_main 调度回主线程。
"""

import io
import re
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QInputDialog, QLabel, QMessageBox, QPushButton,
    QScrollArea, QVBoxLayout, QWidget,
)

import auth
from gui_qt.common import post_to_main

# (key, 中文名, 支持扫码, 支持浏览器登录)
# 扫码：网易云/B站 原生接口，QQ音乐 走微信扫码换 musickey；
# 酷狗/酷我/5sing/小红书/快手 无公开扫码 API，登录页内扫码由「浏览器」入口承载
COOKIE_PLATFORMS = [
    ("bilibili", "B站", True, True),
    ("douyin", "抖音", False, True),
    ("weibo", "微博", False, True),
    ("netease", "网易云", True, False),
    ("qq", "QQ音乐", True, True),
    ("kugou", "酷狗", False, True),
    ("kuwo", "酷我", False, True),
    ("5sing", "5sing", False, True),
    ("xiaohongshu", "小红书", False, True),
    ("kuaishou", "快手", False, True),
]


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
        try:
            import playwright  # noqa: F401
            self.pw_status.setText("浏览器组件：✅ 已安装（Playwright）")
            self.pw_status.setStyleSheet("color: #4caf50;")
            # 已安装：按钮变灰防误点（置灰样式见 _btn 的 :disabled 规则）
            self.pw_btn.setEnabled(False)
            self.pw_btn.setText("✅ 组件已就绪")
            return
        except ImportError:
            pass
        self.pw_status.setText("浏览器组件：❌ 未安装（未登录抖音/微博时采集需要）")
        self.pw_status.setStyleSheet("color: #ff5252;")
        self.pw_btn.setEnabled(True)
        self.pw_btn.setText("📦 下载浏览器组件")

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
        """后台安装 playwright + chromium，弹日志窗口显示进度。"""
        if getattr(sys, "frozen", False):
            QMessageBox.information(
                self, "提示",
                "安装版内置了免登录方案（软件内浏览器登录 + Cookie 直采），无需此组件。\n"
                "该安装功能仅在源码运行（python app_qt.py）时可用。")
            return
        self._pip_install_dialog(
            "下载浏览器组件",
            "正在安装 Playwright + Chromium（约 150 MB，取决于网速）…",
            [("pip install playwright", ["-m", "pip", "install", "playwright"]),
             ("playwright install chromium", ["-m", "playwright", "install", "chromium"])],
            "浏览器组件安装完成！\n现在未登录抖音/微博也可以采集数据了。",
            on_ok=lambda ok: self._refresh_pw_status(),
        )

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
            if can_qr:
                row.addWidget(self._btn("扫码登录", lambda p=plat: self._qr_login(p), True))
            if can_browser:
                row.addWidget(self._btn("浏览器", lambda p=plat: self._browser_login(p)))
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
                else:
                    has = bool((auth.load_cookies().get(plat, "") or "").strip())
                age = auth.get_cookie_age(plat) if has else ""
                labels[1].setText(self._status_text(plat, has, age))
                labels[1].setStyleSheet(self._status_color(plat, has))
                return

    def _verify(self, plat):
        """手动验证：清缓存重新拉取登录用户名。"""
        self._whoami_cache.pop(plat, None)
        self._start_probe(plat)

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
            self._whoami_cache.pop(plat, None)
            self._reload()
            self._notify()
        except Exception as e:
            QMessageBox.critical(self, "清除失败", f"{name} Cookie 清除失败：\n{e}")

    def _browser_login(self, plat):
        """软件内嵌浏览器登录：窗口内加载平台页，自动捕获 Cookie 后保存关闭。"""
        try:
            from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            if not getattr(sys, "frozen", False):
                # 源码运行且 PySide6 是精简安装（Essentials 不含 WebEngine）→ 一键补装
                ret = QMessageBox.question(
                    self, "缺少组件",
                    "内嵌浏览器组件（QtWebEngine）未安装，通常是 PySide6 精简安装导致。\n"
                    "是否现在自动安装 PySide6-Addons（约 150 MB）？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                if ret == QMessageBox.Yes:
                    self._pip_install_dialog(
                        "安装 QtWebEngine 组件",
                        "正在安装 PySide6-Addons（含内嵌浏览器组件）…",
                        [("pip install PySide6-Addons", ["-m", "pip", "install", "PySide6-Addons"])],
                        "组件安装完成！请重启软件后再点「浏览器」登录。",
                    )
                else:
                    QMessageBox.warning(self, "不可用", "可先改用「输入」手动粘贴 Cookie")
                return
            QMessageBox.warning(self, "不可用", "内嵌浏览器组件缺失（QtWebEngine），请改用「输入」手动粘贴 Cookie")
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
            "kuaishou": ("https://www.kuaishou.com/", "快手登录（软件内）",
                         ("passToken",), "点击「登录」→ 扫码或手机号登录"),
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
        hint = QLabel("  在下方页面完成登录后，点击「✅ 已获取」按钮保存 Cookie（无时间限制，由你决定）"
                      + (f"　💡 {login_hint}" if login_hint else ""))
        hint.setStyleSheet("color: #555; padding: 4px; background: #ffffff;")
        v.addWidget(hint)

        # 独立持久 profile：cookieStore 持续接收 cookieAdded（含 HttpOnly）
        profile = QWebEngineProfile(f"login_{plat}", dlg)
        profile.setHttpUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
        page = QWebEnginePage(profile, dlg)
        view = QWebEngineView(dlg)
        view.setPage(page)
        v.addWidget(view, 1)

        # 底部操作栏：只有用户点「已获取」才保存，不自动不超时
        bar = QHBoxLayout()
        self_keys = "/".join(auth_keys)
        cap_lbl = QLabel(f"  已捕获 {0} 个 Cookie（登录特征：{self_keys}）")
        cap_lbl.setStyleSheet("color: #888; padding: 4px;")
        bar.addWidget(cap_lbl, 1)
        got_btn = QPushButton("✅ 已获取（保存 Cookie）")
        got_btn.setStyleSheet("background: #2e7d32; color: white; border-radius: 4px; padding: 4px 14px;")
        got_btn.clicked.connect(lambda *args: save_now())
        bar.addWidget(got_btn)
        cancel_btn = QPushButton("放弃")
        cancel_btn.clicked.connect(dlg.close)
        bar.addWidget(cancel_btn)
        bar_w = QWidget()
        bar_w.setLayout(bar)
        v.addWidget(bar_w)

        jar: dict[str, str] = {}

        def on_cookie_added(cookie):
            try:
                name = bytes(cookie.name()).decode("utf-8", "ignore")
                value = bytes(cookie.value()).decode("utf-8", "ignore")
            except Exception:
                return
            if not name or not value:
                return
            jar[name] = value
            hit = any(k in jar for k in auth_keys)
            cap_lbl.setText(f"  已捕获 {len(jar)} 个 Cookie" + (f"（✅ 检测到登录特征 {self_keys}）" if hit else f"（登录特征：{self_keys}）"))
            cap_lbl.setStyleSheet("color: #2e7d32; padding: 4px;" if hit else "color: #888; padding: 4px;")

        def save_now():
            if not jar:
                QMessageBox.warning(dlg, "无 Cookie", "尚未捕获到任何 Cookie，请先在页面完成登录")
                return
            if not any(k in jar for k in auth_keys):
                ret = QMessageBox.question(
                    dlg, "未检测到登录特征",
                    f"未检测到登录特征 Cookie（{self_keys}），当前捕获 {len(jar)} 个。\n"
                    "可能尚未完成登录，仍要保存吗？",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                if ret != QMessageBox.Yes:
                    return
            cookie_str = "; ".join(f"{k}={v}" for k, v in jar.items())
            try:
                auth.set_cookie(plat, cookie_str)
                self._whoami_cache.pop(plat, None)
                self._reload()
                self._notify()
            except Exception as e:
                QMessageBox.critical(dlg, "保存失败", f"Cookie 写入失败：\n{e}")
                return
            dlg.close()

        profile.cookieStore().cookieAdded.connect(on_cookie_added)

        view.load(QUrl(url))
        dlg.show()

    def _qr_login(self, plat):
        if plat == "netease":
            self._netease_qr()
        elif plat == "bilibili":
            self._bilibili_qr()
        elif plat == "qq":
            self._qq_qr()

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
