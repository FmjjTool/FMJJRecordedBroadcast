"""音乐标签页的二级对话框集合。

包含：自动匹配、组合搜索、封面搜索、歌词搜索、批量改名、查找替换、
编码修复、图片管理、标签历史、标签设置。
"""

from __future__ import annotations

import os
import re
import threading
import time
from io import BytesIO
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from music import tag_history, tag_search, tags_io

PICTURE_TYPE_NAMES = tags_io.PICTURE_TYPE_NAMES


# ==================== 通用小工具 ====================

def _btn(text, style="", tip="", cb=None):
    b = QPushButton(text)
    if style:
        b.setStyleSheet(style)
    if tip:
        b.setToolTip(tip)
    if cb:
        b.clicked.connect(cb)
    return b


def _blue(text, cb=None):
    return _btn(text, "background:#1976d2;color:white;padding:4px 14px;", "", cb)


def _green(text, cb=None):
    return _btn(text, "background:#2e7d32;color:white;padding:4px 14px;", "", cb)


def _plain(text, cb=None):
    return _btn(text, "padding:4px 12px;", "", cb)


def _table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setSelectionMode(QAbstractItemView.SingleSelection)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setStretchLastSection(True)
    return t


def _fill_row(table: QTableWidget, row: int, values: list[str], tip: str = ""):
    for c, value in enumerate(values):
        item = QTableWidgetItem(str(value))
        if tip and c == 0:
            item.setToolTip(tip)
        table.setItem(row, c, item)


def _fmt_size(size: int) -> str:
    if size >= 1048576:
        return f"{size / 1048576:.1f} MB"
    return f"{max(0, size) / 1024:.0f} kB"


def _sibling_image(stem: str, folder: str) -> str:
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
        p = os.path.join(folder, stem + ext)
        if os.path.isfile(p):
            return p
    return ""


def _sibling_lyric(stem: str, folder: str) -> str:
    p = os.path.join(folder, stem + ".lrc")
    return p if os.path.isfile(p) else ""


def _write_sidecar(path: str, data: bytes):
    folder, name = os.path.split(path)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    _ = folder, name


# ==================== 自动匹配 ====================

TEXT_MATCH_FIELDS = [
    ("title", "标题"), ("artist", "艺术家"), ("album", "专辑"), ("year", "年份"),
    ("track", "音轨"), ("disc", "碟号"), ("genre", "风格"), ("comment", "注释"),
]
NUMBER_FIELDS = {"track", "disc"}


class AutoMatchDialog(QDialog):
    """批量自动匹配：多源搜索 → 按字段写入标签 / 同名文件。"""

    def __init__(self, parent, rows: list[dict], settings: dict):
        super().__init__(parent)
        self.setWindowTitle("⚡ 自动匹配标签")
        self.resize(980, 720)
        self.rows = rows
        self.settings = settings
        self.cancel_event = threading.Event()
        self._closed = False
        self._running = False
        self.updated_rows: list[tuple[int, dict]] = []

        v = QVBoxLayout(self)
        info = QLabel(
            f"将对 {len(rows)} 个文件执行多源自动匹配（网易云 / QQ / 酷狗 / 酷我），"
            "取相似度最高的候选；未勾选「覆盖」时只填空字段。")
        info.setWordWrap(True)
        info.setStyleSheet("color:#666;")
        v.addWidget(info)

        cond = QGroupBox("搜索条件")
        cg = QHBoxLayout(cond)
        cg.addWidget(QLabel("标题来源："))
        self.title_mode = QComboBox()
        self.title_mode.addItems(["标题（为空用文件名）", "仅文件名"])
        self.title_mode.setCurrentIndex(1 if settings.get("use_filename_only") else 0)
        cg.addWidget(self.title_mode)
        self.use_artist = QCheckBox("用艺术家")
        self.use_artist.setChecked(settings.get("use_artist", True))
        cg.addWidget(self.use_artist)
        self.use_album = QCheckBox("用专辑")
        self.use_album.setChecked(settings.get("use_album", True))
        cg.addWidget(self.use_album)
        cg.addStretch(1)
        cg.addWidget(QLabel("并发："))
        self.threads = QSpinBox()
        self.threads.setRange(1, 8)
        self.threads.setValue(int(settings.get("threads", 3) or 3))
        cg.addWidget(self.threads)
        v.addWidget(cond)

        src = QGroupBox("标签源")
        sg = QHBoxLayout(src)
        self.source_checks: dict[str, QCheckBox] = {}
        enabled = settings.get("sources") or tag_search.DEFAULT_SOURCES
        for key in tag_search.DEFAULT_SOURCES:
            cb = QCheckBox(tag_search.SOURCE_NAMES[key])
            cb.setChecked(key in enabled)
            self.source_checks[key] = cb
            sg.addWidget(cb)
        sg.addStretch(1)
        v.addWidget(src)

        table = QTableWidget(0, 3)
        table.setHorizontalHeaderLabels(["字段", "写入方式", "覆盖已有值"])
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.field_modes: dict[str, QComboBox] = {}
        self.field_overwrite: dict[str, QCheckBox] = {}
        modes = {
            "cover": ["写入标签", "保存同名图片", "两者都写", "不处理"],
            "lyrics": ["写入标签", "保存同名 .lrc", "两者都写", "不处理"],
        }
        fields = TEXT_MATCH_FIELDS + [("cover", "封面"), ("lyrics", "歌词")]
        for i, (key, label) in enumerate(fields):
            table.insertRow(i)
            table.setItem(i, 0, QTableWidgetItem(label))
            combo = QComboBox()
            combo.addItems(modes.get(key, ["写入标签", "不写入"]))
            combo.setCurrentIndex(0)
            table.setCellWidget(i, 1, combo)
            cb = QCheckBox()
            cb.setChecked(settings.get("overwrite", {}).get(key, False))
            table.setCellWidget(i, 2, cb)
            self.field_modes[key] = combo
            self.field_overwrite[key] = cb
        table.setFixedHeight(360)
        v.addWidget(table)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(150)
        v.addWidget(self.log, 1)

        bar = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        bar.addWidget(self.info, 1)
        self.start_btn = _green("▶ 开始匹配", self._start)
        self.cancel_btn = _plain("■ 停止", self._stop)
        self.cancel_btn.setEnabled(False)
        close = _plain("关闭", self.reject)
        bar.addWidget(self.start_btn)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(close)
        v.addLayout(bar)

    # ---------- 配置 ----------
    def current_settings(self) -> dict:
        return {
            "use_filename_only": self.title_mode.currentIndex() == 1,
            "use_artist": self.use_artist.isChecked(),
            "use_album": self.use_album.isChecked(),
            "threads": self.threads.value(),
            "sources": [k for k, cb in self.source_checks.items() if cb.isChecked()],
            "overwrite": {k: cb.isChecked() for k, cb in self.field_overwrite.items()},
        }

    def _mode(self, key: str) -> int:
        return self.field_modes[key].currentIndex()

    def _overwrite(self, key: str) -> bool:
        return self.field_overwrite[key].isChecked()

    def _log(self, text: str):
        if self._closed:
            return
        self.log.appendPlainText(text)

    # ---------- 执行 ----------
    def _start(self):
        if self._running:
            return
        if not any(cb.isChecked() for cb in self.source_checks.values()):
            QMessageBox.warning(self, "自动匹配", "请至少勾选一个标签源")
            return
        self._running = True
        self.cancel_event.clear()
        self.updated_rows = []
        self.progress.setVisible(True)
        self.progress.setRange(0, len(self.rows))
        self.progress.setValue(0)
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.log.clear()
        settings = self.current_settings()
        # 后台线程不能读 Qt 控件：先把写入方式/覆盖勾选快照下来
        settings["modes"] = {k: self.field_modes[k].currentIndex() for k in self.field_modes}
        rows = self.rows

        def job():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            results = []
            lock = threading.Lock()
            done_n = 0

            def work(index: int, row: dict):
                if self.cancel_event.is_set():
                    return index, None
                title, artist, album, used_filename = self._build_query(row, settings)
                if not title:
                    return index, ("skip", "文件名为空")
                try:
                    match = tag_search.auto_match(
                        title, artist, album,
                        sources=settings["sources"],
                        used_filename=used_filename,
                        need_lyric=settings["modes"].get("lyrics", 3) != 3,
                        need_cover=settings["modes"].get("cover", 3) != 3,
                        connector=tags_io.DEFAULT_CONNECTOR,
                    )
                except Exception as exc:  # noqa: BLE001
                    return index, ("error", f"{type(exc).__name__}: {exc}")
                if not match.candidate:
                    detail = "；".join(f"{tag_search.SOURCE_NAMES.get(k, k)}:{v}"
                                      for k, v in (match.errors or {}).items())
                    return index, ("none", detail or "无候选")
                try:
                    update = self._build_update(row, match, settings)
                    return index, ("ok", update)
                except Exception as exc:  # noqa: BLE001
                    return index, ("error", f"{type(exc).__name__}: {exc}")

            with ThreadPoolExecutor(max_workers=max(1, settings["threads"])) as pool:
                futures = {pool.submit(work, i, row): i for i, row in enumerate(rows)}
                for fut in as_completed(futures):
                    if self.cancel_event.is_set():
                        for f in futures:
                            f.cancel()
                        break
                    index, res = fut.result()
                    done_n += 1
                    with lock:
                        results.append((index, res))
                    post_to_main(lambda n=done_n: self._tick(n))
            return results

        def done(results, err):
            if self._closed:
                return
            self._running = False
            self.start_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self.progress.setVisible(False)
            if err:
                self._log(f"自动匹配失败：{err}")
                self.info.setText("失败")
                return
            ok = fail = none = 0
            for index, res in sorted((r for r in results if r), key=lambda x: x[0]):
                if not res:
                    continue
                status, payload = res
                if status == "ok":
                    ok += 1
                    self.updated_rows.append((index, payload))
                    self._log(f"✓ {os.path.basename(rows[index]['path'])}："
                              f"{payload.get('_matched', '')}")
                elif status == "none":
                    none += 1
                    self._log(f"— {os.path.basename(rows[index]['path'])}：未匹配（{payload}）")
                else:
                    fail += 1
                    self._log(f"✗ {os.path.basename(rows[index]['path'])}：{payload}")
            self.info.setText(f"完成：成功 {ok}，未匹配 {none}，失败 {fail}")
            self.settings.update(self.current_settings())
            self.accepted = True

        self._log(f"开始匹配 {len(rows)} 个文件……")
        run_bg(job, done)

    def _tick(self, n: int):
        if self._closed:
            return
        self.progress.setValue(n)
        self.info.setText(f"已处理 {n}/{len(self.rows)}")

    def _stop(self):
        self.cancel_event.set()
        self.cancel_btn.setEnabled(False)
        self._log("已请求停止，等待正在进行的请求结束……")

    def _build_query(self, row: dict, settings: dict):
        tags = row.get("tags") or {}
        stem = os.path.splitext(os.path.basename(row["path"]))[0]
        if settings.get("use_filename_only") or not (tags.get("title") or "").strip():
            title = stem
            used_filename = True
        else:
            title = (tags.get("title") or "").strip()
            used_filename = False
        artist = (tags.get("artist") or "").strip() if settings.get("use_artist") else ""
        album = (tags.get("album") or "").strip() if settings.get("use_album") else ""
        return title, artist, album, used_filename

    def _build_update(self, row: dict, match, settings: dict) -> dict:
        cand = match.candidate
        tags = dict(row.get("tags") or {})
        overwrite = settings.get("overwrite") or {}
        modes = settings.get("modes") or {}
        for key, _label in TEXT_MATCH_FIELDS:
            if modes.get(key, 1) != 0:
                continue
            value = getattr(cand, key, "")
            if key in NUMBER_FIELDS:
                value = str(value) if value else ""
            value = str(value or "").strip()
            if not value:
                continue
            if overwrite.get(key) or not str(tags.get(key) or "").strip():
                tags[key] = value
                if key == "track" and cand.track:
                    tags["track"] = str(cand.track)
                if key == "disc" and cand.disc:
                    tags["disc"] = str(cand.disc)

        pictures = list(row.get("pictures") or [])
        pictures_dirty = False
        cover_mode = modes.get("cover", 3)
        if cover_mode in (0, 2) and match.cover:
            if overwrite.get("cover") or not any(p.type == 3 for p in pictures):
                pictures = [p for p in pictures if p.type != 3]
                pictures.insert(0, tags_io.Pic(match.cover, match.cover_mime or "image/jpeg", 3, ""))
                pictures_dirty = True

        lyrics_mode = modes.get("lyrics", 3)
        if lyrics_mode in (0, 2) and cand.lyric:
            if overwrite.get("lyrics") or not str(tags.get("lyrics") or "").strip():
                tags["lyrics"] = cand.lyric

        # 同名文件
        folder = os.path.dirname(row["path"])
        stem = os.path.splitext(os.path.basename(row["path"]))[0]
        if cover_mode in (1, 2) and match.cover:
            dst = os.path.join(folder, stem + (".png" if "png" in (match.cover_mime or "") else ".jpg"))
            if overwrite.get("cover") or not os.path.isfile(dst):
                _write_sidecar(dst, match.cover)
        if lyrics_mode in (1, 2) and cand.lyric:
            dst = os.path.join(folder, stem + ".lrc")
            if overwrite.get("lyrics") or not os.path.isfile(dst):
                _write_sidecar(dst, cand.lyric.encode("utf-8"))

        return {
            "tags": tags,
            "pictures": pictures,
            "pictures_dirty": pictures_dirty,
            "_matched": f"{cand.source_name} · {cand.title} · {cand.artist}",
        }

    def closeEvent(self, ev):
        self._closed = True
        self.cancel_event.set()
        super().closeEvent(ev)


# ==================== 组合搜索（单文件网络匹配） ====================

class CombinedSearchDialog(QDialog):
    """按标题/艺术家/专辑搜索四平台，选择候选后套用字段/歌词/封面。"""

    def __init__(self, parent, row: dict):
        super().__init__(parent)
        self.setWindowTitle("🌐 从网络匹配标签")
        self.resize(1040, 700)
        self.row = row
        self.candidates: list[tag_search.TagCandidate] = []
        self._closed = False
        tags = row.get("tags") or {}
        self._cover = b""
        self._cover_mime = ""

        v = QVBoxLayout(self)
        cond = QHBoxLayout()
        cond.addWidget(QLabel("标题："))
        self.title_edit = QLineEdit(tags.get("title") or
                                    os.path.splitext(os.path.basename(row["path"]))[0])
        cond.addWidget(self.title_edit, 2)
        cond.addWidget(QLabel("艺术家："))
        self.artist_edit = QLineEdit(tags.get("artist") or "")
        cond.addWidget(self.artist_edit, 1)
        cond.addWidget(QLabel("专辑："))
        self.album_edit = QLineEdit(tags.get("album") or "")
        cond.addWidget(self.album_edit, 1)
        self.search_btn = _blue("🔍 搜索", self._search)
        cond.addWidget(self.search_btn)
        v.addLayout(cond)

        src = QHBoxLayout()
        src.addWidget(QLabel("标签源："))
        self.source_checks: dict[str, QCheckBox] = {}
        for key in tag_search.DEFAULT_SOURCES:
            cb = QCheckBox(tag_search.SOURCE_NAMES[key])
            cb.setChecked(True)
            self.source_checks[key] = cb
            src.addWidget(cb)
        src.addStretch(1)
        v.addLayout(src)

        self.table = _table(["来源", "标题", "艺术家", "专辑", "年份", "音轨", "碟号", "流派"])
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._apply)
        self.table.itemSelectionChanged.connect(self._on_select)
        v.addWidget(self.table, 2)

        bottom = QHBoxLayout()
        self.preview = QLabel("选择候选后点「预览歌词」/「预览封面」")
        self.preview.setStyleSheet("color:#888;")
        bottom.addWidget(self.preview, 1)
        bottom.addWidget(_plain("🎵 预览歌词", self._preview_lyric))
        bottom.addWidget(_plain("🖼 预览封面", self._preview_cover))
        v.addLayout(bottom)

        fields = QGroupBox("应用字段")
        fg = QHBoxLayout(fields)
        self.field_checks: dict[str, QCheckBox] = {}
        for key, label in [("title", "标题"), ("artist", "艺术家"), ("album", "专辑"),
                           ("year", "年份"), ("track", "音轨"), ("disc", "碟号"),
                           ("genre", "风格"), ("comment", "注释"), ("lyrics", "歌词"),
                           ("cover", "封面")]:
            cb = QCheckBox(label)
            cb.setChecked(key not in ("comment",))
            self.field_checks[key] = cb
            fg.addWidget(cb)
        fg.addStretch(1)
        v.addWidget(fields)

        bar = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        bar.addWidget(self.info, 1)
        bar.addWidget(_green("✔ 应用", self._apply))
        bar.addWidget(_plain("关闭", self.reject))
        v.addLayout(bar)

    def _sources(self) -> list[str]:
        return [k for k, cb in self.source_checks.items() if cb.isChecked()]

    def _search(self):
        title = self.title_edit.text().strip()
        if not title:
            QMessageBox.warning(self, "搜索", "请填写标题")
            return
        self.search_btn.setEnabled(False)
        self.info.setText("搜索中……")

        def job():
            return tag_search.search_tracks(
                title, self.artist_edit.text().strip(), self.album_edit.text().strip(),
                self._sources(),
                used_filename=not bool((self.row.get("tags") or {}).get("title")))

        def done(result, err):
            self.search_btn.setEnabled(True)
            if err:
                self.info.setText(f"搜索失败：{err}")
                return
            self.candidates = result or []
            self.table.setRowCount(0)
            for i, cand in enumerate(self.candidates):
                self.table.insertRow(i)
                _fill_row(self.table, i, [
                    cand.source_name, cand.title, cand.artist, cand.album,
                    cand.year, cand.track_label, str(cand.disc or ""), cand.genre])
            self.info.setText(f"共 {len(self.candidates)} 个候选（双击套用）")

        run_bg(job, done)

    def _selected(self) -> tag_search.TagCandidate | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not self.candidates:
            return None
        i = rows[0].row()
        return self.candidates[i] if 0 <= i < len(self.candidates) else None

    def _on_select(self):
        cand = self._selected()
        if not cand:
            return
        self.preview.setText(f"{cand.source_name} · {cand.title} · {cand.artist} · {cand.album}")

    def _preview_lyric(self):
        cand = self._selected()
        if not cand:
            return
        self.preview.setText("加载歌词……")

        def done(res, err):
            if err:
                self.preview.setText(f"歌词加载失败：{err}")
                return
            lyric, trans = res
            if not lyric:
                self.preview.setText("该候选没有歌词")
                return
            text = lyric + (("\n\n[翻译]\n" + trans) if trans else "")
            dlg = QDialog(self)
            dlg.setWindowTitle(f"歌词预览 — {cand.title}")
            dlg.resize(720, 560)
            lv = QVBoxLayout(dlg)
            edit = QPlainTextEdit(text)
            edit.setReadOnly(True)
            lv.addWidget(edit, 1)
            lv.addWidget(_plain("关闭", dlg.accept))
            dlg.exec()

        run_bg(lambda: tag_search.load_lyric(cand), done)

    def _preview_cover(self):
        cand = self._selected()
        if not cand or not cand.cover_url:
            self.preview.setText("该候选没有封面")
            return
        self.preview.setText("下载封面……")

        def done(res, err):
            data, mime = res if res else (b"", "")
            if err or not data:
                self.preview.setText(f"封面下载失败：{err or '空数据'}")
                return
            self._cover, self._cover_mime = data, mime
            dlg = QDialog(self)
            dlg.setWindowTitle(f"封面预览 — {cand.title}")
            lv = QVBoxLayout(dlg)
            lbl = QLabel()
            pm = QPixmap()
            pm.loadFromData(data)
            lbl.setPixmap(pm.scaled(480, 480, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            lbl.setAlignment(Qt.AlignCenter)
            lv.addWidget(lbl)
            lv.addWidget(QLabel(f"{mime} · {_fmt_size(len(data))}"), alignment=Qt.AlignCenter)
            lv.addWidget(_plain("关闭", dlg.accept))
            dlg.exec()

        run_bg(lambda: tag_search.download_cover(cand.cover_url), done)

    def _apply(self):
        cand = self._selected()
        if not cand:
            self.info.setText("请先选择一个候选")
            return
        want_lyric = self.field_checks["lyrics"].isChecked()
        want_cover = self.field_checks["cover"].isChecked()
        self.setEnabled(False)
        self.info.setText("获取歌词/封面……")

        def job():
            lyric = ""
            if want_lyric:
                lyric, _trans = tag_search.load_lyric(cand)
            cover = b""
            mime = ""
            if want_cover and cand.cover_url:
                cover, mime = tag_search.download_cover(cand.cover_url)
            return lyric, cover, mime

        def done(res, err):
            self.setEnabled(True)
            if err:
                self.info.setText(f"获取失败：{err}")
                return
            lyric, cover, mime = res
            tags_update = {}
            for key, cb in self.field_checks.items():
                if not cb.isChecked():
                    continue
                if key == "lyrics":
                    if lyric:
                        tags_update["lyrics"] = lyric
                elif key == "cover":
                    if cover:
                        self._cover, self._cover_mime = cover, mime
                else:
                    value = getattr(cand, key, "")
                    if key in NUMBER_FIELDS:
                        value = str(value) if value else ""
                    value = str(value or "").strip()
                    if value:
                        tags_update[key] = value
            self.result_data = {
                "tags": tags_update,
                "cover": self._cover if self.field_checks["cover"].isChecked() else b"",
                "cover_mime": self._cover_mime,
                "title": cand.title,
            }
            self.accept()

        run_bg(job, done)

    def closeEvent(self, ev):
        self._closed = True
        super().closeEvent(ev)


# ==================== 封面搜索 ====================

class CoverSearchDialog(QDialog):
    def __init__(self, parent, row: dict):
        super().__init__(parent)
        self.setWindowTitle("🖼 从网络搜索封面")
        self.resize(860, 640)
        tags = row.get("tags") or {}
        self.cover = b""
        self.cover_mime = ""
        self.candidates: list[tag_search.TagCandidate] = []

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title_edit = QLineEdit(tags.get("title") or
                                    os.path.splitext(os.path.basename(row["path"]))[0])
        self.artist_edit = QLineEdit(tags.get("artist") or "")
        top.addWidget(QLabel("标题："))
        top.addWidget(self.title_edit, 2)
        top.addWidget(QLabel("艺术家："))
        top.addWidget(self.artist_edit, 1)
        self.search_btn = _blue("🔍 搜索", self._search)
        top.addWidget(self.search_btn)
        v.addLayout(top)

        self.table = _table(["来源", "标题", "艺术家", "专辑", "封面"])
        self.table.itemSelectionChanged.connect(self._on_select)
        v.addWidget(self.table, 1)

        self.preview = QLabel("选择候选后自动下载预览")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(280)
        self.preview.setStyleSheet("border:1px solid #c3cad6;background:#fff;")
        v.addWidget(self.preview, 2)

        bar = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        bar.addWidget(self.info, 1)
        bar.addWidget(_green("✔ 应用封面", self._apply))
        bar.addWidget(_plain("关闭", self.reject))
        v.addLayout(bar)

    def _search(self):
        title = self.title_edit.text().strip()
        if not title:
            return
        self.search_btn.setEnabled(False)
        self.info.setText("搜索中……")

        def job():
            return tag_search.search_tracks(title, self.artist_edit.text().strip(), "",
                                            tag_search.DEFAULT_SOURCES)

        def done(result, err):
            self.search_btn.setEnabled(True)
            if err:
                self.info.setText(f"搜索失败：{err}")
                return
            self.candidates = [c for c in (result or []) if c.cover_url]
            self.table.setRowCount(0)
            for i, cand in enumerate(self.candidates):
                self.table.insertRow(i)
                _fill_row(self.table, i, [cand.source_name, cand.title, cand.artist,
                                          cand.album, "有"])
            self.info.setText(f"共 {len(self.candidates)} 个封面候选")

        run_bg(job, done)

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        cand = self.candidates[rows[0].row()]
        self.info.setText(f"下载封面：{cand.title}")

        def job():
            return tag_search.download_cover(cand.cover_url)

        def done(res, err):
            data, mime = res if res else (b"", "")
            if err or not data:
                self.preview.setText("封面下载失败")
                return
            self.cover, self.cover_mime = data, mime
            pm = QPixmap()
            pm.loadFromData(data)
            self.preview.setPixmap(pm.scaled(420, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.info.setText(f"{cand.source_name} · {mime} · {_fmt_size(len(data))}")

        run_bg(job, done)

    def _apply(self):
        if not self.cover:
            self.info.setText("请先选择并预览一张封面")
            return
        self.accept()


# ==================== 歌词搜索 / 编辑 ====================

def adjust_lrc_offset(text: str, seconds: float) -> str:
    ms = int(seconds * 1000)
    pattern = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")

    def repl(m):
        total = int(m.group(1)) * 60000 + int(m.group(2)) * 1000
        frac = m.group(3) or "0"
        total += int(frac.ljust(3, "0")[:3])
        total = max(0, total + ms)
        mm, rest = divmod(total, 60000)
        ss, msec = divmod(rest, 1000)
        return f"[{mm:02d}:{ss:02d}.{msec // 10:02d}]"

    return pattern.sub(repl, text)


def clean_lrc(text: str, remove_time: bool = False, remove_blank: bool = False,
              remove_header: bool = False) -> str:
    out = []
    for line in (text or "").splitlines():
        if remove_header and re.match(
                r"^\[(ti|ar|al|by|offset|re|ve|length|kana|language):.*\]$", line.strip(), re.I):
            continue
        if remove_time:
            line = re.sub(r"\[[^\]]*\]", "", line).strip()
        if remove_blank and remove_time and not line.strip():
            continue
        if remove_blank and not remove_time:
            plain = re.sub(r"\[[^\]]*\]", "", line).strip()
            if not plain:
                continue
        out.append(line)
    return "\n".join(out)


class LyricSearchDialog(QDialog):
    def __init__(self, parent, row: dict):
        super().__init__(parent)
        self.setWindowTitle("🎵 从网络搜索歌词")
        self.resize(980, 700)
        tags = row.get("tags") or {}
        self.candidates: list[tag_search.TagCandidate] = []
        self.lyric = ""
        self.lyric_trans = ""
        self._current = None

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        self.title_edit = QLineEdit(tags.get("title") or
                                    os.path.splitext(os.path.basename(row["path"]))[0])
        self.artist_edit = QLineEdit(tags.get("artist") or "")
        top.addWidget(QLabel("标题："))
        top.addWidget(self.title_edit, 2)
        top.addWidget(QLabel("艺术家："))
        top.addWidget(self.artist_edit, 1)
        self.search_btn = _blue("🔍 搜索", self._search)
        top.addWidget(self.search_btn)
        v.addLayout(top)

        self.table = _table(["来源", "标题", "艺术家", "专辑", "时长"])
        self.table.itemSelectionChanged.connect(self._on_select)
        v.addWidget(self.table, 1)

        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("歌词预览（可手动修改后应用）")
        f = self.edit.font()
        f.setFamily("Consolas")
        self.edit.setFont(f)
        v.addWidget(self.edit, 2)

        opts = QHBoxLayout()
        self.remove_time = QCheckBox("去除时间标签")
        self.remove_blank = QCheckBox("删除空行")
        self.remove_header = QCheckBox("删除头部标签")
        opts.addWidget(self.remove_time)
        opts.addWidget(self.remove_blank)
        opts.addWidget(self.remove_header)
        opts.addWidget(QLabel("时间偏移（秒）："))
        self.offset = QSpinBox()
        self.offset.setRange(-600, 600)
        self.offset.setValue(0)
        opts.addWidget(self.offset)
        opts.addWidget(_plain("重新预览", self._reload_preview))
        opts.addStretch(1)
        v.addLayout(opts)

        bar = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        bar.addWidget(self.info, 1)
        bar.addWidget(_green("✔ 应用歌词", self._apply))
        bar.addWidget(_plain("关闭", self.reject))
        v.addLayout(bar)

    def _search(self):
        title = self.title_edit.text().strip()
        if not title:
            return
        self.search_btn.setEnabled(False)
        self.info.setText("搜索中……")

        def job():
            return tag_search.search_tracks(title, self.artist_edit.text().strip(), "",
                                            tag_search.DEFAULT_SOURCES)

        def done(result, err):
            self.search_btn.setEnabled(True)
            if err:
                self.info.setText(f"搜索失败：{err}")
                return
            self.candidates = result or []
            self.table.setRowCount(0)
            for i, cand in enumerate(self.candidates):
                self.table.insertRow(i)
                _fill_row(self.table, i, [cand.source_name, cand.title, cand.artist,
                                          cand.album, tag_search.format_duration(cand.duration)])
            self.info.setText(f"共 {len(self.candidates)} 个候选（选中后自动加载歌词）")

        run_bg(job, done)

    def _on_select(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        self._current = self.candidates[rows[0].row()]
        self.info.setText("加载歌词……")

        def job():
            return tag_search.load_lyric(self._current)

        def done(res, err):
            if err:
                self.info.setText(f"歌词加载失败：{err}")
                return
            self.lyric, self.lyric_trans = res if res else ("", "")
            if not self.lyric:
                self.info.setText("该候选没有歌词")
                self.edit.clear()
                return
            self._reload_preview()
            self.info.setText(f"已加载：{self._current.source_name} · {self._current.title}")

        run_bg(job, done)

    def _reload_preview(self):
        if not self.lyric:
            return
        text = adjust_lrc_offset(self.lyric, self.offset.value())
        text = clean_lrc(text, self.remove_time.isChecked(),
                         self.remove_blank.isChecked(), self.remove_header.isChecked())
        if self.lyric_trans and not self.remove_time.isChecked():
            text = tag_search.merge_lyrics(text, adjust_lrc_offset(
                self.lyric_trans, self.offset.value()))
        self.edit.setPlainText(text)

    def _apply(self):
        text = self.edit.toPlainText().strip()
        if not text:
            self.info.setText("歌词为空")
            return
        self.lyric = text
        self.accept()


# ==================== 批量改名 ====================

def render_rename_pattern(pattern: str, tags: dict) -> str:
    track = str(tags.get("track") or "")
    disc = str(tags.get("disc") or "")
    mapping = {
        "@1": str(tags.get("title") or ""),
        "@2": str(tags.get("artist") or ""),
        "@3": str(tags.get("album") or ""),
        "@4": disc,
        "@5": track,
        "@6": str(tags.get("year") or ""),
        "@7": str(tags.get("comment") or ""),
        "@8": str(tags.get("albumartist") or ""),
    }
    out = pattern
    for key in sorted(mapping, reverse=True):
        out = out.replace(key, mapping[key])
    out = re.sub(r"[\\/]", ";", out)
    out = re.sub(r'[\s":*?<>|]', " ", out).strip()
    return out


class BatchRenameDialog(QDialog):
    def __init__(self, parent, rows: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("✏ 按标签批量改名")
        self.resize(960, 640)
        self.rows = rows
        self.renames: list[tuple[str, str]] = []
        self.renamed: list[tuple[str, str]] = []

        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("文件名模板："))
        self.pattern = QLineEdit("@2 - @1")
        top.addWidget(self.pattern, 2)
        self.preset = QComboBox()
        self.preset.addItems(["@2 - @1", "@1 - @2", "@5. @1", "@5. @2 - @1",
                              "@4@5. @1", "@4@5. @2 - @1", "@1"])
        self.preset.currentTextChanged.connect(self._on_preset)
        top.addWidget(self.preset)
        top.addWidget(_blue("预览", self._preview))
        v.addLayout(top)

        hint = QLabel("@1 标题 · @2 艺术家 · @3 专辑 · @4 碟号 · @5 音轨 · "
                      "@6 年份 · @7 注释 · @8 专辑艺术家；含 @1/@2 但对应标签为空的文件会跳过")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.table = _table(["原文件名", "新文件名", "状态"])
        self.table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table, 1)

        bar = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        bar.addWidget(self.info, 1)
        bar.addWidget(_green("✔ 执行改名", self._apply))
        bar.addWidget(_plain("关闭", self.reject))
        v.addLayout(bar)
        self._preview()

    def _on_preset(self, text):
        if text:
            self.pattern.setText(text)
            self._preview()

    def _preview(self):
        pattern = self.pattern.text().strip()
        self.table.setRowCount(0)
        self.renames = []
        for i, row in enumerate(self.rows):
            old = os.path.basename(row["path"])
            tags = row.get("tags") or {}
            if ("@1" in pattern and not str(tags.get("title") or "").strip()) or \
                    ("@2" in pattern and not str(tags.get("artist") or "").strip()):
                status = "跳过：标题/艺术家为空"
                new = ""
            else:
                new = render_rename_pattern(pattern, tags)
                status = "待改名" if new and new + os.path.splitext(old)[1] != old else "无变化"
            self.table.insertRow(i)
            _fill_row(self.table, i, [old, new, status])
            if status == "待改名":
                self.renames.append((row["path"], new))
        self.info.setText(f"可改名 {len(self.renames)} / {len(self.rows)} 个文件")

    def _apply(self):
        pattern = self.pattern.text().strip()
        if not pattern:
            return
        self._preview()
        moved = 0
        errors = []
        self.renamed = []
        for path, new_stem in self.renames:
            folder = os.path.dirname(path)
            ext = os.path.splitext(path)[1]
            dst = os.path.join(folder, new_stem + ext)
            if os.path.normcase(dst) == os.path.normcase(path):
                continue
            if os.path.exists(dst) and os.path.normcase(dst) != os.path.normcase(path):
                index = 1
                while True:
                    cand = os.path.join(folder, f"{new_stem} ({index}){ext}")
                    if not os.path.exists(cand):
                        dst = cand
                        break
                    index += 1
            try:
                os.replace(path, dst)
                moved += 1
                self.renamed.append((path, dst))
                self._move_related(path, dst)
            except OSError as exc:
                errors.append(f"{os.path.basename(path)}: {exc}")
        self.info.setText(f"已改名 {moved} 个文件" + (f"，失败 {len(errors)}" if errors else ""))
        if errors:
            QMessageBox.warning(self, "改名完成", "\n".join(errors[:6]))
        self.accept()

    @staticmethod
    def _move_related(old_path: str, new_path: str):
        old_stem = os.path.splitext(old_path)[0]
        new_stem = os.path.splitext(new_path)[0]
        for ext in (".lrc", ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"):
            src = old_stem + ext
            if not os.path.isfile(src):
                continue
            dst = new_stem + ext
            if os.path.exists(dst):
                continue
            try:
                os.replace(src, dst)
            except OSError:
                pass


# ==================== 查找替换 ====================

class FindReplaceDialog(QDialog):
    def __init__(self, parent, rows: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("🔍 查找与替换")
        self.resize(680, 460)
        self.rows = rows
        self.result_updates: dict[int, dict] = {}

        v = QVBoxLayout(self)
        form = QFormLayout()
        self.find_edit = QLineEdit()
        self.replace_edit = QLineEdit()
        form.addRow("查找：", self.find_edit)
        form.addRow("替换为：", self.replace_edit)
        v.addLayout(form)

        self.regex_cb = QCheckBox("正则表达式")
        self.case_cb = QCheckBox("区分大小写")
        opt = QHBoxLayout()
        opt.addWidget(self.regex_cb)
        opt.addWidget(self.case_cb)
        opt.addStretch(1)
        v.addLayout(opt)

        fields = QGroupBox("作用字段")
        fg = QHBoxLayout(fields)
        self.field_checks: dict[str, QCheckBox] = {}
        for key, label in [("title", "标题"), ("artist", "艺术家"), ("album", "专辑"),
                           ("albumartist", "专辑艺术家"), ("genre", "风格"),
                           ("composer", "作曲家"), ("lyricist", "作词家"),
                           ("comment", "注释"), ("lyrics", "歌词")]:
            cb = QCheckBox(label)
            cb.setChecked(key in ("title", "artist", "album"))
            self.field_checks[key] = cb
            fg.addWidget(cb)
        fg.addStretch(1)
        v.addWidget(fields)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setPlaceholderText("点击「预览」查看替换结果")
        v.addWidget(self.preview, 1)

        bar = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        bar.addWidget(self.info, 1)
        bar.addWidget(_blue("预览", self._preview))
        bar.addWidget(_green("✔ 应用到选中行", self._apply))
        bar.addWidget(_plain("关闭", self.reject))
        v.addLayout(bar)

    def _matcher(self):
        text = self.find_edit.text()
        if not text:
            return None
        if self.regex_cb.isChecked():
            flags = 0 if self.case_cb.isChecked() else re.I
            try:
                return re.compile(text, flags)
            except re.error as exc:
                QMessageBox.warning(self, "正则错误", str(exc))
                return None
        return text

    def _repl(self, value: str) -> str:
        m = self._matcher()
        if m is None:
            return value
        if isinstance(m, str):
            if self.case_cb.isChecked():
                return value.replace(m, self.replace_edit.text())
            return re.sub(re.escape(m), lambda _x: self.replace_edit.text(), value, flags=re.I)
        return m.sub(self.replace_edit.text(), value)

    def _changed_fields(self):
        return [k for k, cb in self.field_checks.items() if cb.isChecked()]

    def _preview(self):
        fields = self._changed_fields()
        lines = []
        count = 0
        for i, row in enumerate(self.rows):
            tags = row.get("tags") or {}
            changes = []
            for key in fields:
                old = str(tags.get(key) or "")
                if not old:
                    continue
                new = self._repl(old)
                if new != old:
                    changes.append((key, old, new))
            if changes:
                count += 1
                lines.append(f"{os.path.basename(row['path'])}")
                for key, old, new in changes:
                    lines.append(f"  {key}: {old[:80]} → {new[:80]}")
        self.preview.setPlainText("\n".join(lines[:400]))
        self.info.setText(f"命中 {count} 个文件")

    def _apply(self):
        fields = self._changed_fields()
        if not fields:
            return
        self.result_updates = {}
        for i, row in enumerate(self.rows):
            tags = dict(row.get("tags") or {})
            changed = False
            for key in fields:
                old = str(tags.get(key) or "")
                if not old:
                    continue
                new = self._repl(old)
                if new != old:
                    tags[key] = new
                    changed = True
            if changed:
                self.result_updates[i] = tags
        self.info.setText(f"已生成 {len(self.result_updates)} 个文件的修改")
        if self.result_updates:
            self.accept()


# ==================== 编码修复 ====================

class EncodingFixDialog(QDialog):
    def __init__(self, parent, rows: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("🔤 编码修复（乱码重解码）")
        self.resize(760, 380)
        self.rows = rows
        self.updates: dict[int, tuple[str, str]] = {}

        v = QVBoxLayout(self)
        form = QFormLayout()
        self.field = QComboBox()
        for key, label in [("title", "标题"), ("artist", "艺术家"), ("album", "专辑"),
                           ("albumartist", "专辑艺术家"), ("genre", "风格"),
                           ("composer", "作曲家"), ("lyricist", "作词家"),
                           ("comment", "注释")]:
            self.field.addItem(label, key)
        self.encoding = QComboBox()
        self.encoding.addItems(tags_io.ENCODING_PROFILES)
        form.addRow("字段：", self.field)
        form.addRow("按编码解释：", self.encoding)
        v.addLayout(form)

        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        v.addWidget(self.preview, 1)

        bar = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        bar.addWidget(self.info, 1)
        bar.addWidget(_blue("预览", self._preview))
        bar.addWidget(_green("✔ 应用到选中行", self._apply))
        bar.addWidget(_plain("关闭", self.reject))
        v.addLayout(bar)
        self._preview()

    def _field_key(self) -> str:
        return self.field.currentData()

    def _preview(self):
        key = self._field_key()
        enc = self.encoding.currentText()
        lines = []
        for row in self.rows[:50]:
            old = str((row.get("tags") or {}).get(key) or "")
            if not old:
                continue
            try:
                new = tags_io.decode_field_value(row["path"], key, enc, old)
            except Exception as exc:  # noqa: BLE001
                new = f"[失败] {exc}"
            lines.append(f"{os.path.basename(row['path'])}\n  旧: {old}\n  新: {new}")
        self.preview.setPlainText("\n\n".join(lines) or "没有可预览的字段值")
        self.info.setText(f"{len(lines)} 个文件有该字段值")

    def _apply(self):
        key = self._field_key()
        enc = self.encoding.currentText()
        self.updates = {}
        for i, row in enumerate(self.rows):
            old = str((row.get("tags") or {}).get(key) or "")
            if not old:
                continue
            try:
                new = tags_io.decode_field_value(row["path"], key, enc, old)
            except Exception:
                continue
            if new and new != old:
                self.updates[i] = (key, new)
        self.info.setText(f"已生成 {len(self.updates)} 个文件的修改")
        if self.updates:
            self.accept()


# ==================== 图片管理 ====================

class PicturesDialog(QDialog):
    def __init__(self, parent, pictures: list[tags_io.Pic], title: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"🖼 图片管理 — {title}" if title else "🖼 图片管理")
        self.resize(900, 620)
        self.pictures = [tags_io.Pic(p.data, p.mime, p.type, p.desc) for p in pictures]
        self.result_pictures = list(self.pictures)

        v = QVBoxLayout(self)
        self.listw = QListWidget()
        self.listw.setIconSize(QSize(96, 96))
        self.listw.currentRowChanged.connect(self._show_current)
        v.addWidget(self.listw, 1)

        self.preview = QLabel("选择图片查看")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(240)
        self.preview.setStyleSheet("border:1px solid #c3cad6;background:#fff;")
        v.addWidget(self.preview, 2)

        row = QHBoxLayout()
        row.addWidget(_plain("＋ 添加图片", self._add))
        row.addWidget(_plain("🗑 移除", self._remove))
        row.addWidget(_plain("⭐ 设为前置封面", self._front))
        row.addWidget(_plain("💾 导出", self._export))
        row.addStretch(1)
        self.info = QLabel("")
        self.info.setStyleSheet("color:#888;")
        row.addWidget(self.info)
        v.addLayout(row)

        bar = QHBoxLayout()
        bar.addStretch(1)
        bar.addWidget(_green("✔ 确定", self._accept))
        bar.addWidget(_plain("取消", self.reject))
        v.addLayout(bar)
        self._reload()

    def _reload(self):
        self.listw.clear()
        for i, pic in enumerate(self.pictures):
            type_name = PICTURE_TYPE_NAMES[pic.type] if 0 <= pic.type < len(PICTURE_TYPE_NAMES) else "Other"
            item = QListWidgetItem(f"{i + 1}. {type_name} · {pic.mime} · {_fmt_size(len(pic.data))}")
            pm = QPixmap()
            if pm.loadFromData(pic.data):
                item.setIcon(pm.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.listw.addItem(item)
        if self.pictures:
            self.listw.setCurrentRow(0)
        else:
            self.preview.setText("无内嵌图片")
        self.info.setText(f"共 {len(self.pictures)} 张")

    def _show_current(self):
        row = self.listw.currentRow()
        if row < 0 or row >= len(self.pictures):
            return
        pic = self.pictures[row]
        pm = QPixmap()
        if pm.loadFromData(pic.data):
            self.preview.setPixmap(pm.scaled(420, 420, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            self.preview.setText("无法解码该图片")

    def _add(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择图片", "", "图片 (*.jpg *.jpeg *.png *.gif *.webp *.bmp)")
        for f in files:
            try:
                data = open(f, "rb").read()
            except OSError:
                continue
            self.pictures.append(tags_io.Pic(data, "", 3, os.path.basename(f)))
        self._reload()

    def _remove(self):
        row = self.listw.currentRow()
        if 0 <= row < len(self.pictures):
            self.pictures.pop(row)
            self._reload()

    def _front(self):
        row = self.listw.currentRow()
        if 0 <= row < len(self.pictures):
            pic = self.pictures.pop(row)
            pic.type = 3
            self.pictures.insert(0, pic)
            self._reload()

    def _export(self):
        row = self.listw.currentRow()
        if not (0 <= row < len(self.pictures)):
            return
        pic = self.pictures[row]
        ext = ".png" if "png" in (pic.mime or "") else ".jpg"
        f, _ = QFileDialog.getSaveFileName(self, "导出图片", "cover" + ext)
        if f:
            with open(f, "wb") as fp:
                fp.write(pic.data)

    def _accept(self):
        self.result_pictures = list(self.pictures)
        self.accept()


# ==================== 标签历史 ====================

class HistoryDialog(QDialog):
    def __init__(self, parent, path: str):
        super().__init__(parent)
        self.setWindowTitle(f"🕘 标签历史 — {os.path.basename(path)}")
        self.resize(820, 560)
        self.path = path
        self.restored: dict | None = None
        self.records = tag_history.history(path)

        v = QVBoxLayout(self)
        self.table = _table(["时间", "标题", "艺术家", "专辑", "年份", "音轨", "碟号"])
        self.table.itemSelectionChanged.connect(self._detail)
        v.addWidget(self.table, 1)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        v.addWidget(self.detail, 2)

        bar = QHBoxLayout()
        self.info = QLabel(f"共 {len(self.records)} 条历史（最多保留 5 条）")
        self.info.setStyleSheet("color:#888;")
        bar.addWidget(self.info, 1)
        bar.addWidget(_green("↩ 恢复选中快照", self._restore))
        bar.addWidget(_plain("关闭", self.reject))
        v.addLayout(bar)
        self._reload()

    def _reload(self):
        self.table.setRowCount(0)
        for i, rec in enumerate(self.records):
            self.table.insertRow(i)
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec.get("createtime") or 0))
            _fill_row(self.table, i, [when, rec.get("title", ""), rec.get("artist", ""),
                                      rec.get("album", ""), rec.get("year", ""),
                                      rec.get("track", ""), rec.get("disc", "")])

    def _detail(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        rec = self.records[rows[0].row()]
        lines = [f"{k}: {v}" for k, v in rec.items() if k != "createtime"]
        self.detail.setPlainText("\n".join(lines))

    def _restore(self):
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            return
        rec = self.records[rows[0].row()]
        self.restored = {k: v for k, v in rec.items() if k != "createtime"}
        self.accept()


# ==================== 标签设置 ====================

class TagSettingsDialog(QDialog):
    def __init__(self, parent, settings: dict):
        super().__init__(parent)
        self.setWindowTitle("⚙ 音乐标签设置")
        self.resize(480, 260)
        self.settings = settings
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.id3 = QComboBox()
        self.id3.addItems(["ID3v2.3（兼容性最好，默认）", "ID3v2.4（UTF-8）"])
        self.id3.setCurrentIndex(1 if int(settings.get("id3_version", 3)) == 4 else 0)
        form.addRow("MP3 标签版本：", self.id3)
        self.connector = QLineEdit(str(settings.get("connector") or "/"))
        self.connector.setMaxLength(8)
        form.addRow("多值连接符：", self.connector)
        self.threads = QSpinBox()
        self.threads.setRange(1, 8)
        self.threads.setValue(int(settings.get("threads", 3) or 3))
        form.addRow("自动匹配并发：", self.threads)
        v.addLayout(form)
        hint = QLabel("ID3v2.3 用 UTF-16 写入，兼容老播放器；ID3v2.4 用 UTF-8。\n"
                      "多值连接符只影响读取多个艺术家/风格时的拼接显示。")
        hint.setStyleSheet("color:#888;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        bar = QHBoxLayout()
        bar.addStretch(1)
        bar.addWidget(_green("✔ 保存", self._save))
        bar.addWidget(_plain("取消", self.reject))
        v.addLayout(bar)

    def _save(self):
        self.settings["id3_version"] = 4 if self.id3.currentIndex() == 1 else 3
        self.settings["connector"] = self.connector.text().strip() or "/"
        self.settings["threads"] = self.threads.value()
        self.accept()
