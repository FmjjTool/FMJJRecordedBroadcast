"""网易云音乐下载页（PySide6 版）：四种模式 + 音质选择 + 批量下载。"""

import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage

QUALITY_NAMES = {
    "standard": "标准", "exhigh": "极高", "lossless": "无损", "hires": "Hi-Res",
    "sky": "沉浸环绕声", "jyeffect": "高清环绕声", "jymaster": "超清母带",
}

MODES = [
    ("search", "🎵 歌曲搜索"), ("song", "🎶 单曲解析"),
    ("playlist", "📋 歌单解析"), ("album", "💿 专辑解析"),
]


def _parse_id(text):
    s = (text or "").strip()
    if s.isdigit():
        return int(s)
    m = re.search(r"id[=/](\d+)", s)
    return int(m.group(1)) if m else None


class NeteasePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.mode = "search"
        self.results = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)
        self.tabs.addTab(self._build_func(), "功能")
        self.tabs.addTab(self._build_settings(), "设置")

    def _build_func(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("🎧 网易云音乐工具箱", styleSheet="font-size: 18px; font-weight: bold;"))

        # 模式按钮 + 输入
        mode_row = QHBoxLayout()
        self.mode_btns = {}
        for mode_id, label in MODES:
            b = QPushButton(label)
            b.setCheckable(True)
            b.clicked.connect(lambda checked, m=mode_id: self._switch_mode(m))
            mode_row.addWidget(b)
            self.mode_btns[mode_id] = b
        mode_row.addStretch(1)
        v.addLayout(mode_row)

        input_row = QHBoxLayout()
        self.singer_entry = QLineEdit()
        self.singer_entry.setPlaceholderText("歌手（默认封茗囧菌）")
        self.singer_entry.setFixedWidth(150)
        input_row.addWidget(self.singer_entry)
        self.kw_entry = QLineEdit()
        self.kw_entry.setPlaceholderText("歌曲关键词 / 链接 / ID")
        self.kw_entry.returnPressed.connect(self._search)
        input_row.addWidget(self.kw_entry, 1)
        search_btn = QPushButton("🔍 获取")
        search_btn.clicked.connect(self._search)
        input_row.addWidget(search_btn)
        v.addLayout(input_row)

        self.list = QListWidget()
        v.addWidget(self.list, 1)

        # 底部操作
        bottom = QHBoxLayout()
        bottom.addWidget(QPushButton("☑ 全选", clicked=lambda: self._sel("all")))
        bottom.addWidget(QPushButton("🔄 反选", clicked=lambda: self._sel("invert")))
        bottom.addWidget(QPushButton("□ 全不选", clicked=lambda: self._sel("none")))
        bottom.addWidget(QLabel("音质:"))
        self.quality = QComboBox()
        self.quality.addItems(list(QUALITY_NAMES.values()))
        self.quality.setCurrentText(QUALITY_NAMES["lossless"])
        bottom.addWidget(self.quality)
        dl_btn = QPushButton("⬇ 下载选中")
        dl_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        dl_btn.clicked.connect(self._download_selected)
        bottom.addWidget(dl_btn)
        self.status_label = QLabel("")
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        v.addLayout(bottom)
        self._switch_mode("search")
        return w

    def _build_settings(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.addWidget(QLabel("⚙️ 网易云音乐设置", styleSheet="font-size: 16px; font-weight: bold;"))
        v.addWidget(QLabel(
            "五种功能说明\n\n"
            "🎵 歌曲搜索：输入「歌名 / 歌手」关键词，可勾选下载\n"
            "🎶 单曲解析：输入歌曲链接或 ID，展示详情，可立即下载\n"
            "📋 歌单解析：输入歌单链接或 ID，批量下载\n"
            "💿 专辑解析：输入专辑链接/ID 或专辑名\n\n"
            "音质选项：标准 / 极高 / 无损 / Hi-Res / 沉浸环绕声 / 高清环绕声 / 超清母带\n"
            "默认无损；登录 VIP 账号可获取更高音质\n\n"
            "左侧底部「🍪 Cookie → 网易云」可扫码登录", styleSheet="color: #888;"))
        v.addStretch(1)
        return w

    def _switch_mode(self, mode):
        self.mode = mode
        for m, b in self.mode_btns.items():
            b.setChecked(m == mode)
        placeholders = {
            "song": "歌曲链接或 ID", "playlist": "歌单链接或 ID", "album": "专辑链接 / ID / 专辑名",
        }
        self.kw_entry.setPlaceholderText(placeholders.get(mode, "歌曲关键词"))
        self.singer_entry.setVisible(mode in ("search", "album"))
        self.list.clear()
        self.results = []

    def _cookies(self):
        from auth import get_netease_cookie
        from netease import CookieManager, COOKIE_FILE
        try:
            content = get_netease_cookie()
            return CookieManager(COOKIE_FILE).parse_cookie_string(content) if content else {}
        except Exception:
            return {}

    def _sel(self, mode):
        for i in range(self.list.count()):
            it = self.list.item(i)
            if mode == "all":
                it.setCheckState(Qt.Checked)
            elif mode == "none":
                it.setCheckState(Qt.Unchecked)
            else:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _search(self):
        kw = self.kw_entry.text().strip()
        if not kw:
            QMessageBox.warning(self, "提示", "请输入关键词或链接")
            return
        singer = self.singer_entry.text().strip() if self.singer_entry.isVisible() else ""
        mode = self.mode
        self.status_label.setText("⏳ 获取中...")
        self.list.clear()

        def work():
            from netease import search_music, search_album, playlist_detail, album_detail, get_song_detail
            cookies = self._cookies()
            if mode == "search":
                keyword = f"{singer} {kw}".strip() if singer else kw
                return ("songs", search_music(keyword, cookies, limit=50))
            if mode == "song":
                sid = _parse_id(kw)
                if sid is None:
                    raise ValueError("请输入正确的歌曲链接或 ID")
                return ("song", get_song_detail(sid))
            if mode == "playlist":
                pid = _parse_id(kw)
                if pid is None:
                    raise ValueError("请输入正确的歌单链接或 ID")
                return ("playlist", playlist_detail(pid, cookies))
            if mode == "album":
                aid = _parse_id(kw)
                if aid is not None:
                    return ("album", album_detail(aid, cookies))
                keyword = f"{singer} {kw}".strip() if singer else kw
                return ("albums", search_album(keyword, cookies, limit=30))
            return ("songs", [])

        def done(result, err):
            if err:
                self.status_label.setText(f"❌ {err}")
                return
            kind, data = result
            self._render(kind, data)

        run_bg(work, done)

    def _render(self, kind, data):
        self.list.clear()
        self.results = []
        if kind in ("songs", "albums"):
            songs = data or []
            if kind == "albums":
                songs = [{"id": a.get("id"), "name": a.get("name"), "artists": a.get("artist", {}).get("name", "") if isinstance(a.get("artist"), dict) else "", "album": ""} for a in songs]
            for s in songs:
                name = s.get("name", "")
                artists = s.get("artists", "")
                if isinstance(artists, list):
                    artists = " / ".join(a.get("name", "") for a in artists)
                album = s.get("album", "")
                if isinstance(album, dict):
                    album = album.get("name", "")
                it = QListWidgetItem(f"{name} - {artists}  ({album})")
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Unchecked)
                it.setData(Qt.UserRole, s.get("id"))
                self.list.addItem(it)
                self.results.append(s)
            self.status_label.setText(f"✅ 共 {len(self.results)} 首，勾选后批量下载")
        elif kind in ("playlist", "album"):
            songs = (data or {}).get("songs", []) or (data or {}).get("tracks", [])
            for s in songs:
                name = s.get("name", "")
                ar = s.get("ar", []) or s.get("artists", [])
                artists = " / ".join(a.get("name", "") for a in ar)
                it = QListWidgetItem(f"{name} - {artists}")
                it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                it.setCheckState(Qt.Unchecked)
                it.setData(Qt.UserRole, s.get("id"))
                self.list.addItem(it)
                self.results.append(s)
            self.status_label.setText(f"✅ 共 {len(self.results)} 首，勾选后批量下载")
        elif kind == "song":
            s = data or {}
            name = s.get("name", "")
            it = QListWidgetItem(f"{name} - 单曲")
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, s.get("id"))
            self.list.addItem(it)
            self.results.append(s)
            self.status_label.setText("✅ 单曲解析完成")

    def _download_selected(self):
        selected = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == Qt.Checked:
                sid = it.data(Qt.UserRole)
                if sid:
                    selected.append(sid)
        if not selected:
            QMessageBox.warning(self, "提示", "请先勾选要下载的歌曲")
            return
        quality = [k for k, v in QUALITY_NAMES.items() if v == self.quality.currentText()][0]
        self.status_label.setText(f"⏳ 下载 {len(selected)} 首...")

        def work():
            from netease import MusicDownloader, COOKIE_FILE
            from toolbox.config_manager import get_export_subdir
            out_dir = str(get_export_subdir("songs"))
            dl = MusicDownloader(download_dir=out_dir, cookie_file=COOKIE_FILE)
            ok = fail = 0
            for sid in selected:
                try:
                    r = dl.download_music_file(sid, quality)
                    if r:
                        ok += 1
                    else:
                        fail += 1
                except Exception:
                    fail += 1
            return ok, fail

        def done(result, err):
            if err:
                self.status_label.setText(f"❌ {err}")
                return
            ok, fail = result
            self.status_label.setText(f"✅ 下载完成：成功 {ok}，失败 {fail}")

        run_bg(work, done)
