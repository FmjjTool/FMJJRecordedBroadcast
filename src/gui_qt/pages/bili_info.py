"""B站资源 · 用户空间页：按 UID 查看用户公开信息并下载空间资源。

复刻自 BiliResourceDownloader 的 space/[id].vue
（下载头像/横幅/空间资源；视频信息与综合搜索已并入「信息获取」）。
"""

import re

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTextBrowser, QVBoxLayout,
)

from bili import api
from toolbox.config_manager import get_export_dir
from gui_qt import table_sort
from gui_qt.common import refresh_browser_colors, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_emoji import _DlBar, _padded, render_files


from toolbox.bili_assets import space_files  # noqa: F401  共用 Qt-free 清单


class BiliInfoPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._space_current = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self._build_space_tab(), 1)

    # ---------- 空间 ----------
    def _build_space_tab(self):
        card = QFrame()
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("用户空间")
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        uid_label = QLabel("用户 UID")
        uid_label.setProperty("role", "caption")
        row.addWidget(uid_label)
        self.space_entry = QLineEdit()
        self.space_entry.setPlaceholderText("如 470880931，可粘贴空间链接")
        self.space_entry.setClearButtonEnabled(True)
        self.space_entry.returnPressed.connect(self._load_space)
        row.addWidget(self.space_entry, 1)
        btn = QPushButton("查看")
        btn.setProperty("variant", "primary")
        btn.clicked.connect(self._load_space)
        row.addWidget(btn)
        v.addLayout(row)

        self.space_view = QTextBrowser()
        self.space_view.setOpenExternalLinks(True)
        # 监听实际浏览器，主题切换无需依赖逻辑宿主是否可见，
        # 或外层是否转发 on_theme_changed。
        self.space_view.installEventFilter(self)
        v.addWidget(self.space_view, 1)
        self.space_table = QTableWidget(0, 2)
        self.space_table.setHorizontalHeaderLabels(["文件", "目录"])
        self.space_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.space_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._space_sorter = table_sort.install(self.space_table, skip_columns=())
        v.addWidget(self.space_table, 1)
        self.space_bar = _DlBar(self, lambda: self._space_current, "空间资源", show_fmt=False)
        self.space_bar.dl_btn.setText("下载头像/横幅/表情")
        v.addWidget(self.space_bar)
        return _padded(card)

    def _load_space(self):
        raw = self.space_entry.text().strip()
        m = re.search(r"(\d{3,})", raw)
        if not m:
            self.space_view.setPlainText("请输入用户 UID（数字）或空间链接")
            return
        mid = m.group(1)

        def job():
            card = api.user_card(mid)
            notice = api.space_notice(mid)
            try:
                master = api.live_master_info(mid)
            except api.BiliApiError:
                master = {}
            try:
                appinfo = api.app_space(mid)
            except api.BiliApiError:
                appinfo = {}
            emotes = api.upower_emote(mid)
            return {"card": card, "notice": notice, "master": master,
                    "appinfo": appinfo, "emotes": emotes}

        def done(d, err):
            if err:
                self.space_view.setPlainText(f"加载失败：{err}")
                return
            card = d["card"].get("card", {})
            name = card.get("name", mid)
            room = ""
            live_url = ""
            if d["master"].get("info", {}).get("room_id"):
                rid = d["master"]["info"]["room_id"]
                live_url = f"<a href='https://live.bilibili.com/{rid}'>直播房间 {rid}</a> · 粉丝 {d['master'].get('follower_num', 0)}"
                room = f"<p>直播：{live_url} · 粉丝牌「{d['master'].get('medal_name', '')}」</p>"
            html = (
                f"<h3>{name} <span style='color:#888;font-size:12px'>(UID {mid})</span></h3>"
                f"<p>关注 {d['card'].get('card', {}).get('attention', '?')} · 粉丝 {d['card'].get('follower', '?')}"
                f" · 获赞 {d['card'].get('like_num', '?')}</p>"
                f"<p>{card.get('sign', '')}</p>"
                f"{room}"
                f"<p style='color:#666'>公告：{d['notice'] or '（无）'}</p>"
                f"<p><a href='https://space.bilibili.com/{mid}'>打开空间</a></p>"
            )
            self.space_view.setHtml(html)
            self._refresh_space_colors()
            self._space_current = {"title": f"{api.sanitize(name)} 空间",
                                   "files": space_files(d["card"], d["appinfo"], d["emotes"])}
            render_files(self.space_table, QLabel(""), self._space_current)
            self._space_sorter.reapply()
            self.space_bar.progress.setText(f"{len(self._space_current['files'])} 个资源就绪")

        run_bg(job, done)

    def _refresh_space_colors(self):
        refresh_browser_colors(self.space_view, ("#666666", "#888888"))

    def eventFilter(self, obj, event):
        if obj is self.space_view and event.type() in (QEvent.PaletteChange, QEvent.Show):
            self._refresh_space_colors()
        return super().eventFilter(obj, event)


