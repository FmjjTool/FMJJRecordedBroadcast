"""腾讯文档 · 中间层同步器（内嵌在「腾讯文档监控」的「中间层同步」页签）。

比对本地「已获取」/增量数据库与已同步的腾讯表格，把尚未上传的条目按
「官方动态」列标准整理成表格：可一键复制 TSV 手工粘贴，也可在勾选确认后
连同本地图片上传到腾讯文档（仅限官方动态 / 直播记录，需用户点击确认）。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QTimer, QAbstractTableModel
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFormLayout,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QStackedWidget, QTableView,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import common
from gui_qt.common import run_bg
from gui_qt.pages.base import BasePage
from gui_qt.skeleton import LoadingSkeleton
from toolbox import tcdoc as tc_engine
from toolbox.config_manager import load_convert_config, save_convert_config
from toolbox.tcdoc import bridge

logger = logging.getLogger(__name__)

_DAYS = (("近 7 天", 7), ("近 30 天", 30), ("近 90 天", 90), ("全部历史", 0))
_UPLOAD_LIMIT = 50
_DISPLAY_COLUMNS = list(bridge.COLUMNS) + ["图片"]
_WIDTHS = {
    "作品标题": 240, "发布距今": 90, "作品内容": 240, "发布账号": 110,
    "封面图": 80, "动态类型": 90, "发布时间": 140, "发布平台": 90,
    "发布位置": 80, "作品时长": 90, "作品链接": 220, "动态链接(b站)": 220,
    "歌曲名称": 240, "演唱者": 110, "完整歌词": 240, "歌曲时长": 90,
    "作词人": 130, "作曲人": 130, "出品方": 130, "首发日期": 110,
    "语言": 110, "合唱形式": 90, "所属专辑": 160,
    "QQ音乐": 200, "网易云音乐": 200, "酷狗音乐": 200, "酷我音乐": 200,
    "5sing": 180, "汽水音乐": 200,
    "图片": 90,
}


class _PendingModel(QAbstractTableModel):
    """保留全部行，只为可见单元格生成显示文本。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.columns = list(_DISPLAY_COLUMNS)

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def set_columns(self, columns):
        self.beginResetModel()
        self.columns = list(columns)
        self.endResetModel()

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        row = self.rows[index.row()]
        column = self.columns[index.column()]
        if column == "图片":
            images = row.get("_images") or []
            if role == Qt.DisplayRole:
                return f"有 {len(images)} 张" if images else "无"
            return "\n".join(images[:20]) + ("\n…" if len(images) > 20 else "")
        value = str(row.get(column) or "")
        return value[:600] if role == Qt.ToolTipRole else value


class _UploadDialog(QDialog):
    """上传前的人工确认清单：默认全选，可逐行取消（复选框冗余保护）。"""

    def __init__(self, records, parent=None, profile="posts"):
        super().__init__(parent)
        self._records = list(records)
        self.setWindowTitle("上传到腾讯文档")
        self.resize(900, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        if profile == "songs":
            note = ("默认全选；取消勾选可跳过某些歌曲。上传会新增歌曲记录并上传封面图，"
                    "同名歌曲会自动跳过，不会修改或删除表内已有行。")
        else:
            note = ("默认全选；取消勾选可跳过某些行。上传会新增记录并把本地图片一起传到腾讯文档，"
                    "链接已存在的记录会自动跳过，不会修改或删除表内已有行。")
        tip = QLabel(note)
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888;")
        layout.addWidget(tip)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            ["上传", "标题", "账号", "平台", "时间", "图片"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        for index, width in enumerate((56, 380, 130, 90, 150, 120)):
            self.table.setColumnWidth(index, width)
        for record in self._records:
            self._add_row(record)
        layout.addWidget(self.table, 1)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        row = QHBoxLayout()
        self.all_btn = QPushButton("全选")
        self.none_btn = QPushButton("全不选")
        self.cancel_btn = QPushButton("取消")
        self.ok_btn = QPushButton("开始上传")
        self.ok_btn.setProperty("variant", "primary")
        row.addWidget(self.all_btn)
        row.addWidget(self.none_btn)
        row.addStretch(1)
        row.addWidget(self.cancel_btn)
        row.addWidget(self.ok_btn)
        layout.addLayout(row)

        self.all_btn.clicked.connect(lambda: self._set_all(True))
        self.none_btn.clicked.connect(lambda: self._set_all(False))
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self.accept)
        self.table.itemChanged.connect(lambda _item: self._refresh_summary())
        self._refresh_summary()

    def _add_row(self, record: dict) -> None:
        columns = record["columns"]
        row = self.table.rowCount()
        self.table.insertRow(row)
        blocked = str(record.get("blocked") or "")
        check = QTableWidgetItem()
        if blocked:
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Unchecked)
            check.setToolTip(f"不可上传：{blocked}")
        else:
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            check.setCheckState(Qt.Checked)
        self.table.setItem(row, 0, check)
        note = f"{len(record.get('images') or [])} 张"
        if record.get("missing_slots"):
            note += f"（缺 {len(record['missing_slots'])}）"
        if blocked:
            note += " · 不可上传"
        texts = [str(record.get("title") or columns.get("作品标题") or columns.get("歌曲名称") or ""),
                 str(record.get("account") or columns.get("发布账号") or columns.get("演唱者") or ""),
                 str(record.get("platform") or columns.get("发布平台") or ""),
                 str(record.get("time") or columns.get("发布时间") or columns.get("首发日期") or ""),
                 note]
        for index, text in enumerate(texts, start=1):
            item = QTableWidgetItem(text.replace("\n", " ")[:160])
            item.setToolTip(text[:1500])
            self.table.setItem(row, index, item)

    def _set_all(self, checked: bool) -> None:
        state = Qt.Checked if checked else Qt.Unchecked
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item is not None and item.flags() & Qt.ItemIsUserCheckable:
                item.setCheckState(state)
        self._refresh_summary()

    def selected_records(self) -> list[dict]:
        chosen = []
        for row, record in enumerate(self._records):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() == Qt.Checked:
                chosen.append(record)
        return chosen

    def _refresh_summary(self) -> None:
        chosen = self.selected_records()
        images = sum(len(r.get("images") or []) for r in chosen)
        gaps = sum(len(r.get("missing_slots") or []) for r in chosen)
        blocked = sum(1 for r in self._records if r.get("blocked"))
        note = f"；缺图 {gaps} 张会留空" if gaps else ""
        block_note = f"；不可上传 {blocked} 条（缺字段或选项）" if blocked else ""
        self.summary.setText(
            f"已选 {len(chosen)} / {len(self._records)} 条 · 图片 {images} 张{note}{block_note}")
        self.ok_btn.setEnabled(bool(chosen))


class TcDocBridgePanel(QWidget):
    # 后台任务看门狗：正常比对 <1 秒（全部历史约 3 秒），超时只用于解除卡住状态
    COMPARE_TIMEOUT_MS = 20000
    TARGETS_TIMEOUT_MS = 15000

    def __init__(self, parent=None, app=None, prefer_doc_key: str = "",
                 embedded: bool = False, defer_load: bool = False):
        super().__init__(parent)
        self.app = app or getattr(parent, "app", None)
        self._task_owner = parent
        ancestor = parent
        while ancestor is not None:
            if isinstance(ancestor, BasePage):
                self._task_owner = ancestor
                break
            ancestor = ancestor.parent()
        self._embedded = embedded
        self._targets: list[dict] = []
        self._result: dict | None = None
        self._rows: list[dict] = []
        self._account_names: list[str] = []
        self._account_actions: dict[str, object] = {}
        self._busy = False
        self._uploading = False
        self._upload_steps = 0
        self._upload_steps_total = 0
        self._upload_records = 0
        self._upload_images = 0
        self._upload_records_total = 0
        self._upload_images_total = 0
        self._image_busy = False
        self._image_temp_dir = None
        self._closed = False
        self._targets_loading = False
        self._targets_pending = None
        self._targets_req = 0
        self._compare_req = 0
        self._compare_owner = 0
        self._compare_pending = False
        self._saved_accounts = []
        self._account_checked: dict[str, bool] = {}
        self._songs_mode = False
        self._posts_days_index = None
        self._refreshing = False

        self._acc_timer = QTimer(self)
        self._acc_timer.setSingleShot(True)
        self._acc_timer.setInterval(250)
        self._acc_timer.timeout.connect(self._run_compare)
        self._compare_watchdog = QTimer(self)
        self._compare_watchdog.setSingleShot(True)
        self._compare_watchdog.timeout.connect(self._on_compare_watchdog)
        self._targets_watchdog = QTimer(self)
        self._targets_watchdog.setSingleShot(True)
        self._targets_watchdog.timeout.connect(self._on_targets_watchdog)

        self._build_ui()
        if not defer_load:
            self._load_targets(prefer_doc_key)

    # ==================== 界面 ====================

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(6)
        top.addWidget(QLabel("目标表格"))
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(340)
        self.target_combo.setToolTip("已同步到本地的腾讯表格（自动识别含官方动态字段的工作表）")
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        top.addWidget(self.target_combo, 2)

        top.addWidget(QLabel("时间"))
        self.days_combo = QComboBox()
        for label, days in _DAYS:
            self.days_combo.addItem(label, days)
        self.days_combo.setFixedWidth(100)
        self.days_combo.currentIndexChanged.connect(self._on_days_changed)
        top.addWidget(self.days_combo)

        top.addWidget(QLabel("账号"))
        self.account_btn = QPushButton("全部")
        self.account_btn.setMinimumWidth(130)
        self.account_btn.setToolTip("勾选要比对的发布账号；默认只比对腾讯表出现过的账号")
        self._account_menu = QMenu(self.account_btn)
        self.account_btn.setMenu(self._account_menu)
        top.addWidget(self.account_btn)

        self.compare_btn = QPushButton("重新比对")
        self.compare_btn.clicked.connect(self._run_compare)
        top.addWidget(self.compare_btn)

        self.manual_btn = QPushButton("手动录入")
        self.manual_btn.setToolTip(
            "为当前表手动新增一行；只生成可复制的 TSV，不会直接写入腾讯文档。"
            "图片列请用「复制图片」或到腾讯文档里补。")
        self.manual_btn.setVisible(False)
        self.manual_btn.clicked.connect(self._manual_entry)
        top.addWidget(self.manual_btn)

        self.refresh_sync_btn = QPushButton("同步前刷新")
        self.refresh_sync_btn.setToolTip(
            "上传前必须先刷新，保证数据清洁：\n"
            "1) 采集平台资料（信息获取 + 网媒账号追踪）\n"
            "2) 重新拉取一遍腾讯文档表格快照\n"
            "三步都成功后「上传到腾讯文档」才可用；上传完成或超过 60 分钟后需重新刷新。")
        self.refresh_sync_btn.clicked.connect(self._refresh_before_sync)
        top.addWidget(self.refresh_sync_btn)
        top.addStretch(1)

        self.upload_btn = QPushButton("上传到腾讯文档")
        self.upload_btn.setToolTip(
            "把清单中勾选的记录连同本地图片上传到腾讯文档（官方动态 / 直播记录可新增，"
            "完整歌曲仅新增、不改不删；需在弹窗里确认勾选，已存在的自动跳过）")
        self.upload_btn.setEnabled(False)
        self.upload_btn.clicked.connect(self._open_upload)
        top.addWidget(self.upload_btn)

        self.copy_btn = QPushButton("复制全部（TSV）")
        self.copy_btn.setProperty("variant", "primary")
        self.copy_btn.setToolTip("复制全部待同步行（不含表头），到腾讯文档表格末尾粘贴")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy)
        top.addWidget(self.copy_btn)
        layout.addLayout(top)

        self.song_search_row = QWidget(self)
        search_layout = QHBoxLayout(self.song_search_row)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(6)
        search_layout.addWidget(QLabel("歌曲检索"))
        self.song_singer_edit = QLineEdit()
        self.song_singer_edit.setPlaceholderText("歌手（默认封茗囧菌）")
        self.song_singer_edit.setFixedWidth(150)
        search_layout.addWidget(self.song_singer_edit)
        self.song_title_edit = QLineEdit()
        self.song_title_edit.setPlaceholderText("输入歌曲名，回车或多平台检索")
        self.song_title_edit.returnPressed.connect(self._search_song)
        search_layout.addWidget(self.song_title_edit, 1)
        self.song_search_btn = QPushButton("多平台检索")
        self.song_search_btn.clicked.connect(self._search_song)
        search_layout.addWidget(self.song_search_btn)
        self.song_search_row.setVisible(False)
        layout.addWidget(self.song_search_row)

        self.summary = QLabel("加载中…")
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet("font-weight: 600;")
        self.summary.setTextFormat(Qt.PlainText)
        layout.addWidget(self.summary)

        self.table = QTableView()
        self._model = _PendingModel(self.table)
        self.table.setModel(self._model)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        for index, name in enumerate(_DISPLAY_COLUMNS):
            self.table.setColumnWidth(index, _WIDTHS.get(name, 100))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.selectionModel().selectionChanged.connect(self._update_copy_row_btn)
        self._copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        self._copy_shortcut.activated.connect(self._copy_selected_rows)
        self._table_stack = QStackedWidget()
        self._table_stack.addWidget(self.table)
        self._skeleton = LoadingSkeleton(text="正在读取目标表格…")
        self._table_stack.addWidget(self._skeleton)
        layout.addWidget(self._table_stack, 1)

        self.progress_row = QWidget(self)
        progress_layout = QHBoxLayout(self.progress_row)
        progress_layout.setContentsMargins(0, 0, 0, 0)
        progress_layout.setSpacing(8)
        self.upload_progress = QProgressBar(self)
        self.upload_progress.setFixedHeight(14)
        self.upload_progress.setTextVisible(True)
        self.upload_progress.setFormat("%p%")
        progress_layout.addWidget(self.upload_progress, 1)
        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet("color: #888;")
        progress_layout.addWidget(self.progress_label)
        self.progress_row.setVisible(False)
        layout.addWidget(self.progress_row)

        bottom = QHBoxLayout()
        self.status = QLabel("选中行可单行复制；上传需点「上传到腾讯文档」并确认勾选。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: #888;")
        bottom.addWidget(self.status, 1)
        self.copy_images_btn = QPushButton("复制全部图片")
        self.copy_images_btn.setToolTip(
            "把检测到的图片下载后复制到剪贴板，可直接粘贴到腾讯文档"
            "（有多少张复制多少张，不参与 TSV 粘贴）")
        self.copy_images_btn.setEnabled(False)
        self.copy_images_btn.clicked.connect(self._copy_all_images)
        bottom.addWidget(self.copy_images_btn)
        self.copy_row_btn = QPushButton("复制选中行")
        self.copy_row_btn.setToolTip("复制表格里选中的行（Ctrl+C 同样可用，可多选），不含表头")
        self.copy_row_btn.setEnabled(False)
        self.copy_row_btn.clicked.connect(self._copy_selected_rows)
        bottom.addWidget(self.copy_row_btn)
        if not self._embedded:
            close_btn = QPushButton("关闭")
            close_btn.clicked.connect(self._close)
            bottom.addWidget(close_btn)
        layout.addLayout(bottom)

    def _close(self) -> None:
        window = self.window()
        accept = getattr(window, "accept", None)
        if callable(accept):
            accept()

    # ==================== 目标 / 配置 ====================

    def set_doc_key(self, doc_key: str) -> None:
        """页签模式下跟随腾讯文档页当前选中的文档。"""
        if not doc_key:
            return
        current = self.target_combo.currentData()
        if current and current.get("doc_key") == doc_key:
            return
        indexes = [index for index in range(self.target_combo.count())
                   if (self.target_combo.itemData(index) or {}).get("doc_key") == doc_key]
        if indexes:
            index = next((i for i in indexes
                          if (self.target_combo.itemData(i) or {}).get("profile") == "posts"),
                         indexes[0])
            self.target_combo.setCurrentIndex(index)
            return
        self.refresh(reload_targets=True, prefer_doc_key=doc_key)

    def refresh(self, reload_targets: bool = False, prefer_doc_key: str = "") -> None:
        """手动/切回页签时重新比对；reload_targets=True 时同时刷新表格清单。"""
        if reload_targets:
            self._load_targets(prefer_doc_key or self._preferred_doc_key())
            return
        self._run_compare()

    def _preferred_doc_key(self) -> str:
        data = self.target_combo.currentData()
        if data:
            return str(data.get("doc_key") or "")
        try:
            return str(load_convert_config().get("tcdoc_bridge_doc_key") or "")
        except Exception:  # noqa: BLE001 - 配置读取失败时退回自动选择
            return ""

    def _load_targets(self, prefer_doc_key: str = "") -> None:
        if self._closed:
            return
        self._targets_req += 1
        self._compare_req += 1
        self._compare_pending = False
        self._set_loading("正在读取目标表格…")
        self.target_combo.setEnabled(False)
        if self._targets_loading:
            self._targets_pending = prefer_doc_key
            return
        self._start_targets(prefer_doc_key)

    def _start_targets(self, prefer_doc_key):
        self._targets_loading = True
        req = self._targets_req

        def work():
            return bridge.list_targets(), load_convert_config()

        def done(result, err):
            if self._closed:
                return
            self._targets_loading = False
            self._targets_watchdog.stop()
            if req != self._targets_req:
                preferred = self._targets_pending
                self._targets_pending = None
                self._start_targets(preferred or "")
                return
            self.target_combo.setEnabled(True)
            if err:
                self._targets = []
                self.target_combo.blockSignals(True)
                self.target_combo.clear()
                self.target_combo.blockSignals(False)
                self._clear_result()
                self.summary.setText(f"表格清单读取失败：{err}；可重新比对重试")
                self._show_table()
                return
            self._targets, cfg = result
            self._saved_accounts = list(cfg.get("tcdoc_bridge_accounts") or [])
            self._apply_targets(cfg, prefer_doc_key)

        thread = run_bg(work, done, owner=self._task_owner)
        if thread is None:
            self._targets_loading = False
            self.summary.setText("表格清单读取任务未启动，请重试")
            self._show_table()
            return
        self._targets_watchdog.start(self.TARGETS_TIMEOUT_MS)

    def _apply_targets(self, cfg, prefer_doc_key):
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        for target in self._targets:
            self.target_combo.addItem(
                f"{target['doc_name']} · {target['tab_name']}（{target['n_rows']} 行）",
                target)
        if not self._targets:
            self.target_combo.blockSignals(False)
            self._clear_result()
            self.summary.setText(
                "本地还没有可用的表格数据：请先在「表格同步」页签同步一次表格。")
            self._show_table()
            return

        doc_key = str(cfg.get("tcdoc_bridge_doc_key") or "")
        tab_id = str(cfg.get("tcdoc_bridge_tab_id") or "")
        index = next((i for i, target in enumerate(self._targets)
                      if target["doc_key"] == doc_key and target["tab_id"] == tab_id
                      and (not prefer_doc_key or target["doc_key"] == prefer_doc_key)), -1)
        if index < 0 and prefer_doc_key:
            index = next((i for i, target in enumerate(self._targets)
                          if target["doc_key"] == prefer_doc_key), 0)
        if index < 0:
            index = next((i for i, target in enumerate(self._targets)
                          if target.get("profile") == "posts"), 0)
        self.target_combo.setCurrentIndex(index)
        self.target_combo.blockSignals(False)

        try:
            days = int(cfg.get("tcdoc_bridge_days", 30) or 30)
        except (TypeError, ValueError):
            days = 30
        days_index = self.days_combo.findData(days)
        self.days_combo.blockSignals(True)
        self.days_combo.setCurrentIndex(days_index if days_index >= 0 else 1)
        self.days_combo.blockSignals(False)

        self._on_target_changed()

    def _save_cfg(self, **kwargs) -> None:
        try:
            cfg = load_convert_config()
            cfg.update(kwargs)
            save_convert_config(cfg)
        except Exception:  # noqa: BLE001 - 配置保存失败不影响本次比对
            pass

    def _on_target_changed(self, *_args) -> None:
        target = self.target_combo.currentData()
        if target:
            self._save_cfg(tcdoc_bridge_doc_key=target["doc_key"],
                           tcdoc_bridge_tab_id=target["tab_id"])
        profile = str((target or {}).get("profile") or "posts")
        songs = profile == "songs"
        lives = profile == "lives"
        readonly = profile in ("events", "merch", "lives")
        self.manual_btn.setVisible(profile in ("events", "merch", "lives"))
        if songs and not self._songs_mode:
            self._posts_days_index = self.days_combo.currentIndex()
        if songs:
            index = self.days_combo.findData(0)
            self.days_combo.blockSignals(True)
            self.days_combo.setCurrentIndex(index if index >= 0 else len(_DAYS) - 1)
            self.days_combo.blockSignals(False)
        elif self._songs_mode and self._posts_days_index is not None:
            self.days_combo.blockSignals(True)
            self.days_combo.setCurrentIndex(self._posts_days_index)
            self.days_combo.blockSignals(False)
        self._songs_mode = songs
        self.days_combo.setEnabled(not songs and not lives)
        self.account_btn.setEnabled(not songs and not readonly)
        if readonly:
            self.account_btn.setText("全部")
        self.song_search_row.setVisible(songs)
        if songs:
            self._show_song_search()
        else:
            self._update_account_btn()
            self._run_compare()

    def _show_song_search(self) -> None:
        """完整歌曲模式：不做表格比对，提示输入歌曲走五平台检索。"""
        self._acc_timer.stop()
        self._busy = False
        self._compare_owner = 0
        self._compare_pending = False
        self._compare_req += 1
        self._clear_result()
        self._fill_table([], list(bridge.song_columns()), append_images=False)
        self._show_table()
        self.compare_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)
        self.copy_images_btn.setEnabled(False)
        self.copy_row_btn.setEnabled(False)
        self.account_btn.setText("全部")
        self.summary.setText("完整歌曲：输入歌名，五平台检索后生成按表头对齐的录入行（不读写腾讯表格）")
        self.status.setText(
            "输入歌曲名后点「多平台检索」：生成一行与完整歌曲表头完全一致的数据，"
            "QQ音乐 / 网易云音乐 / 酷狗音乐 / 酷我音乐 / 5sing 链接分别填入对应列；"
            "复制后从表格第一列粘贴。")

    def _search_song(self, *_args) -> None:
        if self._closed or not self._songs_mode or not self.song_search_btn.isEnabled():
            return
        title = self.song_title_edit.text().strip()
        if not title:
            self.status.setText("请先输入歌曲名")
            self.song_title_edit.setFocus()
            return
        singer = self.song_singer_edit.text().strip() or "封茗囧菌"
        self.song_search_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.copy_row_btn.setEnabled(False)
        self.summary.setText(f"正在检索五平台：{singer} - {title} …")
        self.status.setText("QQ音乐 / 网易云音乐 / 酷狗音乐 / 酷我音乐 / 5sing 并行检索中…")

        def work():
            from toolbox.music_complete_songs import fetch_platform_songs
            results = fetch_platform_songs(
                {"title": title, "singer": singer, "links": {}}, limit=8)
            matches = bridge.best_platform_songs(results, title, singer)
            return matches, bridge.song_row_from_matches(matches, title, singer)

        def done(result, err):
            if self._closed:
                return
            self.song_search_btn.setEnabled(True)
            if err:
                self.summary.setText(f"检索失败：{err}")
                self.status.setText("可稍后重试；不会读写腾讯表格。")
                self.copy_btn.setEnabled(bool(self._rows))
                self._update_copy_row_btn()
                return
            matches, row = result
            self._show_song_row(row, matches)

        thread = run_bg(work, done, owner=self._task_owner)
        if thread is None:
            self.song_search_btn.setEnabled(True)
            self.summary.setText("检索任务未启动，请重试")

    def _show_song_row(self, row: dict, matches: dict) -> None:
        columns = list(bridge.song_columns())
        self._result = {"profile": "songs_row", "rows": [row], "columns": columns,
                        "link_columns": list(bridge.SONG_LINK_COLUMNS)}
        self._fill_table([row], columns, append_images=False)
        self.table.selectRow(0)
        self._show_table()
        self.compare_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)
        self.copy_btn.setEnabled(True)
        self.copy_row_btn.setEnabled(True)
        self.copy_images_btn.setEnabled(bool(row.get("_images")))

        present = [(label, matches[key]) for key, label in bridge.SONG_PLATFORM_ORDER
                   if key in matches]
        missing = [label for key, label in bridge.SONG_PLATFORM_ORDER if key not in matches]
        links = sum(1 for column in bridge.SONG_LINK_COLUMNS if row.get(column))
        durations, dates = set(), set()
        for _label, item in present:
            duration = int(bridge._item_value(item, "duration", 0) or 0)
            if duration:
                durations.add(duration)
            day = bridge._date_key(bridge._item_value(item, "release_date")
                                   or bridge._item_album(item).get("release_date"))
            if day:
                dates.add(day)
        duration_note = "时长一致" if len(durations) == 1 else f"时长 {len(durations)} 种"
        date_note = "日期一致" if len(dates) == 1 else f"日期 {len(dates)} 种"
        self.summary.setText(
            f"已生成 1 行，43 列与完整歌曲表头一致；链接 {links}/5 已填入对应列；"
            f"平台匹配 {len(present)}/5，{duration_note}，{date_note}"
            + (f"；缺 {'、'.join(missing)}" if missing else "；五平台链接齐全")
            + "。复制后从表格第一列粘贴。")

        lines = []
        for key, label in bridge.SONG_PLATFORM_ORDER:
            item = matches.get(key)
            if item is None:
                lines.append(f"{label}：未匹配到同名歌曲（链接留空）")
                continue
            duration = int(bridge._item_value(item, "duration", 0) or 0)
            day = bridge._date_key(bridge._item_value(item, "release_date")
                                   or bridge._item_album(item).get("release_date"))
            lines.append(
                f"{label}：{bridge._item_value(item, 'song_name')} · "
                f"{bridge._item_value(item, 'singer')} · "
                f"{bridge._mmss(duration) or '时长?'} · {day or '日期?'} · 链接已填入")
        self.status.setText("\n".join(lines))

    def _on_days_changed(self, *_args) -> None:
        self._save_cfg(tcdoc_bridge_days=int(self.days_combo.currentData() or 0))
        self._run_compare()

    # ==================== 账号 ====================

    def _rebuild_account_menu(self, names: list[str], checked: set[str]) -> None:
        self._account_names = list(names)
        self._account_checked = {name: name in checked for name in names}
        self._account_menu.clear()
        self._account_actions = {}
        for name in names:
            action = self._account_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name in checked)
            action.toggled.connect(
                lambda state, account=name: self._on_account_toggled(account, state))
            self._account_actions[name] = action
        if names:
            self._account_menu.addSeparator()
            all_action = self._account_menu.addAction("全选")
            all_action.triggered.connect(lambda: self._set_all_accounts(True))
            none_action = self._account_menu.addAction("全不选")
            none_action.triggered.connect(lambda: self._set_all_accounts(False))
        self._update_account_btn()

    def _checked_accounts(self) -> list[str]:
        return [name for name, checked in self._account_checked.items() if checked]

    def _set_all_accounts(self, checked: bool) -> None:
        for name in self._account_checked:
            self._account_checked[name] = checked
        for action in self._account_actions.values():
            try:
                action.blockSignals(True)
                action.setChecked(checked)
                action.blockSignals(False)
            except RuntimeError:  # 菜单重建后旧 QAction 已销毁
                continue
        self._after_account_change()

    def _on_account_toggled(self, name: str, checked: bool) -> None:
        self._account_checked[name] = bool(checked)
        self._after_account_change()

    def _after_account_change(self) -> None:
        self._update_account_btn()
        self._saved_accounts = self._checked_accounts()
        self._save_cfg(tcdoc_bridge_accounts=self._saved_accounts)
        self._acc_timer.start()

    def _update_account_btn(self) -> None:
        checked = len(self._checked_accounts())
        total = len(self._account_names)
        if not total or checked in (0, total):
            self.account_btn.setText("全部")
        else:
            self.account_btn.setText(f"已选 {checked}/{total}")

    def _selected_accounts(self):
        if not self._account_checked:
            return self._saved_accounts or "default"
        checked = self._checked_accounts()
        return checked or None  # 全部取消勾选 = 不过滤账号

    # ==================== 上传 ====================

    def _refresh_before_sync(self) -> None:
        """上传前刷新：采平台资料 → 拉腾讯文档，成功后才允许上传。"""
        if self._refreshing or self._closed:
            return
        from toolbox import sync_refresh

        self._refreshing = True
        self.refresh_sync_btn.setEnabled(False)
        self.status.setText("同步前刷新：采集平台资料 + 拉取腾讯文档…")

        def work():
            return sync_refresh.run(on_status=lambda text: common.post_to_main(
                self.status.setText, text))

        def done(result, err):
            self._refreshing = False
            self.refresh_sync_btn.setEnabled(True)
            if self._closed:
                return
            if err or not result:
                self.status.setText(f"刷新失败：{err or '未知错误'}")
                return
            if result.get("ok"):
                self.status.setText(
                    f"刷新完成（{result.get('ts')}）：平台资料与腾讯文档均已更新，可以上传")
                self._maybe_run_pending_compare()
                return
            detail = "\n".join(
                f"{label}：{'成功' if (result.get('steps') or {}).get(key, {}).get('ok') else '失败'}"
                f"（{(result.get('steps') or {}).get(key, {}).get('msg') or '-'}）"
                for key, label in (("info", "信息获取"), ("tracker", "网媒账号追踪"),
                                   ("tcdoc", "拉取腾讯文档")))
            self.status.setText(result.get("message") or "刷新未完成")
            QMessageBox.warning(
                self, "刷新未完成",
                f"{result.get('message') or ''}\n\n{detail}\n\n"
                "上传已锁定，请修复后重新点「同步前刷新」。")

        thread = run_bg(work, done, owner=self)
        if thread is None:
            self._refreshing = False
            self.refresh_sync_btn.setEnabled(True)
            self.status.setText("刷新任务未启动，请重试")

    def _upload_target_ok(self) -> bool:
        """只允许白名单里的官方动态 / 直播记录，测试表与其它文档一律拒绝。"""
        target = self.target_combo.currentData()
        if not target:
            return False
        try:
            from toolbox.tcdoc.import_local import validate_target
            validate_target(str(target.get("doc_key") or ""), str(target.get("tab_id") or ""))
        except Exception:  # noqa: BLE001 - 校验失败一律视为不可上传
            return False
        return True

    def _open_upload(self) -> None:
        if self._closed or self._busy or self._uploading:
            return
        target = self.target_combo.currentData()
        if not target:
            return
        from toolbox import sync_refresh
        fresh, why = sync_refresh.is_fresh()
        if not fresh:
            QMessageBox.information(
                self, "需要先刷新",
                f"{why}\n\n请先点「同步前刷新」，完成后再上传。")
            return
        if not self._upload_target_ok():
            QMessageBox.warning(
                self, "范围限制",
                "只允许上传到《封茗囧菌数据汇总库》的「官方动态」「直播记录」，"
                "或「完整歌曲」（完整歌曲仅新增，不改不删）。")
            return
        rows = list((self._result or {}).get("rows") or [])
        if not rows:
            QMessageBox.information(self, "没有待同步行", "当前清单为空，无需上传。")
            return
        if len(rows) > _UPLOAD_LIMIT:
            QMessageBox.warning(
                self, "数量过多",
                f"一次最多上传 {_UPLOAD_LIMIT} 条，请缩小时间范围或筛选账号后再试。")
            return

        days = int(self.days_combo.currentData() or 0)
        collected = list(common.COLLECTED)
        profile = str(target.get("profile") or "posts")
        self._busy = True
        self._compare_owner = 0
        self.upload_btn.setEnabled(False)
        self.status.setText("正在准备待上传清单与本地图片…")
        self._progress_start()

        def work():
            from toolbox.tcdoc import import_pending as pending
            from toolbox.tcdoc.import_local import LocalImage, LocalImportClient, ManagedAuth
            import auth as auth_module
            if profile == "songs":
                records = pending.song_records_from_rows(pending.ROOT, rows)
            else:
                sources = bridge.collect_local_items(days, collected)
                catalogue = pending.build_catalogue(pending.ROOT, sources)
                records, _missing = pending.records_from_rows(pending.ROOT, rows, catalogue)
                media = pending.ROOT / "download/已获取数据"
                for record in records:
                    record["_images"] = [LocalImage.inspect(pending.ROOT / p, media)
                                         for p in record["images"]]
            # 预检：字段/选项不满足的行标成不可上传，避免上传到一半才中断
            client = LocalImportClient(
                ManagedAuth(lambda: auth_module.get_cookie("tcdoc_openapi")),
                str(target["doc_key"]), str(target["tab_id"]))
            client.connect()
            fields = client.get_fields()
            for record in records:
                try:
                    if profile == "songs":
                        pending.make_song_plan(record, fields)
                    else:
                        pending.make_plan(record, fields, available_only=False)
                except pending.ImportErrorDetail as exc:
                    record["blocked"] = str(exc)
            return records

        def done(result, err):
            self._busy = False
            self._hide_progress()
            if self._closed:
                return
            self._restore_buttons()
            if err:
                self.status.setText(f"准备失败：{err}")
                QMessageBox.warning(self, "准备失败", str(err))
                self._maybe_run_pending_compare()
                return
            dialog = _UploadDialog(result, self, profile=profile)
            if dialog.exec() != QDialog.Accepted:
                self.status.setText("已取消上传。")
                self._maybe_run_pending_compare()
                return
            chosen = dialog.selected_records()
            if not chosen:
                self.status.setText("未勾选任何记录，已取消上传。")
                self._maybe_run_pending_compare()
                return
            self._start_upload(target, chosen)

        thread = run_bg(work, done, owner=self)
        if thread is None:
            self._busy = False
            self._hide_progress()
            self._restore_buttons()
            self.status.setText("上传准备任务未启动，请重试")
            self._maybe_run_pending_compare()

    def _start_upload(self, target: dict, records: list[dict]) -> None:
        if self._uploading:
            return
        self._uploading = True
        self._busy = True
        self._compare_owner = 0
        total_images = sum(len(r.get("_images") or []) for r in records)
        self._upload_records_total = len(records)
        self._upload_images_total = total_images
        self._progress_start(len(records) + total_images)
        self.status.setText(f"正在上传 {len(records)} 条记录 / {total_images} 张图片…")
        for button in (self.upload_btn, self.copy_btn, self.compare_btn):
            button.setEnabled(False)

        def emit(event):
            phase = event.get("phase")
            if phase == "image_uploaded":
                common.post_to_main(self._progress_tick, 0, 1)
                common.post_to_main(self.status.setText, f"上传图片：{event.get('name')}")
            elif phase == "record_created":
                common.post_to_main(self._progress_tick, 1, 0)
                common.post_to_main(
                    self.status.setText,
                    f"已新增：{str(event.get('title') or '')[:40]}"
                    f"（{event.get('image_count')} 张图）")
            elif phase == "readback":
                state = "读回校验通过" if event.get("verified") else "读回校验失败"
                common.post_to_main(self.status.setText,
                                    f"{state}：{str(event.get('title') or '')[:40]}")
            elif phase == "stopped":
                common.post_to_main(self.status.setText, f"已停止：{event.get('error')}")

        def work():
            from toolbox.tcdoc import import_pending as pending
            from toolbox.tcdoc.import_local import LocalImportClient, ManagedAuth
            import auth as auth_module
            client = LocalImportClient(
                ManagedAuth(lambda: auth_module.get_cookie("tcdoc_openapi")),
                str(target["doc_key"]), str(target["tab_id"]))
            client.connect()
            return pending.run(client, records, apply=True, available_only=False,
                               verify_each=False, emit=emit,
                               profile=str(target.get("profile") or "posts"))

        def done(receipt, err):
            self._uploading = False
            self._busy = False
            if self._closed:
                return
            if err:
                self.status.setText(f"上传失败：{err}")
                QMessageBox.warning(self, "上传失败", str(err))
                self._restore_buttons()
                self._progress_finish(3000)
                return
            added = len(receipt.get("added") or [])
            # 上传已写入远端：刷新记录作废，下一次上传必须重新「同步前刷新」
            from toolbox import sync_refresh
            sync_refresh.invalidate()
            skipped = len(receipt.get("skipped") or [])
            failed = [item for item in receipt.get("added") or [] if not item.get("verified")]
            duplicates = receipt.get("duplicates") or []
            self.status.setText(
                f"上传完成：新增 {added} 条 · 跳过 {skipped} 条 · "
                f"图片 {receipt.get('uploaded_images', 0)} 张 · "
                f"缺图 {receipt.get('missing_images', 0)} 张"
                + (f" · 读回异常 {len(failed)} 条" if failed else "")
                + (f" · 重复链接 {len(duplicates)} 条" if duplicates else ""))
            if failed:
                QMessageBox.warning(
                    self, "读回异常",
                    "以下记录的读回值不一致，请到表格里核对：\n"
                    + "\n".join(f"{i.get('record_id')} {i.get('title')}" for i in failed))
            if duplicates:
                QMessageBox.warning(
                    self, "发现重复链接",
                    "以下原帖链接在表里出现多次，可能是之前已存在或被并发写入，请人工确认：\n"
                    + "\n".join(duplicates[:10]))
            self._progress_finish(5000)
            self._refresh_archive(target)

        thread = run_bg(work, done, owner=self)
        if thread is None:
            self._uploading = False
            self._busy = False
            self._progress_finish(3000)
            self._restore_buttons()
            self.status.setText("上传任务未启动，请重试")
            self._maybe_run_pending_compare()

    def _refresh_archive(self, target: dict) -> None:
        """上传后重新抓一遍该文档，否则中间层仍按旧存档判定「还没同步」。"""
        doc = {"key": target.get("doc_key"), "name": target.get("doc_name") or "",
               "url": target.get("doc_url") or "", "kind": "smartsheet"}
        self.status.setText("上传完成，正在刷新本地存档…")

        def work():
            return tc_engine.job.sync_tables(
                [doc], on_status=lambda text: common.post_to_main(
                    self.status.setText, f"{text}"))

        def done(result, err):
            if self._closed:
                return
            if err:
                self.status.setText(f"上传已完成，但刷新存档失败：{err}")
            else:
                self.status.setText("上传完成，本地存档已刷新。")
            self.refresh()

        run_bg(work, done, owner=self)

    def _progress_start(self, total_steps: int = 0) -> None:
        self._upload_steps = 0
        self._upload_records = 0
        self._upload_images = 0
        self._upload_steps_total = max(0, int(total_steps))
        self.progress_row.setVisible(True)
        if self._upload_steps_total:
            self.upload_progress.setRange(0, self._upload_steps_total)
            self.upload_progress.setValue(0)
            self.upload_progress.setFormat("%p%")
        else:
            self.upload_progress.setRange(0, 0)
            self.upload_progress.setFormat("准备中…")
        self.progress_label.setText("")

    def _progress_tick(self, records: int = 0, images: int = 0) -> None:
        self._upload_records += records
        self._upload_images += images
        self._upload_steps += records + images
        if self._upload_steps_total:
            self.upload_progress.setValue(min(self._upload_steps, self._upload_steps_total))
            self.progress_label.setText(
                f"记录 {self._upload_records}/{self._upload_records_total} · "
                f"图片 {self._upload_images}/{self._upload_images_total}")
        else:
            self.progress_label.setText("正在连接腾讯文档…")

    def _progress_finish(self, hide_ms: int = 5000) -> None:
        if self._upload_steps_total:
            self.upload_progress.setRange(0, self._upload_steps_total)
            self.upload_progress.setValue(self._upload_steps_total)
        self._hide_progress_later(hide_ms)

    def _hide_progress_later(self, delay_ms: int) -> None:
        QTimer.singleShot(delay_ms, lambda: self.progress_row.setVisible(False))

    def _hide_progress(self) -> None:
        self.progress_row.setVisible(False)

    def _restore_buttons(self) -> None:
        has_rows = bool((self._result or {}).get("rows"))
        self.compare_btn.setEnabled(True)
        self.copy_btn.setEnabled(has_rows)
        self.upload_btn.setEnabled(has_rows and self._upload_target_ok())

    def _maybe_run_pending_compare(self) -> None:
        """其它后台任务结束后，补跑等待中的比对请求。"""
        if (self._closed or self._songs_mode or self._busy or self._targets_loading
                or not self._compare_pending):
            return
        self._compare_pending = False
        self._run_compare()

    # ==================== 比对 ====================

    def _run_compare(self, *_args) -> None:
        if self._closed:
            return
        if self._songs_mode:
            self._show_song_search()
            return
        if self._targets_loading:
            self._compare_pending = True
            return
        if not self.target_combo.currentData():
            self._load_targets(self._preferred_doc_key())
            return
        self._acc_timer.stop()
        if self._busy:
            # 上传/准备等任务还在跑：不切骨架，也不启动第二个比对
            self._compare_pending = True
            if not self._rows:
                self.status.setText("等待当前任务结束后自动比对…")
            return
        self._compare_req += 1
        if self._rows:
            # 已有结果时保留表格，避免每次重新比对都整块闪骨架
            self.summary.setText("正在重新比对本地数据与腾讯表格…")
            self.compare_btn.setEnabled(False)
        else:
            self._set_loading("正在比对本地数据与腾讯表格…")
        self._start_compare()

    def _start_compare(self):
        target = self.target_combo.currentData()
        if not target:
            return
        profile = str(target.get("profile") or "posts")
        if profile == "lives":
            # 直播记录暂无本地自动数据源：给出空表 + 手动录入入口。
            columns = [str(col.get("name") or "")
                       for col in target.get("columns") or []
                       if str(col.get("name") or "")] or list(bridge.LIVE_FALLBACK_COLUMNS)
            self._busy = False
            self._compare_owner = 0
            self._compare_watchdog.stop()
            self._result = bridge.manual_target_result("lives", columns)
            self._fill_table([], columns)
            self._show_table()
            self.compare_btn.setEnabled(True)
            self.copy_btn.setEnabled(False)
            self.upload_btn.setEnabled(False)
            self.copy_images_btn.setEnabled(False)
            self.summary.setText(
                "直播记录暂无本地自动数据源：可点「手动录入」新增一行，再复制 TSV 粘贴。")
            self.status.setText(
                "手动录入只生成可复制的行，不会直接写入腾讯文档；图片列请在腾讯文档里补。")
            return
        self._busy = True
        self._compare_pending = False
        req = self._compare_req
        self._compare_owner = req

        try:
            self.summary.setText("正在比对本地数据与腾讯表格…")
            self.status.setText("本地已获取 + 增量数据库合并去重后比对，不会修改腾讯文档。")
            days = int(self.days_combo.currentData() or 0)
            accounts = self._selected_accounts()
            doc_key, tab_id = target["doc_key"], target["tab_id"]
            collected = list(common.COLLECTED)  # 主线程快照，避免采集线程并发追加

            profile = str(target.get("profile") or "posts")
            if profile == "events":
                def work():
                    return bridge.build_event_pending(days=days, collected=collected)
            elif profile == "merch":
                def work():
                    return bridge.build_merch_pending(days=days, collected=collected)
            else:
                def work():
                    return bridge.build_pending(doc_key, tab_id, days=days,
                                                accounts=accounts, collected=collected)

            def done(result, err):
                self._finish_compare(req, result, err, accounts)

            logger.info("中间层比对开始：doc=%s tab=%s days=%s accounts=%s",
                        doc_key, tab_id, days, accounts)
            thread = run_bg(work, done, owner=self._task_owner)
        except Exception as exc:  # noqa: BLE001 - 启动失败必须解除忙碌态
            logger.exception("中间层比对启动失败")
            self._busy = False
            self._compare_owner = 0
            self._compare_watchdog.stop()
            if self._rows:
                self._show_table()
                self.summary.setText(f"比对启动失败（保留上次结果）：{exc}")
            else:
                self._clear_result()
                self.summary.setText(f"比对启动失败：{exc}")
            self._restore_buttons()
            return
        if thread is None:
            self._busy = False
            self._compare_owner = 0
            self._compare_watchdog.stop()
            if self._rows:
                self._show_table()
            self.summary.setText("比对任务未启动，请重试")
            self._restore_buttons()
            return
        self._compare_watchdog.start(self.COMPARE_TIMEOUT_MS)

    def _finish_compare(self, req, result, err, used_accounts):
        if self._closed or self._songs_mode:
            return
        if req != self._compare_req:
            # 迟到结果：结果丢弃，但它若还占着忙碌态就必须释放，
            # 否则等待中的比对永远等不到，界面一直停在骨架。
            if req == self._compare_owner:
                self._busy = False
                self._compare_owner = 0
                self._compare_watchdog.stop()
            if (self._compare_pending and not self._busy
                    and not self._targets_loading):
                self._compare_pending = False
                self._compare_req += 1
                self._start_compare()
            return
        self._busy = False
        self._compare_owner = 0
        self._compare_watchdog.stop()
        if self._compare_pending and not self._targets_loading:
            # 比对期间又收到新的比对请求：按最新条件重跑一次
            self._compare_pending = False
            self._compare_req += 1
            self._start_compare()
            return
        self._show_table()
        if err:
            logger.warning("中间层比对失败：%s", err)
            if self._rows:
                self.summary.setText(f"比对失败（保留上次结果）：{err}")
                self._restore_buttons()
            else:
                self._clear_result()
                self.summary.setText(f"比对失败：{err}")
            return
        self._result = result
        self._fill_table(result["rows"], result.get("columns"))
        stats = result["stats"]
        timing = stats.get("timing") or {}
        profile = str(result.get("profile") or "posts")
        logger.info(
            "中间层比对完成：profile=%s 用时=%.2fs remote=%.2fs local=%.2fs match=%.2fs "
            "本地=%s 待同步=%s", profile, float(timing.get("total") or 0),
            float(timing.get("remote") or 0), float(timing.get("local") or 0),
            float(timing.get("match") or 0), stats.get("local_items"),
            stats.get("pending"))
        image_note = ""
        if stats["pending"]:
            if profile == "songs":
                image_note = (f"（有封面 {stats['with_images']} 首 / "
                              f"{stats['image_count']} 张，无封面 {stats['without_images']} 首）")
            else:
                image_note = (f"（有图片 {stats['with_images']} 行 / "
                              f"{stats['image_count']} 张，无图片 {stats['without_images']} 行）")
        if profile == "songs":
            self.summary.setText(
                f"本地音乐信息导出 {stats['local_items']} 首 → 去重 {stats['local_groups']} 首；"
                f"已同步 {stats['synced_groups']} 首，待同步 {stats['pending']} 首{image_note}"
                f"；用时 {float(timing.get('total') or 0):.2f} 秒")
        elif profile in ("events", "merch"):
            label = "线下活动" if profile == "events" else "周边图鉴"
            scope = stats["since"] or "全部历史"
            source_label = {"ticket": "票务解析", "shop": "购物解析"}.get(
                stats.get("source"), "本地动态")
            self.summary.setText(
                f"{source_label} {scope} 共 {stats['local_items']} 条 → 识别 "
                f"{stats['local_groups']} 个{label}候选；已存在 {stats['synced_groups']} 个，"
                f"待补 {stats['pending']} 行{image_note}；"
                f"用时 {float(timing.get('total') or 0):.2f} 秒")
        else:
            scope = stats["since"] or "全部历史"
            self.summary.setText(
                f"本地 {scope} 共 {stats['local_items']} 条 → 合并 {stats['local_groups']} 个作品；"
                f"已同步 {stats['synced_groups']} 个，待同步 {stats['pending']} 行{image_note}"
                f"；用时 {float(timing.get('total') or 0):.2f} 秒")
        if timing:
            self.status.setToolTip(
                f"读取腾讯存档 {timing.get('remote', 0):.2f}s · "
                f"读取本地条目 {timing.get('local', 0):.2f}s · "
                f"分组去重 {timing.get('match', 0):.2f}s")
        self.copy_btn.setEnabled(bool(result["rows"]))
        self.upload_btn.setEnabled(bool(result["rows"]) and self._upload_target_ok())
        self.copy_images_btn.setEnabled(stats["image_count"] > 0 and not self._image_busy)
        self._refresh_accounts(result, used_accounts)
        if result["rows"]:
            if profile == "songs":
                self.status.setText(
                    f"共 {len(result['rows'])} 首待同步歌曲（按歌曲名称比对；列顺序与完整歌曲表头一致，"
                    "整行复制后从表格第一列粘贴）；上传只新增、不改不删。")
            elif profile in ("events", "merch"):
                source_label = {"ticket": "票务解析", "shop": "购物解析"}.get(
                    stats.get("source"), "本地动态")
                self.status.setText(
                    f"共 {len(result['rows'])} 行候选（只读预览）：由{source_label}自动识别，"
                    "请人工核对；该表不在写回白名单，未开放上传，可复制 TSV 后手工粘贴。")
            else:
                self.status.setText(
                    f"共 {len(result['rows'])} 行待同步（不含表头；"
                    "发布距今/封面图留空占位）；选中行可单行复制，右键行也有复制菜单，"
                    "图片可点「复制全部图片」。")
        else:
            self.status.setText(
                "没有待同步歌曲；如果刚上传过，请先同步一次腾讯表格再比对。"
                if profile == "songs" else
                "没有待补候选；只读预览，未写入腾讯文档。"
                if profile in ("events", "merch") else
                "没有待同步条目；如果刚上传过，请先同步一次腾讯表格再比对。")

    # ==================== 看门狗 ====================

    def _on_targets_watchdog(self) -> None:
        if self._closed or not self._targets_loading:
            return
        self._targets_loading = False
        self._targets_req += 1
        self.target_combo.setEnabled(True)
        logger.warning("读取腾讯表格清单超过 %s 秒未完成，已解除等待",
                       self.TARGETS_TIMEOUT_MS / 1000)
        self._show_table()
        if self._rows:
            self.summary.setText("读取表格清单超时，已保留上次结果；请稍后重试")
        else:
            self._clear_result()
            self.summary.setText("读取表格清单超时；请稍后重新比对重试")
        self.status.setText("可点「重新比对」重试；若反复超时请查看 logs/app_*.log")

    def _on_compare_watchdog(self) -> None:
        if self._closed or not self._busy:
            return
        self._busy = False
        self._compare_owner = 0
        self._compare_pending = False
        self._compare_req += 1  # 丢弃迟到结果，避免覆盖当前界面
        seconds = int(self.COMPARE_TIMEOUT_MS / 1000)
        logger.warning("中间层比对超过 %s 秒未完成，已解除等待", seconds)
        self._show_table()
        if self._rows:
            self.summary.setText(
                f"比对超时（{seconds} 秒未完成），已保留上次结果，请稍后重试")
            self._restore_buttons()
        else:
            self._clear_result()
            self.summary.setText(f"比对超时（{seconds} 秒未完成），请稍后重试")
        self.status.setText("可点「重新比对」重试；若反复超时请查看 logs/app_*.log")

    def _refresh_accounts(self, result: dict, used_accounts) -> None:
        names = sorted(set(result.get("accounts") or []) |
                       set(result.get("local_accounts") or []))
        if names == self._account_names:
            self._update_account_btn()
            return
        previous = set(self._checked_accounts())
        saved = set(self._saved_accounts)
        default_checked = (set(result.get("accounts") or [])
                           & set(result.get("local_accounts") or []))
        checked = previous or (saved & set(names)) or default_checked or set(result.get("accounts") or [])
        self._rebuild_account_menu(names, checked)
        selected = self._selected_accounts()
        if (previous or saved) and selected != used_accounts:
            self._acc_timer.start()

    def _fill_table(self, rows: list[dict], columns=None,
                    append_images: bool = True) -> None:
        if columns:
            display = list(columns) + (["图片"] if append_images else [])
            if display != self._model.columns:
                self._model.set_columns(display)
                for index, name in enumerate(display):
                    self.table.setColumnWidth(index, _WIDTHS.get(name, 100))
        self._rows = list(rows)
        self._model.replace(self._rows)
        self._update_copy_row_btn()
        self.copy_images_btn.setEnabled(
            any(row.get("_images") for row in self._rows) and not self._image_busy)

    def _clear_result(self):
        self._result = None
        self._fill_table([])
        self.copy_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)

    def _set_loading(self, text):
        self._skeleton.setText(text)
        self._table_stack.setCurrentWidget(self._skeleton)
        self.compare_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)
        self.copy_row_btn.setEnabled(False)
        self.copy_images_btn.setEnabled(False)

    def _show_table(self):
        self._table_stack.setCurrentWidget(self.table)
        self.compare_btn.setEnabled(True)

    def shutdown(self):
        self._closed = True
        self._acc_timer.stop()
        self._compare_watchdog.stop()
        self._targets_watchdog.stop()

    # ==================== 复制 ====================

    def _selected_rows(self) -> list[dict]:
        model = self.table.selectionModel()
        if model is None:
            return []
        indexes = sorted(model.selectedRows(), key=lambda item: item.row())
        return [self._rows[index.row()] for index in indexes
                if 0 <= index.row() < len(self._rows)]

    def _update_copy_row_btn(self, *_args) -> None:
        button = getattr(self, "copy_row_btn", None)
        if button is not None:
            button.setEnabled(not self._busy and not self._targets_loading
                              and bool(self._selected_rows()))

    def _copy(self) -> None:
        rows = (self._result or {}).get("rows") or []
        if not rows:
            return
        self._copy_rows(rows, f"全部 {len(rows)} 行")

    def _copy_all_images(self) -> None:
        urls: list[str] = []
        for row in self._rows:
            for url in row.get("_images") or []:
                if url not in urls:
                    urls.append(url)
        self._prepare_and_copy_images(urls, "全部行")

    def _copy_row_images(self, row: dict, row_index: int) -> None:
        self._prepare_and_copy_images(row.get("_images") or [], f"第 {row_index + 1} 行")

    def _prepare_and_copy_images(self, urls: list[str], label: str) -> None:
        """把图片下载到临时目录后按文件列表写入剪贴板，可直接粘贴到腾讯文档。"""
        urls = [str(url).strip() for url in urls if str(url).strip()]
        if not urls:
            self.status.setText("没有检测到可复制的图片")
            return
        if self._image_busy:
            return
        self._image_busy = True
        self.copy_images_btn.setEnabled(False)
        self.status.setText(f"正在准备{label}的 {len(urls)} 张图片…")

        def work():
            return self._download_images(urls)

        def done(result, err):
            if self._closed:
                return
            self._image_busy = False
            self.copy_images_btn.setEnabled(
                any(row.get("_images") for row in self._rows)
                and not self._busy and not self._targets_loading)
            if err:
                self.status.setText(f"图片处理失败：{err}")
                return
            paths, failed = result or ([], 0)
            if not paths:
                self.status.setText("图片下载失败，未能复制")
                return
            self._set_image_clipboard(paths)
            note = f"，失败 {failed} 张" if failed else ""
            self.status.setText(
                f"已复制{label} {len(paths)} 张图片内容，可直接到腾讯文档粘贴{note}")
            if self.app is not None:
                try:
                    self.app.toast(f"已复制 {len(paths)} 张图片", "success")
                except Exception:  # noqa: BLE001 - 提示失败不影响复制
                    pass

        run_bg(work, done, owner=self._task_owner)

    def _download_images(self, urls: list[str]):
        """并发下载图片到临时目录，返回 (路径列表, 失败数)。"""
        import mimetypes
        import shutil
        import tempfile
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime
        from pathlib import Path

        import httpx

        if self._image_temp_dir is not None:
            shutil.rmtree(self._image_temp_dir, ignore_errors=True)
        root = (Path(tempfile.gettempdir()) / "fmjj_tcdoc_images"
                / datetime.now().strftime("%Y%m%d_%H%M%S"))
        root.mkdir(parents=True, exist_ok=True)
        self._image_temp_dir = root

        def referer(url: str) -> str:
            host = url.lower()
            for key, ref in (("hdslb", "https://www.bilibili.com/"),
                             ("bilibili", "https://www.bilibili.com/"),
                             ("sinaimg", "https://weibo.com/"),
                             ("weibo", "https://weibo.com/"),
                             ("douyin", "https://www.douyin.com/"),
                             ("kuaishou", "https://www.kuaishou.com/"),
                             ("xiaohongshu", "https://www.xiaohongshu.com/"),
                             ("xhscdn", "https://www.xiaohongshu.com/")):
                if key in host:
                    return ref
            return "https://www.bilibili.com/"

        def fetch(item):
            index, url = item
            if not url.lower().startswith(("http://", "https://", "//")):
                local = Path(url)
                if not local.is_absolute():
                    from toolbox.config_manager import get_app_root
                    local = Path(get_app_root()) / local
                if not local.is_file():
                    return None
                ext = local.suffix.lower() or ".jpg"
                target = root / f"{index:03d}{ext}"
                try:
                    shutil.copyfile(local, target)
                    return str(target)
                except OSError:
                    return None
            try:
                response = httpx.get(
                    url, timeout=20, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": referer(url)})
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").split(";")[0].strip()
                ext = mimetypes.guess_extension(content_type) or Path(url.split("?")[0]).suffix
                if not ext or len(ext) > 6:
                    ext = ".jpg"
                if ext.lower() in (".jpe", ".jpeg"):
                    ext = ".jpg"
                target = root / f"{index:03d}{ext.lower()}"
                target.write_bytes(response.content)
                return str(target)
            except Exception:  # noqa: BLE001 - 单张失败不影响其余图片
                return None

        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(fetch, enumerate(urls, 1)))
        paths = [path for path in results if path]
        return paths, len(urls) - len(paths)

    def _set_image_clipboard(self, paths: list[str]) -> None:
        """文件列表写剪贴板；单张图片再附带图片数据，兼容只接收图片的目标。"""
        from PySide6.QtCore import QMimeData, QUrl
        from PySide6.QtGui import QImage

        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path) for path in paths])
        if len(paths) == 1:
            image = QImage(paths[0])
            if not image.isNull():
                mime.setImageData(image)
        QApplication.clipboard().setMimeData(mime)

    def _copy_selected_rows(self, *_args) -> None:
        rows = self._selected_rows()
        if not rows:
            self.status.setText("请先在表格里选中一行（可按住 Ctrl 多选）")
            return
        label = (f"第 {self.table.currentIndex().row() + 1} 行" if len(rows) == 1
                 else f"选中的 {len(rows)} 行")
        self._copy_rows(rows, label)

    def _copy_rows(self, rows: list[dict], label: str) -> None:
        if not rows:
            return
        columns = (self._result or {}).get("columns") or bridge.COLUMNS
        QApplication.clipboard().setText(bridge.to_tsv(rows, columns))
        self.status.setText(f"已复制{label}（不含表头），去腾讯文档粘贴即可。")
        if self.app is not None:
            try:
                self.app.toast(f"已复制{label}待同步内容", "success")
            except Exception:  # noqa: BLE001 - 提示失败不影响复制
                pass

    def _copy_text(self, text: str, label: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        QApplication.clipboard().setText(text)
        self.status.setText(f"已复制{label}")
        if self.app is not None:
            try:
                self.app.toast(f"已复制{label}", "success")
            except Exception:  # noqa: BLE001
                pass

    def _table_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        row_index = index.row()
        if not (0 <= row_index < len(self._rows)):
            return
        self.table.selectRow(row_index)
        row = self._rows[row_index]
        profile = str((self._result or {}).get("profile") or "posts")
        if profile in ("songs", "songs_row"):
            links = [str(row.get(column) or "").strip()
                     for column in (self._result or {}).get("link_columns") or []]
            page = next((link for link in links if link), "")
            dyn = ""
        else:
            page = str(row.get("作品链接") or "").strip()
            dyn = str(row.get("动态链接(b站)") or "").strip()

        menu = QMenu(self.table)
        act_row = menu.addAction("复制该行（TSV）")
        images = row.get("_images") or []
        act_images = menu.addAction("复制该行图片")
        act_images.setEnabled(bool(images))
        act_page = menu.addAction("复制歌曲链接" if profile in ("songs", "songs_row") else "复制作品链接")
        act_page.setEnabled(bool(page or dyn))
        act_dyn = None
        if dyn:
            act_dyn = menu.addAction("复制动态链接(b站)")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen == act_row:
            self._copy_rows([row], f"第 {row_index + 1} 行")
        elif chosen == act_images:
            self._copy_row_images(row, row_index)
        elif chosen == act_page:
            if page:
                self._copy_text(page, "作品链接")
            elif dyn:
                self._copy_text(dyn, "动态链接")
        elif act_dyn is not None and chosen == act_dyn:
            self._copy_text(dyn, "动态链接")
