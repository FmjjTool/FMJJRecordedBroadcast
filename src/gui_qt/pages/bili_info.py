"""B站资源 · 用户空间页：按 UID 查看用户公开信息并下载空间资源。

复刻自 BiliResourceDownloader 的 space/[id].vue
（下载头像/横幅/空间资源；视频信息与综合搜索已并入「信息获取」）。
"""

import re

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTabWidget, QTextBrowser, QVBoxLayout, QWidget,
)

from bili import api
from toolbox.config_manager import get_export_dir
from gui_qt.common import refresh_browser_colors, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_emoji import _DlBar, render_files


def space_files(card: dict, appinfo: dict, emotes: list) -> list[dict]:
    """空间资源：头像/头像框(增强+普通+App)/网页+App背景图/投稿封面/充电表情/收藏集封面。"""
    files = [{"path": "头像", "url": card.get("card", {}).get("face", "")}]
    space = card.get("space", {}) or {}
    if space.get("l_img"):
        files.append({"path": "网页端空间背景图", "url": space["l_img"]})
    if space.get("s_img") and space["s_img"] != space.get("l_img"):
        files.append({"path": "网页端空间背景图(小图)", "url": space["s_img"]})
    pendant = card.get("card", {}).get("pendant", {}) or {}
    pen_name = api.sanitize(pendant.get("name", "头像框"))
    enhanced = pendant.get("image_enhance", "")
    if enhanced:
        files.append({"path": f"头像框 - {pen_name}", "url": enhanced})
    if pendant.get("image") and pendant["image"] != enhanced:
        files.append({"path": f"头像框 - {pen_name}(普通版)", "url": pendant["image"]})
    if appinfo.get("card", {}).get("pendant_url"):
        files.append({"path": f"头像框 - {pen_name}(App端)", "url": appinfo["card"]["pendant_url"]})
    images = appinfo.get("images", {}) or {}
    if images.get("imgUrl"):
        files.append({"path": "App端空间背景图", "url": images["imgUrl"]})
    if images.get("night_imgurl"):
        files.append({"path": "App端空间背景图（夜间模式）", "url": images["night_imgurl"]})
    for it in (appinfo.get("archive", {}) or {}).get("item", []) or []:
        if it.get("cover"):
            files.append({"path": f"投稿封面/{api.sanitize(it.get('title', '投稿'))}", "url": it["cover"]})
    for e in emotes:
        if e.get("icon"):
            files.append({"path": f"充电表情/{api.sanitize(e.get('name', e.get('emoji', '表情')))}",
                          "url": e["icon"]})
    # 收藏集卡牌封面（collection_top_simple）
    cts = ((images.get("collection_top_simple", {}) or {}).get("top", {}) or {}).get("result", []) or []
    for r in cts:
        if r.get("cover"):
            t = api.sanitize(f"{(r.get('title', {}) or {}).get('title', '')} "
                             f"{(r.get('title', {}) or {}).get('sub_title', '')}".strip() or "收藏集")
            files.append({"path": f"收藏集封面/{t}", "url": r["cover"]})
    return [f for f in files if f["url"]]


class BiliInfoPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._space_current = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_space_tab(), "用户空间")

    # ---------- 空间 ----------
    def _build_space_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("用户 UID："))
        self.space_entry = QLineEdit()
        self.space_entry.setPlaceholderText("如 470880931，可粘贴空间链接")
        self.space_entry.returnPressed.connect(self._load_space)
        row.addWidget(self.space_entry, 1)
        btn = QPushButton("查看")
        btn.setStyleSheet("background:#1976d2;color:white;padding:5px 14px;")
        btn.clicked.connect(self._load_space)
        row.addWidget(btn)
        v.addLayout(row)
        self.space_view = QTextBrowser()
        self.space_view.setOpenExternalLinks(True)
        # 聚合页会把本页 tab 提升出去；监听实际浏览器，主题切换无需
        # 依赖逻辑宿主是否可见、或外层是否转发 on_theme_changed。
        self.space_view.installEventFilter(self)
        v.addWidget(self.space_view, 1)
        self.space_table = QTableWidget(0, 2)
        self.space_table.setHorizontalHeaderLabels(["文件", "目录"])
        self.space_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.space_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.space_table, 1)
        self.space_bar = _DlBar(self, lambda: self._space_current, "空间资源", show_fmt=False)
        self.space_bar.dl_btn.setText("下载头像/横幅/表情")
        v.addWidget(self.space_bar)
        return w

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
            self.space_bar.progress.setText(f"{len(self._space_current['files'])} 个资源就绪")

        run_bg(job, done)

    def _refresh_space_colors(self):
        refresh_browser_colors(self.space_view, ("#666666", "#888888"))

    def eventFilter(self, obj, event):
        if obj is self.space_view and event.type() in (QEvent.PaletteChange, QEvent.Show):
            self._refresh_space_colors()
        return super().eventFilter(obj, event)


