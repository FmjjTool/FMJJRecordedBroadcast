"""B站资源 · 综合页：视频信息 / 用户空间 / 动态详情 / 综合搜索。

复刻自 BiliResourceDownloader 的 video/[id].vue + space/[id].vue +
dynamic/[id].vue + search/general.vue（下载封面/头像/动态配图，视频本体走「视频下载」页）。
"""

import os
import re

from PySide6.QtWidgets import (
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTableWidget, QTableWidgetItem, QTabWidget, QTextBrowser, QVBoxLayout,
    QWidget,
)

from bili import api
from toolbox.config_manager import get_export_dir
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from gui_qt.pages.bili_emoji import _DlBar, render_files


def ts_date(ts) -> str:
    try:
        from datetime import datetime
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts or "")


def video_files(v: dict) -> list[dict]:
    files = [{"path": "封面", "url": v.get("pic", "")}]
    files.append({"path": f"UP主头像/{api.sanitize(v.get('owner', {}).get('name', 'UP'))}",
                  "url": v.get("owner", {}).get("face", "")})
    for st in v.get("staff", []) or []:
        files.append({"path": f"联合投稿/{api.sanitize(st.get('name', 'staff'))}",
                      "url": st.get("face", "")})
    return [f for f in files if f["url"]]


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


def dynamic_files(dyn: dict) -> list[dict]:
    """动态详情 → 配图/视频封面/转发内容图片/内容表情/头像/头像框。"""
    files = []
    modules = dyn.get("modules", {}) or {}
    author = modules.get("module_author", {}) or {}
    if author.get("face"):
        files.append({"path": "作者头像", "url": author["face"]})
    pendant = author.get("pendant", {}) or {}
    if pendant.get("image_enhance") or pendant.get("image"):
        files.append({"path": "作者头像框", "url": pendant.get("image_enhance") or pendant.get("image")})

    def _collect(mod: dict, prefix: str, pic_fmt: str = "{}/图片{}"):
        major = mod.get("module_dynamic", {}).get("major", {}) or {}
        if not major:
            return
        mtype = major.get("type", "")
        if mtype == "MAJOR_TYPE_DRAW":
            for i, item in enumerate(major.get("draw", {}).get("items", []) or []):
                u = item.get("src", "")
                if u:
                    files.append({"path": pic_fmt.format(prefix, i + 1), "url": u})
        elif mtype == "MAJOR_TYPE_ARCHIVE":
            arc = major.get("archive", {}) or {}
            if arc.get("cover"):
                files.append({"path": f"{prefix}/视频封面", "url": arc["cover"]})
            if arc.get("aid"):
                files.append({"path": f"{prefix}/视频UP主头像", "url": arc.get("author_face", "")})
        elif mtype == "MAJOR_TYPE_ARTICLE":
            for i, item in enumerate(major.get("article", {}).get("covers", []) or []):
                if item:
                    files.append({"path": f"{prefix}/文章封面{i + 1}", "url": item})
        elif mtype == "MAJOR_TYPE_OPUS":
            for i, pic in enumerate((major.get("opus", {}) or {}).get("pics", []) or []):
                if pic.get("url"):
                    files.append({"path": pic_fmt.format(prefix, i + 1), "url": pic["url"]})

    _collect(modules, "内容", "{}/图片{}")
    md = modules.get("module_dynamic", {}) or {}
    content = md.get("desc") or ((md.get("major", {}) or {}).get("opus", {}) or {}).get("summary") or {}
    for i, item in enumerate(content.get("rich_text_nodes", []) or []):
        if item.get("type") == "RICH_TEXT_NODE_TYPE_EMOJI" and item.get("emoji", {}).get("icon_url"):
            files.append({"path": f"内容表情{i + 1}", "url": item["emoji"]["icon_url"]})
    # 转发动态的原动态（orig 在顶层且自带 modules），图片用原版扁平命名「原动态图片N」
    orig = dyn.get("orig", {}) or {}
    if orig:
        _collect(orig.get("modules", {}) or {}, "转发原动态", "原动态图片{1}")
    seen, out = set(), []
    for f in files:
        if f["url"] and f["url"] not in seen:
            seen.add(f["url"])
            out.append(f)
    return out


class BiliInfoPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._video_current = None
        self._space_current = None
        self._dyn_current = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_video_tab(), "🎬 视频信息")
        self.tabs.addTab(self._build_space_tab(), "👤 用户空间")
        self.tabs.addTab(self._build_dynamic_tab(), "📰 动态详情")
        self.tabs.addTab(self._build_search_tab(), "🔍 综合搜索")

    # ---------- 视频 ----------
    def _build_video_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("BV/av号："))
        self.video_entry = QLineEdit()
        self.video_entry.setPlaceholderText("如 BV1GJ411x7h7 或 av928861028，可粘贴视频页链接")
        self.video_entry.returnPressed.connect(self._load_video)
        row.addWidget(self.video_entry, 1)
        btn = QPushButton("查看")
        btn.setStyleSheet("background:#1976d2;color:white;padding:5px 14px;")
        btn.clicked.connect(self._load_video)
        row.addWidget(btn)
        v.addLayout(row)
        self.video_view = QTextBrowser()
        self.video_view.setOpenExternalLinks(True)
        v.addWidget(self.video_view, 1)
        self.video_table = QTableWidget(0, 2)
        self.video_table.setHorizontalHeaderLabels(["文件", "目录"])
        self.video_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.video_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.video_table, 1)
        self.video_bar = _DlBar(self, lambda: self._video_current, "视频资源", show_fmt=False)
        self.video_bar.dl_btn.setText("⬇ 下载封面/头像")
        v.addWidget(self.video_bar)
        return w

    def _load_video(self):
        raw = self.video_entry.text().strip()
        m = re.search(r"(BV[0-9A-Za-z]{10}|av\d+)", raw, re.I)
        if not m:
            self.video_view.setPlainText("请输入 BV号 / av号（或视频页链接）")
            return
        vid = m.group(1)

        def job():
            return api.video_view(vid)

        def done(vd, err):
            if err:
                self.video_view.setPlainText(f"加载失败：{err}")
                return
            owner = vd.get("owner", {})
            stat = vd.get("stat", {})
            html = (
                f"<h3>{vd.get('title', '')}</h3>"
                f"<p>分区：{vd.get('tname', '')} · UP主：<a href='https://space.bilibili.com/{owner.get('mid', '')}'>"
                f"{owner.get('name', '')}</a></p>"
                f"<p>发布：{ts_date(vd.get('pubdate', 0))} · AV{vd.get('aid', '')} · {vd.get('bvid', '')}</p>"
                f"<p>播放 {stat.get('view', 0)} · 弹幕 {stat.get('danmaku', 0)} · 点赞 {stat.get('like', 0)}"
                f" · 收藏 {stat.get('favorite', 0)} · 评论 {stat.get('reply', 0)} · 投币 {stat.get('coin', 0)}</p>"
                f"<p><a href='https://www.bilibili.com/video/{vd.get('bvid', '')}'>▶ 打开视频页</a> · "
                f"下载视频本体请到「视频下载」页</p>"
                f"<p style='color:#888'>{(vd.get('desc', '') or '')[:300]}</p>"
            )
            self.video_view.setHtml(html)
            self._video_current = {"title": f"{api.sanitize(vd.get('title', vid))} 图片", "files": video_files(vd)}
            render_files(self.video_table, QLabel(""), self._video_current)
            self.video_bar.progress.setText(f"「{self._video_current['title']}」封面+头像就绪")

        run_bg(job, done)

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

    # ---------- 动态 ----------
    def _build_dynamic_tab(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel("动态ID/链接："))
        self.dyn_entry = QLineEdit()
        self.dyn_entry.setPlaceholderText("如 1095050000000000000 或 t.bilibili.com/xxx 动态链接")
        self.dyn_entry.returnPressed.connect(self._load_dynamic)
        row.addWidget(self.dyn_entry, 1)
        btn = QPushButton("查看")
        btn.setStyleSheet("background:#1976d2;color:white;padding:5px 14px;")
        btn.clicked.connect(self._load_dynamic)
        row.addWidget(btn)
        v.addLayout(row)
        self.dyn_view = QTextBrowser()
        v.addWidget(self.dyn_view, 1)
        self.dyn_table = QTableWidget(0, 2)
        self.dyn_table.setHorizontalHeaderLabels(["文件", "目录"])
        self.dyn_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.dyn_table.setEditTriggers(QTableWidget.NoEditTriggers)
        v.addWidget(self.dyn_table, 1)
        self.dyn_bar = _DlBar(self, lambda: self._dyn_current, "动态资源", show_fmt=False)
        self.dyn_bar.dl_btn.setText("⬇ 下载配图/封面")
        v.addWidget(self.dyn_bar)
        return w

    def _load_dynamic(self):
        raw = self.dyn_entry.text().strip()
        m = re.search(r"(\d{15,})", raw)
        if not m:
            self.dyn_view.setPlainText("请输入动态 ID（长数字）或动态链接")
            return
        did = m.group(1)

        def job():
            return api.dynamic_detail(did)

        def done(d, err):
            if err:
                self.dyn_view.setPlainText(f"加载失败：{err}")
                return
            item = d.get("item", d)
            modules = item.get("modules", {}) or {}
            author = modules.get("module_author", {}) or {}
            major = modules.get("module_dynamic", {}).get("major", {}) or {}
            tname = {"MAJOR_TYPE_DRAW": "图文", "MAJOR_TYPE_ARCHIVE": "视频投稿", "MAJOR_TYPE_WORD": "文字",
                     "MAJOR_TYPE_ARTICLE": "文章", "MAJOR_TYPE_FORWARD": "转发", "MAJOR_TYPE_LIVE_RCMD": "直播"}.get(
                major.get("type", ""), major.get("type", "动态"))
            text = modules.get("module_dynamic", {}).get("desc", {}).get("text", "") or \
                (major.get("archive", {}) or {}).get("title", "")
            html = (
                f"<h3>{author.get('name', '')} 的动态 <span style='color:#888;font-size:12px'>[{tname}]</span></h3>"
                f"<p>发布：{ts_date(author.get('pub_ts', 0))}</p>"
                f"<p>{text[:400]}</p>"
                f"<p><a href='https://t.bilibili.com/{did}'>打开动态</a></p>"
            )
            self.dyn_view.setHtml(html)
            self._dyn_current = {"title": f"动态{did}", "files": dynamic_files(item)}
            render_files(self.dyn_table, QLabel(""), self._dyn_current)
            self.dyn_bar.progress.setText(f"{len(self._dyn_current['files'])} 个资源就绪")

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
        self.search_info2 = QLabel("用户 → 空间页；视频 → 视频信息页（本页内跳转）")
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
            self.tabs.setCurrentIndex(1)
            self.space_entry.setText(r["id"])
            self._load_space()
        else:
            self.tabs.setCurrentIndex(0)
            self.video_entry.setText(r["id"])
            self._load_video()
