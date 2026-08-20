"""音乐信息获取页（PySide6 版）：五平台五列对比搜索。"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList

PLATFORMS = [
    ("kugou", "酷狗音乐"), ("qq", "QQ音乐"), ("netease", "网易云音乐"),
    ("kuwo", "酷我音乐"), ("5sing", "5sing音乐"),
]


class MusicInfoPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.columns = {}  # key -> {"songs": [], "list": QListWidget}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        title = QLabel("🎵 音乐信息获取")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.singer_entry = QLineEdit()
        self.singer_entry.setPlaceholderText("歌手（留空=封茗囧菌）")
        self.singer_entry.setFixedWidth(150)
        row.addWidget(self.singer_entry)
        self.song_entry = QLineEdit()
        self.song_entry.setPlaceholderText("歌曲名 / 专辑名")
        self.song_entry.returnPressed.connect(self._search)
        row.addWidget(self.song_entry, 1)
        self.search_btn = QPushButton("🔍 搜索五平台")
        self.search_btn.clicked.connect(self._search)
        row.addWidget(self.search_btn)
        layout.addLayout(row)

        # 五列
        cols = QHBoxLayout()
        for key, name in PLATFORMS:
            col_w = QWidget()
            cv = QVBoxLayout(col_w)
            cv.setContentsMargins(3, 3, 3, 3)
            cv.addWidget(QLabel(name, styleSheet="font-weight: bold;"))
            bar = QHBoxLayout()
            bar.addWidget(QPushButton("全选", clicked=lambda k=key: self._col_select(k, "all")))
            bar.addWidget(QPushButton("反选", clicked=lambda k=key: self._col_select(k, "invert")))
            bar.addWidget(QPushButton("清除", clicked=lambda k=key: self._col_select(k, "none")))
            cv.addLayout(bar)
            lst = RowCheckList()
            cv.addWidget(lst, 1)
            cols.addWidget(col_w, 1)
            self.columns[key] = {"songs": [], "list": lst}
        layout.addLayout(cols, 1)

        bottom = QHBoxLayout()
        self.get_btn = QPushButton("📥 获取选中歌曲详情并导出")
        self.get_btn.setStyleSheet("background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.get_btn.clicked.connect(self._fetch_selected)
        bottom.addWidget(self.get_btn)
        self.status_label = QLabel("")
        bottom.addWidget(self.status_label)
        bottom.addStretch(1)
        layout.addLayout(bottom)

    def on_return_press(self):
        """主窗口回车统一入口。"""
        self._search()

    def _col_select(self, key, mode):
        lst = self.columns[key]["list"]
        for i in range(lst.count()):
            it = lst.item(i)
            if mode == "all":
                it.setCheckState(Qt.Checked)
            elif mode == "none":
                it.setCheckState(Qt.Unchecked)
            else:
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)

    def _search(self):
        singer = self.singer_entry.text().strip() or "封茗囧菌"
        song = self.song_entry.text().strip()
        if not song:
            QMessageBox.warning(self, "提示", "请输入歌曲名")
            return
        self.search_btn.setEnabled(False)
        self.status_label.setText("⏳ 搜索中...")
        keyword = f"{singer} {song}"

        def work():
            from music import ALL_PLATFORMS
            cls_map = {k: c for k, _n, c in ALL_PLATFORMS}
            results = {}
            for key, _name in PLATFORMS:
                try:
                    scraper = cls_map[key]()
                    if key == "5sing":
                        merged = []
                        for ss in scraper.search_all_modes(keyword).values():
                            for s in ss:
                                s.mode = s.mode or ""
                            merged.extend(ss)
                        results[key] = merged
                    else:
                        results[key] = scraper.search(keyword, limit=8)
                except Exception:
                    results[key] = []
            return results

        def done(result, err):
            self.search_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"❌ {err}")
                return
            for key, _name in PLATFORMS:
                lst = self.columns[key]["list"]
                lst.clear()
                songs = result.get(key, [])
                self.columns[key]["songs"] = songs
                if not songs:
                    it = QListWidgetItem("（无结果）")
                    it.setFlags(Qt.NoItemFlags)
                    lst.addItem(it)
                    continue
                for s in songs:
                    dur = f"{s.duration // 60}:{s.duration % 60:02d}" if s.duration else "?:??"
                    mode = f"[{s.mode}]" if getattr(s, "mode", "") else ""
                    it = QListWidgetItem(f"{s.song_name}{mode}  {s.singer} {dur}")
                    it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
                    it.setCheckState(Qt.Unchecked)
                    lst.addItem(it)
            self.status_label.setText("✅ 搜索完成，勾选歌曲后点「获取」")

        run_bg(work, done)

    def _fetch_selected(self):
        from music import SearchResult, export_all_formats
        selected = []
        for key, _name in PLATFORMS:
            lst = self.columns[key]["list"]
            songs = self.columns[key]["songs"]
            for i in range(lst.count()):
                if lst.item(i).checkState() == Qt.Checked and i < len(songs):
                    selected.append((key, songs[i]))
        if not selected:
            QMessageBox.warning(self, "提示", "请先勾选要获取的歌曲")
            return
        self.get_btn.setEnabled(False)
        self.status_label.setText(f"⏳ 获取 {len(selected)} 首详情...")
        singer = self.singer_entry.text().strip() or "封茗囧菌"
        song = self.song_entry.text().strip()

        def work():
            from music import ALL_PLATFORMS
            scraper_map = {k: c() for k, _n, c in ALL_PLATFORMS}
            final_songs, platforms, albums, lines = [], [], [], []
            for key, song_item in selected:
                name = dict(PLATFORMS)[key]
                lines.append(f"[{name}] {song_item.song_name}")
                scraper = scraper_map.get(key)
                if not scraper:
                    final_songs.append(song_item)
                    platforms.append(name)
                    continue
                try:
                    full = scraper.fetch_full_detail(song_item)
                    if full:
                        final_songs.append(full)
                        platforms.append(name)
                        album_data = full.extra.get("_album")
                        if album_data:
                            from music import AlbumInfo
                            try:
                                albums.append(AlbumInfo(**album_data))
                            except Exception:
                                pass
                    else:
                        final_songs.append(song_item)
                        platforms.append(name)
                except Exception as e:
                    final_songs.append(song_item)
                    platforms.append(name)
                    lines.append(f"  ⚠️ {e}")
            if not final_songs:
                return "未获取到任何歌曲", None
            result = SearchResult(keyword=f"{singer} {song}".strip(),
                                  platforms_available=platforms,
                                  total_songs=len(final_songs),
                                  total_albums=len(albums),
                                  songs=final_songs, albums=albums)
            from toolbox.config_manager import get_export_subdir
            out_dir = str(get_export_subdir("music_info"))
            outputs = export_all_formats(result, out_dir)
            lines.append("\n📤 导出结果:")
            for label, path in outputs.items():
                lines.append(f"  [OK] {label}: {path}")
            return "\n".join(lines), result

        def done(result, err):
            self.get_btn.setEnabled(True)
            if err:
                self.status_label.setText(f"❌ {err}")
                return
            self.status_label.setText(f"✅ 已获取并导出 {len(selected)} 首")

        run_bg(work, done)
