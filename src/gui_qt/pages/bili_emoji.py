"""B站资源 · 表情包页：全部表情包（登录）/ 按ID / 装扮表情包 / 直播间表情。

复刻自 BiliResourceDownloader 的 search/emoji.vue + emoji/[id].vue + liveroom 表情部分。
"""

import json
import os

import httpx
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget,
    QVBoxLayout, QWidget,
)

from bili import api
from bili.links import parse_link
from bili.download import DownloadTask
from toolbox.config_manager import get_export_dir
from toolbox.image_fetch import fetch_thumbnail
from toolbox.memory_cache import ByteLRU
from gui_qt import table_sort
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

FMT_OPTIONS = [("gif", "GIF（动图）"), ("webp", "WEBP"), ("png", "PNG（静图）"), ("all", "全部格式")]

# 全部表情包懒加载：初始显示数 + 每次滚动追加数（对齐原版 50/30）
LIST_INITIAL = 50
LIST_STEP = 30

# 表格缩略图预览
THUMB_SIZE = 44
THUMB_LIMIT = 200
_THUMB_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"}


def fmt_urls(fmt: str, emote: dict) -> list[str]:
    """按格式取表情 URL（all=全部去重）。

    表情面板/收藏集接口的 emote 项只有原始 url（可能是 png 或动图），
    没有独立 gif_url/webp_url 字段 → 所选格式缺失时回退原图。"""
    candidates = {
        "png": emote.get("url") or emote.get("image"),
        "gif": emote.get("gif_url"),
        "webp": emote.get("webp_url"),
    }
    if fmt == "all":
        seen, out = set(), []
        for u in candidates.values():
            if u and u not in seen:
                seen.add(u)
                out.append(u)
        return out
    u = candidates.get(fmt)
    if not u:
        u = emote.get("url") or emote.get("image")
    return [u] if u else []


def _padded(widget: QWidget) -> QWidget:
    """页签内容统一外边距 16px：卡片外再留白，与其它页面版式对齐。"""
    wrap = QWidget()
    lay = QVBoxLayout(wrap)
    lay.setContentsMargins(16, 16, 16, 16)
    lay.setSpacing(0)
    lay.addWidget(widget)
    return wrap


class _DlBar(QWidget):
    """格式选择 + 下载按钮 + 进度标签（每个 Tab 独立一份）。"""

    def __init__(self, parent_page, getter, sub_dir, show_fmt=True):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.fmt_cb = None
        if show_fmt:
            fmt_label = QLabel("保存格式")
            fmt_label.setProperty("role", "caption")
            row.addWidget(fmt_label)
            self.fmt_cb = QComboBox()
            for key, label in FMT_OPTIONS:
                self.fmt_cb.addItem(label, key)
            self.fmt_cb.setFixedWidth(128)
            row.addWidget(self.fmt_cb)
        self.dl_btn = QPushButton("全部下载")
        self.dl_btn.setProperty("variant", "primary")
        row.addWidget(self.dl_btn)
        self.progress = QLabel("")
        self.progress.setProperty("role", "caption")
        row.addWidget(self.progress, 1)
        self._page = parent_page
        self._getter = getter
        self._sub_dir = sub_dir
        self.dl_btn.clicked.connect(self.start)

    def start(self):
        current = self._getter()
        if not current or not current["files"]:
            self._page.app.toast("请先加载表情包", "warning")
            return
        base = os.path.join(str(get_export_dir()), "B站资源", self._sub_dir)
        # 进度走全局下载浮窗（原页面不再逐文件刷新标签）
        from gui_qt.download_float import get_float
        n_files = len(current["files"])
        card = get_float().add_batch(current["title"][:18] or "B站资源",
                                     total=n_files, threads=5)
        task = DownloadTask(api.sanitize(current["title"]), current["files"], base,
                            on_file_start=lambda rel: card.handle_event("start", rel),
                            on_file_done=lambda rel, err: card.handle_event(
                                "done", rel, err is None))
        self.dl_btn.setEnabled(False)
        self.progress.setText(f"下载中 0/{n_files}（进度见下载中心浮窗）")

        def job():
            return task.run(5)

        def done(result, err):
            self.dl_btn.setEnabled(True)
            if err:
                self.progress.setText(f"下载出错：{err}")
                card.finish(f"下载出错: {str(err)[:60]}")
                return
            failed = len(result["failed"])
            msg = f"完成 {result['done']}/{result['total']}" + (f"（{failed} 个失败）" if failed else "")
            self.progress.setText(msg)
            card.finish(("" if not failed else "注意：") + msg)
            self._page.app.toast(msg, "success" if not failed else "warning")
            try:
                os.startfile(result["dir"])
            except Exception:
                pass

        run_bg(job, done)


def render_files(table: QTableWidget, info: QLabel, result: dict):
    files = result["files"]
    table.setRowCount(0)
    ext_count = {}
    for i, f in enumerate(files[:500]):
        table.insertRow(i)
        table.setItem(i, 0, QTableWidgetItem(os.path.basename(f["path"])))
        ext = os.path.splitext(f["url"])[1].lstrip(".").lower() or (f.get("ext") or "?")
        ext_count[ext] = ext_count.get(ext, 0) + 1
        table.setItem(i, 1, QTableWidgetItem(f"{ext} · {os.path.dirname(f['path']) or '根目录'}"))
    rest = len(files) - min(len(files), 500)
    info.setText(f"「{result['title']}」共 {len(files)} 个文件"
                 + ("（表格仅显示前 500，下载不受影响）" if rest > 0 else "")
                 + f"  格式分布: {ext_count}")


class BiliEmojiPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._all_current = None
        self._id_current = None
        self._room_current = None
        self._all_source = []          # 全部包（id/text/mtime/url）
        self._all_filtered = []        # 过滤后
        self._all_shown = 0            # 已渲染行数（懒加载）
        self._thumb_gen: dict[QTableWidget, int] = {}  # 每表格缩略图批次（防串台）
        self._thumb_cache = ByteLRU(16 * 1024 * 1024, max_items=256)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_emoji_tab(), "普通表情包")
        self.tabs.addTab(self._build_room_tab(), "直播间表情")

    def _switch_tab(self, index: int):
        """切换表情包类别页签。"""
        self.tabs.setCurrentIndex(index)

    # ---------- 缩略图预览 ----------
    def _preview(self, table: QTableWidget, urls: list[str], col: int = 0):
        """后台下载并填充表格缩略图（带缓存；加载新内容后旧批次自动作废）。"""
        urls = urls[:THUMB_LIMIT]
        table.setIconSize(QSize(THUMB_SIZE, THUMB_SIZE))
        table.verticalHeader().setDefaultSectionSize(THUMB_SIZE + 10)
        gen = self._thumb_gen.get(table, 0) + 1
        self._thumb_gen[table] = gen
        cache = self._thumb_cache

        def job():
            hits, todo, seen = [], [], set()
            for i, u in enumerate(urls):
                if not u:
                    continue
                cached = cache.get(u)
                if cached is not None:
                    hits.append((i, cached))
                elif u not in seen:
                    seen.add(u)
                    todo.append((i, u))
            for i, u in todo:
                if gen != self._thumb_gen.get(table):
                    break
                try:
                    data = fetch_thumbnail(u, (THUMB_SIZE, THUMB_SIZE), timeout=10,
                                           user_agent=_THUMB_HEADERS["User-Agent"],
                                           referer=_THUMB_HEADERS["Referer"])
                    if gen == self._thumb_gen.get(table):
                        cache[u] = data
                        hits.append((i, data))
                except Exception:
                    continue
            return hits

        def done(items, err):
            if err or gen != self._thumb_gen.get(table):
                return
            for i, data in items:
                it = table.item(i, col)
                if it is None:
                    continue
                pm = QPixmap()
                if pm.loadFromData(data):
                    it.setIcon(QIcon(pm.scaled(THUMB_SIZE, THUMB_SIZE,
                                               Qt.KeepAspectRatio, Qt.SmoothTransformation)))

        run_bg(job, done)

    def on_hide(self):
        for table in self._thumb_gen:
            self._thumb_gen[table] += 1
        self._thumb_cache.clear()

    # ---------- 表情包（全部搜索 + 按ID / 装扮表情包） ----------
    def _build_emoji_tab(self):
        """全部表情包与按 ID 下载合并，避免两个高度重合的 tab。"""
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        split.addWidget(self._build_all_tab())
        split.addWidget(self._build_id_tab())
        split.setSizes([260, 320])
        self._emoji_split = split
        return _padded(split)

    # ---------- Tab1 全部表情包（搜索 + 滚动懒加载） ----------
    def _build_all_tab(self):
        card = QFrame()
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("浏览 / 搜索全部表情包")
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        v.addLayout(head)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.all_search = QLineEdit()
        self.all_search.setPlaceholderText("输入要搜索的表情包名字，回车或点击搜索按钮过滤")
        self.all_search.setClearButtonEnabled(True)
        self.all_search.returnPressed.connect(self._search_all)
        top.addWidget(self.all_search, 1)
        btn = QPushButton("搜索")
        btn.setProperty("variant", "primary")
        btn.clicked.connect(self._search_all)
        top.addWidget(btn)
        v.addLayout(top)

        self.all_table = QTableWidget(0, 4)
        self.all_table.setHorizontalHeaderLabels(["ID", "表情包名称", "更新时间", "查看"])
        self.all_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.all_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.all_table.setSelectionBehavior(QTableWidget.SelectRows)
        self._all_sorter = table_sort.install(self.all_table, skip_columns=(3,))
        # 滚动到底 → 追加下一批（懒加载）
        self.all_table.verticalScrollBar().valueChanged.connect(self._all_scroll)
        v.addWidget(self.all_table, 1)

        self.all_info = QLabel("需要 B站 登录。输入名字搜索，列表滚动到底自动加载更多")
        self.all_info.setProperty("role", "caption")
        self.all_info.setWordWrap(True)
        v.addWidget(self.all_info)

        self.all_dl = _DlBar(self, lambda: self._all_current, "表情包")
        v.addWidget(self.all_dl)
        return card

    def _search_all(self):
        """搜索：首次自动加载全部列表（仅包名等元数据，轻量），再按名字过滤。"""
        if not self._all_source:
            self.all_info.setText("正在加载表情包列表……")
            run_bg(api.emote_panel, lambda result, err: self._on_all(result, err))
        else:
            self._apply_all_filter()

    def _on_all(self, result, err):
        if err:
            self.all_info.setText(f"加载失败：{err}")
            return
        self._all_source = [
            {"id": str(it.get("id", "")), "text": it.get("text", ""), "mtime": it.get("mtime", 0),
             "url": it.get("url", "")}
            for it in result
        ]
        self._apply_all_filter()

    def _apply_all_filter(self):
        text = self.all_search.text().strip().lower()
        data = self._all_source
        if text:
            data = [d for d in self._all_source if text in d["text"].lower()]
        self._all_filtered = data
        self._all_shown = min(LIST_INITIAL, len(data))
        self._fill_all()
        more = len(data) - self._all_shown
        self.all_info.setText(
            f"匹配 {len(data)} 个表情包（已显示 {self._all_shown}{f'，滚动加载更多（剩 {more}）' if more > 0 else ''}；点「查看」加载详情后可下载）")

    def _fill_all(self):
        self.all_table.setRowCount(0)
        from datetime import datetime
        for i, d in enumerate(self._all_filtered[:self._all_shown]):
            self.all_table.insertRow(i)
            self.all_table.setItem(i, 0, QTableWidgetItem(d["id"]))
            self.all_table.setItem(i, 1, QTableWidgetItem(d["text"]))
            mtime = ""
            try:
                mtime = datetime.fromtimestamp(int(d["mtime"])).strftime("%Y-%m-%d")
            except Exception:
                pass
            self.all_table.setItem(i, 2, QTableWidgetItem(mtime))
            btn = QPushButton("查看")
            btn.clicked.connect(lambda _, idx=d["id"]: self._view_normal(idx))
            self.all_table.setCellWidget(i, 3, btn)
        # 封面缩略图（缓存命中即时回填，新图后台下载）
        self._preview(self.all_table, [d.get("url", "") for d in self._all_filtered[:self._all_shown]], col=1)
        self._all_sorter.reapply()

    def _all_scroll(self, val):
        bar = self.all_table.verticalScrollBar()
        if val < bar.maximum() or self._all_shown >= len(self._all_filtered):
            return
        self._all_shown = min(self._all_shown + LIST_STEP, len(self._all_filtered))
        self._fill_all()
        self.all_info.setText(f"已显示 {self._all_shown}/{len(self._all_filtered)} 个表情包（滚动到底继续加载）")

    def _view_normal(self, idx):
        self._switch_tab(0)
        self.id_entry.setText(str(idx))
        self.id_suit_cb.setChecked(False)
        if getattr(self, "_emoji_split", None) is not None:
            self._emoji_split.setSizes([140, 500])
        self._view_by_id()

    # ---------- Tab2 按ID（智能识别：链接/普通包/装扮/收藏集自动判断） ----------
    def _build_id_tab(self):
        card = QFrame()
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("按 ID 下载（普通表情包 / 装扮表情包）")
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        entry_label = QLabel("表情包/装扮 ID")
        entry_label.setProperty("role", "caption")
        row.addWidget(entry_label)
        self.id_entry = QLineEdit()
        self.id_entry.setPlaceholderText(
            "表情包 ID / 装扮 item_id / 链接（装扮、收藏集、b23短链均支持），多个 ID 用逗号分隔")
        self.id_entry.setClearButtonEnabled(True)
        self.id_entry.returnPressed.connect(self._view_by_id)
        row.addWidget(self.id_entry, 1)
        self.id_suit_cb = QCheckBox("装扮表情包")
        self.id_suit_cb.setToolTip("勾选后强制按装扮接口解析；不勾选时自动判断")
        row.addWidget(self.id_suit_cb)
        btn = QPushButton("查看")
        btn.setProperty("variant", "primary")
        btn.clicked.connect(self._view_by_id)
        row.addWidget(btn)
        v.addLayout(row)

        self.id_info = QLabel("自动识别普通表情包和装扮；识别到收藏集活动时自动打开活动详情")
        self.id_info.setProperty("role", "caption")
        self.id_info.setWordWrap(True)
        v.addWidget(self.id_info)
        self.id_table = QTableWidget(0, 2)
        self.id_table.setHorizontalHeaderLabels(["表情", "格式/目录"])
        self.id_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.id_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._id_sorter = table_sort.install(self.id_table, skip_columns=())
        v.addWidget(self.id_table, 1)
        v.addWidget(_DlBar(self, lambda: self._id_current, "表情包"))
        return card

    def _goto_garb(self, act_id: str):
        """切换到收藏集类别并加载活动详情。"""
        page = getattr(self, "_resource_host", None) or self.app.pages.get("bili_resource")
        if page is not None:
            page.show_garb(act_id)

    def _view_by_id(self):
        raw = self.id_entry.text().strip()
        if not raw:
            return
        # 链接输入：解析后路由（dlc→收藏集页 / suit→装扮模式）
        parsed = parse_link(raw) if "http" in raw.lower() else None
        if parsed:
            typ, oid = parsed
            if typ == "lottery":
                self.id_info.setText(f"识别到收藏集链接（act_id={oid}），正在打开活动详情……")
                self._goto_garb(oid)
                return
            if typ == "suit":
                self.id_entry.setText(oid)
                self.id_suit_cb.setChecked(True)
            else:
                self.id_info.setText(f"该链接是「{typ}」类型，请到对应页面解析（本页仅支持表情包/装扮/收藏集）")
                return
        ids = [x.strip() for x in self.id_entry.text().strip().replace("，", ",").split(",") if x.strip()]
        if not ids:
            return
        is_suit = self.id_suit_cb.isChecked()
        fmt = "gif"
        self.id_info.setText("加载中……")

        def job():
            files, title, hints = [], "", []
            for idx in ids:
                suit_mode = is_suit or len(idx) > 8
                if suit_mode:
                    from gui_qt.pages.bili_garb import _emoji_key, _emoji_urls
                    d = api.garb_item(idx)   # data=null 时抛"内容已下架"错误
                    title = d.get("name", idx)
                    pkgs = d.get("suit_items", {}).get("emoji_package", []) or []
                    if not pkgs:
                        pkgs = [{"name": d.get("name", ""), "properties": d.get("properties", {}) or {}}]
                    for pkg in pkgs:
                        try:
                            lst = json.loads(pkg.get("properties", {}).get("item_emoji_list", "[]") or "[]")
                        except Exception:
                            lst = []
                        if not lst:
                            lst = [{"name": e.get("name", ""),
                                    "properties": e.get("properties", {}) or {}}
                                   for e in (d.get("suit_items", {}).get("emoji", []) or [])]
                        pkg_dir = api.sanitize(pkg.get("name") or title or "表情包")
                        for e in lst:
                            for u in _emoji_urls(e.get("properties", {}) or {}):
                                files.append({"path": f"{pkg_dir}/{api.sanitize(_emoji_key(e.get('name', '表情')))}",
                                              "url": u})
                else:
                    pkgs = api.emote_package([idx])
                    if not pkgs:  # 普通包查不到 → 可能是失效装扮/收藏集 ID，逐级回退
                        try:
                            api.act_basic(idx)
                            hints.append(f"{idx} 是收藏集活动 ID，正在打开活动详情")
                            return {"files": [], "title": idx, "act_id": idx, "hints": hints}
                        except api.BiliApiError:
                            pass
                        hints.append(f"{idx}：普通表情包接口无数据（可尝试勾选「装扮表情包」）")
                        continue
                    for p in pkgs:
                        title = p.get("text", idx)
                        pkg_dir = api.sanitize(title)
                        for e in p.get("emote", []):
                            for u in fmt_urls(fmt, e):
                                files.append({"path": f"{pkg_dir}/{api.sanitize(e.get('text', '表情'))}", "url": u})
            return {"title": title or ids[0], "files": files, "hints": hints}

        def done(result, err):
            if err:
                self.id_info.setText(f"加载失败：{err}")
                return
            if result.get("act_id"):  # 命中收藏集 → 自动跳转
                self.id_info.setText("；".join(result["hints"]))
                self._goto_garb(result["act_id"])
                return
            self._id_current = result
            render_files(self.id_table, self.id_info, result)
            self._preview(self.id_table, [f["url"] for f in result["files"]])
            self._id_sorter.reapply()
            if result["hints"]:
                self.id_info.setText(self.id_info.text() + "  |  " + "；".join(result["hints"]))

        run_bg(job, done)

    # ---------- 直播间表情 ----------
    def _build_room_tab(self):
        card = QFrame()
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("直播间表情")
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        v.addLayout(head)

        row = QHBoxLayout()
        row.setSpacing(8)
        room_label = QLabel("房间号")
        room_label.setProperty("role", "caption")
        row.addWidget(room_label)
        self.room_entry2 = QLineEdit()
        self.room_entry2.setPlaceholderText("如 544820 或直播间链接（表情需登录，头像/封面不需要）")
        self.room_entry2.setClearButtonEnabled(True)
        self.room_entry2.returnPressed.connect(self._load_room)
        row.addWidget(self.room_entry2, 1)
        btn = QPushButton("加载")
        btn.setProperty("variant", "primary")
        btn.clicked.connect(self._load_room)
        row.addWidget(btn)
        v.addLayout(row)
        self.room_info = QLabel("加载直播间专属表情/UP主大表情/通用表情 + 主播头像/封面/网页+App背景图/关键帧（表情需登录）")
        self.room_info.setProperty("role", "caption")
        self.room_info.setWordWrap(True)
        v.addWidget(self.room_info)
        self.room_table = QTableWidget(0, 2)
        self.room_table.setHorizontalHeaderLabels(["表情", "格式/目录"])
        self.room_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.room_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._room_sorter = table_sort.install(self.room_table, skip_columns=())
        v.addWidget(self.room_table, 1)
        v.addWidget(_DlBar(self, lambda: self._room_current, "表情包"))
        return _padded(card)

    def _load_room(self):
        rid = self.room_entry2.text().strip()
        if not rid:
            return
        self.room_info.setText("加载中……")

        def job():
            files, notes = [], []
            # 直播间表情（需登录；GetEmoticons → data.data → [{pkg_name, emoticons:[{emoji,url}]}]）
            try:
                pkgs = api.room_emoticons(rid)
                if not pkgs:
                    notes.append("直播间表情为空")
            except api.BiliApiError as e:
                pkgs = []
                notes.append(f"直播间表情获取失败（{e}）")
            # 房间信息：App 端详细接口优先，网页端兜底（对齐原版 liveroom/[id].vue）
            name, images = "", {}
            try:
                d = api.live_room_app(rid)
                ri = d.get("room_info", {}) or {}
                name = ((d.get("anchor_info", {}) or {}).get("base_info", {}) or {}).get("uname", "")
                images = {
                    "主播头像": ((d.get("anchor_info", {}) or {}).get("base_info", {}) or {}).get("face", ""),
                    "封面": ri.get("cover", ""),
                    "网页端背景图": ri.get("background", ""),
                    "App端背景图": ri.get("app_background", ""),
                    "关键帧": ri.get("keyframe", ""),
                }
            except api.BiliApiError:
                try:
                    room = api.live_room_info(rid)
                    uid = str(room.get("uid", ""))
                    name = room.get("title", "")
                    if uid:
                        master = api.live_master_info(uid)
                        name = master.get("info", {}).get("uname", "") or name
                        if master.get("info", {}).get("face"):
                            images = {"主播头像": master["info"]["face"], **images}
                    images.update({
                        "封面": room.get("user_cover", ""),
                        "关键帧": room.get("keyframe", ""),
                        "网页端背景图": room.get("background", ""),
                    })
                except api.BiliApiError:
                    notes.append("房间信息获取失败")
            base = api.sanitize(f"{name}直播间" if name else f"直播间{rid}")
            seen = set()
            for label, u in images.items():
                if u and u not in seen:
                    seen.add(u)
                    files.append({"path": f"{base}/{label}", "url": u})
            # 表情目录：{主播名}直播间/{包名}/{表情名}
            # 专属包（UP主大表情等）排前，通用表情置底——一眼看到直播间专属大表情
            ordered = sorted(pkgs, key=lambda p: (p.get("pkg_name") == "通用表情",))
            for pkg in ordered:
                pkg_dir = api.sanitize(pkg.get("pkg_name", "房间表情"))
                for e in pkg.get("emoticons", []) or []:
                    u = e.get("url", "")
                    if u and u not in seen:
                        seen.add(u)
                        files.append({"path": f"{base}/{pkg_dir}/{api.sanitize(e.get('emoji', '表情'))}",
                                      "url": u})
            if pkgs:
                parts = [f"{p.get('pkg_name', '?')}({len(p.get('emoticons') or [])})" for p in ordered]
                notes.append("表情包：" + "、".join(parts) + "（专属在前，通用在后）")
            return {"title": f"直播间{rid}", "files": files, "notes": notes}

        def done(result, err):
            if err:
                self.room_info.setText(f"加载失败：{err}")
                return
            self._room_current = result
            render_files(self.room_table, self.room_info, result)
            self._preview(self.room_table, [f["url"] for f in result["files"]])
            self._room_sorter.reapply()
            if result["notes"]:
                self.room_info.setText(self.room_info.text() + "  |  " + "；".join(result["notes"]))

        run_bg(job, done)
