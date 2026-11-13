"""音乐服务 · 音乐标签页：原生标签编辑器（对齐 musictag）。

右侧文件列表 + 左侧标签表单：读取/编辑/保存音频标签（mutagen 多格式），
支持四平台网络匹配、批量自动匹配、封面/歌词搜索、XLSX 表格匹配、
批量改名、查找替换、乱码修复、多图管理与标签历史。
"""

from __future__ import annotations

import os
import re

from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtGui import QAction, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
    QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMenuBar,
    QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QSplitter, QStyle,
    QTableWidget, QTableWidgetItem, QTextBrowser, QToolBar, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.pages.music_tag_dialogs import (
    AutoMatchDialog, BatchRenameDialog, CombinedSearchDialog, CoverSearchDialog,
    EncodingFixDialog, FindReplaceDialog, HistoryDialog, LyricSearchDialog,
    PicturesDialog, TagSettingsDialog, clean_lrc, adjust_lrc_offset,
)
from music import tag_history, tag_search, tags_io

# 表格列：(字段键, 标题, 默认宽度)
COLUMNS = [
    ("filename", "文件名", 170), ("filedir", "目录", 120),
    ("tagtypes", "标签类型", 90), ("title", "标题", 150),
    ("artist", "艺术家", 130), ("album", "专辑", 130),
    ("albumartist", "专辑艺术家", 120), ("year", "年份", 60),
    ("trackstr", "音轨", 60), ("discstr", "碟号", 50),
    ("genre", "风格", 90), ("composer", "作曲家", 90),
    ("lyricist", "作词家", 90), ("comment", "注释", 110),
    ("haspicture", "封面", 50), ("lyrics", "歌词", 100),
    ("bitpersample", "位深", 50), ("channels", "声道", 50),
    ("samplerate", "采样率", 70), ("bitrate", "比特率", 70),
    ("durationinms", "时长", 80), ("updatetime", "修改时间", 130),
]

DEFAULT_SETTINGS = {
    "id3_version": 3,
    "connector": "/",
    "threads": 3,
    "use_filename_only": False,
    "use_artist": True,
    "use_album": True,
    "sources": list(tag_search.DEFAULT_SOURCES),
    "overwrite": {},
    "columns": {},
}

HELP_HTML = (
    "<p><b>音频标签（Tag）查看与批量编辑：右侧文件列表 + 左侧标签表单。</b></p>"
    "<p>· <b>文件</b>：文件菜单 → 添加文件 / 添加文件夹（递归），支持 "
    "FLAC/MP3/M4A/OGG/OPUS/WAV/APE/WV/WMA/AIFF/DSF 等</p>"
    "<p>· <b>字段</b>：标题/艺术家/专辑/专辑艺术家/年份/风格/作曲家/作词家/注释/"
    "音轨(号+总数)/碟号(号+总数) + 多图封面 + 歌词</p>"
    "<p>· <b>保存</b>：保存标签 / 全部保存；只写有变化的字段，保留无关标签与封面</p>"
    "<p>· <b>标签源</b>：网络匹配（手动选候选）/ 自动匹配（四源相似度）/ "
    "封面搜索 / 歌词搜索</p>"
    "<p>· <b>批量</b>：XLSX 表格匹配 / 按标签改名 / 提取封面 / 歌词时间轴处理</p>"
    "<p>· <b>工具</b>：编码修复（乱码重解码）/ 标签历史 / 设置"
    "（ID3v2.3 UTF-16 或 v2.4 UTF-8）</p>"
)

EDIT_FIELDS = [
    ("title", "标题"), ("artist", "艺术家"), ("album", "专辑"),
    ("albumartist", "专辑艺术家"), ("year", "年份"),
]
EDIT_FIELDS_TAIL = [
    ("genre", "风格"), ("composer", "作曲家"), ("lyricist", "作词家"),
    ("comment", "注释"),
]


# ==================== 配置 ====================

# 加密音乐扩展名（交给 music_unlock 解密后再进入标签列表）
ENCRYPTED_EXTS = {
    ".ncm",
    ".qmc0", ".qmc2", ".qmc3", ".qmc4", ".qmc6", ".qmc8", ".qmcflac", ".qmcogg",
    ".mflac", ".mflac0", ".mgg", ".mgg0", ".mgg1",
    ".kgm", ".kgma", ".vpr", ".kwm", ".xm",
    ".tm0", ".tm2", ".tm3", ".tm6", ".x2m", ".x3m",
}


def _load_settings() -> dict:
    try:
        from toolbox.config_manager import load_convert_config
        section = load_convert_config().get("music_tag")
        merged = dict(DEFAULT_SETTINGS)
        if isinstance(section, dict):
            for key, value in section.items():
                if key in merged:
                    merged[key] = value
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def _save_settings(settings: dict):
    try:
        from toolbox.config_manager import load_convert_config, save_convert_config
        cfg = load_convert_config()
        cfg["music_tag"] = {k: v for k, v in settings.items() if k in DEFAULT_SETTINGS}
        save_convert_config(cfg)
    except Exception:
        pass


# ==================== XLSX 导入匹配 ====================

XLSX_FIELD_LABELS = [
    ("filename", "文件名"), ("title", "标题"), ("artist", "艺术家"),
    ("album", "专辑"), ("albumartist", "专辑艺术家"), ("year", "年份"),
    ("track", "音轨号"), ("track_count", "音轨总数"), ("disc", "碟号"),
    ("disc_count", "碟总数"), ("genre", "风格"), ("composer", "作曲家"),
    ("lyricist", "作词家"), ("comment", "注释"), ("lyrics", "歌词"),
]

_XLSX_ALIASES = {
    "filename": ("文件名", "文件", "filename", "file"),
    "title": ("标题", "歌名", "歌曲", "名称", "title", "song", "name"),
    "artist": ("艺术家", "歌手", "演唱者", "演唱", "artist", "singer"),
    "album": ("专辑", "album"),
    "albumartist": ("专辑艺术家", "专辑歌手", "albumartist", "album artist",
                    "album_artist"),
    "year": ("年份", "年代", "发行年份", "发行日期", "日期", "year", "date",
             "release"),
    "track": ("音轨", "音轨号", "曲目号", "轨道", "track", "trackno", "track_no"),
    "track_count": ("音轨总数", "曲目总数", "总曲目", "tracktotal", "track total"),
    "disc": ("碟号", "碟", "唱片", "disc", "discno"),
    "disc_count": ("碟总数", "总碟数", "disctotal", "disc total"),
    "genre": ("风格", "流派", "genre"),
    "composer": ("作曲", "作曲家", "composer"),
    "lyricist": ("作词", "作词家", "作词者", "lyricist", "writer"),
    "comment": ("注释", "备注", "说明", "comment", "note"),
    "lyrics": ("歌词", "lyric", "lyrics", "lrc"),
}

XLSX_MATCH_MIN = 45


def _auto_map_field(header: str) -> str:
    h = str(header or "").strip().lower()
    if not h:
        return ""
    for field, aliases in _XLSX_ALIASES.items():
        if h in aliases:
            return field
    for field, aliases in _XLSX_ALIASES.items():
        if any(a in h or h in a for a in aliases if len(a) >= 2):
            return field
    return ""


def _cell_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v).strip()


def _norm_key(s: str) -> str:
    return re.sub(r"[\s\-_·・~～]+", "", str(s or "")).lower()


def _pair_score(a: str, b: str) -> int:
    if not a or not b:
        return 0
    if a == b:
        return 50
    if a in b or b in a:
        return 35
    return 0


def _xlsx_match_score(page_row: dict, vals: dict) -> float:
    path = page_row["path"]
    tags = page_row["tags"]
    stem = os.path.splitext(os.path.basename(path))[0]
    parts = [p.strip() for p in re.split(r"\s+-\s+", stem) if p.strip()]

    fx = _norm_key(vals.get("filename", ""))
    if fx:
        base = _norm_key(os.path.basename(path))
        st = _norm_key(stem)
        if fx == base or fx == st:
            return 100
        if len(fx) >= 4 and (fx in st or st in fx):
            return 80
        return 0

    xt = _norm_key(vals.get("title", ""))
    if not xt:
        return 0

    title_cands = []
    if tags.get("title"):
        title_cands.append(_norm_key(tags["title"]))
    if len(parts) >= 2:
        title_cands.append(_norm_key(parts[1]))
    elif stem:
        title_cands.append(_norm_key(stem))
    title_score = max((_pair_score(c, xt) for c in title_cands), default=0)

    artist_cands = []
    if tags.get("artist"):
        artist_cands.append(_norm_key(tags["artist"]))
    if len(parts) >= 2:
        artist_cands.append(_norm_key(parts[0]))

    xa = _norm_key(vals.get("artist", ""))
    if not xa or not artist_cands:
        artist_score = 5
    else:
        best = max((_pair_score(c, xa) for c in artist_cands), default=0)
        artist_score = 30 if best == 50 else (20 if best == 35 else -20)

    album_score = 0
    xal = _norm_key(vals.get("album", ""))
    if xal and tags.get("album"):
        a = _norm_key(tags["album"])
        album_score = 10 if a == xal else (5 if (a in xal or xal in a) else 0)

    return title_score + artist_score + album_score


class _XlsxDialog(QDialog):
    def __init__(self, parent, headers: list[str], rows: list[list[str]],
                 page_rows: list[dict]):
        super().__init__(parent)
        self.setWindowTitle("导入 XLSX 匹配歌曲")
        self.resize(960, 640)
        self.headers = headers
        self.rows = rows
        self.page_rows = page_rows
        self.applied = 0
        v = QVBoxLayout(self)

        lbl = QLabel("列映射（用于匹配的字段：文件名 / 标题+艺术家；其余列按需映射）：")
        lbl.setStyleSheet("color:#666;")
        v.addWidget(lbl)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(4)
        self.map_cbs: list[QComboBox] = []
        for c, h in enumerate(headers):
            label = QLabel(f"{h or '列' + chr(65 + c)}：")
            cb = QComboBox()
            cb.addItem("（不使用）", "")
            for key, name in XLSX_FIELD_LABELS:
                cb.addItem(name, key)
            auto = _auto_map_field(h)
            if auto:
                for k in range(cb.count()):
                    if cb.itemData(k) == auto:
                        cb.setCurrentIndex(k)
                        break
            cb.currentIndexChanged.connect(lambda _i: self._rematch())
            r, col = divmod(c, 3)
            grid.addWidget(label, r, col * 2)
            grid.addWidget(cb, r, col * 2 + 1)
            self.map_cbs.append(cb)
        wrap = QWidget()
        wrap.setLayout(grid)
        v.addWidget(wrap)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["音频文件", "匹配到", "匹配分", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        v.addWidget(self.table, 1)

        row2 = QHBoxLayout()
        self.info = QLabel("")
        self.info.setStyleSheet("color:#666;")
        row2.addWidget(self.info, 1)
        apply_btn = QPushButton("✔ 填充匹配文件")
        apply_btn.setStyleSheet("background:#2e7d32;color:white;padding:5px 18px;")
        apply_btn.clicked.connect(self._apply)
        row2.addWidget(apply_btn)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row2.addWidget(cancel)
        v.addLayout(row2)
        self._rematch()

    def _mapping(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c, cb in enumerate(self.map_cbs):
            key = cb.currentData()
            if key and key not in out:
                out[key] = c
        return out

    def _row_vals(self, r: int, mapping: dict[str, int]) -> dict:
        return {k: self.rows[r][c] for k, c in mapping.items()
                if c < len(self.rows[r])}

    def _matches(self) -> list[int]:
        mapping = self._mapping()
        out = []
        for pr in self.page_rows:
            best_i, best_s = -1, 0
            for r in range(len(self.rows)):
                s = _xlsx_match_score(pr, self._row_vals(r, mapping))
                if s > best_s:
                    best_i, best_s = r, s
            out.append(best_i if best_s >= XLSX_MATCH_MIN else -1)
        return out

    def _rematch(self):
        matches = self._matches()
        self.table.setRowCount(0)
        for i, m in enumerate(matches):
            self.table.insertRow(i)
            pr = self.page_rows[i]
            self.table.setItem(i, 0, QTableWidgetItem(os.path.basename(pr["path"])))
            if m < 0:
                self.table.setItem(i, 1, QTableWidgetItem("—"))
                self.table.setItem(i, 2, QTableWidgetItem(""))
                self.table.setItem(i, 3, QTableWidgetItem("未匹配"))
            else:
                vals = self._row_vals(m, self._mapping())
                desc = " - ".join(x for x in (vals.get("title", ""),
                                              vals.get("artist", "")) if x) or \
                    vals.get("filename", "")
                self.table.setItem(i, 1, QTableWidgetItem(desc))
                self.table.setItem(i, 2, QTableWidgetItem(
                    f"{_xlsx_match_score(pr, vals):.0f}"))
                self.table.setItem(i, 3, QTableWidgetItem("✓ 待填充"))
        n = sum(1 for m in matches if m >= 0)
        if not self._mapping().get("title") and not self._mapping().get("filename"):
            self.info.setText("请先把某一列映射为「文件名」或「标题」用于匹配")
        else:
            self.info.setText(f"匹配 {n}/{len(self.page_rows)} 个文件 · "
                              f"表格 {len(self.rows)} 行 · 仅写入非空单元格")

    def _apply(self):
        mapping = self._mapping()
        matches = self._matches()
        n = 0
        for i, m in enumerate(matches):
            if m < 0:
                continue
            vals = self._row_vals(m, mapping)
            pr = self.page_rows[i]
            for k, val in vals.items():
                if k != "filename" and val:
                    pr["tags"][k] = val
            pr["dirty"] = True
            n += 1
        if not n:
            QMessageBox.information(self, "表格匹配", "当前映射下没有任何文件匹配成功，可调整列映射后重试")
            return
        self.applied = n
        self.accept()


# ==================== 页面 ====================

class MusicTagPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._rows: list[dict] = []
        self._edit_targets: list[int] = []
        self._settings = _load_settings()
        self._sort_col = -1
        self._sort_desc = False
        self._clipboard: tuple[dict, list[tags_io.Pic]] | None = None
        self._pending_encrypted: list[str] = []

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 2, 6, 6)
        v.setSpacing(2)
        v.addWidget(self._build_menubar())
        self.decrypt_bar = self._build_decrypt_bar()
        v.addWidget(self.decrypt_bar)
        v.addWidget(self._build_toolbar())
        v.addWidget(self._build_body(), 1)
        v.addWidget(self._build_statusbar())

    # ==================== 菜单 / 工具栏（对齐官方 musictag） ====================
    @staticmethod
    def _icon(name: str) -> QIcon:
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "assets", "music_tag", name + ".png")
        return QIcon(path) if os.path.isfile(path) else QIcon()

    def _act(self, text: str, slot, *, icon: str = "", shortcut: str = "",
             tip: str = "") -> QAction:
        action = QAction(text, self)
        if icon:
            action.setIcon(self._icon(icon))
        if shortcut:
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        if tip:
            action.setToolTip(tip)
            action.setStatusTip(tip)
        action.triggered.connect(slot)
        return action

    def _build_menubar(self):
        bar = QMenuBar()
        bar.setNativeMenuBar(False)
        bar.setStyleSheet(
            "QMenuBar { background: transparent; border: none; padding: 0; }"
            "QMenuBar::item { padding: 3px 6px; background: transparent; }"
            "QMenuBar::item:selected { background: rgba(127,127,127,0.18); }")

        file_menu = bar.addMenu("文件(&F)")
        file_menu.addAction(self._act("添加文件...", self._add_files,
                                      shortcut="Ctrl+O", tip="选择音频文件加入列表"))
        file_menu.addAction(self._act("添加文件夹...", self._add_folder,
                                      shortcut="Ctrl+Shift+O", tip="递归扫描文件夹内全部音频"))
        file_menu.addSeparator()
        file_menu.addAction(self._act("重新读取标签", self._reload_selected,
                                      shortcut="Ctrl+R", tip="丢弃未保存修改，从磁盘重新读取"))
        file_menu.addAction(self._act("保存标签", self._save_selected,
                                      icon="save", shortcut="Ctrl+S", tip="把表单写入选中文件"))
        file_menu.addAction(self._act("全部保存", self._save_all,
                                      shortcut="Ctrl+Shift+S", tip="保存所有已修改文件"))
        file_menu.addSeparator()
        file_menu.addAction(self._act("清除标签", self._clear_tags, icon="remove_tags"))
        file_menu.addAction(self._act("标签历史", self._history, icon="history"))
        file_menu.addSeparator()
        file_menu.addAction(self._act("移除选中", self._remove_selected))
        file_menu.addAction(self._act("清空列表", self._clear_list))

        edit_menu = bar.addMenu("编辑(&E)")
        edit_menu.addAction(self._act("全选文件", self._select_all_files, shortcut="Ctrl+A"))
        edit_menu.addAction(self._act("取消选择", self._unselect_all_files))
        edit_menu.addAction(self._act("反选", self._invert_selection))
        edit_menu.addSeparator()
        edit_menu.addAction(self._act("复制标签", self._copy_tags, shortcut="Ctrl+Shift+C"))
        edit_menu.addAction(self._act("粘贴标签", self._paste_tags, shortcut="Ctrl+Shift+V"))
        edit_menu.addSeparator()
        edit_menu.addAction(self._act("查找替换...", self._find_replace, shortcut="Ctrl+F"))

        view_menu = bar.addMenu("视图(&V)")
        view_menu.addAction(self._act("刷新", self._reload_selected, icon="refresh",
                                      shortcut="F5", tip="重新读取选中文件标签"))
        view_menu.addAction(self._act("自定义列...", self._customize_columns, icon="manage_dirs"))
        view_menu.addAction(self._act("显示全部列", lambda: self._toggle_all_columns(True)))

        source_menu = bar.addMenu("标签源(&S)")
        source_menu.addAction(self._act("网络匹配...", self._net_match, icon="combined_source",
                                        tip="四平台搜索并手动选择候选"))
        source_menu.addAction(self._act("自动匹配标签...", self._auto_match, icon="auto_match",
                                        tip="四平台按相似度自动匹配"))
        source_menu.addSeparator()
        source_menu.addAction(self._act("封面搜索...", self._cover_search, icon="cover_source"))
        source_menu.addAction(self._act("歌词搜索...", self._lyric_search, icon="lyric_source"))

        batch_menu = bar.addMenu("批量(&B)")
        batch_menu.addAction(self._act("自动匹配标签...", self._auto_match, icon="auto_match"))
        xlsx_action = self._act("XLSX 表格匹配...", self._xlsx_import)
        xlsx_action.setIcon(self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        batch_menu.addAction(xlsx_action)
        batch_menu.addAction(self._act("按标签改名...", self._batch_rename, icon="filename_rel"))
        batch_menu.addAction(self._act("提取封面", self._extract_covers, icon="extract_cover"))
        batch_menu.addSeparator()
        lyric_menu = batch_menu.addMenu("歌词")
        lyric_menu.setIcon(self._icon("lyric_source"))
        lyric_menu.addAction(self._act("重新格式化时间标签", lambda: self._apply_lyric_op("reformat")))
        lyric_menu.addAction(self._act("去除时间标签", lambda: self._apply_lyric_op("remove_time")))
        lyric_menu.addAction(self._act("删除空行", lambda: self._apply_lyric_op("remove_blank")))
        lyric_menu.addAction(self._act("删除头部标签", lambda: self._apply_lyric_op("remove_header")))
        lyric_menu.addSeparator()
        lyric_menu.addAction(self._act("导入 LRC 文件...", self._import_lrc_selected))

        tools_menu = bar.addMenu("工具(&T)")
        tools_menu.addAction(self._act("编码修复...", self._encoding_fix, icon="charset"))
        tools_menu.addAction(self._act("标签历史", self._history, icon="history"))
        tools_menu.addSeparator()
        tools_menu.addAction(self._act("设置...", self._open_settings, icon="options"))

        lang_menu = bar.addMenu("语言(&L)")
        zh = self._act("简体中文", lambda: None)
        zh.setCheckable(True)
        zh.setChecked(True)
        lang_menu.addAction(zh)
        for label in ("繁體中文", "English"):
            action = self._act(label, lambda: None)
            action.setEnabled(False)
            lang_menu.addAction(action)

        help_menu = bar.addMenu("帮助(&H)")
        help_menu.addAction(self._act("功能介绍", self._show_help, icon="read_tags"))
        help_menu.addAction(self._act("关于", self._about))
        return bar

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setFloatable(False)
        tb.setIconSize(QSize(22, 22))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        tb.setStyleSheet(
            "QToolBar { spacing: 2px; border: none; padding: 1px 0; }"
            "QToolBar QToolButton { padding: 2px; border: none; border-radius: 3px; }"
            "QToolBar QToolButton:hover { background: rgba(127,127,127,0.18); }")

        def add(text, icon, slot, tip):
            action = self._act(text, slot, tip=tip)
            if icon:
                action.setIcon(self._icon(icon))
            tb.addAction(action)
            return action

        def std_icon(text, pixmap, slot, tip):
            action = self._act(text, slot, tip=tip)
            action.setIcon(self.style().standardIcon(pixmap))
            tb.addAction(action)
            return action

        std_icon("添加文件", QStyle.SP_FileIcon, self._add_files, "添加音频文件")
        add("添加文件夹", "add_filename", self._add_folder, "递归扫描文件夹内全部音频")
        tb.addSeparator()
        add("保存标签", "save", self._save_selected, "保存选中文件（Ctrl+S）")
        add("清除标签", "remove_tags", self._clear_tags, "清空选中文件的全部标签与封面")
        add("重新读取", "read_tags", self._reload_selected, "从磁盘重新读取选中文件标签")
        add("编码修复", "charset", self._encoding_fix, "把乱码标签按指定编码重新解码")
        add("标签历史", "history", self._history, "查看/恢复标签历史")
        tb.addSeparator()
        add("全选", "select_all", self._select_all_files, "全选文件")
        add("取消选择", "unselect_all", self._unselect_all_files, "取消选择")
        tb.addSeparator()
        add("刷新", "refresh", self._reload_selected, "刷新选中文件标签（F5）")
        tb.addSeparator()
        add("封面搜索", "cover_source", self._cover_search, "从网络搜索封面")
        add("歌词搜索", "lyric_source", self._lyric_search, "从网络搜索歌词")
        add("网络匹配", "combined_source", self._net_match, "四平台搜索并手动选择候选")
        tb.addSeparator()
        add("自动匹配", "auto_match", self._auto_match, "按相似度批量自动匹配标签")
        add("提取封面", "extract_cover", self._extract_covers, "把内嵌封面导出为同名图片")
        add("批量改名", "filename_rel", self._batch_rename, "按 @1~@8 标签占位符批量改名")
        tb.addSeparator()
        add("设置", "options", self._open_settings, "ID3v2 版本、连接符、自动匹配并发")
        std_icon("帮助", QStyle.SP_DialogHelpButton, self._show_help, "功能介绍")
        return tb

    def _build_decrypt_bar(self):
        """拖入加密音乐时出现的顶部解密条。"""
        bar = QFrame()
        bar.setObjectName("decryptBar")
        bar.setStyleSheet(
            "QFrame#decryptBar { background: palette(alternate-base);"
            " border: 1px solid palette(mid); border-radius: 6px; }")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        self.decrypt_label = QLabel("")
        row.addWidget(self.decrypt_label, 1)
        self.decrypt_btn = QPushButton("🔓 音乐解密")
        self.decrypt_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 4px 12px; border-radius: 4px;")
        self.decrypt_btn.setToolTip("解密拖入的加密音乐，完成后自动加入标签列表")
        self.decrypt_btn.clicked.connect(self._start_decrypt)
        row.addWidget(self.decrypt_btn)
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self._clear_pending)
        row.addWidget(clear_btn)
        bar.setVisible(False)
        return bar

    def _update_decrypt_bar(self):
        n = len(self._pending_encrypted)
        self.decrypt_bar.setVisible(n > 0)
        if not n:
            return
        names = "、".join(os.path.basename(p) for p in self._pending_encrypted[:3])
        more = "…" if n > 3 else ""
        self.decrypt_label.setText(f"检测到 {n} 个加密文件：{names}{more}")

    def _clear_pending(self):
        self._pending_encrypted.clear()
        self._update_decrypt_bar()

    def _start_decrypt(self):
        if not self._pending_encrypted:
            return
        files = list(self._pending_encrypted)
        self.decrypt_btn.setEnabled(False)
        self.decrypt_label.setText(f"⏳ 正在解密 {len(files)} 个文件...")
        from toolbox.config_manager import get_music_download_dir
        out_dir = str(get_music_download_dir())

        def work():
            from music_unlock import decrypt_to
            results = []
            for p in files:
                try:
                    results.append((p, decrypt_to(p, out_dir), ""))
                except Exception as e:
                    results.append((p, "", str(e)))
            return results

        def done(results, err):
            self.decrypt_btn.setEnabled(True)
            if err:
                self.decrypt_label.setText(f"❌ 解密失败：{err}")
                return
            ok = [out for _p, out, e in results if out and not e]
            failed = [(p, e) for p, out, e in results if e]
            self._pending_encrypted = [p for p, _out, e in results if e]
            self._update_decrypt_bar()
            if ok:
                self._load_files(ok)
                self.status.setText(f"✅ 已解密 {len(ok)} 个文件，已加入标签列表")
            if failed:
                self.app.toast(f"解密完成：成功 {len(ok)}，失败 {len(failed)}", "warning")
            elif ok:
                self.app.toast(f"已解密 {len(ok)} 个文件并加入列表", "success")

    # ---- 菜单辅助功能 ----
    def _select_all_files(self):
        self.table.clearSelection()
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i):
                self.table.selectRow(i)

    def _unselect_all_files(self):
        self.table.clearSelection()

    def _invert_selection(self):
        selected = {i.row() for i in self.table.selectionModel().selectedRows()}
        self.table.clearSelection()
        for i in range(self.table.rowCount()):
            if not self.table.isRowHidden(i) and i not in selected:
                self.table.selectRow(i)

    def _customize_columns(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("自定义列")
        dlg.resize(360, 520)
        v = QVBoxLayout(dlg)
        checks: dict[str, QCheckBox] = {}
        visible = self._settings.get("columns") or {}
        for key, label, _width in COLUMNS:
            cb = QCheckBox(label)
            cb.setChecked(visible.get(key, True) is not False)
            checks[key] = cb
            v.addWidget(cb)
        bar = QHBoxLayout()
        bar.addWidget(QPushButton("全部显示", clicked=lambda: [c.setChecked(True) for c in checks.values()]))
        bar.addStretch(1)
        ok = QPushButton("✔ 确定")
        ok.setStyleSheet("background:#2e7d32;color:white;padding:4px 16px;")
        bar.addWidget(ok)
        bar.addWidget(QPushButton("取消", clicked=dlg.reject))
        v.addLayout(bar)

        def apply():
            cols = self._settings.setdefault("columns", {})
            for key, cb in checks.items():
                cols[key] = cb.isChecked()
            _save_settings(self._settings)
            self._apply_column_visibility()
            dlg.accept()

        ok.clicked.connect(apply)
        dlg.exec()

    def _extract_covers(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要提取封面的文件行", "warning")
            return
        ok = 0
        for i in rows:
            r = self._rows[i]
            pics = r.get("pictures") or []
            front = next((p for p in pics if p.type == 3), pics[0] if pics else None)
            if not front:
                continue
            folder = os.path.dirname(r["path"])
            stem = os.path.splitext(os.path.basename(r["path"]))[0]
            ext = ".png" if "png" in (front.mime or "") else ".jpg"
            dst = os.path.join(folder, stem + ext)
            try:
                with open(dst, "wb") as f:
                    f.write(front.data)
                ok += 1
            except OSError:
                continue
        self.app.toast(f"已提取 {ok}/{len(rows)} 个封面", "success" if ok else "warning")

    def _apply_lyric_op(self, kind: str):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中文件行", "warning")
            return
        changed = 0
        for i in rows:
            tags = self._rows[i]["tags"]
            text = str(tags.get("lyrics") or "")
            if not text:
                continue
            if kind == "reformat":
                new = adjust_lrc_offset(text, 0)
            elif kind == "remove_time":
                new = clean_lrc(text, remove_time=True)
            elif kind == "remove_blank":
                new = clean_lrc(text, remove_blank=True)
            elif kind == "remove_header":
                new = clean_lrc(text, remove_header=True)
            else:
                new = text
            if new != text:
                tags["lyrics"] = new
                self._rows[i]["dirty"] = True
                changed += 1
                self._mark_row(i)
        if self._edit_targets:
            self._fill_form(self._rows[self._edit_targets[0]])
        self.app.toast(f"已处理 {changed} 个文件的歌词（记得保存）", "success" if changed else "info")

    def _import_lrc_selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中文件行", "warning")
            return
        f, _ = QFileDialog.getOpenFileName(self, "导入 LRC 歌词", "",
                                           "歌词 (*.lrc *.txt);;所有文件 (*.*)")
        if not f:
            return
        try:
            text = open(f, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            self.app.toast(f"读取失败：{exc}", "error")
            return
        for i in rows:
            self._rows[i]["tags"]["lyrics"] = text
            self._rows[i]["dirty"] = True
            self._mark_row(i)
        if self._edit_targets:
            self._fill_form(self._rows[self._edit_targets[0]])
        self.app.toast(f"已导入歌词到 {len(rows)} 个文件（记得保存）", "success")

    def _show_help(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("音乐标签 · 功能介绍")
        dlg.resize(720, 560)
        v = QVBoxLayout(dlg)
        browser = QTextBrowser()
        browser.setHtml(HELP_HTML)
        v.addWidget(browser, 1)
        bar = QHBoxLayout()
        bar.addStretch(1)
        bar.addWidget(QPushButton("关闭", clicked=dlg.accept))
        v.addLayout(bar)
        dlg.exec()

    def _about(self):
        try:
            from version import APP_VERSION
        except Exception:
            APP_VERSION = ""
        QMessageBox.information(
            self, "关于 音乐标签",
            "音乐标签编辑器\n"
            "行为对齐 musictag（TagLibSharp）：四平台标签/歌词/封面搜索、"
            "自动匹配、批量改名、编码修复、标签历史。\n\n"
            f"工具箱版本：{APP_VERSION}")


    def _build_body(self):
        sp = QSplitter(Qt.Horizontal)

        # ---- 左：标签表单 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 6, 0)
        self.form_title = QLabel("（未选择文件）")
        self.form_title.setStyleSheet("font-weight:bold;color:#2f3440;")
        lv.addWidget(self.form_title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.edits: dict[str, QLineEdit] = {}
        for key, label in EDIT_FIELDS:
            e = QLineEdit()
            e.textEdited.connect(lambda _t, k=key: self._on_edited(k))
            self.edits[key] = e
            form.addRow(label, e)

        self.track_edit = QLineEdit()
        self.track_edit.setMaximumWidth(60)
        self.track_edit.textEdited.connect(lambda _t: self._on_edited("track"))
        self.track_count_edit = QLineEdit()
        self.track_count_edit.setMaximumWidth(60)
        self.track_count_edit.textEdited.connect(lambda _t: self._on_edited("track_count"))
        row_td = QHBoxLayout()
        row_td.addWidget(self.track_edit)
        row_td.addWidget(QLabel("/"))
        row_td.addWidget(self.track_count_edit)
        row_td.addSpacing(18)
        self.disc_edit = QLineEdit()
        self.disc_edit.setMaximumWidth(60)
        self.disc_edit.textEdited.connect(lambda _t: self._on_edited("disc"))
        self.disc_count_edit = QLineEdit()
        self.disc_count_edit.setMaximumWidth(60)
        self.disc_count_edit.textEdited.connect(lambda _t: self._on_edited("disc_count"))
        row_td.addWidget(QLabel("碟号"))
        row_td.addWidget(self.disc_edit)
        row_td.addWidget(QLabel("/"))
        row_td.addWidget(self.disc_count_edit)
        row_td.addStretch(1)
        form.addRow("音轨", row_td)

        for key, label in EDIT_FIELDS_TAIL:
            e = QLineEdit()
            e.textEdited.connect(lambda _t, k=key: self._on_edited(k))
            self.edits[key] = e
            form.addRow(label, e)

        lyr_row = QHBoxLayout()
        lyr_row.setContentsMargins(0, 0, 0, 0)
        self.lyr_edit = QPlainTextEdit()
        self.lyr_edit.setMaximumHeight(150)
        self.lyr_edit.textChanged.connect(lambda: self._on_edited("lyrics"))
        lyr_row.addWidget(self.lyr_edit, 1)
        lyr_side = QVBoxLayout()
        lyr_btn = QPushButton("⛶\n展开\n编辑")
        lyr_btn.setFixedWidth(52)
        lyr_btn.setToolTip("打开二级窗口查看/编辑完整歌词")
        lyr_btn.clicked.connect(self._edit_lyrics_dialog)
        lyr_side.addWidget(lyr_btn)
        lyr_side.addStretch(1)
        lyr_row.addLayout(lyr_side)
        form.addRow("歌词", lyr_row)
        lv.addLayout(form)

        # 封面
        cov_row = QHBoxLayout()
        self.cov_label = QLabel()
        self.cov_label.setFixedSize(140, 140)
        self.cov_label.setAlignment(Qt.AlignCenter)
        self.cov_label.setStyleSheet("border:1px solid #c3cad6;background:#ffffff;")
        self.cov_label.setText("无封面")
        cov_row.addWidget(self.cov_label)
        cv = QVBoxLayout()
        self.cov_info = QLabel("")
        self.cov_info.setStyleSheet("color:#888;")
        cv.addWidget(self.cov_info)
        for text, cb, tip in [
            ("🖼 更换封面", self._cover_set, "把选中图片设为前置封面"),
            ("🗂 图片管理", self._manage_pictures, "查看/添加/移除多张内嵌图片"),
            ("💾 导出封面", self._cover_export, "导出当前前置封面"),
            ("✖ 移除封面", self._cover_del, "移除所有前置封面图片"),
        ]:
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(cb)
            cv.addWidget(b)
        cv.addStretch(1)
        cov_row.addLayout(cv, 1)
        lv.addLayout(cov_row)
        lv.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(left)
        scroll.setMinimumWidth(390)
        sp.addWidget(scroll)

        # ---- 右：文件表格 ----
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels([c[1] for c in COLUMNS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSortingEnabled(False)
        self.table.setAlternatingRowColors(False)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.installEventFilter(self)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._row_menu)
        header = self.table.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._column_menu)
        header.sectionClicked.connect(self._sort_by_column)
        header.setSectionResizeMode(QHeaderView.Interactive)
        for i, (_key, _label, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(i, width)
        self._apply_column_visibility()
        sp.addWidget(self.table)
        sp.setStretchFactor(1, 4)
        sp.setSizes([430, 900])
        return sp

    def _build_statusbar(self):
        w = QWidget()
        row = QHBoxLayout(w)
        row.setContentsMargins(0, 0, 0, 0)
        self.status = QLabel("添加音频文件开始编辑（FLAC/MP3/M4A/OGG/OPUS/WAV/APE/WV/WMA/AIFF/DSF 等）")
        self.status.setStyleSheet("color:#666;")
        row.addWidget(self.status, 1)
        row.addWidget(QLabel("过滤："))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("按文件名/标题/艺术家/专辑过滤")
        self.filter_edit.setFixedWidth(220)
        self.filter_edit.textChanged.connect(self._apply_filter)
        row.addWidget(self.filter_edit)
        return w

    # ==================== 列表 ====================
    def _add_files(self):
        audio_filter = " ".join("*" + e for e in sorted(tags_io.SUPPORTED_EXT))
        enc_filter = " ".join("*" + e for e in sorted(ENCRYPTED_EXTS))
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择音频或加密音乐文件", "",
            f"音频 / 加密音乐 ({audio_filter} {enc_filter});;"
            f"音频文件 ({audio_filter});;所有文件 (*.*)")
        self._accept_paths(files)

    def _add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "选择文件夹")
        if not d:
            return
        files = []
        for root, _dirs, names in os.walk(d):
            for name in names:
                ext = os.path.splitext(name)[1].lower()
                if ext in tags_io.SUPPORTED_EXT or ext in ENCRYPTED_EXTS:
                    files.append(os.path.join(root, name))
        if not files:
            self.app.toast("该文件夹内没有音频或加密音乐文件", "warning")
            return
        self._accept_paths(files)

    def add_dropped_files(self, files: list[str]):
        self._accept_paths(files)

    def add_dropped_folder(self, path: str):
        if not os.path.isdir(path):
            return
        files = []
        for root, _dirs, names in os.walk(path):
            for name in names:
                ext = os.path.splitext(name)[1].lower()
                if ext in tags_io.SUPPORTED_EXT or ext in ENCRYPTED_EXTS:
                    files.append(os.path.join(root, name))
        if not files:
            self.app.toast("该文件夹内没有音频或加密音乐文件", "warning")
            return
        self._accept_paths(files)

    def _accept_paths(self, paths: list[str]):
        """分离普通音频与加密音乐：音频直接入列，加密文件显示顶部解密按钮。"""
        audio = [p for p in paths
                 if os.path.splitext(p)[1].lower() in tags_io.SUPPORTED_EXT]
        encrypted = [p for p in paths
                     if os.path.splitext(p)[1].lower() in ENCRYPTED_EXTS]
        if audio:
            self._load_files(audio)
        if encrypted:
            for p in encrypted:
                if p not in self._pending_encrypted:
                    self._pending_encrypted.append(p)
            self._update_decrypt_bar()
        if not audio and not encrypted:
            self.app.toast("没有可处理的音频或加密音乐文件", "warning")

    def _load_files(self, files: list[str]):
        existing = {r["path"] for r in self._rows}
        files = [f for f in files if f not in existing]
        if not files:
            return
        self.status.setText(f"读取 {len(files)} 个文件的标签……")
        connector = self._settings.get("connector") or "/"

        def job():
            out = []
            for p in files:
                info = tags_io.probe(p)
                tags = tags_io.load_tags(p, connector)
                out.append({
                    "path": p, "info": info, "tags": tags,
                    "pictures": tags_io.load_pictures(p),
                    "pictures_dirty": False, "dirty": False,
                    "history_snapshot": tag_history.snapshot(tags),
                })
            return out

        def done(rows, err):
            if err:
                self.app.toast(f"读取失败：{err}", "error")
                return
            existing_paths = {r["path"] for r in self._rows}
            for r in rows:
                if r["path"] in existing_paths:
                    continue
                existing_paths.add(r["path"])
                self._rows.append(r)
                self._insert_table_row(len(self._rows) - 1)
            self._update_status()

        run_bg(job, done)

    def _cell_value(self, r: dict, key: str) -> str:
        tags = r["tags"]
        info = r["info"]
        if key == "filename":
            return os.path.basename(r["path"])
        if key == "filedir":
            return os.path.dirname(r["path"])
        if key == "tagtypes":
            return info.get("tagtypes", "")
        if key == "trackstr":
            track = str(tags.get("track") or "")
            total = str(tags.get("track_count") or "")
            return f"{track}/{total}" if track and total else track
        if key == "discstr":
            disc = str(tags.get("disc") or "")
            total = str(tags.get("disc_count") or "")
            return f"{disc}/{total}" if disc and total else disc
        if key == "haspicture":
            return "√" if r.get("pictures") else ""
        if key == "lyrics":
            text = str(tags.get("lyrics") or "").replace("\n", " ")
            return text[:20]
        if key == "bitpersample":
            bits = int(info.get("bits") or 0)
            return f"{bits}bit" if bits else ""
        if key == "channels":
            return str(info.get("channels") or "")
        if key == "samplerate":
            sr = int(info.get("sr") or 0)
            return f"{sr}Hz" if sr else ""
        if key == "bitrate":
            br = int(info.get("bitrate") or 0)
            return f"{br // 1000}kbps" if br else ""
        if key == "durationinms":
            return tags_io.fmt_duration_ms(int(info.get("duration") or 0) * 1000)
        if key == "updatetime":
            try:
                return __import__("time").strftime(
                    "%Y-%m-%d %H:%M", __import__("time").localtime(os.path.getmtime(r["path"])))
            except OSError:
                return ""
        return str(tags.get(key) or "")

    def _insert_table_row(self, i: int):
        self.table.insertRow(i)
        for c, (key, _label, _width) in enumerate(COLUMNS):
            text = self._cell_value(self._rows[i], key)
            if key == "filename" and self._rows[i].get("dirty"):
                text = "● " + text
            item = QTableWidgetItem(text)
            if key in ("filename", "filedir"):
                item.setToolTip(self._rows[i]["path"])
            self.table.setItem(i, c, item)

    def _refresh_row_cells(self, i: int):
        if i < 0 or i >= len(self._rows):
            return
        for c, (key, _label, _width) in enumerate(COLUMNS):
            text = self._cell_value(self._rows[i], key)
            if key == "filename" and self._rows[i].get("dirty"):
                text = "● " + text
            item = self.table.item(i, c)
            if item is None:
                item = QTableWidgetItem()
                self.table.setItem(i, c, item)
            if item.text() != text:
                item.setText(text)
        self._apply_filter(self.filter_edit.text())

    def _mark_row(self, i: int):
        self._refresh_row_cells(i)

    def _rebuild_table(self, selected_paths: set[str] | None = None):
        selected_paths = selected_paths or set()
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(0)
        for i in range(len(self._rows)):
            self._insert_table_row(i)
            if self._rows[i]["path"] in selected_paths:
                self.table.selectRow(i)
        self.table.setUpdatesEnabled(True)
        self._update_status()

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
            self.track_edit.clear()
            self.track_count_edit.clear()
            self.disc_edit.clear()
            self.disc_count_edit.clear()
            self.lyr_edit.blockSignals(True)
            self.lyr_edit.clear()
            self.lyr_edit.blockSignals(False)
            self._show_cover(None)
            return
        self.form_title.setText(f"{os.path.basename(r['path'])}（{r['info'].get('fmt', '')}）")
        tags = r["tags"]
        for key, e in self.edits.items():
            e.setText(tags.get(key, ""))
        self.track_edit.setText(str(tags.get("track") or ""))
        self.track_count_edit.setText(str(tags.get("track_count") or ""))
        self.disc_edit.setText(str(tags.get("disc") or ""))
        self.disc_count_edit.setText(str(tags.get("disc_count") or ""))
        self.lyr_edit.blockSignals(True)
        self.lyr_edit.setPlainText(str(tags.get("lyrics") or ""))
        self.lyr_edit.blockSignals(False)
        self._show_cover(r)

    def _show_cover(self, r: dict | None):
        pics = (r or {}).get("pictures") or []
        front = next((p for p in pics if p.type == 3), pics[0] if pics else None)
        if not front:
            self.cov_label.setPixmap(QPixmap())
            self.cov_label.setText("无封面")
            self.cov_info.setText("")
            return
        data = front.data
        pm = QPixmap()
        pm.loadFromData(data)
        self.cov_label.setText("")
        self.cov_label.setPixmap(pm.scaled(138, 138, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        try:
            from PIL import Image
            from io import BytesIO
            img = Image.open(BytesIO(data))
            w, h = img.size
        except Exception:
            w = h = 0
        ext = "PNG" if "png" in (front.mime or "") else "JPEG"
        self.cov_info.setText(f"{ext}\n{w}×{h}\n{len(data) / 1024:.1f} kB\n共 {len(pics)} 张")

    # ==================== 编辑 / 保存 ====================
    def _on_edited(self, key: str):
        if not self._edit_targets:
            return
        if key in ("title", "artist", "album", "albumartist", "year", "genre",
                   "composer", "lyricist", "comment"):
            value = self.edits[key].text().strip()
        elif key == "track":
            value = self.track_edit.text().strip()
        elif key == "track_count":
            value = self.track_count_edit.text().strip()
        elif key == "disc":
            value = self.disc_edit.text().strip()
        elif key == "disc_count":
            value = self.disc_count_edit.text().strip()
        elif key == "lyrics":
            value = self.lyr_edit.toPlainText()
        else:
            return
        for i in self._edit_targets:
            self._rows[i]["tags"][key] = value
            self._rows[i]["dirty"] = True
            self._mark_row(i)
        self._update_status()

    def _save_rows(self, idxs: list[int]):
        rows = [(i, self._rows[i]) for i in idxs if self._rows[i].get("dirty")]
        if not rows:
            self.app.toast("没有已修改的文件", "info")
            return
        id3_version = int(self._settings.get("id3_version", 3) or 3)

        def job():
            ok, failed = 0, []
            for i, r in rows:
                try:
                    pictures = r["pictures"] if r.get("pictures_dirty") else None
                    tags_io.save_tags(r["path"], r["tags"], id3_version=id3_version,
                                      pictures=pictures)
                    tag_history.record(r["path"], r.get("history_snapshot") or {}, r["tags"])
                    r["history_snapshot"] = tag_history.snapshot(r["tags"])
                    r["pictures_dirty"] = False
                    r["dirty"] = False
                    r["info"] = tags_io.probe(r["path"])
                    ok += 1
                except Exception as exc:  # noqa: BLE001
                    failed.append(f"{os.path.basename(r['path'])}: {exc}")
            return ok, failed, idxs

        def done(result, err):
            if err:
                self.app.toast(f"保存失败：{err}", "error")
                return
            ok, failed, saved_idx = result
            for i in saved_idx:
                self._refresh_row_cells(i)
            if failed:
                self.status.setText(f"保存完成 {ok} 个，失败 {len(failed)} 个：" + "；".join(failed[:3]))
                self.app.toast(f"{ok} 成功 / {len(failed)} 失败", "warning")
            else:
                self.status.setText(f"已保存 {ok} 个文件 ✓")
                self.app.toast(f"已保存 {ok} 个文件", "success")
            self._update_status()

        run_bg(job, done)

    def _save_selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要保存的文件行", "warning")
            return
        self._save_rows(rows)

    def _save_all(self):
        self._save_rows(list(range(len(self._rows))))

    def _reload_selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要重新读取的文件行", "warning")
            return
        if any(self._rows[i].get("dirty") for i in rows):
            if QMessageBox.question(
                    self, "重新读取",
                    "选中文件有未保存修改，重新读取会丢弃这些修改，继续？"
                    ) != QMessageBox.StandardButton.Yes:
                return
        connector = self._settings.get("connector") or "/"

        def job():
            out = []
            for i in rows:
                p = self._rows[i]["path"]
                tags = tags_io.load_tags(p, connector)
                out.append((i, tags_io.probe(p), tags, tags_io.load_pictures(p), tags))
            return out

        def done(result, err):
            if err:
                self.app.toast(f"重新读取失败：{err}", "error")
                return
            for i, info, tags, pics, snap in result:
                r = self._rows[i]
                r.update({"info": info, "tags": tags, "pictures": pics,
                          "pictures_dirty": False, "dirty": False,
                          "history_snapshot": tag_history.snapshot(snap)})
                self._refresh_row_cells(i)
            if self._edit_targets:
                self._fill_form(self._rows[self._edit_targets[0]])
            self.app.toast(f"已重新读取 {len(result)} 个文件", "success")

        run_bg(job, done)

    def _remove_selected(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()}, reverse=True)
        for i in rows:
            if self._rows[i].get("dirty"):
                self.app.toast(f"{os.path.basename(self._rows[i]['path'])} 有未保存修改，请先保存或放弃",
                               "warning")
                return
        for i in rows:
            self.table.removeRow(i)
            self._rows.pop(i)
        self._edit_targets = []
        self._fill_form(None)
        self._update_status()

    def eventFilter(self, obj, ev):
        if obj is self.table and ev.type() == QEvent.KeyPress:
            if ev.key() in (Qt.Key_Delete, Qt.Key_Backspace) and \
                    self.table.selectionModel().selectedRows():
                self._remove_selected()
                return True
        return super().eventFilter(obj, ev)

    def _clear_tags(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要清除标签的文件行", "warning")
            return
        if QMessageBox.question(
                self, "清除标签",
                f"将清空 {len(rows)} 个文件的全部标签与封面（保存后从文件中删除），继续？"
                ) != QMessageBox.StandardButton.Yes:
            return
        for i in rows:
            r = self._rows[i]
            for key in tags_io.FIELDS:
                r["tags"][key] = ""
            r["pictures"] = []
            r["pictures_dirty"] = True
            r["dirty"] = True
            self._mark_row(i)
        if self._edit_targets:
            self._fill_form(self._rows[self._edit_targets[0]])
        self._update_status()
        self.app.toast(f"已清空 {len(rows)} 个文件的标签（记得保存）", "success")

    def _clear_list(self):
        dirty = [r for r in self._rows if r.get("dirty")]
        if dirty:
            self.app.toast(f"有 {len(dirty)} 个文件未保存，请先全部保存", "warning")
            return
        self.table.setRowCount(0)
        self._rows = []
        self._edit_targets = []
        self._fill_form(None)
        self._update_status()

    # ==================== 封面 ====================
    def _current_row(self) -> int | None:
        return self._edit_targets[0] if self._edit_targets else None

    def _set_pictures(self, pictures: list[tags_io.Pic], row_index: int | None = None):
        targets = [row_index] if row_index is not None else list(self._edit_targets)
        if not targets:
            return
        for i in targets:
            self._rows[i]["pictures"] = [tags_io.Pic(p.data, p.mime, p.type, p.desc)
                                         for p in pictures]
            self._rows[i]["pictures_dirty"] = True
            self._rows[i]["dirty"] = True
            self._mark_row(i)
        self._show_cover(self._rows[targets[0]] if targets else None)
        self._update_status()

    def _cover_set(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        f, _ = QFileDialog.getOpenFileName(self, "选择封面图片", "",
                                           "图片 (*.jpg *.jpeg *.png *.gif *.webp *.bmp)")
        if not f:
            return
        try:
            data = open(f, "rb").read()
        except OSError as exc:
            self.app.toast(f"读取图片失败：{exc}", "error")
            return
        pic = tags_io.Pic(data, "", 3, "")
        for j in self._edit_targets:
            pics = [p for p in self._rows[j]["pictures"] if p.type != 3]
            pics.insert(0, tags_io.Pic(pic.data, pic.mime, 3, ""))
            self._rows[j]["pictures"] = pics
            self._rows[j]["pictures_dirty"] = True
            self._rows[j]["dirty"] = True
            self._mark_row(j)
        self._show_cover(self._rows[i])
        self._update_status()

    def _cover_export(self):
        i = self._current_row()
        if i is None:
            return
        pics = self._rows[i].get("pictures") or []
        front = next((p for p in pics if p.type == 3), pics[0] if pics else None)
        if not front:
            self.app.toast("当前文件没有内嵌封面", "info")
            return
        ext = ".png" if "png" in (front.mime or "") else ".jpg"
        base = os.path.splitext(os.path.basename(self._rows[i]["path"]))[0]
        f, _ = QFileDialog.getSaveFileName(self, "导出封面", base + ext)
        if f:
            with open(f, "wb") as fp:
                fp.write(front.data)
            self.app.toast("封面已导出", "success")

    def _cover_del(self):
        if not self._edit_targets:
            return
        for j in self._edit_targets:
            self._rows[j]["pictures"] = [p for p in self._rows[j]["pictures"] if p.type != 3]
            self._rows[j]["pictures_dirty"] = True
            self._rows[j]["dirty"] = True
            self._mark_row(j)
        self._show_cover(self._rows[self._edit_targets[0]])
        self._update_status()

    def _manage_pictures(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        dlg = PicturesDialog(self, self._rows[i].get("pictures") or [],
                             os.path.basename(self._rows[i]["path"]))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._set_pictures(dlg.result_pictures)

    # ==================== 网络匹配 ====================
    def _net_match(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        dlg = CombinedSearchDialog(self, self._rows[i])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = getattr(dlg, "result_data", None) or {}
        r = self._rows[i]
        for key, value in (data.get("tags") or {}).items():
            r["tags"][key] = value
        if data.get("cover"):
            pics = [p for p in r["pictures"] if p.type != 3]
            pics.insert(0, tags_io.Pic(data["cover"], data.get("cover_mime") or "image/jpeg", 3, ""))
            r["pictures"] = pics
            r["pictures_dirty"] = True
        r["dirty"] = True
        self._refresh_row_cells(i)
        self._fill_form(r)
        self._update_status()
        self.app.toast(f"已填充「{data.get('title', '')}」的标签（记得保存）", "success")

    def _auto_match(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要批量匹配的文件行", "warning")
            return
        dlg = AutoMatchDialog(self, [self._rows[i] for i in rows], self._settings)
        dlg.exec()
        self._settings.update(dlg.current_settings())
        _save_settings(self._settings)
        for index, update in dlg.updated_rows:
            if index < 0 or index >= len(rows):
                continue
            i = rows[index]
            r = self._rows[i]
            r["tags"].update(update.get("tags") or {})
            if update.get("pictures_dirty"):
                r["pictures"] = update.get("pictures") or []
                r["pictures_dirty"] = True
            r["dirty"] = True
            self._refresh_row_cells(i)
        if dlg.updated_rows:
            if self._edit_targets:
                self._fill_form(self._rows[self._edit_targets[0]])
            self._update_status()
            self.app.toast(f"自动匹配完成 {len(dlg.updated_rows)} 个文件（记得保存）", "success")

    def _cover_search(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        dlg = CoverSearchDialog(self, self._rows[i])
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.cover:
            r = self._rows[i]
            pics = [p for p in r["pictures"] if p.type != 3]
            pics.insert(0, tags_io.Pic(dlg.cover, dlg.cover_mime or "image/jpeg", 3, ""))
            r["pictures"] = pics
            r["pictures_dirty"] = True
            r["dirty"] = True
            self._refresh_row_cells(i)
            self._show_cover(r)
            self._update_status()
            self.app.toast("封面已套用（记得保存）", "success")

    def _lyric_search(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        dlg = LyricSearchDialog(self, self._rows[i])
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.lyric:
            for j in self._edit_targets:
                self._rows[j]["tags"]["lyrics"] = dlg.lyric
                self._rows[j]["dirty"] = True
                self._mark_row(j)
            self.lyr_edit.blockSignals(True)
            self.lyr_edit.setPlainText(dlg.lyric)
            self.lyr_edit.blockSignals(False)
            self._update_status()
            self.app.toast("歌词已套用（记得保存）", "success")

    # ==================== 歌词编辑 ====================
    def _edit_lyrics_dialog(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"歌词编辑 — {os.path.basename(self._rows[i]['path'])}")
        dlg.resize(820, 600)
        v = QVBoxLayout(dlg)
        row = QHBoxLayout()
        row.addWidget(QPushButton("📥 导入 .lrc", clicked=lambda: self._import_lrc(edit)))
        self._lyric_dialog_info = QLabel("")
        self._lyric_dialog_info.setStyleSheet("color:#888;")
        row.addWidget(self._lyric_dialog_info, 1)
        v.addLayout(row)
        edit = QPlainTextEdit(str(self._rows[i]["tags"].get("lyrics") or ""))
        f = edit.font()
        f.setFamily("Consolas")
        edit.setFont(f)
        edit.textChanged.connect(lambda: self._lyric_dialog_info.setText(
            f"{len(edit.toPlainText())} 字符 · {edit.toPlainText().count(chr(10)) + 1} 行"))
        v.addWidget(edit, 1)
        row2 = QHBoxLayout()
        row2.addStretch(1)
        ok = QPushButton("✔ 写回表单")
        ok.setStyleSheet("background:#2e7d32;color:white;padding:5px 18px;")
        ok.clicked.connect(dlg.accept)
        row2.addWidget(ok)
        row2.addWidget(QPushButton("取消", clicked=dlg.reject))
        v.addLayout(row2)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = edit.toPlainText()
        for j in self._edit_targets:
            self._rows[j]["tags"]["lyrics"] = text
            self._rows[j]["dirty"] = True
            self._mark_row(j)
        self.lyr_edit.blockSignals(True)
        self.lyr_edit.setPlainText(text)
        self.lyr_edit.blockSignals(False)
        self._update_status()
        self.app.toast(f"歌词已写回 {len(self._edit_targets)} 个文件（记得保存）", "success")

    def _import_lrc(self, edit: QPlainTextEdit):
        f, _ = QFileDialog.getOpenFileName(self, "选择歌词文件", "",
                                           "歌词 (*.lrc *.txt);;所有文件 (*.*)")
        if not f:
            return
        try:
            text = open(f, "r", encoding="utf-8", errors="replace").read()
        except OSError as exc:
            self.app.toast(f"读取失败：{exc}", "error")
            return
        edit.setPlainText(text)

    # ==================== 表格 / 搜索 / 修复 ====================
    def _xlsx_import(self):
        if not self._rows:
            self.app.toast("请先添加音频文件，再导入表格匹配", "warning")
            return
        f, _ = QFileDialog.getOpenFileName(self, "选择 XLSX 表格", "",
                                           "表格 (*.xlsx *.xlsm)")
        if not f:
            return

        def job():
            from openpyxl import load_workbook
            wb = load_workbook(f, read_only=True, data_only=True)
            try:
                ws = wb.active
                matrix = [[_cell_str(c) for c in row]
                          for row in ws.iter_rows(values_only=True)]
            finally:
                wb.close()
            while matrix and not any(matrix[0]):
                matrix.pop(0)
            if not matrix:
                raise ValueError("表格为空")
            headers = matrix[0]
            data = [(r + [""] * max(0, len(headers) - len(r)))[:len(headers)]
                    for r in matrix[1:] if any(r)]
            if not data:
                raise ValueError("表格没有数据行")
            return headers, data

        def done(res, err):
            if err:
                self.app.toast(f"读取表格失败：{err}", "error")
                return
            headers, data = res
            dlg = _XlsxDialog(self, headers, data, self._rows)
            if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.applied:
                return
            for i in range(len(self._rows)):
                self._mark_row(i)
            if self._edit_targets:
                self._fill_form(self._rows[self._edit_targets[0]])
            self._update_status()
            self.app.toast(f"已按表格填充 {dlg.applied} 个文件（记得保存）", "success")

        run_bg(job, done)

    def _batch_rename(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要改名的文件行", "warning")
            return
        dlg = BatchRenameDialog(self, [self._rows[i] for i in rows])
        dlg.exec()
        if not getattr(dlg, "renamed", None):
            return
        mapping = dict(dlg.renamed)
        for i in rows:
            old = self._rows[i]["path"]
            new = mapping.get(old)
            if new:
                self._rows[i]["path"] = new
                self._refresh_row_cells(i)
        self._fill_form(self._rows[rows[0]])
        self.app.toast(f"已改名 {len(dlg.renamed)} 个文件", "success")

    def _find_replace(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要查找替换的文件行", "warning")
            return
        dlg = FindReplaceDialog(self, [self._rows[i] for i in rows])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for local_i, tags in dlg.result_updates.items():
            i = rows[local_i]
            self._rows[i]["tags"].update(tags)
            self._rows[i]["dirty"] = True
            self._refresh_row_cells(i)
        if self._edit_targets:
            self._fill_form(self._rows[self._edit_targets[0]])
        self.app.toast(f"已修改 {len(dlg.result_updates)} 个文件（记得保存）", "success")

    def _encoding_fix(self):
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            self.app.toast("请先选中要修复编码的文件行", "warning")
            return
        dlg = EncodingFixDialog(self, [self._rows[i] for i in rows])
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        for local_i, (key, value) in dlg.updates.items():
            i = rows[local_i]
            self._rows[i]["tags"][key] = value
            self._rows[i]["dirty"] = True
            self._refresh_row_cells(i)
        if self._edit_targets:
            self._fill_form(self._rows[self._edit_targets[0]])
        self.app.toast(f"已修复 {len(dlg.updates)} 个文件（记得保存）", "success")

    def _history(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        dlg = HistoryDialog(self, self._rows[i]["path"])
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.restored:
            return
        r = self._rows[i]
        for key, value in dlg.restored.items():
            if key in tags_io.FIELDS:
                r["tags"][key] = value
        r["dirty"] = True
        self._refresh_row_cells(i)
        self._fill_form(r)
        self._update_status()
        self.app.toast("已恢复历史标签（记得保存）", "success")

    def _copy_tags(self):
        i = self._current_row()
        if i is None:
            self.app.toast("请先选中一个文件", "warning")
            return
        r = self._rows[i]
        self._clipboard = ({k: str(v or "") for k, v in r["tags"].items()},
                           [tags_io.Pic(p.data, p.mime, p.type, p.desc) for p in r["pictures"]])
        self.app.toast("已复制标签与图片", "success")

    def _paste_tags(self):
        if not self._clipboard or not self._edit_targets:
            self.app.toast("剪贴板为空或未选中文件", "warning")
            return
        tags, pictures = self._clipboard
        for i in self._edit_targets:
            self._rows[i]["tags"].update(tags)
            self._rows[i]["pictures"] = [tags_io.Pic(p.data, p.mime, p.type, p.desc) for p in pictures]
            self._rows[i]["pictures_dirty"] = True
            self._rows[i]["dirty"] = True
            self._mark_row(i)
        self._fill_form(self._rows[self._edit_targets[0]])
        self._update_status()
        self.app.toast(f"已粘贴到 {len(self._edit_targets)} 个文件（记得保存）", "success")

    def _open_settings(self):
        dlg = TagSettingsDialog(self, dict(self._settings))
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._settings.update(dlg.settings)
            _save_settings(self._settings)
            self.app.toast("设置已保存", "success")

    # ==================== 列 / 排序 ====================
    def _row_menu(self, pos):
        if not self._edit_targets:
            return
        menu = QMenu(self)
        menu.addAction("🌐 网络匹配", self._net_match)
        menu.addAction("⚡ 自动匹配", self._auto_match)
        menu.addAction("🖼 封面搜索", self._cover_search)
        menu.addAction("🎵 歌词搜索", self._lyric_search)
        menu.addSeparator()
        menu.addAction("📋 复制标签", self._copy_tags)
        menu.addAction("📌 粘贴标签", self._paste_tags)
        menu.addAction("🕘 标签历史", self._history)
        menu.addSeparator()
        menu.addAction("💾 保存选中", self._save_selected)
        menu.addAction("✖ 移除选中", self._remove_selected)
        menu.addAction("🗑 清除标签", self._clear_tags)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _column_menu(self, pos):
        menu = QMenu(self)
        visible = self._settings.get("columns") or {}
        for i, (key, label, _width) in enumerate(COLUMNS):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(visible.get(key, True) is not False)
            action.triggered.connect(lambda checked, k=key: self._toggle_column(k, checked))
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("显示全部列", lambda: self._toggle_all_columns(True))
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _toggle_column(self, key: str, visible: bool):
        cols = self._settings.setdefault("columns", {})
        cols[key] = bool(visible)
        _save_settings(self._settings)
        self._apply_column_visibility()

    def _toggle_all_columns(self, visible: bool):
        cols = self._settings.setdefault("columns", {})
        for key, _label, _width in COLUMNS:
            cols[key] = bool(visible)
        _save_settings(self._settings)
        self._apply_column_visibility()

    def _apply_column_visibility(self):
        cols = self._settings.get("columns") or {}
        for i, (key, _label, _width) in enumerate(COLUMNS):
            self.table.setColumnHidden(i, cols.get(key, True) is False)

    def _sort_by_column(self, col: int):
        if col < 0 or col >= len(COLUMNS):
            return
        selected_paths = {self._rows[i]["path"] for i in self._edit_targets}
        if self._sort_col == col:
            self._sort_desc = not self._sort_desc
        else:
            self._sort_col = col
            self._sort_desc = False
        key = COLUMNS[col][0]

        def natural(text):
            return [int(p) if p.isdigit() else p.lower()
                    for p in re.split(r"(\d+)", str(text or ""))]

        def sort_key(r):
            if key in ("year", "trackstr", "discstr", "bitpersample", "channels",
                       "samplerate", "bitrate", "durationinms"):
                text = self._cell_value(r, key)
                m = re.search(r"\d+(?:\.\d+)?", text)
                return float(m.group()) if m else -1
            if key == "updatetime":
                try:
                    return os.path.getmtime(r["path"])
                except OSError:
                    return 0
            return natural(self._cell_value(r, key))

        self._rows.sort(key=sort_key, reverse=self._sort_desc)
        self._edit_targets = []
        self._rebuild_table(selected_paths)

    # ==================== 状态 ====================
    def _apply_filter(self, text):
        text = str(text or "").strip().lower()
        for i, r in enumerate(self._rows):
            hay = f"{os.path.basename(r['path'])} {r['tags'].get('title', '')} " \
                  f"{r['tags'].get('artist', '')} {r['tags'].get('album', '')}".lower()
            self.table.setRowHidden(i, bool(text) and text not in hay)

    def _update_status(self):
        n = len(self._rows)
        dirty = sum(1 for r in self._rows if r.get("dirty"))
        sel = self.table.selectionModel().selectedRows() if hasattr(self, "table") else []
        if sel:
            rows = [self._rows[i.row()] for i in sel if i.row() < len(self._rows)]
            dur = tags_io.fmt_duration(sum(int(r["info"].get("duration") or 0) for r in rows))
            size = 0
            for r in rows:
                try:
                    size += os.path.getsize(r["path"])
                except OSError:
                    pass
            tail = f"｜选中 {len(rows)}：{dur} · {size / 1048576:.1f} MB"
        else:
            tail = ""
        self.status.setText(f"共 {n} 个文件，{dirty} 个已修改（●）{tail}")
