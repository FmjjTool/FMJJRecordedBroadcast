"""音乐服务 · 音乐标签页：原生标签编辑器（类似 music-tag-web / Mp3tag）。

右侧文件列表 + 左侧标签表单：读取/编辑/保存音频标签（mutagen 多格式），
支持从网络（网易云/QQ/酷狗/酷我/5sing 爬虫）按文件名匹配并填充标签、封面、歌词。
"""

import os
import re
from urllib.parse import urlparse

import httpx
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from music import tags_io
from music.models import SongInfo

# 表格列：(字段, 标题)
COLUMNS = [
    ("file", "文件名"), ("dir", "目录"), ("fmt", "格式"), ("title", "标题"),
    ("artist", "艺术家"), ("album", "专辑"), ("albumartist", "专辑艺术家"),
    ("year", "年份"), ("track", "音轨"), ("disc", "碟"), ("genre", "风格"),
]

# 网络匹配可应用字段（勾选框 key → SongInfo 取值 lambda）
NET_FIELDS = [
    ("title", "标题"), ("artist", "艺术家"), ("album", "专辑"), ("year", "年份"),
    ("track", "音轨号"), ("disc", "碟号"), ("genre", "风格"), ("lyrics", "歌词"),
    ("cover", "封面"),
]


def _song_tags(song: SongInfo) -> dict:
    """SongInfo → 统一标签字段。"""
    album = (song.extra or {}).get("_album") or {}
    if not isinstance(album, dict):
        album = {}
    return {
        "title": song.song_name or song.title,
        "artist": song.singer,
        "album": song.album_name,
        "albumartist": album.get("singer", ""),
        "year": album.get("release_date", "") or song.release_date,
        "track": str(song.track_no or ""),
        "disc": str(song.disc_no or ""),
        "genre": album.get("genre", "") or song.genre,
        "composer": "", "lyricist": "", "comment": "",
        "lyrics": song.lyrics or "",
    }


def _cover_candidates(song: SongInfo) -> list[str]:
    """封面候选 URL：原图优先、缩略图兜底。"""
    album = (song.extra or {}).get("_album") or {}
    urls: list[str] = []
    if isinstance(album, dict) and album.get("cover_url"):
        urls.append(album["cover_url"])
    if song.cover_url:
        urls.append(song.cover_url)

    out: list[str] = []
    for u in urls:
        u = (u or "").strip().replace("http://", "https://")
        if not u:
            continue
        if "music.126.net" in u:
            u = u.split("?")[0]  # 去 ?param=NNyNN 缩放参数 → 原图
        elif "gtimg.cn" in u:
            u = re.sub(r"R\d+x\d+", "R800x800", u)  # QQ 300 缩略图 → 大图
        elif "kugou" in u or "kgimg" in u:
            u = u.replace("{size}", "480")
            u = re.sub(r"_(?:150|200|240|400)\.(jpe?g|png|webp)$", r"_480.\1", u)
            u = re.sub(r"/(?:150|200|240|400)/(\w+\.(?:jpe?g|png|webp))$", r"/480/\1", u)
        elif "kuwo" in u:
            u = re.sub(r"/star/albumcover/(?:\d+/)+", "/star/albumcover/700/", u)  # 尺寸收敛 700
        if u and u not in out:
            out.append(u)
    return out


def _referer_for(url: str) -> str:
    host = urlparse(url).hostname or ""
    if "163" in host or "126.net" in host:
        return "https://music.163.com/"
    if "kugou" in host or "kgimg" in host:
        return "https://www.kugou.com/"
    if "qq.com" in host:
        return "https://y.qq.com/"
    if "kuwo" in host:
        return "https://www.kuwo.cn/"
    if "5sing" in host:
        return "https://www.5sing.com/"
    return ""


def _song_cover(song: SongInfo) -> tuple[bytes, str] | None:
    """按候选 URL 依次拉取封面（按图床带对应 Referer）→ (bytes, mime)。"""
    for url in _cover_candidates(song):
        if "templet" in url or "default" in url:
            continue
        headers = {"User-Agent": "Mozilla/5.0"}
        ref = _referer_for(url)
        if ref:
            headers["Referer"] = ref
        try:
            r = httpx.get(url, timeout=15, follow_redirects=True, headers=headers)
            r.raise_for_status()
            data = r.content
            if len(data) < 500:  # CDN 占位小图
                continue
            mime = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if "png" in mime:
                mime = "image/png"
            elif "webp" in mime:
                mime = "image/webp"
            else:
                mime = "image/jpeg"
            return data, mime
        except Exception:
            continue
    return None


def _guess_kw(path: str, tags: dict) -> str:
    """网络搜索关键词：标题+艺术家，否则用文件名。"""
    if tags.get("title"):
        return f"{tags['title']} {tags.get('artist', '')}".strip()
    stem = os.path.splitext(os.path.basename(path))[0]
    # 去掉常见前缀「艺术家 - 标题」取标题部分，去音质后缀
    stem = re.sub(r"\s*[\[(](FLAC|320K|320kbps|24bit|96kHz|44\.1kHz)[\])]\s*", "", stem, flags=re.I)
    parts = [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]
    return parts[1] if len(parts) >= 2 else stem


class _NetDialog(QDialog):
    """网络匹配对话框：选源 → 搜索 → 勾选字段 → 应用。"""

    def __init__(self, parent, kw: str, checked: dict[str, bool]):
        super().__init__(parent)
        self.setWindowTitle("从网络拉取标签")
        self.resize(720, 480)
        self.song: SongInfo | None = None
        v = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("标签源："))
        from music import ALL_PLATFORMS
        self.src_cb = QComboBox()
        for key, name, _cls in ALL_PLATFORMS:
            self.src_cb.addItem(name, key)
        row.addWidget(self.src_cb)
        self.kw_edit = QLineEdit(kw)
        row.addWidget(self.kw_edit, 1)
        btn = QPushButton("🔍 搜索")
        btn.setStyleSheet("background:#1976d2;color:white;padding:4px 14px;")
        btn.clicked.connect(self._search)
        row.addWidget(btn)
        v.addLayout(row)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["标题", "歌手", "专辑", "时长", "音质", "热度"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.doubleClicked.connect(self._pick)
        v.addWidget(self.table, 1)

        fl = QFormLayout()
        wrap = QHBoxLayout()
        self.checks: dict[str, QCheckBox] = {}
        for key, label in NET_FIELDS:
            cb = QCheckBox(label)
            cb.setChecked(checked.get(key, True))
            self.checks[key] = cb
            wrap.addWidget(cb)
        fl.addRow("应用字段：", wrap)
        v.addLayout(fl)

        row2 = QHBoxLayout()
        self.info = QLabel("双击结果行或点「应用」填充左侧表单（保存后写入文件）")
        self.info.setStyleSheet("color:#888;")
        row2.addWidget(self.info, 1)
        btn_apply = QPushButton("✔ 应用")
        btn_apply.setStyleSheet("background:#2e7d32;color:white;padding:4px 18px;")
        btn_apply.clicked.connect(self._pick)
        row2.addWidget(btn_apply)
        btn_cancel = QPushButton("关闭")
        btn_cancel.clicked.connect(self.reject)
        row2.addWidget(btn_cancel)
        v.addLayout(row2)

    def _search(self):
        kw = self.kw_edit.text().strip()
        if not kw:
            return
        idx = self.src_cb.currentData()
        from music import ALL_PLATFORMS
        cls = dict((k, c) for k, _n, c in ALL_PLATFORMS)[idx]
        self.info.setText("搜索中……")
        self.setEnabled(False)

        def job():
            return cls().search(kw, limit=25)

        def done(songs, err):
            self.setEnabled(True)
            if err:
                self.info.setText(f"搜索失败：{err}")
                return
            self._songs = songs
            self.table.setRowCount(0)
            for i, s in enumerate(songs):
                self.table.insertRow(i)
                self.table.setItem(i, 0, QTableWidgetItem(s.song_name or s.title))
                self.table.setItem(i, 1, QTableWidgetItem(s.singer))
                self.table.setItem(i, 2, QTableWidgetItem(s.album_name))
                q = next((f.quality for f in s.files if f.quality), "")
                self.table.setItem(i, 3, QTableWidgetItem(tags_io.fmt_duration(s.interval or s.duration)))
                self.table.setItem(i, 4, QTableWidgetItem(q))
                self.table.setItem(i, 5, QTableWidgetItem(f"{s.popularity:.0f}" if s.popularity else ""))
            self.info.setText(f"共 {len(songs)} 个结果（双击选择）")

        run_bg(job, done)

    def _pick(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows or not getattr(self, "_songs", None):
            return
        song = self._songs[rows[0].row()]
        self.info.setText("获取详情（歌词/专辑）……")
        self.setEnabled(False)

        def job():
            src = self.src_cb.currentData()
            from music import ALL_PLATFORMS
            cls = dict((k, c) for k, _n, c in ALL_PLATFORMS)[src]
            return cls().fetch_full_detail(song)

        def done(full, err):
            self.setEnabled(True)
            if err:
                self.info.setText(f"详情获取失败：{err}")
                return
            self.song = full
            self.accept()


class MusicTagPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._rows: list[dict] = []       # 每文件内存态
        self._edit_targets: list[int] = []  # 表单当前对应行
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.addWidget(self._build_toolbar())
        v.addWidget(self._build_body(), 1)
        v.addWidget(self._build_statusbar())

    # ==================== UI ====================
    def _build_toolbar(self):
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)

        def btn(text, style, tip, cb):
            b = QPushButton(text)
            b.setStyleSheet(style)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            row.addWidget(b)
            return b

        blue = "background:#1976d2;color:white;padding:5px 14px;"
        green = "background:#2e7d32;color:white;padding:5px 14px;"
        plain = "padding:5px 12px;"
        btn("＋ 添加文件", plain, "选择音频文件加入列表", self._add_files)
        btn("📂 添加文件夹", plain, "扫描文件夹内全部音频", self._add_folder)
        btn("💾 应用并保存（选中）", green, "把左侧表单内容写入所有选中文件", self._save_selected)
        btn("💾 全部保存", green, "保存所有已修改（标记●）文件", self._save_all)
        btn("🌐 网络匹配（当前）", blue, "按文件名搜索网络标签，手动选择候选", self._net_match)
        btn("⚡ 批量匹配（选中）", blue, "对选中文件逐一自动匹配（取第一候选，覆盖全部字段）", self._net_batch)
        btn("✖ 移除选中", plain, "从列表移除（不删文件）", self._remove_selected)
        btn("🧹 清空列表", plain, "", self._clear_list)
        row.addStretch(1)
        return w

    def _build_body(self):
        sp = QSplitter(Qt.Horizontal)

        # ---- 左：标签表单 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        self.form_title = QLabel("（未选择文件）")
        self.form_title.setStyleSheet("font-weight:bold;color:#333;")
        lv.addWidget(self.form_title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.edits: dict[str, QLineEdit] = {}
        for key, label in [("title", "标题："), ("artist", "艺术家："), ("album", "专辑："),
                           ("year", "年份："), ("genre", "风格："), ("albumartist", "专辑艺术家："),
                           ("composer", "作曲家："), ("lyricist", "作词家："), ("comment", "注释：")]:
            e = QLineEdit()
            e.textEdited.connect(lambda _t, k=key: self._on_edited(k))
            self.edits[key] = e
            form.addRow(label, e)
        row_td = QHBoxLayout()
        for key, label in [("track", "音轨号："), ("disc", "碟号：")]:
            e = QLineEdit()
            e.setMaximumWidth(80)
            e.textEdited.connect(lambda _t, k=key: self._on_edited(k))
            self.edits[key] = e
            row_td.addWidget(QLabel(label))
            row_td.addWidget(e)
        row_td.addStretch(1)
        form.addRow("编号：", row_td)
        self.lyr_edit = QPlainTextEdit()
        self.lyr_edit.setMaximumHeight(140)
        self.lyr_edit.textChanged.connect(lambda: self._on_edited("lyrics"))
        form.addRow("歌词：", self.lyr_edit)
        lv.addLayout(form)

        # 封面区
        cov_row = QHBoxLayout()
        self.cov_label = QLabel()
        self.cov_label.setFixedSize(140, 140)
        self.cov_label.setAlignment(Qt.AlignCenter)
        self.cov_label.setStyleSheet("border:1px solid #ccc;background:#f5f5f5;")
        self.cov_label.setText("无封面")
        cov_row.addWidget(self.cov_label)
        cv = QVBoxLayout()
        self.cov_info = QLabel("")
        self.cov_info.setStyleSheet("color:#888;")
        cv.addWidget(self.cov_info)
        for text, cb in [("🖼 更换封面", self._cover_set), ("💾 导出封面", self._cover_export), ("✖ 移除封面", self._cover_del)]:
            b = QPushButton(text)
            b.clicked.connect(cb)
            cv.addWidget(b)
        cv.addStretch(1)
        cov_row.addLayout(cv, 1)
        lv.addLayout(cov_row)
        lv.addStretch(1)
        sp.addWidget(left)

        # ---- 右：文件表格 ----
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self._on_select)
        sp.addWidget(self.table)
        sp.setStretchFactor(1, 3)
        sp.setSizes([340, 760])
        return sp

    def _build_statusbar(self):
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("添加音频文件开始编辑（支持 FLAC/MP3/M4A/OGG/WAV/APE/WMA 等 16 种格式）")
        self.status.setStyleSheet("color:#666;")
        row.addWidget(self.status, 1)
        row.addWidget(QLabel("过滤："))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("按文件名/标题/艺术家过滤")
        self.filter_edit.setFixedWidth(220)
        self.filter_edit.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter_edit)
        return w

    # ==================== 列表管理 ====================
    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音频文件", "", f"音频文件 ({' '.join('*' + e for e in sorted(tags_io.SUPPORTED_EXT))})")
        self._load_files(files)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not d:
            return
        files = [os.path.join(d, f) for f in os.listdir(d)
                 if os.path.splitext(f)[1].lower() in tags_io.SUPPORTED_EXT]
        if not files:
            self.app.toast("该文件夹内没有音频文件", "warning")
            return
        self._load_files(files)

    # ---- 拖放接收（主窗口分发；只入本页列表，不改全局工作目录） ----
    def add_dropped_files(self, files: list[str]):
        audio = [f for f in files if os.path.splitext(f)[1].lower() in tags_io.SUPPORTED_EXT]
        if not audio:
            self.app.toast("拖入的文件中没有受支持的音频格式", "warning")
            return
        self._load_files(audio)

    def add_dropped_folder(self, path: str):
        if not os.path.isdir(path):
            return
        files = [os.path.join(path, f) for f in os.listdir(path)
                 if os.path.splitext(f)[1].lower() in tags_io.SUPPORTED_EXT]
        if not files:
            self.app.toast("该文件夹内没有音频文件", "warning")
            return
        self._load_files(files)

    def _load_files(self, files: list[str]):
        files = [f for f in files if f not in {r["path"] for r in self._rows}]

        def job():
            out = []
            for p in files:
                info = tags_io.probe(p)
                out.append({"path": p, "info": info, "tags": tags_io.load_tags(p),
                            "cover": tags_io.load_cover(p), "cover_state": "loaded", "dirty": False})
            return out

        def done(rows, err):
            if err:
                self.app.toast(f"读取失败：{err}", "error")
                return
            existing = {r["path"] for r in self._rows}  # 后台加载期间的重复拖入在此去重
            for r in rows:
                if r["path"] in existing:
                    continue
                existing.add(r["path"])
                i = len(self._rows)
                self._rows.append(r)
                self._insert_table_row(i)
            self._update_status()

        run_bg(job, done)

    def _insert_table_row(self, i: int):
        r = self._rows[i]
        self.table.insertRow(i)
        for c, (key, _label) in enumerate(COLUMNS):
            if key == "file":
                v = os.path.basename(r["path"])
            elif key == "dir":
                v = os.path.dirname(r["path"])
            elif key == "fmt":
                v = r["info"]["fmt"]
            else:
                v = r["tags"].get(key, "")
            it = QTableWidgetItem(v)
            if c in (0, 1, 2):
                it.setToolTip(r["path"])
            self.table.setItem(i, c, it)
        self._mark_row(i)

    def _mark_row(self, i: int):
        """dirty 行文件名前加 ●。"""
        it = self.table.item(i, 0)
        base = os.path.basename(self._rows[i]["path"])
        it.setText(("● " if self._rows[i]["dirty"] else "") + base)

    def _on_select(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        self._edit_targets = rows
        if rows:
            self._fill_form(self._rows[rows[0]])
        else:
            self._fill_form(None)
        self._update_status()

    def _fill_form(self, r: dict | None):
        if r is None:
            self.form_title.setText("（未选择文件）")
            for e in self.edits.values():
                e.clear()
            self.lyr_edit.clear()
            self._show_cover(None)
            return
        self.form_title.setText(f"{os.path.basename(r['path'])}（{r['info']['fmt']}）")
        for k, e in self.edits.items():
            e.setText(r["tags"].get(k, ""))
        self.lyr_edit.blockSignals(True)
        self.lyr_edit.setPlainText(r["tags"].get("lyrics", ""))
        self.lyr_edit.blockSignals(False)
        self._show_cover(r["cover"])

    def _show_cover(self, cover: tuple[bytes, str] | None):
        if not cover:
            self.cov_label.setPixmap(QPixmap())
            self.cov_label.setText("无封面")
            self.cov_info.setText("")
            return
        data, mime = cover
        pm = QPixmap()
        pm.loadFromData(data)
        self.cov_label.setText("")
        self.cov_label.setPixmap(pm.scaled(138, 138, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        from PIL import Image
        import io as _io
        try:
            img = Image.open(_io.BytesIO(data))
            w, h = img.size
        except Exception:
            w = h = 0
        ext = "PNG" if "png" in mime else "JPEG"
        self.cov_info.setText(f"{ext}\n{w}×{h}\n{len(data) / 1024:.1f} kB")

    # ==================== 编辑 / 保存 ====================
    def _on_edited(self, _key: str):
        """表单变化 → 同步到全部编辑目标行的内存态（多选可同时改多文件）。"""
        if not self._edit_targets:
            return
        vals = {k: e.text().strip() for k, e in self.edits.items()}
        vals["lyrics"] = self.lyr_edit.toPlainText()
        for i in self._edit_targets:
            self._rows[i]["tags"].update(vals)
            self._rows[i]["dirty"] = True
            self._mark_row(i)
        self._update_status()

    def _save_rows(self, idxs: list[int]):
        rows = [self._rows[i] for i in idxs if self._rows[i]["dirty"]]

        def job():
            done_n, failed = 0, []
            for r in rows:
                try:
                    tags_io.save_tags(r["path"], r["tags"])
                    st = r["cover_state"]
                    if st == "new" and r["cover"]:
                        tags_io.save_cover(r["path"], r["cover"][0], r["cover"][1])
                    elif st == "removed":
                        tags_io.clear_cover(r["path"])
                    r["dirty"] = False
                    r["cover_state"] = "loaded"
                    done_n += 1
                except Exception as e:  # noqa: BLE001
                    failed.append(f"{os.path.basename(r['path'])}: {e}")
            return done_n, failed, idxs

        def done(res, err):
            if err:
                self.app.toast(f"保存失败：{err}", "error")
                return
            n, failed, saved_idx = res
            for i in saved_idx:
                if not self._rows[i]["dirty"]:
                    self._mark_row(i)
            if failed:
                self.status.setText(f"保存完成 {n} 个，失败 {len(failed)} 个：" + "；".join(failed[:3]))
                self.app.toast(f"{n} 成功 / {len(failed)} 失败", "warning")
            else:
                self.status.setText(f"已保存 {n} 个文件 ✓")
                self.app.toast(f"已保存 {n} 个文件", "success")
            self._update_status()

        if not rows:
            self.app.toast("没有已修改的文件", "info")
            return
        run_bg(job, done)

    def _save_selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要保存的文件行", "warning")
            return
        self._save_rows(rows)

    def _save_all(self):
        self._save_rows(list(range(len(self._rows))))

    def _remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()}, reverse=True)
        for i in rows:
            if self._rows[i]["dirty"]:
                self.app.toast(f"{os.path.basename(self._rows[i]['path'])} 有未保存修改，请先保存或放弃", "warning")
                return
        for i in rows:
            self.table.removeRow(i)
            self._rows.pop(i)
        self._fill_form(None)
        self._edit_targets = []
        self._update_status()

    def _clear_list(self):
        dirty = [r for r in self._rows if r["dirty"]]
        if dirty:
            self.app.toast(f"有 {len(dirty)} 个文件未保存，请先全部保存", "warning")
            return
        self.table.setRowCount(0)
        self._rows = []
        self._fill_form(None)
        self._edit_targets = []
        self._update_status()

    # ==================== 封面 ====================
    def _current_row(self) -> int | None:
        return self._edit_targets[0] if self._edit_targets else None

    def _cover_set(self):
        i = self._current_row()
        if i is None:
            return
        f, _ = QFileDialog.getOpenFileName(self, "选择封面图片", "", "图片 (*.jpg *.jpeg *.png *.webp)")
        if not f:
            return
        data = open(f, "rb").read()
        mime = "image/png" if f.lower().endswith(".png") else "image/jpeg"
        for j in self._edit_targets:
            self._rows[j]["cover"], self._rows[j]["cover_state"] = (data, mime), "new"
            self._rows[j]["dirty"] = True
            self._mark_row(j)
        self._show_cover((data, mime))

    def _cover_export(self):
        i = self._current_row()
        if i is None or not self._rows[i]["cover"]:
            return
        base = os.path.splitext(os.path.basename(self._rows[i]["path"]))[0]
        ext = ".png" if "png" in self._rows[i]["cover"][1] else ".jpg"
        f, _ = QFileDialog.getSaveFileName(self, "导出封面", base + ext)
        if f:
            open(f, "wb").write(self._rows[i]["cover"][0])

    def _cover_del(self):
        if not self._edit_targets:
            return
        for j in self._edit_targets:
            self._rows[j]["cover"], self._rows[j]["cover_state"] = None, "removed"
            self._rows[j]["dirty"] = True
            self._mark_row(j)
        self._show_cover(None)

    # ==================== 网络匹配 ====================
    def _net_match(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        r = self._rows[i]
        dlg = _NetDialog(self, _guess_kw(r["path"], r["tags"]), {})
        if dlg.exec() and dlg.song:
            nt = _song_tags(dlg.song)

            def job():
                return _song_cover(dlg.song) if dlg.checks["cover"].isChecked() else None

            def apply(cover, _err):
                for key, _label in NET_FIELDS:
                    if not dlg.checks[key].isChecked():
                        continue
                    if key == "cover":
                        if cover:
                            r["cover"], r["cover_state"] = cover, "new"
                    elif nt.get(key):
                        r["tags"][key] = nt[key]
                r["dirty"] = True
                self._mark_row(i)
                self._fill_form(r)
                self._update_status()
                self.app.toast(f"已填充「{nt.get('title', '')}」的标签（记得保存）", "success")

            if dlg.checks["cover"].isChecked():
                run_bg(job, apply)
            else:
                apply(None, None)

    def _net_batch(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要批量匹配的文件行", "warning")
            return
        if QMessageBox.question(self, "批量匹配",
                                f"将对 {len(rows)} 个文件按文件名自动匹配（取第一候选，覆盖标题/艺术家/专辑/年份/音轨/风格/歌词/封面），继续？"
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.status.setText(f"批量匹配中（0/{len(rows)}）……")

        def job():
            from music import ALL_PLATFORMS
            cls = dict((k, c) for k, _n, c in ALL_PLATFORMS)["netease"]
            ok_n, fail = 0, []
            for n, i in enumerate(rows, 1):
                r = self._rows[i]
                kw = _guess_kw(r["path"], r["tags"])
                try:
                    songs = cls().search(kw, limit=5)
                    if not songs:
                        raise RuntimeError("无候选")
                    song = cls().fetch_full_detail(songs[0])
                    nt = _song_tags(song)
                    for key, _label in NET_FIELDS:
                        if key == "cover":
                            c = _song_cover(song)
                            if c:
                                r["cover"], r["cover_state"] = c, "new"
                        elif nt.get(key):
                            r["tags"][key] = nt[key]
                    r["dirty"] = True
                    ok_n += 1
                except Exception as e:  # noqa: BLE001
                    fail.append(f"{os.path.basename(r['path'])}: {e}")
                post_status = f"批量匹配中（{n}/{len(rows)}）……"
                from gui_qt.common import post_to_main
                post_to_main(lambda s=post_status: self.status.setText(s))
            return ok_n, fail, rows

        def done(res, err):
            if err:
                self.status.setText(f"批量匹配失败：{err}")
                return
            ok_n, fail, idxs = res
            for i in idxs:
                self._mark_row(i)
            if self._edit_targets:
                self._fill_form(self._rows[self._edit_targets[0]])
            self._update_status()
            self.app.toast(f"匹配成功 {ok_n}/{len(idxs)}" + (f"，失败：{'；'.join(fail[:3])}" if fail else "（记得保存）"),
                           "success" if not fail else "warning")

        run_bg(job, done)

    # ==================== 状态栏 ====================
    def _update_status(self):
        n = len(self._rows)
        dirty = sum(1 for r in self._rows if r["dirty"])
        sel = self.table.selectionModel().selectedRows()
        if sel:
            rows = [self._rows[i.row()] for i in sel]
            dur = tags_io.fmt_duration(sum(r["info"]["duration"] for r in rows))
            size = sum(os.path.getsize(r["path"]) for r in rows if os.path.isfile(r["path"]))
            tail = f"｜选中 {len(rows)}：{dur} · {size / 1048576:.1f} MB"
        else:
            tail = ""
        self.status.setText(f"共 {n} 个文件，{dirty} 个已修改（●）{tail}")

    def _apply_filter(self, text):
        text = text.strip().lower()
        for i, r in enumerate(self._rows):
            hay = f"{os.path.basename(r['path'])} {r['tags'].get('title', '')} {r['tags'].get('artist', '')}".lower()
            self.table.setRowHidden(i, bool(text) and text not in hay)
