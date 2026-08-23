"""音乐下载工具箱（PySide6 版，对应原版 gui/netease_page.py）。

五平台：网易云音乐 / QQ音乐 / 酷狗 / 酷我 / 5sing
- 功能/设置 双标签
- 平台选择 + 模式按钮 + 动态输入区（搜索模式带歌手预填）
- 网易云走 netease 模块（Cookie + 音质选择 + 专辑解析）
- 其余四平台走 music/scrapers（游客直采：搜索/单曲解析/歌单解析）
- 详情滚动区：歌曲列表（封面+时长）、单曲详情卡（封面+歌词+立即下载）、歌单信息卡
- 下载：网易云 MusicDownloader；四平台 music/downloader（QQ 仅解析）
"""

import os
import re
import time

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QTabWidget,
    QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import make_row_clickable

QUALITY_NAMES = {
    "standard": "标准",
    "exhigh": "极高",
    "lossless": "无损",
    "hires": "Hi-Res",
    "sky": "沉浸环绕声",
    "jyeffect": "高清环绕声",
    "jymaster": "超清母带",
}

# (platform_id, 显示名, 模式列表) —— netease 走独立模块，其余走 music scrapers
PLATFORMS = [
    ("netease", "🎧 网易云音乐", ["search", "song", "playlist", "album"]),
    ("qq", "🐧 QQ音乐", ["search", "song", "playlist"]),
    ("kugou", "🐕 酷狗音乐", ["search", "song", "playlist"]),
    ("kuwo", "🐼 酷我音乐", ["search", "song", "playlist"]),
    ("5sing", "🎤 5sing音乐", ["search", "song", "playlist"]),
]

MODES = [
    ("search", "🎵 歌曲搜索"),
    ("song", "🎶 单曲解析"),
    ("playlist", "📋 歌单解析"),
    ("album", "💿 专辑解析"),
]

PLACEHOLDERS = {
    "netease": {
        "song": "歌曲链接或 ID（例：https://music.163.com/song?id=xxx）",
        "playlist": "歌单链接或 ID（例：https://music.163.com/playlist?id=xxx）",
        "album": "专辑链接 / ID / 专辑名",
    },
    "qq": {
        "song": "歌曲链接或 songmid（例：https://y.qq.com/n/ryqq/songDetail/xxx）",
        "playlist": "歌单链接或 dissid（例：https://y.qq.com/n/ryqq/playlist/7256912512）",
    },
    "kugou": {
        "song": "歌曲链接或 hash（例：https://www.kugou.com/mixsong/xxx.html）",
        "playlist": "歌单链接或 specialid（例：https://www.kugou.com/yy/special/single/9580287.html）",
    },
    "kuwo": {
        "song": "歌曲链接或 rid（例：https://www.kuwo.cn/play_detail/228908）",
        "playlist": "歌单链接或 pid（例：https://www.kuwo.cn/playlist_detail/1082685104）",
    },
    "5sing": {
        "song": "歌曲页链接（例：https://5sing.kugou.com/yc/4219209.html）",
        "playlist": "歌单链接（例：https://5sing.kugou.com/65188239/dj/61d1a130683d8134f5a28493.html）",
    },
}


def _parse_netease_id(text):
    """从输入解析网易云 ID（支持纯数字或链接里的 id=xxx）。"""
    s = (text or "").strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"id[=/](\d+)", s)
    if m:
        return int(m.group(1))
    return None


def _fmt_duration(ms):
    if not ms:
        return "?:??"
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _song_to_row(s):
    """SongInfo(pydc) → 页面统一行 dict（duration 统一为 ms）。"""
    return {
        "id": s.song_id,
        "name": s.song_name or s.title,
        "artists": s.singer or "",
        "album": s.album_name or "",
        "picUrl": s.cover_url or "",
        "duration": (s.duration or 0) * 1000,
        "_platform": s.platform,
        "_song": s,
    }


class NeteasePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.platform = "netease"
        self.mode = "search"
        self.results = []           # 当前模式的结果（统一为 [{id, name, artists, album, duration, _kind}]）
        self.check_boxes = []
        self.single_song_id = None  # 单曲解析时的当前歌曲 ID
        self.single_song = None     # 单曲解析时的 SongInfo（四平台）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_func(), "功能")
        self.tabs.addTab(self._build_settings(), "设置")

    # ===================== 公共：Cookie =====================
    def _cookies(self):
        from auth import get_netease_cookie
        from netease import CookieManager, COOKIE_FILE
        try:
            content = get_netease_cookie()
            return CookieManager(COOKIE_FILE).parse_cookie_string(content) if content else {}
        except Exception:
            return {}

    # ===================== 功能 tab =====================
    def _build_func(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(6, 6, 6, 6)
        v.addWidget(QLabel("🎧 音乐下载工具箱", styleSheet="font-size: 18px; font-weight: bold;"))

        # 顶部：平台选择 + 模式按钮
        top = QHBoxLayout()
        self.platform_menu = QComboBox()
        for _, label, _ in PLATFORMS:
            self.platform_menu.addItem(label)
        self.platform_menu.currentIndexChanged.connect(self._switch_platform)
        top.addWidget(self.platform_menu)
        self.mode_btns = {}
        self.mode_row = QHBoxLayout()
        top.addLayout(self.mode_row)
        top.addStretch(1)
        v.addLayout(top)

        # 输入区（动态，随模式变化）
        self.input_row = QHBoxLayout()
        v.addLayout(self.input_row)

        # 详情区
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setStyleSheet("QScrollArea { background: #ffffff; border-radius: 6px; }")
        self.detail_container = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_container)
        self.detail_layout.setContentsMargins(8, 8, 8, 8)
        self.detail_layout.setSpacing(2)
        self.detail_layout.setAlignment(Qt.AlignTop)
        self.detail_scroll.setWidget(self.detail_container)
        v.addWidget(self.detail_scroll, 1)

        # 底部：操作栏
        bottom = QHBoxLayout()
        bottom.addWidget(QPushButton("☑ 全选", clicked=lambda: self._sel("all")))
        bottom.addWidget(QPushButton("🔄 反选", clicked=lambda: self._sel("invert")))
        bottom.addWidget(QPushButton("□ 全不选", clicked=lambda: self._sel("none")))
        self.fallback_check = QCheckBox("自动换源")
        self.fallback_check.setChecked(True)
        self.fallback_check.setToolTip(
            "下载失败或仅试听时，自动到其他平台找同名曲：酷我→酷狗→网易云\n"
            "（网易云需已在 Cookie 管理中登录）")
        bottom.addWidget(self.fallback_check)
        bottom.addWidget(QLabel("音质:"))
        self.quality_menu = QComboBox()
        bottom.addWidget(self.quality_menu)
        self.download_btn = QPushButton("⬇ 下载选中")
        self.download_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        self.download_btn.clicked.connect(self._download_selected)
        bottom.addWidget(self.download_btn)
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        bottom.addWidget(self.status_label, 1)
        v.addLayout(bottom)

        # 默认平台/模式
        self._apply_platform("netease")
        return w

    # ===================== 设置 tab =====================
    def _build_settings(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(10, 10, 10, 10)
        v.addWidget(QLabel("⚙️ 音乐下载设置", styleSheet="font-size: 18px; font-weight: bold;"))
        tip = QLabel(
            "功能说明\n\n"
            "🎵 歌曲搜索：输入「歌名 / 歌手」关键词，可勾选下载\n"
            "🎶 单曲解析：输入歌曲链接或 ID，展示详情 + 歌词 + 封面，可立即下载\n"
            "📋 歌单解析：输入歌单链接或 ID，展示歌单信息 + 歌曲列表，可批量下载\n"
            "💿 专辑解析（仅网易云）：输入专辑链接/ID 或专辑名\n\n"
            "🔄 自动换源（默认开启）：\n"
            "勾选后，下载失败或只拿到试听片段时，自动到其他平台\n"
            "搜索同名曲继续下载，顺序：酷我 → 酷狗 → 网易云\n"
            "（网易云换源需要已在 Cookie 管理中登录）\n\n"
            "平台说明：\n"
            "· 网易云音乐：全音质下载，VIP 歌曲需登录 Cookie\n"
            "· 酷狗音乐：免费歌 128k 直链；VIP 付费歌游客无法下载\n"
            "· 酷我音乐：免费歌全曲；VIP 歌仅 11 秒试听片段\n"
            "· 5sing音乐：站内原唱/翻唱/伴奏 192k/128k 全曲可下\n"
            "· QQ音乐：官方直链接口已关闭，下载靠自动换源到其他平台\n\n"
            "提示：版权歌曲（VIP/付费）游客接口拿不到完整直链，\n"
            "此时自动换源会尝试其他平台是否有免费版本；\n"
            "若各平台均需付费，建议在网易云平台用已登录 Cookie 下载\n\n"
            "网易云 Cookie 文件：netease/cookie.txt\n"
            "下载目录：全局设置里配置的导出目录（默认 download/）\n\n"
            "左侧底部「🍪 管理 Cookie → 网易云」可扫码登录或手动输入 Cookie"
        )
        tip.setStyleSheet("color: #888; font-size: 12px;")
        tip.setWordWrap(True)
        tip.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        v.addWidget(tip)
        v.addStretch(1)
        return w

    # ===================== 平台切换 =====================
    def _switch_platform(self, index):
        self._apply_platform(PLATFORMS[index][0])

    def _apply_platform(self, platform):
        self.platform = platform
        # 模式按钮按平台重建
        while self.mode_row.count():
            item = self.mode_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.mode_btns = {}
        allowed = next(modes for pid, _, modes in PLATFORMS if pid == platform)
        for mode_id, label in MODES:
            if mode_id not in allowed:
                continue
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, m=mode_id: self._switch_mode(m))
            self.mode_row.addWidget(btn)
            self.mode_btns[mode_id] = btn
        # 音质选项
        self.quality_menu.blockSignals(True)
        self.quality_menu.clear()
        if platform == "netease":
            self.quality_menu.addItems(list(QUALITY_NAMES.values()))
            self.quality_menu.setCurrentText(QUALITY_NAMES["lossless"])
            self.quality_menu.setEnabled(True)
        else:
            from music.downloader import quality_options
            for value, name in quality_options(platform):
                self.quality_menu.addItem(name, value)
            self.quality_menu.setEnabled(self.quality_menu.count() > 1)
        self.quality_menu.blockSignals(False)
        # 默认模式（album 仅网易云）
        self._switch_mode("search")

    # ===================== 模式切换 =====================
    def _switch_mode(self, mode):
        self.mode = mode
        for m, btn in self.mode_btns.items():
            btn.setChecked(m == mode)
            if m == mode:
                btn.setStyleSheet("background: #1a5c2a; color: white;")
            else:
                btn.setStyleSheet("")
        # 清空输入区重建
        while self.input_row.count():
            item = self.input_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.singer_entry = None
        if mode == "search":
            self.singer_entry = QLineEdit()
            self.singer_entry.setPlaceholderText("歌手（默认封茗囧菌）")
            self.singer_entry.setFixedWidth(150)
            if self.platform == "netease":
                self.singer_entry.setText("封茗囧菌")
            self.input_row.addWidget(self.singer_entry)
        ph = PLACEHOLDERS.get(self.platform, {}).get(mode, "歌曲关键词")
        self.kw_entry = QLineEdit()
        self.kw_entry.setPlaceholderText(ph)
        self.kw_entry.returnPressed.connect(self._search)
        self.input_row.addWidget(self.kw_entry, 1)
        self.search_btn = QPushButton("🔍 获取")
        self.search_btn.clicked.connect(self._search)
        self.input_row.addWidget(self.search_btn)
        # 清空详情
        self._clear_detail()
        self.results = []
        self.check_boxes = []
        self.single_song_id = None
        self.single_song = None
        self._show_empty()

    def _clear_detail(self):
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    sub = item.layout().takeAt(0)
                    if sub.widget():
                        sub.widget().deleteLater()

    def _show_empty(self):
        hints = {
            "search": "输入「歌手 + 关键词」搜索歌曲",
            "song": "输入歌曲链接或 ID，查看详情 + 歌词",
            "playlist": "输入歌单链接或 ID，查看歌单信息 + 歌曲列表",
            "album": "输入专辑链接 / ID / 专辑名",
        }
        lbl = QLabel("💡 " + hints.get(self.mode, ""))
        lbl.setStyleSheet("color: #888; font-size: 13px; padding: 40px;")
        lbl.setAlignment(Qt.AlignCenter)
        self.detail_layout.addWidget(lbl)

    # ===================== 全选/反选 =====================
    def _sel(self, mode):
        for cb in self.check_boxes:
            if mode == "all":
                cb.setChecked(True)
            elif mode == "none":
                cb.setChecked(False)
            else:
                cb.setChecked(not cb.isChecked())

    # ===================== 封面图（异步加载） =====================
    def _load_image_async(self, url, size, parent_layout):
        """异步加载网络图片，返回占位 QLabel（加载成功后替换 pixmap）。"""
        lbl = QLabel("🎵")
        lbl.setFixedSize(size, size)
        lbl.setAlignment(Qt.AlignCenter)
        lbl.setStyleSheet("background: #ffffff; border-radius: 4px; color: #555;")
        parent_layout.addWidget(lbl)
        if not url:
            return lbl

        def load():
            try:
                import requests
                headers = {"User-Agent": "Mozilla/5.0"}
                host = re.match(r"https?://([^/]+)/", url)
                if host and host.group(1).endswith("126.net"):
                    headers["Referer"] = "https://music.163.com/"
                resp = requests.get(url, timeout=8, headers=headers)
                img = QImage.fromData(resp.content)
                if img.isNull():
                    return
                pm = QPixmap.fromImage(img).scaled(
                    size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

                def _set():
                    try:
                        lbl.setPixmap(pm)
                    except Exception:
                        pass
                post_to_main(_set)
            except Exception:
                pass

        import threading
        threading.Thread(target=load, daemon=True).start()
        return lbl

    # ===================== 搜索/解析入口 =====================
    def _search(self):
        kw = self.kw_entry.text().strip()
        if not kw:
            QMessageBox.warning(self, "提示", "请输入关键词或链接")
            return
        singer = self.singer_entry.text().strip() if getattr(self, "singer_entry", None) else ""

        self.search_btn.setEnabled(False)
        self.status_label.setText("⏳ 获取中...")
        self._clear_detail()
        self.results = []
        self.check_boxes = []
        self.single_song_id = None
        self.single_song = None

        mode = self.mode
        platform = self.platform

        def work():
            if platform == "netease":
                return self._work_netease(mode, kw, singer)
            return self._work_scraper(platform, mode, kw)

        def done(result, err):
            self.search_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"❌ {err}")
                self._show_empty()
                return
            kind, data = result
            if kind == "songs":
                self._render_songs(data or [])
            elif kind == "song":
                self._render_single_song(data)
            elif kind == "playlist":
                self._render_playlist(data)
            elif kind == "album":
                self._render_album(data)
            elif kind == "albums":
                self._render_albums(data or [])

        run_bg(work, done)

    def _work_netease(self, mode, kw, singer):
        from netease import (
            search_music, search_album, playlist_detail, album_detail,
            get_song_detail, get_lyric, get_pic_url,
        )
        cookies = self._cookies()
        if mode == "search":
            keyword = f"{singer} {kw}".strip() if singer else kw
            return ("songs", search_music(keyword, cookies, limit=50))
        if mode == "song":
            sid = _parse_netease_id(kw)
            if sid is None:
                raise ValueError("请输入正确的歌曲链接或 ID")
            info = get_song_detail(sid)
            # 补充歌词和封面
            lyric_data = get_lyric(sid, cookies)
            info["_lyric"] = lyric_data.get("lrc", "")
            al = info.get("al") or {}
            pic = al.get("picUrl")
            if not pic and al.get("pic"):
                pic = get_pic_url(al.get("pic"), 300)
            info["_picUrl"] = pic
            return ("song", info)
        if mode == "playlist":
            pid = _parse_netease_id(kw)
            if pid is None:
                raise ValueError("请输入正确的歌单链接或 ID")
            return ("playlist", playlist_detail(pid, cookies))
        if mode == "album":
            aid = _parse_netease_id(kw)
            if aid is not None:
                return ("album", album_detail(aid, cookies))
            keyword = f"{singer} {kw}".strip() if singer else kw
            return ("albums", search_album(keyword, cookies, limit=30))
        return ("songs", [])

    def _work_scraper(self, platform, mode, kw):
        from music import ALL_PLATFORMS
        scraper_cls = next(cls for pid, _, cls in ALL_PLATFORMS if pid == platform)
        scraper = scraper_cls()
        if mode == "search":
            songs = scraper.search_song(kw, 1, 30) or []
            return ("songs", [_song_to_row(s) for s in songs])
        if mode == "song":
            sid = scraper.extract_song_id(kw)
            if not sid:
                raise ValueError("请输入正确的歌曲链接或 ID")
            info = scraper.get_song_detail(sid)
            if info is None:
                raise ValueError("未找到该歌曲（可能已下架或链接失效）")
            return ("song", info)
        if mode == "playlist":
            pid = scraper.extract_playlist_id(kw)
            if not pid:
                raise ValueError("请输入正确的歌单链接或 ID")
            pl = scraper.get_playlist(pid)
            if pl is None:
                raise ValueError("未找到该歌单（可能已删除或链接失效）")
            return ("playlist", pl)
        return ("songs", [])

    # ===================== 渲染：歌曲列表（带封面） =====================
    def _render_songs(self, songs):
        self.results = songs
        if not songs:
            lbl = QLabel("（无结果）")
            lbl.setStyleSheet("color: gray; padding: 20px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.detail_layout.addWidget(lbl)
            self.status_label.setText("无结果")
            return
        for s in songs:
            row = QFrame()
            row.setStyleSheet("QFrame { background: transparent; border: none; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 1, 0, 1)
            cb = QCheckBox()
            cb.setFixedSize(18, 18)
            self.check_boxes.append(cb)
            h.addWidget(cb)
            pic = s.get("picUrl") or (s.get("al") or {}).get("picUrl")
            self._load_image_async(pic, 44, h)
            text = f"{s.get('name', '')} - {s.get('artists', '')}  ({s.get('album', '')})"
            info = QLabel(text)
            info.setWordWrap(True)
            h.addWidget(info, 1)
            dur = QLabel(_fmt_duration(s.get("duration", 0)))
            dur.setStyleSheet("color: #888; font-size: 11px;")
            h.addWidget(dur)
            make_row_clickable(row, cb)
            self.detail_layout.addWidget(row)
        self.status_label.setText(f"✅ 共 {len(songs)} 首，勾选后批量下载")

    # ===================== 渲染：单曲解析（详情 + 歌词） =====================
    def _render_single_song(self, song):
        if not song:
            self._show_empty()
            return
        if self.platform == "netease":
            self._render_single_song_netease(song)
            return
        self.single_song = song
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        self._load_image_async(song.cover_url, 120, h)
        lines = [
            f"🎵 {song.song_name or song.title}",
            f"👤 歌手: {song.singer or '未知'}",
        ]
        if song.album_name:
            lines.append(f"💿 专辑: {song.album_name}")
        if song.duration:
            lines.append(f"⏱ 时长: {song.duration // 60}:{song.duration % 60:02d}")
        if song.mode:
            lines.append(f"🎙 模式: {song.mode}")
        if song.language:
            lines.append(f"🌐 语种: {song.language}")
        if song.genre:
            lines.append(f"🎼 流派: {song.genre}")
        if song.release_date:
            lines.append(f"📅 发行: {song.release_date}")
        if song.pay and song.pay.fee:
            lines.append("👑 VIP 歌曲（下载可能受限）")
        lines.append(f"🆔 ID: {song.song_id}")
        txt = QLabel("\n".join(lines))
        txt.setWordWrap(True)
        h.addWidget(txt, 1)
        dl_btn = QPushButton("⬇ 立即下载")
        dl_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 12px; border-radius: 4px;")
        dl_btn.clicked.connect(self._download_single_song)
        h.addWidget(dl_btn, 0, Qt.AlignBottom)
        self.detail_layout.addWidget(info_card)

        lyric = song.lyrics or ""
        if lyric:
            lyric_card = QFrame()
            lyric_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
            lv = QVBoxLayout(lyric_card)
            lv.setContentsMargins(10, 6, 10, 8)
            lv.setSpacing(2)
            title_lbl = QLabel("📝 歌词")
            title_lbl.setStyleSheet("font-weight: bold; font-size: 12px; background: transparent; border: none;")
            title_lbl.setFixedHeight(18)
            lv.addWidget(title_lbl)
            lyric_text = QPlainTextEdit()
            lyric_text.setReadOnly(True)
            lyric_text.setMinimumHeight(160)
            lyric_text.setMaximumHeight(260)
            lyric_text.setStyleSheet("font-family: Consolas; font-size: 12px;")
            # 仅显示前 100 行避免卡顿
            lines = lyric.splitlines()
            display = "\n".join(lines[:100]) + ("\n…(更多歌词省略)" if len(lines) > 100 else "")
            lyric_text.setPlainText(display)
            lv.addWidget(lyric_text, 1)
            self.detail_layout.addWidget(lyric_card)
        else:
            lbl = QLabel("（暂无歌词）")
            lbl.setStyleSheet("color: #888; padding: 8px;")
            self.detail_layout.addWidget(lbl)
        self.status_label.setText("✅ 单曲解析完成，可立即下载或查看歌词")

    def _render_single_song_netease(self, song):
        self.single_song_id = song.get("id")
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        pic = song.get("_picUrl") or (song.get("al") or {}).get("picUrl")
        self._load_image_async(pic, 120, h)
        info_text = (
            f"🎵 {song.get('name', '')}\n"
            f"👤 歌手: {song.get('artists', '')}\n"
            f"💿 专辑: {song.get('album', '')}\n"
            f"⏱ 时长: {_fmt_duration(song.get('duration', 0))}\n"
            f"🆔 ID: {song.get('id', '')}"
        )
        txt = QLabel(info_text)
        h.addWidget(txt, 1)
        # 单曲下载按钮（独立）
        dl_btn = QPushButton("⬇ 立即下载")
        dl_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 12px; border-radius: 4px;")
        dl_btn.clicked.connect(self._download_single_song)
        h.addWidget(dl_btn, 0, Qt.AlignBottom)
        self.detail_layout.addWidget(info_card)

        # 歌词
        lyric = song.get("_lyric", "")
        if lyric:
            lyric_card = QFrame()
            lyric_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
            lv = QVBoxLayout(lyric_card)
            lv.setContentsMargins(10, 6, 10, 8)
            lv.setSpacing(2)
            # 标题行：固定小高度，避免占大块
            title_lbl = QLabel("📝 歌词")
            title_lbl.setStyleSheet("font-weight: bold; font-size: 12px; background: transparent; border: none;")
            title_lbl.setFixedHeight(18)
            lv.addWidget(title_lbl)
            lyric_text = QPlainTextEdit()
            lyric_text.setReadOnly(True)
            lyric_text.setMinimumHeight(160)
            lyric_text.setMaximumHeight(260)
            lyric_text.setStyleSheet("font-family: Consolas; font-size: 12px;")
            # 仅显示前 100 行避免卡顿
            lines = lyric.splitlines()
            display = "\n".join(lines[:100]) + ("\n…(更多歌词省略)" if len(lines) > 100 else "")
            lyric_text.setPlainText(display)
            lv.addWidget(lyric_text, 1)
            self.detail_layout.addWidget(lyric_card)
        else:
            lbl = QLabel("（暂无歌词）")
            lbl.setStyleSheet("color: #888; padding: 8px;")
            self.detail_layout.addWidget(lbl)
        self.status_label.setText("✅ 单曲解析完成，可立即下载或查看歌词")

    # ===================== 渲染：歌单解析 =====================
    def _render_playlist(self, pl):
        if not pl:
            self._show_empty()
            return
        if self.platform == "netease":
            self._render_playlist_netease(pl)
            return
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        self._load_image_async(pl.cover_url, 96, h)
        lines = [
            f"📋 {pl.name}",
            f"👤 创建者: {pl.creator or '未知'}",
            f"🎵 歌曲数: {pl.song_count}",
        ]
        if pl.play_count:
            lines.append(f"▶ 播放: {pl.play_count:,}")
        if pl.tags:
            lines.append(f"🏷 标签: {pl.tags}")
        if pl.description:
            lines.append(f"📝 简介: {pl.description[:90]}")
        txt = QLabel("\n".join(lines))
        txt.setWordWrap(True)
        h.addWidget(txt, 1)
        self.detail_layout.addWidget(info_card)
        self.results = [_song_to_row(s) for s in pl.songs]
        self._render_songs(self.results)
        self.status_label.setText(f"✅ 歌单「{pl.name}」共 {len(self.results)} 首（{pl.platform_name}）")

    def _render_playlist_netease(self, pl):
        # get_playlist_detail 返回扁平结构：{id, name, coverImgUrl, creator(str), trackCount, description, tracks}
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        pic = pl.get("coverImgUrl")
        self._load_image_async(pic, 96, h)
        txt = QLabel(
            f"📋 {pl.get('name', '')}\n"
            f"👤 创建者: {pl.get('creator') or ''}\n"
            f"🎵 歌曲数: {pl.get('trackCount', 0)}\n"
            f"📝 简介: {(pl.get('description') or '无')[:90]}"
        )
        txt.setWordWrap(True)
        h.addWidget(txt, 1)
        self.detail_layout.addWidget(info_card)
        # 歌曲列表（tracks 已是 {id, name, artists, album, picUrl, duration} 结构）
        tracks = [t for t in (pl.get("tracks") or [])
                  if isinstance(t, dict) and t.get("id")]
        self.results = [
            {"id": t["id"], "name": t.get("name", ""),
             "artists": t.get("artists", ""),
             "album": t.get("album", ""),
             "picUrl": t.get("picUrl", ""),
             "duration": t.get("duration", 0)}
            for t in tracks
        ]
        self._render_songs(self.results)
        self.status_label.setText(f"✅ 歌单「{pl.get('name', '')}」共 {len(self.results)} 首")

    # ===================== 渲染：专辑解析（专辑详情） =====================
    def _render_album(self, album):
        if not album:
            self._show_empty()
            return
        songs = album.get("songs") or []
        album_info = album.get("album") or {}
        info_card = QFrame()
        info_card.setStyleSheet("QFrame { background: #ffffff; border-radius: 6px; }")
        h = QHBoxLayout(info_card)
        h.setContentsMargins(10, 10, 10, 10)
        pic = album_info.get("picUrl") or album_info.get("blurPicUrl")
        self._load_image_async(pic, 96, h)
        artist = album_info.get("artist") or {}
        artist_name = artist.get("name", "") if isinstance(artist, dict) else str(artist)
        publish = album_info.get("publishTime", 0)
        pub_str = time.strftime("%Y-%m-%d", time.localtime(publish / 1000)) if publish else "未知"
        txt = QLabel(
            f"💿 {album_info.get('name', '')}\n"
            f"👤 歌手: {artist_name}\n"
            f"🎵 歌曲数: {album_info.get('size', len(songs))}\n"
            f"📅 发行: {pub_str}"
        )
        h.addWidget(txt, 1)
        self.detail_layout.addWidget(info_card)
        self.results = [
            {"id": s["id"], "name": s.get("name", ""),
             "artists": "/".join(a.get("name", "") for a in (s.get("ar") or [])),
             "album": album_info.get("name", ""),
             "duration": s.get("dt", 0)}
            for s in songs
        ]
        self._render_songs(self.results)
        self.status_label.setText(f"✅ 专辑「{album_info.get('name', '')}」共 {len(self.results)} 首")

    # ===================== 渲染：专辑搜索结果（整张专辑下载） =====================
    def _render_albums(self, albums):
        self.results = albums
        if not albums:
            lbl = QLabel("（无结果）")
            lbl.setStyleSheet("color: gray; padding: 20px;")
            lbl.setAlignment(Qt.AlignCenter)
            self.detail_layout.addWidget(lbl)
            self.status_label.setText("无结果")
            return
        for alb in albums:
            row = QFrame()
            row.setStyleSheet("QFrame { background: transparent; border: none; }")
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 1, 0, 1)
            cb = QCheckBox()
            cb.setFixedSize(18, 18)
            self.check_boxes.append(cb)
            h.addWidget(cb)
            pic = alb.get("picUrl") or alb.get("blurPicUrl")
            self._load_image_async(pic, 44, h)
            artist = alb.get("artist")
            if isinstance(artist, dict):
                artist = artist.get("name", "")
            lbl = QLabel(f"💿 {alb.get('name', '')} - {artist}")
            h.addWidget(lbl, 1)
            make_row_clickable(row, cb)
            self.detail_layout.addWidget(row)
        self.status_label.setText(f"✅ 共 {len(albums)} 个专辑，勾选后下载整张专辑")

    # ===================== 下载：单曲立即下载 =====================
    def _download_single_song(self):
        if self.platform == "netease":
            self._download_single_netease()
            return
        song = self.single_song
        if not song:
            QMessageBox.warning(self, "提示", "请先解析单曲")
            return
        quality = self.quality_menu.currentData() or ""
        use_fallback = self.fallback_check.isChecked()
        self.download_btn.setEnabled(False)
        self.status_label.setText("⏳ 下载中..." if not use_fallback else "⏳ 下载中（失败将自动换源）...")

        def work():
            from music.downloader import download_song_smart
            from toolbox.config_manager import get_export_subdir
            out_dir = str(get_export_subdir("music"))
            try:
                return download_song_smart(song, out_dir, quality, fallback=use_fallback)
            except Exception as e:
                from music.downloader import DownloadResult
                return DownloadResult(error_message=str(e))

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"❌ {err}")
                return
            via = f"（经{result.via_platform}换源）" if result.via_platform else ""
            if result.success:
                if result.is_preview:
                    self.status_label.setText(f"⚠️ 仅试听片段：{os.path.basename(result.file_path)}（各平台均为 VIP 付费曲）{via}")
                else:
                    self.status_label.setText(f"✅ 已下载 {os.path.basename(result.file_path)}{via}")
            else:
                self.status_label.setText(f"❌ {result.error_message[:80]}")

        run_bg(work, done)

    def _download_single_netease(self):
        if not self.single_song_id:
            QMessageBox.warning(self, "提示", "请先解析单曲")
            return
        quality = [k for k, v in QUALITY_NAMES.items() if v == self.quality_menu.currentText()][0]
        sid = int(self.single_song_id)
        self.download_btn.setEnabled(False)
        self.status_label.setText("⏳ 下载中...")

        def work():
            from netease import MusicDownloader, COOKIE_FILE
            from toolbox.config_manager import get_export_subdir
            from auth import sync_netease_to_legacy
            sync_netease_to_legacy()
            out_dir = str(get_export_subdir("music"))
            dl = MusicDownloader(download_dir=out_dir, cookie_file=COOKIE_FILE)
            try:
                r = dl.download_music_file(sid, quality)
                if r and r.success:
                    return (True, os.path.basename(r.file_path), "")
                return (False, "", (r.error_message if r else "失败"))
            except Exception as e:
                return (False, "", str(e))

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"❌ {err}")
                return
            ok, fname, fmsg = result
            if ok:
                self.status_label.setText(f"✅ 已下载 {fname}")
            else:
                self.status_label.setText(f"❌ {fmsg[:60]}")

        run_bg(work, done)

    # ===================== 下载：批量下载（歌曲/专辑） =====================
    def _download_selected(self):
        selected = [self.results[i] for i, cb in enumerate(self.check_boxes)
                    if cb.isChecked() and i < len(self.results)]
        if not selected:
            QMessageBox.warning(self, "提示", "请先勾选要下载的歌曲")
            return
        if self.platform == "netease":
            self._download_selected_netease(selected)
            return

        quality = self.quality_menu.currentData() or ""
        use_fallback = self.fallback_check.isChecked()
        self.download_btn.setEnabled(False)
        self.status_label.setText("⏳ 下载中..." if not use_fallback else "⏳ 下载中（失败将自动换源）...")

        def work():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from music.downloader import download_song_smart
            from toolbox.config_manager import get_export_subdir
            out_dir = str(get_export_subdir("music"))
            ok, fail, preview, via_cnt, first_err = 0, 0, 0, 0, ""

            def _one(item):
                song = item.get("_song")
                if song is None:
                    from music.models import SongInfo
                    song = SongInfo(song_id=str(item.get("id", "")),
                                    song_name=item.get("name", ""),
                                    singer=item.get("artists", ""),
                                    album_name=item.get("album", ""),
                                    duration=(item.get("duration") or 0) // 1000,
                                    platform=self.platform)
                return download_song_smart(song, out_dir, quality, fallback=use_fallback)

            with ThreadPoolExecutor(max_workers=4) as ex:
                futs = {ex.submit(_one, it): it for it in selected}
                for fut in as_completed(futs):
                    try:
                        r = fut.result()
                    except Exception as e:
                        r = None
                        if not first_err:
                            first_err = str(e)
                    if r is None:
                        fail += 1
                    elif r.success and r.is_preview:
                        preview += 1
                    elif r.success:
                        ok += 1
                        if r.via_platform:
                            via_cnt += 1
                    else:
                        fail += 1
                        if not first_err:
                            first_err = r.error_message
            return ok, fail, preview, via_cnt, first_err

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"❌ {err}")
                return
            ok, fail, preview, via_cnt, first_err = result
            parts = [f"成功 {ok}"]
            if via_cnt:
                parts.append(f"换源 {via_cnt}")
            if preview:
                parts.append(f"试听 {preview}")
            if fail:
                parts.append(f"失败 {fail}")
            tip = ("⚠️ 完成（" + " / ".join(parts) + ")") if (fail or preview) else ("✅ 下载完成（成功 " + str(ok) + "）")
            if first_err and (fail or preview):
                tip += f"：{first_err[:40]}"
            self.status_label.setText(tip)

        run_bg(work, done)

    def _download_selected_netease(self, selected):
        quality = [k for k, v in QUALITY_NAMES.items() if v == self.quality_menu.currentText()][0]
        # 判断是专辑（搜索结果含 artist 字段而非 artists）还是歌曲
        is_albums = bool(selected) and ("artists" not in selected[0] and "artist" in selected[0])

        self.download_btn.setEnabled(False)
        self.status_label.setText("⏳ 下载中...")

        def work():
            from netease import MusicDownloader, COOKIE_FILE, album_detail
            from toolbox.config_manager import get_export_subdir
            from auth import sync_netease_to_legacy
            sync_netease_to_legacy()
            out_dir = str(get_export_subdir("music"))
            dl = MusicDownloader(download_dir=out_dir, cookie_file=COOKIE_FILE)
            ok, fail, first_err = 0, 0, ""
            ids = []
            if is_albums:
                cookies = self._cookies()
                for alb in selected:
                    try:
                        info = album_detail(int(alb["id"]), cookies)
                        for song in info.get("songs", []):
                            ids.append(song["id"])
                    except Exception as e:
                        fail += 1
                        if not first_err:
                            first_err = str(e)
                if not ids and not fail:
                    first_err = "专辑内无歌曲"
            else:
                ids = [int(s["id"]) for s in selected]

            for sid in ids:
                try:
                    r = dl.download_music_file(sid, quality)
                    if r and r.success:
                        ok += 1
                    else:
                        fail += 1
                        msg = (r.error_message if r else "失败")
                        if not first_err:
                            first_err = msg
                except Exception as e:
                    fail += 1
                    if not first_err:
                        first_err = str(e)
            return ok, fail, first_err

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"❌ {err}")
                return
            ok, fail, first_err = result
            if fail:
                tip = f"⚠️ 完成（成功 {ok} / 失败 {fail}）" + (f"：{first_err[:50]}" if first_err else "")
                self.status_label.setText(tip)
            else:
                self.status_label.setText(f"✅ 下载完成（成功 {ok}）")

        run_bg(work, done)
