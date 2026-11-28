"""账号与登录对话框（PySide6 版）。

- 第一页「登录状态」：平台列表 + 扫码/浏览器/输入/清除。
- 第二页「账号配置」：三个功能（信息获取 / 网媒追踪 / 音乐追踪）共用的账号与分组管理，
  勾选账号可用于哪些功能（原各页「设置」页已迁移到这里）。

二维码轮询在后台线程执行，所有 UI 更新通过 post_to_main 调度回主线程。
"""

import io
import os
import re
import sqlite3
import threading
import time

from PySide6.QtCore import QEvent, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea, QSizePolicy,
    QTabWidget, QVBoxLayout, QWidget,
)

import auth
from gui_qt.common import post_to_main, run_bg

# (key, 中文名, 支持扫码, 支持浏览器登录)
# 扫码：网易云/B站 原生接口，QQ音乐 走微信扫码换 musickey，
# 酷狗概念版 走概念版登录接口（会话存 config/kugou_lite_session.json，非 Cookie）；
# 酷狗/酷我/5sing/小红书/快手/淘宝 无公开扫码 API，登录页内扫码由「浏览器」入口承载
COOKIE_PLATFORMS = [
    ("bilibili", "B站", True, True),
    ("acfun", "AcFun", False, True),
    ("douyin", "抖音", False, True),
    ("weibo", "微博", False, True),
    ("netease", "网易云（会员下载）", True, False),
    ("netease_playlist", "网易云（歌单创建）", True, False),
    ("qq", "QQ音乐", True, True),
    ("kugou", "酷狗", False, True),
    ("kugou_lite", "酷狗概念版", True, False),
    ("kuwo", "酷我", False, True),
    ("5sing", "5sing", False, True),
    ("xiaohongshu", "小红书", False, True),
    ("kuaishou", "快手", False, True),
    ("taobao", "淘宝", False, True),
    ("xianyu", "闲鱼", False, True),
    ("tcdoc", "腾讯文档", False, True),
    ("qqgroup", "QQ群", False, True),
    ("quark", "夸克网盘", False, True),
    ("baidu", "百度网盘", False, True),
    ("yun139", "移动云盘", False, True),
    ("115", "115网盘", False, True),
]


# 浏览器登录入口配置：(url, 标题, 登录特征 Cookie, 登录操作提示)
# 登录特征 = 登录后才出现、能一眼认出来的 Cookie 名；判不准的平台另见
# REMOTE_VERIFY_PLATFORMS（再问一次站内接口，tracker.whoami.verify）
BROWSER_LOGIN_CONFIGS = {
    # 微博：直达 passport SSO 登录页（扫码 / 手机验证码 / 账号密码都能用），
    # url 参数登录后跳回 m.weibo.cn（采集侧用的是 m 站接口与 SUB 票）
    "weibo": ("https://passport.weibo.com/sso/signin?entry=wapsso&source=wapsso"
              "&url=https%3A%2F%2Fm.weibo.cn%2F", "微博登录（软件内）", ("SUB",),
              "扫码 / 手机验证码 / 账号密码登录（直达 passport 登录页）"),
    "douyin": ("https://www.douyin.com/", "抖音登录（软件内）", ("sessionid",), ""),
    # AcFun：登录后种 acPasstoken（长期票）；acPlatform 游客也有，不能当判据，
    # 最终以站内 personalInfo 接口为准（见 REMOTE_VERIFY_PLATFORMS）
    "acfun": ("https://www.acfun.cn/", "AcFun登录（软件内）",
              ("acPasstoken",), "点击右上角「登录」→ 扫码 / 账号密码登录（回到首页算成功）"),
    "bilibili": ("https://www.bilibili.com/", "B站登录（软件内）", ("SESSDATA",), ""),
    "qq": ("https://y.qq.com/", "QQ音乐登录（软件内）",
           ("uin", "qm_keyst"), "点击右上角「登录」→ 扫码或账号登录"),
    # 酷狗网页端把整个登录态塞在 KuGoo Cookie 里（KugooID=..&NickName=..&Pic=..），
    # 没有独立的 KugooID Cookie（旧版写成 KugouID 单 o，更找不到）
    "kugou": ("https://www.kugou.com/", "酷狗登录（软件内）",
              ("KuGoo", "KugooID", "userid"), "点击右上角「登录」→ 扫码或账号登录"),
    "kuwo": ("https://www.kuwo.cn/", "酷我登录（软件内）",
             ("userid",), "点击顶部「登录」→ 手机号验证码登录"),
    # 5sing 登录后种 5sing_auth / 5sing_ssid（游客没有），但没有它们也不代表没登录，
    # 最终以站内 user/checkstatus 接口为准（见 REMOTE_VERIFY_PLATFORMS）
    "5sing": ("http://5sing.kugou.com/login", "5sing登录（软件内）",
              ("5sing_auth", "5sing_ssid"), "账号密码登录（支持酷狗账号）"),
    "xiaohongshu": ("https://www.xiaohongshu.com/", "小红书登录（软件内）",
                    ("web_session",), "点击「登录」→ 扫码或短信验证"),
    "kuaishou": ("https://www.kuaishou.com/account/login", "快手登录（软件内）",
                 # web_st=30天票；webday7_st=7天票（新版常见，只认前者会误报未登录）
                 ("kuaishou.server.web_st", "kuaishou.server.webday7_st"),
                 "扫码 / 手机号验证码登录（直达登录页，避开首页登录按钮被备案号遮挡）"),
    "taobao": ("https://www.taobao.com/", "淘宝登录（软件内）",
               ("cookie2", "unb", "sg"),
               "点击顶部「登录」→ 扫码或手机号登录（商品列表采集用）"),
    "xianyu": ("https://www.goofish.com/", "闲鱼登录",
               ("cookie2", "unb"), "在闲鱼官网完成登录，仅用于商品搜索与详情读取"),
    # 腾讯文档：浏览器登录后除 Cookie 外还要存 localStorage（文档页的登录信息也存这里），
    # 所以登录完成时额外落一份 Playwright storage_state（见 _do_save 的 tcdoc 分支）
    "tcdoc": ("https://docs.qq.com/desktop", "腾讯文档登录（软件内）",
              ("uid", "uid_key", "DOC_SID"),
              "扫码 / 微信 / QQ 登录（登录后回文档列表即算成功）"),
    # QQ群：登录后 qinfo 群资料接口按群号读群名/人数；skey/p_skey 用于 bkn
    "qqgroup": ("https://qun.qq.com/", "QQ群登录（软件内）",
                ("uin", "skey", "p_skey"),
                "QQ扫码 / 账号密码登录（进入群管理页即算成功）"),
    # 夸克网盘：登录态在 __puus / __pus（约 3 小时一轮，页面活动会自动续期），
    # 备份任务用同一份 Cookie 直连 drive-pc.quark.cn
    "quark": ("https://pan.quark.cn/", "夸克网盘登录（软件内）",
              ("__puus", "__pus"),
              "打开夸克 App 扫码登录，或手机号 + 验证码登录（进到网盘文件页即算成功）"),
    # 移动云盘：网页登录会把会话写进 Cookie（token / skey / auth_token），
    # 带上它们就能调 /orchestration/... 的云盘接口
    "yun139": ("https://yun.139.com/", "移动云盘登录（软件内）",
               ("token", "skey"),
               "手机号 + 验证码 / 密码登录（进到「我的云盘」文件列表即算成功）"),
    # 百度网盘：登录票是 BDUSS / STOKEN，进到文件列表页即算成功
    "baidu": ("https://pan.baidu.com/disk/main", "百度网盘登录（软件内）",
              ("BDUSS", "STOKEN"),
              "扫码 / 账号密码登录（登录后进到网盘文件列表即算成功）"),
    # 115 网盘：登录后种 UID / CID / SEID 三件套，网页接口（webapi.115.com）
    # 与下载直链都靠它；登录窗停在文件列表页即可
    "115": ("https://115.com/?ct=file&ac=file&mode=wangpan", "115网盘登录（软件内）",
            ("UID", "SEID"),
            "扫码 / 账号密码登录（进到「我的文件」列表即算成功）"),
}

# 登录窗里"顺手抓昵称"的 JS（接口风控严、只有页面上下文问得到的平台）
# 返回字符串；抓不到返回空串（不猜 DOM，免得把别人的昵称存进来）
_NICK_FETCH_JS = {
    "kuaishou": """async () => {
      try {
        const r = await fetch('/rest/wd/user/profile', {credentials: 'include',
          headers: {'Accept': 'application/json'}});
        const j = await r.json();
        const u = (j && (j.userProfile || j.data)) || j || {};
        return (u.user_name || u.userName || u.name || '').toString().trim();
      } catch (e) { return ''; }
    }""",
}


def _looks_logged_in(plat: str, jar: dict) -> bool:
    """登录窗里判断"可以抓昵称了"：出现登录票据 Cookie 才算，避免抓游客空数据。"""
    if plat == "kuaishou":
        return any(k.startswith("kuaishou.server.web") or k == "passToken" for k in jar)
    return True


def _save_nick(plat: str, userid, name: str) -> None:
    """把登录时抓到的昵称写进缓存（whoami 取不到昵称时用它展示）。"""
    try:
        from tracker import nick_cache
        nick_cache.save(plat, userid, name)
    except Exception:
        pass


# Cookie 名判不准、需要额外问站内接口确认登录态的平台
# （5sing 的会话 Cookie 游客也有，只有 user/checkstatus 说得准）
REMOTE_VERIFY_PLATFORMS = {"5sing", "acfun"}


def _kugou_lite_state() -> tuple[bool, str]:
    """酷狗概念版登录态 + 展示名。返回 (已登录, 名字文本)。

    名字来源优先级：会话文件里的 nickname（扫码登录时存）→ 同 userid 的酷狗
    主账号昵称 → 「用户ID xxx」。概念版没有可用的用户信息接口，只能这样取。
    """
    try:
        from music.scrapers.kugou_lite import load_session
        d = load_session()
        if not d.get("token"):
            return False, ""
        uid = str(d.get("userid") or "")
        name = str(d.get("nickname") or "").strip()
        if not name and uid:
            from tracker import whoami
            name = whoami.kugou_nick_for(uid)
        return True, name or (f"用户ID {uid}" if uid else "")
    except Exception:
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


class _PageScroll(QScrollArea):
    """整页滚动区：宽度跟随视口，高度按内容自适应，最小尺寸不随内容变大。

    不用 setWidgetResizable(True) 是因为页面里有换行 QLabel，布局的
    heightForWidth 会把内容高度锁死，滚动区就再也压不下去（弹窗最小高度
    随之被顶大，超过最大高度后 Qt 会忽略最大尺寸，拖动窗口就会失控放大）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWidgetResizable(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def setWidget(self, widget):
        super().setWidget(widget)
        self.sync_content()

    def sync_content(self):
        widget = self.widget()
        if widget is None:
            return
        width = max(1, self.viewport().width())
        hfw = widget.heightForWidth(width)
        height = hfw if hfw > 0 else widget.sizeHint().height()
        height = max(height, self.viewport().height())
        if widget.width() != width or widget.height() != height:
            widget.resize(width, height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_content()

    def sizeHint(self):
        return QSize(640, 420)

    def minimumSizeHint(self):
        return QSize(320, 200)


class CookieManagerDialog(QDialog):
    def __init__(self, parent, on_changed=None, initial_tab=0):
        super().__init__(parent)
        self.setWindowTitle("账号与登录")
        self._want_size = (820, 640)
        self.on_changed = on_changed

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)

        # ---- 第一页：各平台登录状态 ----
        cookie_tab = QWidget()
        cookie_layout = QVBoxLayout(cookie_tab)
        cookie_layout.setContentsMargins(0, 0, 0, 0)
        cookie_layout.setSpacing(6)
        # 只显示管理层实际绑定的路径，不迁移或导入另一处登录记录。
        self.storage_location = QLabel()
        path_text = str(auth.COOKIE_FILE)
        shown_path = (path_text if len(path_text) <= 46
                      else path_text[:16] + "…" + path_text[-27:])
        self.storage_location.setText(f"登录记录位置：{shown_path}")
        self.storage_location.setWordWrap(True)
        self.storage_location.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.storage_location.setToolTip(
            f"{path_text}\n登录数据只从此位置读取；更新不会合并或覆盖其他位置的 Cookie。")
        cookie_layout.addWidget(self.storage_location)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setAlignment(Qt.AlignTop)
        scroll.setWidget(self.container)
        cookie_layout.addWidget(scroll, 1)

        # 登录用户名探测：缓存 {plat: {"ok":..,"username":..} | "probing"}
        self._whoami_cache: dict = {}
        self._whoami_probing: set = set()

        # 底部：浏览器环境状态（安装统一在「全局设置 → 检测依赖」，此处只显示与刷新）
        bottom = QHBoxLayout()
        self.pw_status = QLabel()
        # 状态文字较长，允许压缩（完整内容看 tooltip），避免撑宽弹窗
        self.pw_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        bottom.addWidget(self.pw_status, 1)
        self.openapi_btn = self._btn("腾讯文档 OpenAPI", self._open_openapi_credentials)
        bottom.addWidget(self.openapi_btn)
        self.github_btn = self._btn("GitHub 账户", self._open_github_account)
        bottom.addWidget(self.github_btn)
        self.pw_btn = self._btn("刷新环境", self._refresh_pw_status, True)
        bottom.addWidget(self.pw_btn)
        cookie_layout.addLayout(bottom)

        self.tabs.addTab(cookie_tab, "登录状态")

        # ---- 第二页：账号配置（原三个功能页的「设置」页） ----
        from gui_qt.account_center import AccountCenterWidget
        self.account_center = AccountCenterWidget(app=parent, parent=self)
        self.account_center.changed.connect(self._notify)
        # 本页内容较高，外面套一层滚动区：弹窗的最小尺寸才不会被内容高度顶大。
        # 否则「最小尺寸 > 最大尺寸」时 Qt 会忽略最大尺寸，拖动放大就会失控。
        center_scroll = _PageScroll()
        center_scroll.setWidget(self.account_center)
        self.account_center.changed.connect(center_scroll.sync_content)
        self.tabs.addTab(center_scroll, "账号配置")

        if 0 <= initial_tab < self.tabs.count():
            self.tabs.setCurrentIndex(initial_tab)
        self._refresh_pw_status()
        self._reload()
        self._wechat_timer = QTimer(self)
        self._wechat_timer.setInterval(1000)
        self._wechat_timer.timeout.connect(self._refresh_wechat_session_status)
        self._wechat_timer.start()
        self._fit_to_screen(*self._want_size)

    def _fit_to_screen(self, want_w: int, want_h: int):
        """按屏幕可用区域定尺寸：既能小于屏幕，也绝不超过屏幕。

        Qt 里 maximumSize 小于最小尺寸时会被忽略（窗口反被最小尺寸顶大，
        拖动时表现为「越拉越炸」），所以先量可用区域，再取两者的合理值。
        """
        screen = None
        if self.parent() is not None:
            screen = self.parent().screen()
        screen = screen or QApplication.primaryScreen()
        if screen is None:
            self.resize(want_w, want_h)
            return
        avail = screen.availableGeometry()
        max_w = max(420, avail.width() - 30)
        max_h = max(320, avail.height() - 40)
        self.setMaximumSize(max_w, max_h)
        width = max(420, min(want_w, max_w))
        height = max(320, min(want_h, max_h))
        self.resize(width, height)
        anchor = None
        if self.parentWidget() is not None:
            anchor = self.parentWidget().window().frameGeometry().center()
        else:
            anchor = avail.center()
        self.move(anchor.x() - width // 2, anchor.y() - height // 2)
        self._keep_on_screen()

    def _keep_on_screen(self):
        """把窗口夹在屏幕可用区域内（拖动放大时同样生效）。"""
        screen = self.screen() or QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        width = min(self.width(), max(360, avail.width() - 8))
        height = min(self.height(), max(300, avail.height() - 8))
        x = min(max(self.x(), avail.left()), max(avail.left(), avail.right() - width + 1))
        y = min(max(self.y(), avail.top()), max(avail.top(), avail.bottom() - height + 1))
        if (width, height) != (self.width(), self.height()):
            self.resize(width, height)
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "_clamping", False):
            return
        self._clamping = True
        try:
            self._keep_on_screen()
        finally:
            self._clamping = False

    def _open_openapi_credentials(self):
        _TcdocOpenApiDialog(self).exec()

    def _open_github_account(self):
        _GitHubTokenDialog(self).exec()

    def _open_wechat_session(self):
        dialog = getattr(self, "_wechat_detection_dialog", None)
        if dialog is None:
            dialog = _WechatReadingDetectionDialog(self)
            self._wechat_detection_dialog = dialog
            dialog.session_ready.connect(self._wechat_session_ready)
            dialog.finished.connect(
                lambda _result, current=dialog: self._wechat_detection_finished(current))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _wechat_session_ready(self):
        self._refresh_wechat_session_status()
        self._notify()

    def _wechat_detection_finished(self, dialog):
        if getattr(self, "_wechat_detection_dialog", None) is dialog:
            self._wechat_detection_dialog = None
        dialog.deleteLater()
        result = getattr(self, "_wechat_pending_result", None)
        self._wechat_pending_result = None
        if result is not None:
            QTimer.singleShot(0, lambda: self.done(result))

    def _wechat_can_close(self, result):
        dialog = getattr(self, "_wechat_detection_dialog", None)
        if dialog is not None and not dialog.request_close():
            self._wechat_pending_result = result
            return False
        return True

    def done(self, result):
        if self._wechat_can_close(result):
            super().done(result)

    def reject(self):
        self.done(QDialog.Rejected)

    def accept(self):
        self.done(QDialog.Accepted)

    def closeEvent(self, event):
        if not self._wechat_can_close(QDialog.Rejected):
            event.ignore()
            return
        super().closeEvent(event)

    def _clear_wechat_session(self):
        from wechat_session import clear_session
        clear_session()
        self._refresh_wechat_session_status()
        self._notify()

    def _refresh_wechat_session_status(self):
        label = getattr(self, "_wechat_status", None)
        if label is None:
            return
        from wechat_session import session_status
        status = session_status()
        if status["ready"]:
            minutes = (status["remaining_seconds"] + 59) // 60
            label.setText(f"阅读信息已提供 · 本地约剩 {minutes} 分钟")
        else:
            label.setText("临时阅读未就绪")
        self._wechat_clear.setEnabled(status["ready"])

    def _add_wechat_session_row(self):
        # 独立于长期登录记录列表，绝不进入 auth 的保存/清除/探测路径。
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(6)
        row.addWidget(QLabel("微信公众号"))
        self._wechat_status = QLabel()
        self._wechat_status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._wechat_status.setToolTip("阅读会话仅驻留内存；过期、清除或账号使用关闭后立即不可用。")
        row.addWidget(self._wechat_status, 1)
        enter = QPushButton("自动检测")
        enter.clicked.connect(self._open_wechat_session)
        row.addWidget(enter)
        self._wechat_clear = QPushButton("清除")
        self._wechat_clear.setProperty("variant", "danger")
        self._wechat_clear.clicked.connect(self._clear_wechat_session)
        row.addWidget(self._wechat_clear)
        self.container_layout.addWidget(widget)
        self._refresh_wechat_session_status()

    def _refresh_pw_status(self):
        """浏览器环境状态：Playwright 库 + 系统 Edge/Chrome + 本地采集引擎（只读检测）。"""
        try:
            import playwright  # noqa: F401
        except ImportError:
            self._set_pw_status(
                "浏览器组件：Playwright 未安装（采集需要，请在「全局设置 → 检测依赖」安装）",
                "#ff5252")
            return
        try:
            from toolbox.browser import detect
            env = detect()
        except Exception:
            env = {"edge": None, "chrome": None, "shell": False}
        edge, chrome, shell = env["edge"], env["chrome"], env["shell"]
        if edge or chrome:
            which = f"Edge {edge}" if edge else f"Chrome {chrome}"
            self._set_pw_status(
                f"浏览器环境：系统 {which}（采集走系统浏览器，无需下载）", "#4caf50")
        elif shell:
            self._set_pw_status("浏览器环境：本地采集引擎已就绪", "#4caf50")
        else:
            self._set_pw_status(
                "浏览器环境：无系统 Edge/Chrome，请在「全局设置 → 检测依赖」下载采集引擎（约 91MB）",
                "#eab308")

    def _set_pw_status(self, text: str, color: str):
        self.pw_status.setText(text)
        self.pw_status.setToolTip(text)
        self.pw_status.setStyleSheet(f"color: {color};")

    def _reload(self):
        while self.container_layout.count():
            item = self.container_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cookies = auth.load_cookies()
        netease_has = bool(auth.get_netease_cookie_stored().strip())
        for plat, name, can_qr, can_browser in COOKIE_PLATFORMS:
            lite_name = ""  # 概念版的展示名（会话文件/同账号酷狗昵称）
            if plat == "netease":
                has = netease_has
                age = auth.get_cookie_age("netease") if has else ""
            elif plat == "kugou_lite":
                has, lite_name = _kugou_lite_state()
                age = _kugou_lite_age() if has else ""
            else:
                has = bool(cookies.get(plat, "") and len(cookies.get(plat, "")) > 10)
                age = auth.get_cookie_age(plat)
            row = QHBoxLayout()
            row.setContentsMargins(8, 2, 8, 2)
            row.setSpacing(6)
            row.addWidget(QLabel(name, styleSheet="font-weight: bold;"))
            sl = QLabel(self._status_text(plat, has, age, lite_name))
            sl.setStyleSheet(self._status_color(plat, has))
            # 状态文字（含长昵称）允许被压缩，否则整行最小宽度会把弹窗撑得过宽
            sl.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            sl.setToolTip(self._status_tooltip(plat, has, age, lite_name))
            row.addWidget(sl, 1)
            if has:
                row.addWidget(self._btn("验证", lambda p=plat: self._verify(p)))
            if plat == "kugou_lite":
                row.addWidget(self._btn("领VIP", lambda: self._kugou_lite_claim_vip(), True))
            if plat == "kugou":
                row.addWidget(self._btn("App扫码", self._kugou_app_qr, True))
            if plat == "kuwo":
                row.addWidget(self._btn("App扫码", self._kuwo_app_qr, True))
            if can_qr:
                row.addWidget(self._btn("扫码登录", lambda p=plat: self._qr_login(p), True))
            if can_browser:
                row.addWidget(self._btn("浏览器", lambda p=plat: self._browser_login(p)))
            if plat != "kugou_lite":
                row.addWidget(self._btn("输入", lambda p=plat: self._input(p)))
            row.addWidget(self._btn("清除", lambda p=plat: self._clear(p), danger=True))
            w = QWidget()
            w.setLayout(row)
            w.setProperty("platform", plat)
            w.setStyleSheet("QWidget { background: #ffffff; border-radius: 6px; }")
            self.container_layout.addWidget(w)

        self._add_wechat_session_row()

        # 自动检测只在两处发生：进入软件时（main_window 启动后台跑一遍全量）与
        # 单个平台刚登录时（保存/扫码后已用 forget_platform_login 作废该平台缓存）。
        # 因此这里只补测「没有记录」的平台，其余直接用已有结果——打开账号配置
        # 不再全量重测、也不再按时间过期重测，避免频繁请求平台接口。
        from toolbox import account_health
        records = account_health.records()
        stored = auth.load_cookies()
        pending = []
        for plat, _n, _q, _b in COOKIE_PLATFORMS:
            if plat == "kugou_lite":
                continue  # 概念版登录态在会话文件，走 _kugou_lite_verify
            cookie = (auth.get_netease_cookie_stored() if plat == "netease"
                      else stored.get(plat, "")) or ""
            if len(cookie) > 10 and plat not in records:
                pending.append(plat)
        self._start_probes(pending)

    def _mark_relogged(self, plat):
        """刚登录成功的平台：作废它的登录态缓存，随后 _reload 只补测这一个平台。"""
        self._whoami_cache.pop(plat, None)
        from toolbox import account_health
        account_health.forget_platform_login(plat)

    @staticmethod
    def _short(s: str, limit: int = 20) -> str:
        """超长文本截断（如 QQ 音乐微信登录的 19 位 uin），避免撑破行布局。"""
        s = s or ""
        return s if len(s) <= limit else s[:limit - 3] + "..."

    def _status_text(self, plat, has, age, name=""):
        """状态文字：登录态由 account_health 中间层给出（酷狗概念版走会话文件）。

        name：非 Cookie 平台的展示名（如酷狗概念版来自会话文件）。
        最后成功时间 = 中间层记录的 ok_at，登录态掉掉后仍能看到「上次正常」。
        """
        from toolbox import account_health
        info = self._login_info(plat, has)
        state = info["state"]
        shown = self._short(info.get("username") or name)
        ok_at = account_health.stamp(info.get("ok_at")) if info.get("ok_at") else ""
        if state == "probing":
            return "验证中…" + (f" · {shown}" if shown else "") + (f" · {age}" if age else "")
        if state == "ok":
            base = "已登录" + (f" · {shown}" if shown else "")
            when = account_health.human_age(info.get("ok_at")) or age
            return base + (f" · {when}" if when else "")
        if state == "warn":
            base = "无法验证" + (f" · {shown}" if shown else "")
            return base + (f" · 上次正常 {ok_at}" if ok_at else "")
        if state == "bad":
            base = "登录态已失效" if has else "未登录"
            return base + (f" · 上次正常 {ok_at}" if ok_at else "")
        # unknown：还没检测完，先按有没有存过登录态显示
        base = "已保存 · 待验证" if has else "未登录"
        return base + (f" · {shown}" if shown else "") + (f" · {age}" if age else "")

    def _login_info(self, plat, has=False):
        """{state, username, detail, checked_at, ok_at}；酷狗概念版由会话文件判定。"""
        from toolbox import account_health
        if plat == "kugou_lite":
            cached = self._whoami_cache.get(plat)
            if cached == "probing" or plat in self._whoami_probing:
                return {"state": "probing", "username": "", "detail": "",
                        "checked_at": 0, "ok_at": 0}
            if cached is None:
                # 还没验证过：概念版只有会话文件，存在即先按已登录展示
                return {"state": "ok" if has else "bad", "username": "",
                        "detail": "", "checked_at": 0, "ok_at": 0}
            ok = cached.get("ok")
            state = "ok" if ok is True else ("bad" if ok is False else "warn")
            return {"state": state, "username": cached.get("username") or "",
                    "detail": "", "checked_at": 0, "ok_at": 0}
        info = dict(account_health.status(plat))
        if plat in self._whoami_probing:
            info["state"] = "probing"
        return info

    def _status_color(self, plat, has):
        state = self._login_info(plat, has)["state"]
        return {"ok": "color: #4caf50;", "warn": "color: #eab308;",
                "bad": "color: #ff5252;"}.get(state, "color: #9e9e9e;")

    def _status_tooltip(self, plat, has, age, name=""):
        from toolbox import account_health
        info = self._login_info(plat, has)
        lines = [account_health.STATE_TEXT.get(info["state"], info["state"])]
        if info.get("username") or name:
            lines.append(f"登录账号：{info.get('username') or name}")
        if info.get("detail"):
            lines.append(info["detail"])
        if info.get("checked_at"):
            lines.append(f"本次检测：{account_health.stamp(info['checked_at'])}"
                         f"（{account_health.human_age(info['checked_at'])}）")
        if info.get("ok_at"):
            lines.append(f"上次正常：{account_health.stamp(info['ok_at'])}"
                         f"（{account_health.human_age(info['ok_at'])}）")
        if age:
            lines.append(f"登录记录保存于 {age}")
        return "\n".join(lines)

    def _start_probes(self, plats):
        """批量检测：一次交给中间层并发校验，结果落盘供各处复用。"""
        from gui_qt.common import post_to_main

        plats = [p for p in plats if p and p not in self._whoami_probing]
        if not plats:
            return
        self._whoami_probing.update(plats)
        for plat in plats:
            self._render_status(plat)

        def run():
            from toolbox import account_health
            try:
                account_health.refresh(accounts=[], only_platforms=plats, workers=4)
            except Exception:
                pass

            def done():
                self._whoami_probing.difference_update(plats)
                for plat in plats:
                    self._render_status(plat)
            post_to_main(done)

        threading.Thread(target=run, daemon=True).start()

    def _start_probe(self, plat):
        self._start_probes([plat])

    def _render_status(self, plat):
        """只更新对应行的状态文字（避免整表重建闪烁）。"""
        for i in range(self.container_layout.count()):
            w = self.container_layout.itemAt(i).widget()
            if not w:
                continue
            labels = w.findChildren(QLabel)
            if w.property("platform") != plat:
                continue
            if labels:
                lite_name = ""
                if plat == "netease":
                    has = bool(auth.get_netease_cookie_stored().strip())
                    age = auth.get_cookie_age(plat) if has else ""
                elif plat == "kugou_lite":
                    has, lite_name = _kugou_lite_state()
                    age = _kugou_lite_age() if has else ""
                else:
                    has = bool((auth.load_cookies().get(plat, "") or "").strip())
                    age = auth.get_cookie_age(plat) if has else ""
                labels[1].setText(self._status_text(plat, has, age, lite_name))
                labels[1].setStyleSheet(self._status_color(plat, has))
                labels[1].setToolTip(self._status_tooltip(plat, has, age, lite_name))
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
            _has, lite_name = _kugou_lite_state()
            try:
                from music.scrapers.kugou_lite import KuGouLiteClient
                ok, msg = KuGouLiteClient().verify_session()
                info = {"ok": ok, "username": lite_name}
            except Exception as e:  # noqa: BLE001 - 网络异常按验证失败展示
                info = {"ok": False, "username": ""}
                msg = str(e)

            def done():
                self._whoami_cache["kugou_lite"] = info
                self._render_status("kugou_lite")
                if info.get("ok") is False:
                    QMessageBox.warning(self, "酷狗概念版验证", f"{msg}")
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
            self._whoami_cache.pop(plat, None)
            from toolbox import account_health
            account_health.forget_platform_login(plat)
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
            from tracker import nick_cache
            nick_cache.drop(plat)  # 昵称缓存跟着登录态一起清
            if plat == "kugou_lite":
                # 概念版登录态在会话文件（非 Cookie 统一存储），需一并清除
                from music.scrapers.kugou_lite import clear_session
                clear_session()
            if plat == "tcdoc":
                # 腾讯文档还有一份 Playwright storage_state 快照，一起删掉
                # （否则清了 Cookie 但快照还在，采集侧仍认为已登录）
                from toolbox.tcdoc import session as tc_session
                tc_session.clear()
            self._whoami_cache.pop(plat, None)
            from toolbox import account_health
            account_health.forget_platform_login(plat)  # 登录态清除后账号卡片重新判定
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

        configs = BROWSER_LOGIN_CONFIGS
        # 快手登录浏览器回种白名单：只保留设备/环境字段。
        # 登录态票（*_st/passToken/userId）过期后回种会同名覆盖新票；
        # pt_*/p_*/qrsig 等是 QQ 互联残留，会干扰快手登录流程。
        _KS_LOGIN_KEEP = {
            "did", "didv", "clientid", "kpf", "kpn",
            "_bl_uid", "__aegis_uid", "kwfv1", "ktrace-context",
        }
        # 抖音特例：回种旧的登录票据（sessionid/sid_guard…）会让页面直接显示已登录，
        # 用户点「已获取」只是把过期票据又存一遍 —— 登录态永远刷新不了（2026-11 实测）。
        # 只回种设备/环境字段，登录票全部靠本次新登录种下。
        _DY_LOGIN_DROP = {
            "sessionid", "sessionid_ss", "sid_tt", "sid_guard", "sid_ucp_v1",
            "ssid_ucp_v1", "uid_tt", "uid_tt_ss", "session_tlb_tag",
            "passport_csrf_token", "passport_csrf_token_default",
            "passport_auth_mix_state", "passport_mfa_token", "d_ticket",
            "n_mh", "csrf_session_id", "login_time", "is_staff_user",
            "has_biz_token", "x_tt_token",
        }
        if plat not in configs:
            QMessageBox.information(self, "提示", f"{plat} 暂不支持浏览器登录，请使用「输入」")
            return
        url, title, auth_keys, login_hint = configs[plat]
        # Cookie 名判不准的平台：登录态由站内接口确认（底部提示会显示结论）
        need_remote = plat in REMOTE_VERIFY_PLATFORMS

        dlg = QDialog(self)
        dlg.setWindowTitle(title)
        dlg.resize(1100, 780)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("  在下方页面完成登录后，点击「已获取」按钮保存 Cookie（页面已自动登录时可直接点击）"
                      + ("　登录态会自动向站内接口确认" if need_remote else "")
                      + (f"　{login_hint}" if login_hint else ""))
        hint.setStyleSheet("color: #555; padding: 4px; background: #ffffff;")
        v.addWidget(hint)

        # 旧持久 profile 时代留下的登录态一次性迁移（必须在 profile 创建前读库）
        if plat != "tcdoc" and _import_legacy_profile(plat, auth_keys):
            self._whoami_cache.pop(plat, None)
            self._reload()
            self._notify()

        # 贴合宿主：worker 找到浏览器窗口句柄后，由主线程去边框贴合此区域
        host = QWidget()
        host.setAttribute(Qt.WA_NativeWindow)  # 原生句柄取物理坐标（免 DPI 换算）
        host.setStyleSheet("background: #ffffff;")
        host_lay = QVBoxLayout(host)
        host_lay.setContentsMargins(0, 0, 0, 0)
        host_lbl = QLabel("正在启动登录浏览器…", host)
        host_lbl.setAlignment(Qt.AlignCenter)
        host_lbl.setStyleSheet("color: #888; background: #ffffff;")
        host_lay.addWidget(host_lbl)
        v.addWidget(host, 1)

        def _set_host_hint(text):
            """宿主区域提示文案（'' = 隐藏，浏览器贴上来后调用）。"""
            try:
                host_lbl.setText(text)
                host_lbl.setVisible(bool(text))
            except Exception:
                pass

        # 底部操作栏：只有用户点「已获取」才保存，不自动不超时
        bar = QHBoxLayout()
        self_keys = "/".join(auth_keys)
        cap_lbl = QLabel("  已捕获 0 个 Cookie" + (
            "（登录态：等待页面加载后向站内接口确认）" if need_remote
            else f"（登录特征：{self_keys}）"))
        cap_lbl.setStyleSheet("color: #888; padding: 4px;")
        bar.addWidget(cap_lbl, 1)
        reload_btn = QPushButton("刷新")
        reload_btn.setStyleSheet("color: #1565c0;")
        reload_btn.setToolTip("页面白屏/卡死/加载异常时点此重新加载（Cookie 捕获不受影响）")
        reload_btn.clicked.connect(lambda *args: state["reload"].set())
        bar.addWidget(reload_btn)
        got_btn = QPushButton("已获取（保存 Cookie）")
        got_btn.setStyleSheet("background: #2e7d32; color: white; border-radius: 4px; padding: 4px 14px;")
        got_btn.clicked.connect(lambda *args: save_now())
        bar.addWidget(got_btn)
        pop_btn = QPushButton("⌨ 改独立窗口")
        pop_btn.setStyleSheet("color: #1565c0;")
        pop_btn.setToolTip("页面按钮/输入异常，或需要拖动缩放浏览器时点此弹出为独立窗口；"
                           "再点一次贴回登录框（Cookie 捕获不受影响）")
        pop_btn.setEnabled(False)  # 嵌入成功后才可用
        pop_btn.clicked.connect(lambda *args: _toggle_pop())
        bar.addWidget(pop_btn)
        cancel_btn = QPushButton("放弃")
        cancel_btn.clicked.connect(dlg.close)
        bar.addWidget(cancel_btn)
        bar_w = QWidget()
        bar_w.setLayout(bar)
        v.addWidget(bar_w)

        jar: dict[str, str] = {}
        # 同名不同域 Cookie（如 5sing 的会话 Cookie 可能同时挂在 5sing.kugou.com
        # 与 .kugou.com 上）：主值验证不过时逐个换着试
        jar_alt: dict[str, list[str]] = {}
        probe = {"ok": None, "name": "", "running": False, "last": 0.0,
                 "sig": -1, "best": ""}

        def _jar_str(d: dict) -> str:
            return "; ".join(f"{k}={v}" for k, v in d.items())

        def _variants():
            """捕获到的 Cookie 串候选：主值 + 同名备选（一次只换一个名）。"""
            yield _jar_str(jar)
            for name, alts in jar_alt.items():
                for v in alts[:2]:
                    d = dict(jar)
                    d[name] = v
                    yield _jar_str(d)

        def _paint_cap():
            n = len(jar)
            cap_lbl.setToolTip("、".join(sorted(jar)))
            hit = any(k in jar for k in auth_keys)
            if not need_remote:
                cap_lbl.setText(f"  已捕获 {n} 个 Cookie" + (
                    f"（检测到登录特征 {self_keys}）" if hit
                    else f"（登录特征：{self_keys}）"))
                cap_lbl.setStyleSheet(
                    "color: #2e7d32; padding: 4px;" if hit else "color: #888; padding: 4px;")
                return
            # 需要站内接口确认：接口结论优先，接口没结论时才用 Cookie 名兜底
            ok, name = probe["ok"], probe["name"]
            if ok is True:
                txt, color = (f"  已捕获 {n} 个 Cookie（已登录"
                              + (f"：{name}" if name else "") + "）"), "#2e7d32"
            elif ok is False:
                txt, color = ((f"  已捕获 {n} 个 Cookie（检测到登录 Cookie，"
                               "但站内接口判定未登录）") if hit else
                              (f"  已捕获 {n} 个 Cookie"
                               "（站内接口判定未登录，请先在页面完成登录）")), "#ef6c00"
            elif probe["running"]:
                txt, color = (f"  已捕获 {n} 个 Cookie"
                              + ("（检测到登录 Cookie，正在向站内接口确认…）" if hit
                                 else "（正在向站内接口确认登录态…）")), (
                    "#2e7d32" if hit else "#888")
            elif hit:
                txt, color = (f"  已捕获 {n} 个 Cookie（检测到登录特征 {self_keys}，"
                              "接口不可达未能确认昵称）"), "#2e7d32"
            elif n:
                txt, color = (f"  已捕获 {n} 个 Cookie"
                              "（站内接口不可达，无法自动确认；可直接保存）"), "#888"
            else:
                txt, color = "  已捕获 0 个 Cookie（等待页面加载…）", "#888"
            cap_lbl.setText(txt)
            cap_lbl.setStyleSheet(f"color: {color}; padding: 4px;")

        def _remote_check(force=False):
            """Cookie 名判不准的平台：拿捕获到的 Cookie 直接问站内接口。

            在页面里跑 JSONP 会受页面协议/CORS 限制，这里在后台线程请求接口；
            主值不通过时把同名不同域的备选值也试一遍。
            """
            if not need_remote or not jar or probe["running"]:
                return
            now = time.time()
            sig = len(jar)
            if not force and sig == probe["sig"] and now - probe["last"] < 5:
                return
            probe["sig"], probe["last"], probe["running"] = sig, now, True
            candidates = list(_variants())[:4]
            _paint_cap()

            def run():
                from tracker import whoami
                info, best = {"ok": None, "username": ""}, ""
                for ck in candidates:
                    r = whoami.verify(plat, ck)
                    if r.get("ok") is True:
                        info, best = r, ck
                        break
                    if r.get("ok") is False and info.get("ok") is None:
                        info = r

                def done():
                    probe["running"] = False
                    probe["ok"] = info.get("ok")
                    probe["name"] = info.get("username") or ""
                    probe["best"] = best
                    _paint_cap()
                post_to_main(done)

            threading.Thread(target=run, daemon=True).start()

        def _captured_login() -> bool:
            """本次捕获是否已经拿到登录态（供「已获取」判断要不要确认覆盖）。"""
            if any(k in jar for k in auth_keys):
                return True
            return need_remote and probe["ok"] is True

        def _refresh_cap():
            _paint_cap()
            _remote_check()

        # ==== Win32 覆盖层辅助（Playwright 启动的 Chromium 系窗口类名）====
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
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

        def _window_rect(hwnd):
            """窗口屏幕矩形 (l, t, r, b)；失败返回 None。"""
            from ctypes import wintypes
            rc = wintypes.RECT()
            try:
                if not user32.GetWindowRect(int(hwnd), ctypes.byref(rc)):
                    return None
            except Exception:
                return None
            return rc.left, rc.top, rc.right, rc.bottom

        def _cursor_in(hwnd):
            """鼠标是否落在浏览器窗口矩形内（用户正在页面里操作）。"""
            rc = _window_rect(hwnd) if hwnd else None
            if not rc:
                return False
            from ctypes import wintypes
            pt = wintypes.POINT()
            try:
                if not user32.GetCursorPos(ctypes.byref(pt)):
                    return False
            except Exception:
                return False
            return rc[0] <= pt.x < rc[2] and rc[1] <= pt.y < rc[3]

        def _mouse_down():
            """左键是否按下（拖动窗口/在页面里拖拽时不要抢前台）。"""
            try:
                return bool(user32.GetAsyncKeyState(0x01) & 0x8000)
            except Exception:
                return False

        def _activate(hwnd):
            """把浏览器窗口真正拿到系统前台（Chromium 非激活状态会丢弃点击/按键）。

            后台进程直接 SetForegroundWindow 会被 Windows 前台锁静默拒绝（这正是
            「内嵌后页面按钮点不动、只能改独立窗口」的根因）：把前台窗口和浏览器
            窗口的输入线程都 AttachThreadInput 到本线程，调用结束后再解绑，
            SetFocus 才能真正落到 Chromium 的窗口上。
            """
            if not hwnd:
                return False
            try:
                fg = user32.GetForegroundWindow()
                tid_me = kernel32.GetCurrentThreadId()
                attached = []
                for wnd in (fg, hwnd):
                    if not wnd:
                        continue
                    tid = user32.GetWindowThreadProcessId(wnd, None)
                    if tid and tid != tid_me and tid not in attached:
                        if user32.AttachThreadInput(tid_me, tid, True):
                            attached.append(tid)
                try:
                    user32.BringWindowToTop(hwnd)
                    user32.SetForegroundWindow(hwnd)
                    user32.SetFocus(hwnd)
                finally:
                    for tid in attached:
                        user32.AttachThreadInput(tid_me, tid, False)
                return user32.GetForegroundWindow() == hwnd
            except Exception:
                return False

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
                if _fg_root() == hwnd:
                    return True
                # 前台还是登录框、鼠标已经进到页面区域：用户正要点页面，
                # 提前保持显示（否则这一刻被收起，点击就落在空白处）
                return _fg_root() == int(dlg.winId()) and _cursor_in(hwnd)
            except Exception:
                return True

        def _resync(activate=False):
            """按激活状态同步贴合层可见性（显示时同时贴合+抬 z 序）。

            activate=True 或鼠标已在页面里时，顺带把前台交给浏览器窗口：
            Chromium 未拿到系统激活就不派发点击（页面按钮点了没反应）。
            """
            hwnd = state.get("hwnd") or 0
            if not hwnd or not state.get("overlay") or state.get("box_open"):
                return
            if _overlay_should_show():
                _bw_show(hwnd, True)
                _reposition()
                if (activate or _cursor_in(hwnd)) and _fg_root() != hwnd \
                        and not _mouse_down():
                    _activate(hwnd)
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
                        if _mouse_down():
                            return  # 正在页面里点击/拖拽：此刻不要动窗口
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

        def _apply_fit(hwnd):
            """去边框 + 工具窗口 + 贴合 host，并把前台交给浏览器。

            保持顶层独立窗口是关键：作子窗口嵌入时 Chromium 收不到系统激活
            消息（WM_ACTIVATE/WM_ACTIVATEAPP），会按「未激活」丢弃按键，登录框
            完全打不了字（实测）；顶层贴合方案下键盘输入原生可用。
            可重复调用（弹出后再次贴回共用）。"""
            try:
                style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                user32.SetWindowLongW(hwnd, GWL_STYLE,
                                      int(style) & ~(WS_CAPTION | WS_THICKFRAME))
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE,
                                      int(ex) | WS_EX_TOOLWINDOW)  # 不进任务栏/Alt-Tab
                state["overlay"] = True
                _set_host_hint("")
                _resync(activate=True)
                # NOACTIVATE 贴入的窗口常常一次激活不成功（前台锁、页面还没
                # 首帧都会让 Chromium 处于未激活，点击被丢弃）—— 多点几次，
                # 已经在前台时 _resync 内部会直接跳过，不会抢输入焦点
                for ms in (120, 350, 800, 1500, 2600):
                    QTimer.singleShot(ms, lambda: _resync(True))
                return True
            except Exception:
                return False

        def _embed(hwnd):
            """主线程：浏览器改成无边框顶层窗口，贴合 host 区域（视觉等同内嵌）。"""
            state["hwnd"] = hwnd
            if not _apply_fit(hwnd):
                pass  # 贴合失败时浏览器保持独立窗口，登录功能不受影响
            else:
                pop_btn.setText("⌨ 改独立窗口")
                pop_btn.setToolTip("页面按钮/输入异常，或需要拖动缩放浏览器时点此"
                                   "弹出为独立窗口；再点一次贴回登录框")
                pop_btn.setEnabled(True)
            if not state.get("focus_filter"):
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
            _refresh_cap()

        def _find_window(before, timeout=8.0):
            """等 Playwright 启动的浏览器窗口出现（只看窗口，不等页面加载）。"""
            deadline = time.time() + timeout
            while time.time() < deadline and not state["stop"].is_set():
                new = [h for h in _enum_browser_windows() if h not in before]
                if new:
                    # 取面积最大的那个：启动瞬间可能还有「首次运行」之类的小弹窗
                    best, area = 0, -1
                    for h in new:
                        rc = _window_rect(h)
                        if not rc:
                            continue
                        a = (rc[2] - rc[0]) * (rc[3] - rc[1])
                        if a > area:
                            best, area = h, a
                    return best or new[0]
                time.sleep(0.12)
            return None

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
                    # 腾讯文档重新登录使用干净的临时上下文：扁平串已经丢失域信息，
                    # 回种到 .qq.com 会让旧票与扫码新票并存，干扰跨域登录跳转。
                    stored = "" if plat == "tcdoc" else auth.load_cookies().get(plat, "")
                    if plat == "kuaishou":
                        # 快手特例：过期的登录态票（web*_st/passToken/userId）与
                        # QQ 互联残留（pt_*/p_*/qrsig 等）回种后会同名覆盖新登录
                        # 种下的票，收割拿到假 Cookie（2026-10 实测）。只回种
                        # 设备/环境字段，登录态全靠本次新种。
                        keep = _KS_LOGIN_KEEP
                        stored = "; ".join(
                            p.strip() for p in stored.split(";")
                            if p.strip().split("=")[0].strip() in keep)
                    elif plat == "douyin":
                        # 抖音同理：过期 sessionid 回种后页面显示「已登录」，
                        # 点「已获取」只会把旧票再存一遍（2026-11 实测）
                        stored = "; ".join(
                            p.strip() for p in stored.split(";")
                            if p.strip().split("=")[0].strip() not in _DY_LOGIN_DROP)
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
                    # 先贴进登录框、再导航：窗口一出现就嵌入，页面在自己的窗口里
                    # 加载并显示进度条，不必等导航完成。原来的顺序是 goto 返回后
                    # 才找窗口 —— 慢站点要空白等几十秒，goto 超时更是永远不贴。
                    hwnd = _find_window(before, 3.5)
                    if hwnd:
                        post_to_main(lambda h=hwnd: _embed(h))
                    # commit = 拿到响应头就返回（比 domcontentloaded 早得多），
                    # 慢页面不再把嵌入和 Cookie 捕获一起堵住
                    try:
                        page.goto(url, wait_until="commit", timeout=45000)
                    except Exception:
                        # 已贴进登录框：页面会自己慢加载，也可点「刷新」重试
                        if not hwnd:
                            post_to_main(lambda: _set_host_hint(
                                "页面加载较慢，稍候…（可点下方「刷新」重试）"))
                    if not hwnd:
                        hwnd = _find_window(before, 10.0)
                        if hwnd:
                            post_to_main(lambda h=hwnd: _embed(h))
                    # 轮询捕获（全量含 HttpOnly；窗口被用户手动关闭时退出）
                    nick_tries = 0  # 昵称抓取次数（接口风控严，只在登录窗里问得到）
                    poll_i = 0
                    while not state["stop"].is_set():
                        time.sleep(1.5)
                        poll_i += 1
                        # 见到登录票据后再问、6s 一次、最多 12 次（登录往往要等一会儿）
                        if (plat in _NICK_FETCH_JS and nick_tries < 12
                                and poll_i % 4 == 0 and _looks_logged_in(plat, jar)):
                            nick_tries += 1
                            try:
                                got = page.evaluate(_NICK_FETCH_JS[plat])
                            except Exception:
                                got = ""
                            if isinstance(got, str) and got.strip():
                                _save_nick(plat, jar.get("userId") or "", got)
                                nick_tries = 99  # 拿到了就不再问
                        if state["reload"].is_set():
                            state["reload"].clear()
                            try:  # 白屏/卡死的救急重载，失败保留现状
                                page.reload(wait_until="domcontentloaded",
                                            timeout=45000)
                            except Exception:
                                pass
                            continue
                        try:
                            captured_cookies = ctx.cookies()
                            for c in captured_cookies:
                                n, val = c.get("name"), c.get("value")
                                if n and val:
                                    prev = jar.get(n)
                                    if prev is not None and prev != val:
                                        alts = jar_alt.setdefault(n, [])
                                        if prev not in alts:
                                            alts.append(prev)
                                    jar[n] = val
                            # 只读取当前页面/框架：完整 storage_state 会临时开页，
                            # 扫码跨域期间不应每 6 秒触发这类辅助页面导航。
                            if plat == "tcdoc" and poll_i % 4 == 0:
                                try:
                                    from toolbox.tcdoc.session import capture_login_state
                                    state["storage_state"] = capture_login_state(
                                        ctx, state.get("storage_state"), cookies=captured_cookies)
                                except Exception:
                                    pass
                            post_to_main(_refresh_cap)
                        except Exception:
                            break
            except Exception as e:
                def _fail(msg=str(e)):
                    _set_host_hint("浏览器启动失败，请改用「输入」手动粘贴 Cookie")
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
            pop_btn.setText("↩ 贴回登录框")
            pop_btn.setToolTip("浏览器已弹出为独立窗口；点此重新贴回登录框")
            pop_btn.setEnabled(True)
            _set_host_hint("浏览器已在独立窗口中打开（不遮挡主界面），"
                           "登录完成后回到这里点「已获取」")
            try:
                style = user32.GetWindowLongW(hwnd, GWL_STYLE)
                user32.SetWindowLongW(hwnd, GWL_STYLE,
                                      int(style) | WS_CAPTION | WS_THICKFRAME)
                ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                user32.SetWindowLongW(hwnd, GWL_EXSTYLE, int(ex) & ~WS_EX_TOOLWINDOW)
                # 尾部 0x4040 = SWP_SHOWWINDOW|SWP_ASYNCWINDOWPOS
                user32.SetWindowPos(hwnd, 0, 100, 100, 1100, 760, 0x4040)
                _activate(hwnd)
            except Exception:
                pass

        def _re_embed():
            """把弹出为独立窗口的浏览器重新贴回登录框。"""
            hwnd = state.get("hwnd") or 0
            if not hwnd:
                return
            if _apply_fit(hwnd):
                pop_btn.setText("⌨ 改独立窗口")
                pop_btn.setToolTip("页面按钮/输入异常，或需要拖动缩放浏览器时点此"
                                   "弹出为独立窗口；再点一次贴回登录框")
                pop_btn.setEnabled(True)

        def _toggle_pop():
            """同一个按钮：贴合 ↔ 独立窗口 互相切换。"""
            if state.get("overlay"):
                _pop_out()
            else:
                _re_embed()

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
            # 站内接口验证通过的那一组候选优先（同名不同域时才是对的会话 Cookie）
            cookie_str = probe["best"] or _jar_str(jar)
            try:
                auth.set_cookie(plat, cookie_str)
                if plat == "tcdoc":
                    # 腾讯文档登录态还要 localStorage 那一半，顺手落一份
                    # Playwright storage_state（采集侧直接用它开浏览器）
                    from toolbox.tcdoc import session as tc_session
                    tc_session.save_state_snapshot(state.get("storage_state"))
                self._whoami_cache.pop(plat, None)
                from toolbox import account_health
                account_health.forget_platform_login(plat)  # 登录态变了，账号卡片重算
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
            if not _captured_login():
                # 只有用「没拿到登录态的捕获」覆盖「已存有效登录」时才确认；
                # 其余直接保存（旧版每次都弹「仍要保存吗」，且模态弹窗会卡死）
                stored = auth.load_cookies().get(plat, "")
                had_login = (any(k in stored for k in auth_keys) if auth_keys
                             else bool(stored.strip()))
                if had_login:
                    why = f"登录特征 {self_keys}" if auth_keys else "站内接口"
                    if need_remote:
                        why += "／站内接口"
                    _confirm_box(
                        "覆盖确认",
                        f"当前捕获 {len(jar)} 个 Cookie，但未检测到登录态（{why}）。\n"
                        "保存会覆盖已存的有效登录，确定吗？",
                        _do_save)
                    return
            _do_save()

        dlg.closeEvent = lambda ev: _cleanup()

        threading.Thread(target=worker, daemon=True).start()
        dlg.show()

    def _qr_login(self, plat):
        if plat in ("netease", "netease_playlist"):
            self._netease_qr(plat)
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
                post_to_main(status.setText, "请用酷狗概念版 App 扫码")
                while dlg.isVisible():
                    time.sleep(2)
                    r = client.qr_poll(key)
                    st = r.get("status")
                    if st == 4 and r.get("logged_in"):
                        post_to_main(self._on_qr_success, dlg,
                                    "酷狗概念版登录成功（会话已保存，下次免扫码）")
                        return
                    if st == 4:
                        post_to_main(status.setText, "已确认但未返回 token，请重试")
                        return
                    if st == 0:
                        post_to_main(status.setText, "二维码已失效，请重新登录")
                        return
                    if st == 2:
                        post_to_main(status.setText, "扫码成功，请在手机上确认")
                    elif r.get("message") and st != 1:
                        post_to_main(status.setText, f"{r['message']}")
            except Exception as e:
                post_to_main(status.setText, f"{e}")

        threading.Thread(target=run, daemon=True).start()

    def _kugou_app_qr(self):
        """酷狗主版 App 扫码：换取云歌单写入所需的 App 令牌。

        只把令牌存到独立的 App 会话文件，不改动统一登录里已保存的网页登录态。
        """
        dlg, qr_label, status = self._qr_dialog("酷狗 App 扫码登录")

        def run():
            try:
                from music import kugou_app_auth
                key, png = kugou_app_auth.qr_create()
                self._show_qr_bytes(qr_label, png)
                post_to_main(status.setText, "请用酷狗 App 扫码（用于云歌单写入，码会自动刷新）")
                while dlg.isVisible():
                    time.sleep(2)
                    result = kugou_app_auth.qr_poll(key)
                    state = result.get("status")
                    if state == 4 and result.get("logged_in"):
                        post_to_main(self._on_qr_success, dlg,
                                     "酷狗 App 登录成功，云歌单写入已就绪")
                        return
                    if state == 4:
                        post_to_main(status.setText, "已确认但未返回令牌，请重试")
                        return
                    if state == 0:
                        # 过期就自动换一张，不让用户抢时间
                        key, png = kugou_app_auth.qr_create()
                        post_to_main(self._show_qr_bytes, qr_label, png)
                        post_to_main(status.setText, "二维码已刷新，请用酷狗 App 重新扫码")
                        continue
                    if state == 2:
                        post_to_main(status.setText, "扫码成功，请在手机上确认")
                    else:
                        post_to_main(status.setText, result.get("message") or "等待扫码")
            except Exception as exc:  # noqa: BLE001 - 登录失败原因直接展示
                post_to_main(status.setText, f"{exc}")

        threading.Thread(target=run, daemon=True).start()

    def _kuwo_app_qr(self):
        """酷我 App 扫码：拿 uid + sid，供云歌单写入（pl.svc）使用。

        只写独立会话文件，不改动统一登录里的网页登录态。
        """
        dlg, qr_label, status = self._qr_dialog("酷我 App 扫码登录")

        def run():
            try:
                from music import kuwo_app_auth
                token, image = kuwo_app_auth.qr_create()
                self._show_qr_bytes(qr_label, image)
                post_to_main(status.setText, "请用酷我 App 扫码（用于歌单写入，码会自动刷新）")
                while dlg.isVisible():
                    time.sleep(2)
                    result = kuwo_app_auth.qr_poll(token)
                    state = result.get("status")
                    if state == "ok":
                        post_to_main(self._on_qr_success, dlg,
                                     "酷我 App 登录成功，歌单写入已就绪")
                        return
                    if state == "scanned":
                        post_to_main(status.setText, "扫码成功，请在手机上确认登录")
                    elif state == "expired":
                        token, image = kuwo_app_auth.qr_create()
                        post_to_main(self._show_qr_bytes, qr_label, image)
                        post_to_main(status.setText, "二维码已刷新，请用酷我 App 重新扫码")
                    elif state == "failed":
                        post_to_main(status.setText, result.get("message") or "扫码失败")
                        return
                    else:
                        post_to_main(status.setText, result.get("message") or "等待扫码")
            except Exception as exc:  # noqa: BLE001 - 登录失败原因直接展示
                post_to_main(status.setText, f"{exc}")

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
                    f"{'成功' if ok else '失败'} {msg}\n\n"
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
        status = QLabel("生成二维码中...")
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
                    post_to_main(status.setText, "生成二维码失败")
                    return
                uuid = m.group(1)
                img = s.get(f"https://open.weixin.qq.com/connect/qrcode/{uuid}",
                            headers={"Referer": CONNECT}, timeout=20)
                if not img.content:
                    post_to_main(status.setText, "二维码图片获取失败")
                    return
                self._show_qr_bytes(qr_label, img.content)
                post_to_main(status.setText, "请用微信扫码（QQ音乐登录）")
                while dlg.isVisible():
                    p = s.get(POLL, params={"uuid": uuid, "_": int(time.time() * 1000)},
                              headers={"Referer": "https://open.weixin.qq.com/"}, timeout=35)
                    m_err = re.search(r"window\.wx_errcode=(\d+);", p.text)
                    m_code = re.search(r"window\.wx_code='([^']*)';", p.text)
                    err = m_err.group(1) if m_err else ""
                    if err == "405" and m_code and m_code.group(1):
                        cookie_str = self._qq_exchange_wxcode(s, m_code.group(1))
                        if not cookie_str:
                            post_to_main(status.setText, "获取登录凭据失败，请重试")
                            return
                        auth.set_cookie("qq", cookie_str)
                        self._mark_relogged("qq")
                        post_to_main(self._on_qr_success, dlg, "QQ音乐登录成功，Cookie 已保存")
                        return
                    elif err == "404":
                        post_to_main(status.setText, "扫码成功，请在手机上确认")
                    elif err == "403":
                        post_to_main(status.setText, "二维码已失效，请重新登录")
                        return
                    elif err == "408":
                        post_to_main(status.setText, "等待扫码...")
                    time.sleep(2)
            except Exception as e:
                post_to_main(status.setText, f"{e}")

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
        # 微信登录的账号日后问不到昵称（musicu 用户接口恒 500003），登录这一刻存下来
        nick = (user.get("nick") or user.get("nickname") or user.get("str_nick")
                or user.get("name") or "")
        _save_nick("qq", musicid, nick)
        return (f"uin={musicid}; qqmusic_key={musickey}; qm_keyst={musickey}; "
                f"tmeLoginType=1; wxuin={musicid};")

    def _netease_qr(self, slot: str = "netease"):
        """网易云扫码登录；slot 区分会员下载账号与歌单创建账号。"""
        from netease.music_api import QRLoginManager

        playlist = slot == "netease_playlist"
        title = "网易云歌单账号扫码登录" if playlist else "网易云扫码登录"
        saved = ("网易云歌单账号登录成功，Cookie 已保存（建歌单使用这个账号）"
                 if playlist else "网易云登录成功，Cookie 已保存（下载 / 采集使用）")
        dlg, qr_label, status = self._qr_dialog(title)

        def run():
            try:
                manager = QRLoginManager()
                unikey = manager.generate_qr_key()
                if not unikey:
                    post_to_main(status.setText, "生成二维码失败")
                    return
                import qrcode
                qr = qrcode.QRCode(border=1)
                qr.add_data(f"https://music.163.com/login?codekey={unikey}")
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                self._show_qr(qr_label, img)
                post_to_main(status.setText, "请用网易云音乐 APP 扫码")
                while dlg.isVisible():
                    time.sleep(2)
                    code, cookies = manager.check_qr_login(unikey)
                    if code == 803:
                        cookie_str = f"MUSIC_U={cookies.get('MUSIC_U', '')};os=pc;appver=8.9.70;"
                        auth.set_cookie(slot, cookie_str)
                        self._mark_relogged(slot)
                        post_to_main(self._on_qr_success, dlg, saved)
                        return
                    elif code == 801:
                        post_to_main(status.setText, "等待扫码...")
                    elif code == 802:
                        post_to_main(status.setText, "扫码成功，请在手机上确认")
                    else:
                        post_to_main(status.setText, f"登录失败，错误码 {code}")
            except Exception as e:
                post_to_main(status.setText, f"{e}")

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
                    post_to_main(status.setText, "生成二维码失败")
                    return
                url, key = d["data"]["url"], d["data"]["qrcode_key"]
                qr = qrcode.QRCode(border=1)
                qr.add_data(url)
                qr.make(fit=True)
                img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
                self._show_qr(qr_label, img)
                post_to_main(status.setText, "请用哔哩哔哩 APP 扫码")
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
                        self._mark_relogged("bilibili")
                        post_to_main(self._on_qr_success, dlg, "B站登录成功，Cookie 已保存")
                        return
                    elif code == 86101:
                        post_to_main(status.setText, "等待扫码...")
                    elif code == 86090:
                        post_to_main(status.setText, "扫码成功，请在手机上确认")
                    elif code == 86038:
                        post_to_main(status.setText, "二维码已失效，请重新登录")
                        return
                    else:
                        post_to_main(status.setText, f"登录失败，错误码 {code}")
            except Exception as e:
                post_to_main(status.setText, f"{e}")

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


class _TcdocOpenApiDialog(QDialog):
    """腾讯文档官方 OpenAPI 三个 ID 的统一管理入口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("腾讯文档 OpenAPI")
        self.setMinimumWidth(460)
        from toolbox.tcdoc import openapi
        self._openapi = openapi

        v = QVBoxLayout(self)
        hint = QLabel("Client-Id / Access-Token / Open-Id 加密保存在本机。")
        hint.setWordWrap(True)
        v.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.client_id = QLineEdit()
        self.client_id.setPlaceholderText("Client-Id / APPid")
        self.access_token = QLineEdit()
        self.access_token.setPlaceholderText("Access-Token")
        self.access_token.setEchoMode(QLineEdit.Password)
        self.open_id = QLineEdit()
        self.open_id.setPlaceholderText("Open-Id")
        show = QCheckBox("显示 Token")
        show.toggled.connect(
            lambda on: self.access_token.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        grid.addWidget(QLabel("Client-Id"), 0, 0)
        grid.addWidget(self.client_id, 0, 1)
        grid.addWidget(QLabel("Access-Token"), 1, 0)
        grid.addWidget(self.access_token, 1, 1)
        grid.addWidget(show, 1, 2)
        grid.addWidget(QLabel("Open-Id"), 2, 0)
        grid.addWidget(self.open_id, 2, 1)
        grid.setColumnStretch(1, 1)
        v.addLayout(grid)

        self.status = QLabel("")
        self.status.setProperty("role", "caption")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        row = QHBoxLayout()
        save = QPushButton("保存")
        save.setProperty("variant", "primary")
        save.clicked.connect(self._save)
        clear = QPushButton("清除")
        clear.setProperty("variant", "danger")
        clear.clicked.connect(self._clear)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(save)
        row.addWidget(clear)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)

        self._fill()

    def _fill(self):
        try:
            creds = self._openapi.load_credentials()
        except Exception:
            creds = {}
        self.client_id.setText(str(creds.get("client_id") or ""))
        self.access_token.setText(str(creds.get("access_token") or ""))
        self.open_id.setText(str(creds.get("open_id") or ""))
        self.status.setText("已保存" if creds.get("access_token") else "未保存")

    def _save(self):
        client_id = self.client_id.text().strip()
        token = self.access_token.text().strip()
        open_id = self.open_id.text().strip()
        if not (client_id and token and open_id):
            QMessageBox.warning(self, "信息不完整", "请填写 Client-Id、Access-Token、Open-Id")
            return
        try:
            self._openapi.save_credentials(client_id, token, open_id)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))
            return
        self.status.setText("已保存")
        QMessageBox.information(self, "已保存", "OpenAPI 凭证已加密保存到本机")

    def _clear(self):
        if QMessageBox.question(
                self, "清除", "确认清除本机保存的 OpenAPI 凭证？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self._openapi.clear_credentials()
        except Exception as exc:
            QMessageBox.warning(self, "清除失败", str(exc))
            return
        self._fill()


class _WechatReadingDetectionDialog(QDialog):
    """统一入口内的检测进度；系统改动与临时会话均交给管理层。"""

    session_ready = Signal()

    def __init__(self, parent=None, *, detector=None):
        super().__init__(parent)
        if detector is None:
            from toolbox.wechat_detection import get_detector
            detector = get_detector()
        self._detector = detector
        self._finished = False
        self._close_requested = False
        self._close_result = QDialog.Rejected
        self._start_pending = False
        self._stop_requested = False
        self._watching_run = False
        self._success_emitted = False
        self._last_status = {
            "state": "idle", "busy": False, "cleanup_ok": True,
            "cleanup_failed": False, "recovery_required": False,
            "remaining_seconds": 0, "message": "尚未开始检测",
        }
        self.setWindowTitle("微信公众号自动检测")
        self.setWindowModality(Qt.NonModal)
        self.setModal(False)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        notice = QLabel(
            "检测期间会临时设置本机代理，并为本次检测安装当前用户证书，"
            "仅检查微信公众号文章请求。结束后会自动还原代理并移除证书。")
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.consent = QCheckBox("我同意以上临时系统设置")
        self.consent.toggled.connect(self._render_status)
        layout.addWidget(self.consent)
        instruction = QLabel(
            "开始后，请在电脑版微信中打开目标公众号的文章，保持此窗口打开。"
            "最长等待 120 秒；获取到本次阅读信息后会自动结束检测。")
        instruction.setWordWrap(True)
        instruction.setProperty("role", "caption")
        layout.addWidget(instruction)
        self.feedback = QLabel()
        self.feedback.setTextFormat(Qt.PlainText)
        self.feedback.setWordWrap(True)
        self.feedback.setMinimumHeight(self.feedback.fontMetrics().lineSpacing() * 2)
        layout.addWidget(self.feedback)
        self.progress = QProgressBar()
        self.progress.setRange(0, 120)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.countdown = QLabel()
        self.countdown.setProperty("role", "caption")
        layout.addWidget(self.countdown)
        layout.addStretch(1)
        self.manual = QPushButton("手动输入")
        self.manual.clicked.connect(self._open_manual)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.setLayoutDirection(Qt.RightToLeft)
        for button in self.buttons.buttons():
            button.setLayoutDirection(Qt.LeftToRight)
        self.primary = self.buttons.button(QDialogButtonBox.Ok)
        self.primary.setText("开始检测")
        self.primary.setProperty("variant", "primary")
        self.primary.setDefault(False)
        self.primary.setAutoDefault(False)
        self.secondary = self.buttons.button(QDialogButtonBox.Cancel)
        self.secondary.setText("关闭")
        self.secondary.setDefault(True)
        self.buttons.accepted.connect(self._start_or_restore)
        self.buttons.rejected.connect(self._cancel_or_close)
        button_row = QHBoxLayout()
        button_row.addWidget(self.manual)
        button_row.addStretch(1)
        button_row.addWidget(self.buttons)
        layout.addLayout(button_row)
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        max_w = max(1, available.width() - 30) if available else 720
        max_h = max(1, available.height() - 40) if available else 520
        self.setMinimumSize(min(420, max_w), min(380, max_h))
        self.setMaximumSize(max_w, max_h)
        self.resize(min(720, max_w), min(420, max_h))
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self._poll)
        self._poll()
        self._timer.start()

    def _poll(self):
        try:
            status = self._detector.status()
            state = str(status.get("state") or "idle")
            busy = bool(status.get("busy"))
            cleanup_failed = bool(status.get("cleanup_failed", False))
            recovery_required = bool(status.get("recovery_required", False))
            cleanup_ok = (bool(status.get("cleanup_ok", False))
                          and not cleanup_failed and not recovery_required)
            remaining = max(0, min(120, int(status.get("remaining_seconds") or 0)))
            message = str(status.get("message") or "")[:400]
            if self._start_pending:
                if state == "idle" and not busy:
                    state, busy, message = "starting", True, "正在启动检测…"
                else:
                    self._start_pending = False
            self._last_status = {
                "state": state, "busy": busy, "cleanup_ok": cleanup_ok,
                "cleanup_failed": cleanup_failed,
                "recovery_required": recovery_required,
                "remaining_seconds": remaining, "message": message,
            }
        except Exception:
            self._start_pending = False
            self._last_status = {
                "state": "error", "busy": False, "cleanup_ok": False,
                "cleanup_failed": True, "recovery_required": False, "remaining_seconds": 0,
                "message": "暂时无法确认检测状态，请重试还原。",
            }
        status = self._last_status
        if status["busy"]:
            self._watching_run = True
        else:
            self._stop_requested = False
        self._render_status()
        if (status["state"] == "succeeded" and not status["busy"]
                and status["cleanup_ok"] and self._watching_run
                and not self._success_emitted):
            self._success_emitted = True
            self.session_ready.emit()
        if (self._close_requested and not status["busy"]
                and (status["cleanup_ok"] or status["recovery_required"])):
            self._finish_close()

    def _render_status(self, _checked=False):
        status = self._last_status
        busy = status["busy"]
        clean = status["cleanup_ok"]
        cleanup_failed = status["cleanup_failed"]
        recovery_required = status["recovery_required"] and not busy
        restoring = status["state"] == "restoring"
        message = status["message"] or "尚未开始检测"
        if recovery_required:
            message = ("检测进程已失联，无法自动还原。请检查并还原 Windows 网络代理与当前用户证书，"
                       "完成后重新启动程序。\n本次检测证书名称以 FMJJ Reading Detection 开头。")
        elif cleanup_failed or (not clean and not busy):
            message += "\n系统设置尚未完全还原。请点击“重试还原”，完成前请保留此窗口。"
        elif self._close_requested and busy:
            message += "\n还原完成后会自动关闭。"
        self.feedback.setText(message)
        self.consent.setEnabled(not busy and clean and not recovery_required and not self._close_requested)
        self.manual.setEnabled(not busy and clean and not recovery_required and not self._close_requested)
        if recovery_required:
            self.primary.setText("需手动还原")
            self.primary.setEnabled(False)
            self.secondary.setText("关闭")
            self.secondary.setEnabled(True)
            self.progress.setRange(0, 120)
            self.progress.setValue(0)
            self.countdown.setText("新的自动检测已停用；请先完成系统设置还原")
        elif cleanup_failed or (not clean and not busy):
            self.primary.setText("重试还原")
            self.primary.setEnabled(True)
            self.secondary.setText("等待还原")
            self.secondary.setEnabled(False)
            self.progress.setRange(0, 120)
            self.progress.setValue(0)
            self.countdown.setText("完成还原后才能关闭或重新检测")
        elif busy:
            self.primary.setText("正在还原" if restoring else "检测中")
            self.primary.setEnabled(False)
            self.secondary.setText("还原中" if restoring else "停止检测")
            self.secondary.setEnabled(not restoring and not self._stop_requested)
            if status["state"] == "detecting":
                self.progress.setRange(0, 120)
                self.progress.setValue(120 - status["remaining_seconds"])
                self.countdown.setText(f"最多还需等待 {status['remaining_seconds']} 秒")
            else:
                self.progress.setRange(0, 0)
                self.countdown.setText("正在还原临时系统设置…" if restoring else "正在准备检测…")
        else:
            self.primary.setText("开始检测" if status["state"] == "idle" else "重新检测")
            self.primary.setEnabled(self.consent.isChecked() and not self._close_requested)
            self.secondary.setText("关闭")
            self.secondary.setEnabled(True)
            self.progress.setRange(0, 120)
            self.progress.setValue(120 if status["state"] == "succeeded" else 0)
            self.countdown.setText(
                "本次阅读信息仅在内存中短时可用" if status["state"] == "succeeded"
                else "勾选同意后开始检测")

    def _start_or_restore(self):
        self._poll()
        status = self._last_status
        if status["recovery_required"]:
            return
        if status["cleanup_failed"]:
            self._stop_detection()
            return
        if status["busy"]:
            return
        if not status["cleanup_ok"]:
            self._stop_detection()
            return
        if self._close_requested or not self.consent.isChecked():
            return
        self._start_pending = True
        self._watching_run = True
        self._success_emitted = False
        self.primary.setEnabled(False)
        self.manual.setEnabled(False)
        try:
            self._detector.start(consent=True)
        except Exception:
            self._start_pending = False
            self._stop_detection()
        self._poll()

    def _stop_detection(self):
        self._start_pending = False
        self._stop_requested = True
        self.secondary.setEnabled(False)
        try:
            self._detector.stop()
        except Exception:
            # 不展示异常原文，也不把异常当作已完成还原。
            self._last_status = {
                "state": "error", "busy": False, "cleanup_ok": False,
                "cleanup_failed": True, "recovery_required": False, "remaining_seconds": 0,
                "message": "还原未能完成，请重试还原。",
            }
            self._render_status()
            return
        self._poll()

    def _cancel_or_close(self):
        if self._last_status["busy"]:
            self._stop_detection()
        else:
            self.reject()

    def _open_manual(self):
        self._poll()
        if self._last_status["busy"] or not self._last_status["cleanup_ok"]:
            return
        if _WechatReadingSessionDialog(self).exec() == QDialog.Accepted:
            self.session_ready.emit()
            self.done(QDialog.Accepted)

    def request_close(self, result=QDialog.Rejected):
        self._close_result = result
        self._close_requested = True
        self._poll()
        if self._last_status["recovery_required"] and not self._last_status["busy"]:
            return True
        if self._last_status["busy"] or not self._last_status["cleanup_ok"]:
            if (self._last_status["state"] != "restoring"
                    or not self._last_status["busy"] or self._last_status["cleanup_failed"]):
                self._stop_detection()
            return not self._last_status["busy"] and self._last_status["cleanup_ok"]
        return True

    def _finish_close(self):
        if self._finished:
            return
        self._finished = True
        self._timer.stop()
        super().done(self._close_result)

    def done(self, result):
        self.request_close(result)

    def reject(self):
        self.request_close(QDialog.Rejected)

    def accept(self):
        self.request_close(QDialog.Accepted)

    def closeEvent(self, event):
        if not self.request_close():
            event.ignore()
            return
        event.accept()


class _WechatReadingSessionDialog(QDialog):
    """统一入口内的一次性输入；不预填、不持久化、不主动获取。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("微信公众号阅读信息")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        hint = QLabel(
            "粘贴本人已登录的微信文章完整请求地址。普通分享链接只用于单篇文章，"
            "不能用于历史列表。阅读信息仅在内存中保留最多 10 分钟或至软件退出；"
            "平台可能提前判定失效。")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(QLabel("文章请求地址"))
        self.address = QLineEdit()
        self.address.setEchoMode(QLineEdit.Password)
        self.address.setMaxLength(32768)
        self.address.setPlaceholderText("https://mp.weixin.qq.com/s?...")
        self.address.setClearButtonEnabled(True)
        layout.addWidget(self.address)
        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setProperty("role", "caption")
        layout.addWidget(self.feedback)
        layout.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.setLayoutDirection(Qt.RightToLeft)
        for button in buttons.buttons():
            button.setLayoutDirection(Qt.LeftToRight)
        buttons.button(QDialogButtonBox.Ok).setText("使用本次阅读信息")
        buttons.button(QDialogButtonBox.Ok).setProperty("variant", "primary")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._use_session)
        buttons.rejected.connect(self.reject)
        button_row = QHBoxLayout()
        button_row.addStretch(1)
        button_row.addWidget(buttons)
        layout.addLayout(button_row)
        self.finished.connect(lambda _result: self.address.clear())
        screen = self.screen() or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen else None
        max_w = max(1, available.width() - 30) if available else 720
        max_h = max(1, available.height() - 40) if available else 520
        self.setMinimumSize(min(420, max_w), min(300, max_h))
        self.setMaximumSize(max_w, max_h)
        self.resize(min(720, max_w), min(360, max_h))

    def _use_session(self):
        from wechat_session import SessionInputError, set_session_from_url
        try:
            set_session_from_url(self.address.text())
        except SessionInputError as exc:
            self.feedback.setText(str(exc))
            return
        except Exception:
            self.feedback.setText("阅读信息暂不可用，请稍后重试")
            return
        self.address.clear()
        self.accept()


class _GitHubTokenDialog(QDialog):
    """GitHub Token 的统一管理入口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GitHub 账户")
        self.setMinimumWidth(460)
        from toolbox.webwiki import auth as github_auth, credentials as github_creds
        self._auth = github_auth
        self._creds = github_creds

        v = QVBoxLayout(self)
        hint = QLabel("Token 只保存在本机加密存储；仓库仍需保持私有。")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.status = QLabel("")
        self.status.setProperty("role", "caption")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.token_edit = QLineEdit()
        self.token_edit.setPlaceholderText("Personal Access Token（ghp_… / github_pat_…）")
        self.token_edit.setEchoMode(QLineEdit.Password)
        v.addWidget(self.token_edit)
        show = QCheckBox("显示 Token")
        show.toggled.connect(
            lambda on: self.token_edit.setEchoMode(
                QLineEdit.Normal if on else QLineEdit.Password))
        v.addWidget(show)

        row = QHBoxLayout()
        detect = QPushButton("识别本机账户")
        detect.clicked.connect(self._detect)
        save = QPushButton("保存")
        save.setProperty("variant", "primary")
        save.clicked.connect(self._save)
        clear = QPushButton("清除")
        clear.setProperty("variant", "danger")
        clear.clicked.connect(self._clear)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        row.addWidget(detect)
        row.addWidget(save)
        row.addWidget(clear)
        row.addStretch(1)
        row.addWidget(close)
        v.addLayout(row)
        self._fill()

    def _fill(self):
        data = self._creds.load()
        token = str(data.get("token") or "")
        login = str(data.get("login") or "")
        if token:
            self.status.setText(f"已登录 @{login}" if login else "已登录")
        else:
            self.status.setText("未登录")
        self.token_edit.clear()

    def _detect(self):
        self.status.setText("正在识别本机 Git 凭据…")

        def done(result, err):
            data = result or {}
            if err:
                self.status.setText(f"识别失败：{err}")
                return
            login = str(data.get("login") or "")
            if data.get("token"):
                self.token_edit.setText(str(data["token"]))
                self.status.setText(
                    f"检测到 @{login}，点保存完成登录" if login
                    else "检测到本机账户，点保存完成登录")
            elif login:
                self.status.setText(f"检测到用户名 @{login}，但凭据不可用")
            else:
                self.status.setText("未检测到本机 GitHub 凭据")

        run_bg(self._auth.detect_local_account, done)

    def _save(self):
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.warning(self, "缺少 Token", "请填写 Personal Access Token")
            return
        self.status.setText("正在验证 Token…")

        def done(user, err):
            if err:
                self.status.setText(str(err))
                return
            login = str((user or {}).get("login") or "")
            self.status.setText(f"已登录 @{login}" if login else "已登录")
            self.token_edit.clear()

        run_bg(lambda: self._auth.login_with_token(token), done)

    def _clear(self):
        if QMessageBox.question(
                self, "清除", "确认清除本机保存的 GitHub 登录状态？",
                QMessageBox.Yes | QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            self._creds.clear()
        except Exception as exc:
            QMessageBox.warning(self, "清除失败", str(exc))
            return
        self._fill()
        self.status.setText("已清除")
