"""B站资源 · 空间综合搜索页：用户空间 / 综合搜索。

复刻自 BiliResourceDownloader 的 space/[id].vue + search/general.vue
（下载头像/横幅/空间资源；视频信息走「信息获取 · 单个解析」页，动态详情已并入综合搜索场景）。
"""

import re

from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from bili import api
from toolbox.config_manager import get_export_dir
from gui_qt.common import run_bg
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
    _flat_tabs = None   # 嵌入 B站资源聚合页时指向平铺外层 QTabWidget
    _tab_map = None     # 本页各 tab 在平铺外层的下标表（顺序交错时非连续）

    def _switch_tab(self, index: int):
        """切到本页第 index 个功能 tab：嵌入 B站资源聚合页时映射到平铺外层 tab。"""
        if self._flat_tabs is not None:
            self._flat_tabs.setCurrentIndex(self._tab_map[index])
        else:
            self.tabs.setCurrentIndex(index)

    def __init__(self, app):
        super().__init__(app)
        self._space_current = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        head = QHBoxLayout()
        head.addWidget(QLabel("🔍 B站空间综合搜索", styleSheet="font-size: 16px; font-weight: bold;"))
        head.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        head.addWidget(attach_help_overlay(self, "❓ 空间综合搜索 · 功能介绍", (
            "<p><b>B站用户空间查询与综合搜索（两个入口）。</b></p>"
            "<p>· <b>👤 用户空间</b>：输入 UID 查看用户公开信息——关注/粉丝/获赞/签名/直播房间，"
            "可下载头像/头像框/空间背景图/投稿封面/充电表情/收藏集封面</p>"
            "<p>· <b>🔍 综合搜索</b>：关键词搜B站用户与视频（WBI 签名），"
            "用户结果点「查看」跳本页空间页；视频结果点「查看」自动填入「单个解析」页解析</p>"
            "<p>· 全部游客接口，无需登录；视频详情与下载请用「信息获取 · 单个解析」页</p>")))
        layout.addLayout(head)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_space_tab(), "👤 用户空间")
        self.tabs.addTab(self._build_search_tab(), "🔍 综合搜索")

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
        v.addWidget(self.space_view, 1)
        self.space_table = QTableWidget(0, 2)
        self.space_table.setHorizontalHeaderLabels(["文件", "目录"])
        self.space_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.space_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.space_table, 1)
        self.space_bar = _DlBar(self, lambda: self._space_current, "空间资源", show_fmt=False)
        self.space_bar.dl_btn.setText("⬇ 下载头像/横幅/表情")
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
            self._space_current = {"title": f"{api.sanitize(name)} 空间",
                                   "files": space_files(d["card"], d["appinfo"], d["emotes"])}
            render_files(self.space_table, QLabel(""), self._space_current)
            self.space_bar.progress.setText(f"{len(self._space_current['files'])} 个资源就绪")

        run_bg(job, done)

    # ---------- 综合搜索 ----------
    def _build_search_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        self.search_entry2 = QLineEdit()
        self.search_entry2.setPlaceholderText("综合搜索用户/视频（WBI 签名，无需登录），回车搜索")
        self.search_entry2.returnPressed.connect(self._search_general)
        row.addWidget(self.search_entry2, 1)
        btn = QPushButton("搜索")
        btn.setStyleSheet("background:#1976d2;color:white;padding:5px 16px;")
        btn.clicked.connect(self._search_general)
        row.addWidget(btn)
        v.addLayout(row)
        self.search_table2 = QTableWidget(0, 4)
        self.search_table2.setHorizontalHeaderLabels(["类型", "名称", "信息", "操作"])
        self.search_table2.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.search_table2.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.search_table2, 1)
        self.search_info2 = QLabel("用户 → 「空间」tab；视频 → 单个解析页（自动填入解析）")
        self.search_info2.setStyleSheet("color:#888;")
        v.addWidget(self.search_info2)
        return w

    def _search_general(self):
        kw = self.search_entry2.text().strip()
        if not kw:
            return
        self.search_info2.setText("搜索中……（WBI 签名）")

        def job():
            d = api.search_all(kw)
            rows = []
            for block in d.get("result", []) or []:
                rtype = block.get("result_type", "")
                for item in block.get("data", []) or []:
                    if rtype in ("user", "bili_user"):
                        rows.append({"t": "用户", "name": item.get("uname", ""),
                                     "info": f"粉丝 {item.get('fans', 0)} · {item.get('usign', '')[:30]}",
                                     "id": str(item.get("mid", "")), "raw": item})
                    elif rtype == "video":
                        rows.append({"t": "视频", "name": re.sub(r"<[^>]+>", "", item.get("title", "")),
                                     "info": f"UP {item.get('author', '')} · 播放 {item.get('play', 0)}",
                                     "id": item.get("bvid", ""), "raw": item})
            return rows

        def done(rows, err):
            if err:
                self.search_info2.setText(f"搜索失败：{err}")
                return
            self.search_table2.setRowCount(0)
            for i, r in enumerate(rows[:200]):
                self.search_table2.insertRow(i)
                self.search_table2.setItem(i, 0, QTableWidgetItem(r["t"]))
                self.search_table2.setItem(i, 1, QTableWidgetItem(r["name"]))
                self.search_table2.setItem(i, 2, QTableWidgetItem(r["info"]))
                btn = QPushButton("查看")
                btn.clicked.connect(lambda _, ck=r: self._open_search(ck))
                self.search_table2.setCellWidget(i, 3, btn)
            self.search_info2.setText(f"共 {len(rows)} 条（用户+视频，最多显示 200）")

        run_bg(job, done)

    def _open_search(self, r):
        if r["t"] == "用户":
            self._switch_tab(0)
            self.space_entry.setText(r["id"])
            self._load_space()
        else:
            # 视频信息功能已并入「单个解析」页（功能重复，此处直接跳转解析）
            self.app.show_page("info_parse")
            page = self.app.pages.get("info_parse")
            if page is not None:
                page.open_url(f"https://www.bilibili.com/video/{r['id']}")
