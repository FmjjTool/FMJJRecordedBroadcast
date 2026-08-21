"""Cookie 管理对话框（PySide6 版）：平台列表 + 扫码/浏览器/输入/清除。

二维码轮询在后台线程执行，所有 UI 更新通过 post_to_main 调度回主线程。
"""

import io
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

COOKIE_PLATFORMS = [
    ("bilibili", "B站", True, True),
    ("douyin", "抖音", False, True),
    ("weibo", "微博", False, True),
    ("netease", "网易云", True, False),
    ("qq", "QQ音乐", False, False),
    ("kugou", "酷狗", False, False),
    ("kuwo", "酷我", False, False),
    ("5sing", "5sing", False, False),
    ("xiaohongshu", "小红书", False, False),
    ("kuaishou", "快手", False, False),
]


class CookieManagerDialog(QDialog):
    def __init__(self, parent, on_changed=None):
        super().__init__(parent)
        self.setWindowTitle("Cookie 管理")
        self.resize(500, 600)
        self.on_changed = on_changed

        layout = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.container)
        layout.addWidget(scroll)
        self._reload()

    def _reload(self):
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cookies = auth.load_cookies()
        netease_has = bool((auth.get_netease_cookie() or "").strip())
        for plat, name, can_qr, can_browser in COOKIE_PLATFORMS:
            if plat == "netease":
                has = netease_has
                age = auth.get_cookie_age("netease") if has else ""
            else:
                has = bool(cookies.get(plat, "") and len(cookies.get(plat, "")) > 10)
                age = auth.get_cookie_age(plat)
            row = QHBoxLayout()
            row.addWidget(QLabel(name, styleSheet="font-weight: bold;"))
            status = ("🟢 已登录" if has else "🔴 未登录") + (f" · {age}" if age else "")
            sl = QLabel(status)
            sl.setStyleSheet("color: #4caf50;" if has else "color: #ff5252;")
            row.addWidget(sl)
            row.addStretch(1)
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

    def _btn(self, text, slot, primary=False, danger=False):
        b = QPushButton(text)
        b.clicked.connect(slot)
        b.setFixedHeight(28)
        if primary:
            b.setStyleSheet("background: #2e7d32; color: white; border-radius: 4px; padding: 2px 8px;")
        elif danger:
            b.setStyleSheet("background: #c62828; color: white; border-radius: 4px; padding: 2px 8px;")
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
            QMessageBox.warning(self, "不可用", "内嵌浏览器组件缺失（QtWebEngine），请改用「输入」手动粘贴 Cookie")
            return

        configs = {
            "weibo": ("https://m.weibo.cn/", "微博登录（软件内）", ("SUB",)),
            "douyin": ("https://www.douyin.com/", "抖音登录（软件内）", ("sessionid",)),
            "bilibili": ("https://www.bilibili.com/", "B站登录（软件内）", ("SESSDATA",)),
        }
        if plat not in configs:
            QMessageBox.information(self, "提示", f"{plat} 暂不支持浏览器登录，请使用「输入」")
            return
        url, title, auth_keys = configs[plat]

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(1100, 760)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("  在下方页面完成登录后，将自动保存 Cookie 并关闭本窗口（最长等待 3 分钟）")
        hint.setStyleSheet("color: #555; padding: 4px; background: #ffffff;")
        v.addWidget(hint)

        # 独立持久 profile：cookieStore 持续接收 cookieAdded（含 HttpOnly）
        profile = QWebEngineProfile(f"login_{plat}", dlg)
        profile.setHttpUserAgent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
        page = QWebEnginePage(profile, dlg)
        view = QWebEngineView(dlg)
        view.setPage(page)
        v.addWidget(view, 1)

        jar: dict[str, str] = {}
        state = {"saved": False, "timer": None}

        def on_cookie_added(cookie):
            try:
                name = bytes(cookie.name()).decode("utf-8", "ignore")
                value = bytes(cookie.value()).decode("utf-8", "ignore")
            except Exception:
                return
            if not name or not value:
                return
            jar[name] = value
            if name in auth_keys and not state["saved"]:
                # 登录特征 cookie 出现 → 延时 2s 等其余 cookie 到齐再保存
                if state["timer"] is not None:
                    state["timer"].stop()
                t = QTimer(dlg)
                t.setSingleShot(True)
                t.timeout.connect(save_and_close)
                state["timer"] = t
                t.start(2000)

        def save_and_close():
            if state["saved"] or not any(k in jar for k in auth_keys):
                return
            state["saved"] = True
            cookie_str = "; ".join(f"{k}={v}" for k, v in jar.items())
            auth.set_cookie(plat, cookie_str)
            self._reload()
            self._notify()
            dlg.close()

        profile.cookieStore().cookieAdded.connect(on_cookie_added)

        view.load(QUrl(url))
        dlg.show()
        QTimer.singleShot(180000, dlg.close)  # 3 分钟超时自动关

    def _qr_login(self, plat):
        if plat == "netease":
            self._netease_qr()
        elif plat == "bilibili":
            self._bilibili_qr()

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
        data = buf.getvalue()
        qimg = QImage.fromData(data)
        pm = QPixmap.fromImage(qimg).scaled(280, 280, Qt.KeepAspectRatio, Qt.SmoothTransformation)

        def _set():
            try:
                qr_label.setPixmap(pm)
            except Exception:
                pass
        post_to_main(_set)

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
