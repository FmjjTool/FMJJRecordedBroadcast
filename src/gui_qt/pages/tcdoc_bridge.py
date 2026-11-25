"""腾讯文档 · 中间层同步器（内嵌在「腾讯文档监控」的「中间层同步」页签）。

比对本地「已获取」/增量数据库与已同步的腾讯表格，把尚未上传的条目按
「官方动态」列标准整理成表格：可一键复制 TSV 手工粘贴，也可在勾选确认后
连同本地图片上传到腾讯文档（官方动态 / 直播记录 / 完整歌曲 / 线下活动 /
周边图鉴，后三张仅新增）；工具条「推送历史」按表查看每次写入结果。
"""

from __future__ import annotations

import json
import logging
import re
import time

from PySide6.QtCore import Qt, QTimer, QAbstractTableModel
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
    QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QScrollArea, QSizePolicy, QStackedWidget, QTableView, QTableWidget,
    QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt import common, table_sort
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
    "场馆名称": 160, "场馆地址": 200,
    "场馆地址（文本）": 220,
    "图片": 90,
}


def _annotate_local_images(result: dict, days: int, collected) -> None:
    """给官方动态待同步行补「本地已下载图片数」。

    中间层只比对元数据、不下载媒体，所以「有图片」必须区分链接与本地文件，
    否则会出现界面显示 42 张图、上传却报缺图的矛盾。
    """
    rows = result.get("rows") or []
    if not rows or str(result.get("profile") or "") != "posts":
        return
    started = time.perf_counter()
    try:
        from toolbox.tcdoc import import_pending as pending

        sources = bridge.collect_local_items(days, collected)
        catalogue = pending.build_catalogue(pending.ROOT, sources)
        pending.annotate_local_images(pending.ROOT, rows, catalogue)
        logger.info("本地图片标注完成：%s 行 用时=%.2fs", len(rows),
                    time.perf_counter() - started)
    except Exception:  # noqa: BLE001 - 标注失败不影响比对结果
        logger.warning("本地图片可用性标注失败", exc_info=True)


def _caption(text: str) -> QLabel:
    """工具条里的字段名：统一小号灰字，避免和正文/按钮抢注意力。"""
    label = QLabel(text)
    label.setProperty("role", "caption")
    return label


class _StatusLabel(QLabel):
    """单行状态提示：超宽自动省略，悬停看全文，文案变化不撑高布局。"""

    def __init__(self, text: str = "", parent=None):
        super().__init__("", parent)
        self._full_text = str(text or "")
        self.setWordWrap(False)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setToolTip(self._full_text)
        self.setText(self._full_text)

    def setText(self, text):  # noqa: N802 - 与 Qt 接口保持一致
        self._full_text = "" if text is None else str(text)
        self.setToolTip(self._full_text)
        self._apply_elide()

    def text(self):
        return getattr(self, "_full_text", "")

    def resizeEvent(self, event):  # noqa: N802 - 与 Qt 接口保持一致
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self):
        full = getattr(self, "_full_text", "")
        width = self.contentsRect().width()
        if width > 0:
            full = self.fontMetrics().elidedText(full, Qt.ElideRight, width)
        if super().text() != full:
            super().setText(full)


class _PendingModel(QAbstractTableModel):
    """保留全部行，只为可见单元格生成显示文本。"""

    # 数值 / 缩略图列居中，文本列左对齐，避免整表读起来像居中的公告
    _CENTER_COLUMNS = frozenset((
        "发布距今", "封面图", "图片", "动态类型", "发布平台", "发布位置",
        "作品时长", "歌曲时长", "首发日期", "语言",
    ))

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.columns = list(_DISPLAY_COLUMNS)
        # 显示列名 → 内部字段名（字段映射改了目标列名时按内部字段取值）
        self.sources = {}
        self._sort_column = -1
        self._sort_order = Qt.AscendingOrder

    def replace(self, rows):
        self.beginResetModel()
        self.rows = rows
        if self._sort_column >= 0:
            self._sort_locked()
        self.endResetModel()

    def _sort_locked(self) -> None:
        """按当前排序列原地排序（调用方需自行包 begin/endResetModel）。"""
        if not (0 <= self._sort_column < len(self.columns)) or len(self.rows) < 2:
            return
        column = self.columns[self._sort_column]
        key = lambda row: table_sort.sort_key(self.cell_text(row, column))
        self.rows = table_sort.sort_records(
            self.rows, key, self._sort_order == Qt.AscendingOrder)

    def sort(self, column, order=Qt.AscendingOrder):
        """表头点击排序：排的是全部行（不只可见行），选中行会重置。"""
        self.layoutAboutToBeChanged.emit()
        self._sort_column, self._sort_order = column, order
        self._sort_locked()
        self.layoutChanged.emit()

    def set_columns(self, columns, sources=None):
        self.beginResetModel()
        self.columns = list(columns)
        self.sources = dict(sources or {})
        self.endResetModel()

    def _source(self, column: str) -> str:
        return self.sources.get(column, column)

    def rowCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.rows)

    def columnCount(self, parent=None):
        return 0 if parent is not None and parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.columns[section]
        if role == Qt.TextAlignmentRole and orientation == Qt.Horizontal:
            name = self.columns[section]
            if name in self._CENTER_COLUMNS:
                return int(Qt.AlignCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        column = self.columns[index.column()]
        if role == Qt.TextAlignmentRole:
            if self._source(column) in self._CENTER_COLUMNS:
                return int(Qt.AlignCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role not in (Qt.DisplayRole, Qt.ToolTipRole):
            return None
        row = self.rows[index.row()]
        if self._source(column) == "图片":
            images = row.get("_images") or []
            local = row.get("_local_images")
            missing = row.get("_missing_images")
            if role == Qt.DisplayRole:
                return self.cell_text(row, column)
            tip = "\n".join(images[:20]) + ("\n…" if len(images) > 20 else "")
            if local is not None and missing:
                tip = (f"本地已下载 {local} 张，缺 {missing} 张"
                       "（上传时可尝试在线补图）\n" + tip)
            return tip
        value = str(row.get(self._source(column)) or "")
        if bridge.tables.LINK_FIELD_SEP in value:
            # 链接列两级结构：表格里显示文本，悬停看 URL
            label, url = bridge.tables.split_link(value)
            return (url or label) if role == Qt.ToolTipRole else label
        return value[:600] if role == Qt.ToolTipRole else value

    def cell_text(self, row, column) -> str:
        """单元格显示文本（排序键也按这份文本解析）。"""
        if self._source(column) == "图片":
            images = row.get("_images") or []
            local = row.get("_local_images")
            if not images:
                return "无"
            if local is None:
                return f"有 {len(images)} 张"
            if not local:
                return f"缺 {len(images)} 张"
            if local < len(images):
                return f"本地 {local}/{len(images)} 张"
            return f"本地 {len(images)} 张"
        value = str(row.get(self._source(column)) or "")
        if bridge.tables.LINK_FIELD_SEP in value:
            label, _url = bridge.tables.split_link(value)
            return label
        return value


class _UploadDialog(QDialog):
    """上传前的人工确认清单：默认全选，可逐行取消（复选框冗余保护）。"""

    def __init__(self, records, parent=None, profile="posts"):
        super().__init__(parent)
        self._records = list(records)
        self._profile = str(profile or "posts")
        self.setWindowTitle("上传到腾讯文档")
        self.resize(900, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        if profile == "songs":
            note = ("默认全选；取消勾选可跳过某些歌曲。上传会新增歌曲记录并上传封面图，"
                    "同名歌曲会自动跳过，不会修改或删除表内已有行。")
        elif profile == "events":
            note = ("默认全选；取消勾选可跳过某些行。新增行会把本地封面/宣传图写入"
                    "「宣传图」列；已存在的活动只向该行「宣传图」追加缺的本地图片，"
                    "不改其他字段、不删除已有图片。场馆地址只写「场馆地址（文本）」"
                    "文本列（原地址列是地理位置字段，OpenAPI 写不进去）。备注信息会追加"
                    "「自动推送程序上传+当前时间」。")
        elif profile == "merch":
            note = ("默认全选；取消勾选可跳过某些行。上传只新增候选记录（不改不删），"
                    "图片列不随程序上传，可先用「复制全部图片」补；"
                    "备注信息会追加「自动推送程序上传+当前时间」。")
        else:
            note = ("默认全选；取消勾选可跳过某些行。上传会新增记录并把本地图片一起传到腾讯文档；"
                    "若本地已保存该作品的评论原始数据，会把封茗囧菌本人评论的评论区长图与原图"
                    "写入「评论区图片」，仅随新增行写入。已存在的记录会自动跳过，"
                    "不会修改或删除表内已有行；备注信息会追加「自动推送程序上传+当前时间」。")
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
        for index, width in enumerate((56, 330, 110, 80, 130, 150)):
            self.table.setColumnWidth(index, width)
        # 行号 == self._records 下标：勾选/汇总都按行号反查记录，排序时必须同步重排记录列表
        self._upload_sorter = table_sort.install(self.table, rows=self._records)
        for record in self._records:
            self._add_row(record)
        self._upload_sorter.apply()
        layout.addWidget(self.table, 1)

        self._upload_summary = QLabel("")
        self._upload_summary.setWordWrap(True)
        layout.addWidget(self._upload_summary)

        self.fetch_box = QCheckBox("缺图时尝试在线补图（下载到本地缓存后再上传）")
        self.fetch_box.setChecked(True)
        self.fetch_box.setVisible(self._profile not in ("songs", "events", "merch"))
        layout.addWidget(self.fetch_box)

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
        self.fetch_box.stateChanged.connect(lambda _state: self._refresh_summary())
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
        cover_n = len(record.get("images") or [])
        comment_n = len(record.get("_comment_images") or [])
        if self._profile == "events":
            note = f"宣传图 {cover_n} 张"
            if record.get("_existing"):
                note += " · 已存在·追加"
        else:
            note = f"封面 {cover_n} · 评论 {comment_n}"
            if record.get("missing_slots"):
                note += f"（缺封面 {len(record['missing_slots'])}）"
        if blocked:
            note += " · 不可上传"
        comment_note = str(record.get("comment_note") or "")
        image_tip = note + (f"\n{comment_note}" if comment_note else "")
        texts = [str(record.get("title") or columns.get("作品标题")
                     or columns.get("歌曲名称") or columns.get("活动名称")
                     or columns.get("周边名称") or ""),
                 str(record.get("account") or columns.get("发布账号")
                     or columns.get("演唱者") or columns.get("城市")
                     or columns.get("来源渠道") or ""),
                 str(record.get("platform") or columns.get("发布平台") or ""),
                 str(record.get("time") or columns.get("发布时间")
                     or columns.get("首发日期") or columns.get("活动日期") or ""),
                 note]
        for index, text in enumerate(texts, start=1):
            item = QTableWidgetItem(text.replace("\n", " ")[:160])
            item.setToolTip(text[:1500])
            self.table.setItem(row, index, item)
        image_item = self.table.item(row, 5)
        if image_item is not None:
            image_item.setToolTip(image_tip[:1500])

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

    @property
    def fetch_missing(self) -> bool:
        """是否在写入前尝试按 URL 补下缺图（缺图复选框只在有缺图时可用）。"""
        return self.fetch_box.isEnabled() and self.fetch_box.isChecked()

    def _refresh_summary(self) -> None:
        chosen = self.selected_records()
        covers = sum(len(r.get("images") or []) for r in chosen)
        comments = sum(len(r.get("_comment_images") or []) for r in chosen)
        gaps = sum(len(r.get("missing_slots") or []) for r in chosen)
        blocked = sum(1 for r in self._records if r.get("blocked"))
        self.fetch_box.setEnabled(gaps > 0)
        gap_note = ""
        if gaps:
            gap_note = (f"；缺封面图 {gaps} 张"
                        + ("（上传时尝试在线补图）" if self.fetch_missing else "（会留空）"))
        block_note = f"；不可上传 {blocked} 条（缺字段或选项）" if blocked else ""
        if self._profile == "events":
            appends = sum(1 for r in chosen if r.get("_existing"))
            self._upload_summary.setText(
                f"已选 {len(chosen)} / {len(self._records)} 条 · 新增 {len(chosen) - appends} 条 · "
                f"追加宣传图 {appends} 条 · 宣传图 {covers} 张{block_note}")
        else:
            self._upload_summary.setText(
                f"已选 {len(chosen)} / {len(self._records)} 条 · 封面图 {covers} 张 · "
                f"评论区图片 {comments} 张{gap_note}{block_note}")
        self.ok_btn.setEnabled(bool(chosen))


class _PushHistoryDialog(QDialog):
    """推送历史：按表汇总 + 最近批次明细（只读，数据来自 推送历史.jsonl）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        from toolbox.tcdoc import push_history

        self._history = push_history
        self.setWindowTitle("推送历史")
        self.resize(880, 580)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 10)
        layout.setSpacing(8)

        path = QLabel(f"记录文件：{push_history.history_path()}")
        path.setProperty("role", "caption")
        path.setWordWrap(True)
        path.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(path)

        self.empty = QLabel("还没有推送记录：完成一次「上传到腾讯文档」或「单条试写」后，"
                            "这里会按表记下时间、新增/跳过/图片数量。")
        self.empty.setWordWrap(True)
        self.empty.setProperty("role", "caption")
        layout.addWidget(self.empty)

        layout.addWidget(QLabel("按表汇总"))
        self.summary = QTableWidget(0, 4, self)
        self.summary.setHorizontalHeaderLabels(["表", "最近推送", "批次", "累计新增"])
        self.summary.verticalHeader().setVisible(False)
        self.summary.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.summary.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.summary.setAlternatingRowColors(True)
        for index, width in enumerate((240, 170, 70, 90)):
            self.summary.setColumnWidth(index, width)
        self.summary.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.summary, 1)

        layout.addWidget(QLabel("最近批次"))
        self.table = QTableWidget(0, 7, self)
        self.table.setHorizontalHeaderLabels(
            ["时间", "表", "来源", "新增", "跳过", "图片", "结果"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        for index, width in enumerate((150, 150, 110, 60, 60, 60, 170)):
            self.table.setColumnWidth(index, width)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.addStretch(1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self.refresh)
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        row.addWidget(refresh)
        row.addWidget(close)
        layout.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        entries = self._history.load(limit=200)
        groups = self._history.summary(entries)
        self.empty.setVisible(not entries)
        self.summary.setRowCount(len(groups))
        for row, item in enumerate(groups):
            texts = [item.get("tab_name") or item.get("tab_id") or "-",
                     item.get("last_time") or "-",
                     str(item.get("batches") or 0),
                     str(item.get("added") or 0)]
            for column, text in enumerate(texts):
                cell = QTableWidgetItem(str(text))
                cell.setToolTip(f"{item.get('doc_key') or ''} / {item.get('tab_id') or ''}")
                self.summary.setItem(row, column, cell)
        self.table.setRowCount(len(entries))
        for row, item in enumerate(entries):
            status = str(item.get("status") or "成功")
            if status != "成功" and item.get("note"):
                status = f"{status}：{item.get('note')}"
            texts = [item.get("time") or "-",
                     item.get("tab_name") or item.get("tab_id") or "-",
                     item.get("source") or "-",
                     str(item.get("added") or 0),
                     str(item.get("skipped") or 0),
                     str(item.get("images") or 0),
                     status]
            tip = "\n".join(part for part in (
                f"recordID：{'、'.join(item.get('record_ids') or [])[:200]}",
                f"备注：{item.get('remark')}" if item.get("remark") else "",
            ) if part)
            for column, text in enumerate(texts):
                cell = QTableWidgetItem(str(text))
                if tip:
                    cell.setToolTip(tip)
                self.table.setItem(row, column, cell)


class _PartnerFilterDialog(QDialog):
    """合作方过滤设置：开关 + 关键词（腾讯表名单之外的账号只收录提及关键词的作品）。"""

    def __init__(self, enabled: bool, keywords, parent=None):
        super().__init__(parent)
        self.setWindowTitle("合作方过滤")
        self.setMinimumWidth(430)
        layout = QVBoxLayout(self)
        self.enable_box = QCheckBox(
            "腾讯表「发布账号」名单之外的账号，只收录提到关键词的作品")
        self.enable_box.setChecked(bool(enabled))
        layout.addWidget(self.enable_box)
        form = QFormLayout()
        self.keyword_edit = QLineEdit("，".join(keywords or ()))
        self.keyword_edit.setPlaceholderText("多个关键词用逗号分隔，如：封茗囧菌，囧菌")
        form.addRow("关键词", self.keyword_edit)
        layout.addLayout(form)
        tip = QLabel(
            "名单内账号（封茗囧菌、封茗囧菌咨讯站等）不受影响；\n"
            "名单外的合作方账号只保留标题或正文提到关键词的作品，"
            "对方空间的无关投稿不会进入待同步。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: gray;")
        layout.addWidget(tip)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[bool, list[str]]:
        words = []
        for word in re.split(r"[,，;；\s]+", self.keyword_edit.text().strip()):
            if word and word not in words:
                words.append(word)
        return self.enable_box.isChecked(), words


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
        # 已自动勾过的合作方作者：只勾一次，用户后续取消不再强制勾回
        self._partner_seen: set[str] = set()
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

        # 工具条：上行选范围，下行放动作；整块用同一个容器包住，
        # 动作统一靠右，避免按钮散在两侧造成视觉跳跃。
        toolbar = QFrame()
        toolbar.setProperty("variant", "toolbar")
        tb = QVBoxLayout(toolbar)
        tb.setContentsMargins(10, 8, 10, 8)
        tb.setSpacing(8)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        row1.addWidget(_caption("表格"))
        self.target_combo = QComboBox()
        self.target_combo.setMinimumWidth(320)
        self.target_combo.setToolTip("已同步到本地的腾讯表格（自动识别含官方动态字段的工作表）")
        self.target_combo.currentIndexChanged.connect(self._on_target_changed)
        row1.addWidget(self.target_combo, 2)

        row1.addWidget(_caption("时间"))
        self.days_combo = QComboBox()
        for label, days in _DAYS:
            self.days_combo.addItem(label, days)
        self.days_combo.setFixedWidth(110)
        self.days_combo.currentIndexChanged.connect(self._on_days_changed)
        row1.addWidget(self.days_combo)

        row1.addWidget(_caption("账号"))
        self.account_btn = QPushButton("全部")
        self.account_btn.setProperty("menuButton", True)
        self.account_btn.setMinimumWidth(120)
        self.account_btn.setToolTip("勾选要比对的发布账号；默认只比对腾讯表出现过的账号")
        self._account_menu = QMenu(self.account_btn)
        self.account_btn.setMenu(self._account_menu)
        row1.addWidget(self.account_btn)

        # 合作方过滤：名单之外的账号只收录提到关键词的作品（默认启用，可在弹窗改关键词）
        self.partner_filter_btn = QPushButton("合作方过滤")
        self.partner_filter_btn.setToolTip(
            "配置合作方账号的收录口径：腾讯表「发布账号」名单之外的账号，"
            "只保留标题或正文提到关键词的作品；可关闭或修改关键词。")
        self.partner_filter_btn.clicked.connect(self._edit_partner_filter)
        row1.addWidget(self.partner_filter_btn)

        # 直播记录专用：主播多选 + 重复处理策略（仅 profile=lives 时可见）
        self.live_account_btn = QPushButton("主播：全部")
        self.live_account_btn.setProperty("menuButton", True)
        self.live_account_btn.setMinimumWidth(150)
        self.live_account_btn.setToolTip(
            "勾选要写入直播记录的抖音主播（来源：账号追踪的「直播历史」）")
        self._live_account_menu = QMenu(self.live_account_btn)
        self.live_account_btn.setMenu(self._live_account_menu)
        self.live_account_btn.setVisible(False)
        row1.addWidget(self.live_account_btn)
        self._live_rows: list[dict] = []
        self._live_accounts_checked: set[str] = set()

        self.dup_combo = QComboBox()
        for label, key in (("重复：跳过", "skip"), ("重复：只填空", "fill"),
                           ("重复：覆盖自动字段", "overwrite")):
            self.dup_combo.addItem(label, key)
        self.dup_combo.setToolTip(
            "表里已有同一场直播时怎么处理：只影响自动字段"
            "（账户/标题/开始/结束/平台/链接1）；AI 摘要、截图、标签、备注等人工列不会被覆盖")
        self.dup_combo.setVisible(False)
        self.dup_combo.currentIndexChanged.connect(self._on_dup_mode_changed)
        row1.addWidget(self.dup_combo)
        tb.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)

        self.compare_btn = QPushButton("重新比对")
        self.compare_btn.clicked.connect(self._run_compare)
        row2.addWidget(self.compare_btn)

        self.refresh_sync_btn = QPushButton("同步前刷新")
        self.refresh_sync_btn.setToolTip(
            "上传前必须先刷新，保证数据清洁：\n"
            "1) 采集平台资料（信息获取 + 网媒账号追踪）\n"
            "2) 重新拉取一遍腾讯文档表格快照\n"
            "三步都成功后「上传到腾讯文档」才可用；上传完成或超过 60 分钟后需重新刷新。")
        self.refresh_sync_btn.clicked.connect(self._refresh_before_sync)
        row2.addWidget(self.refresh_sync_btn)

        # 免校对模式：上传不再强制「同步前刷新」，直接推送本地数据（用户 2026-11-23 要求）
        from toolbox import sync_refresh as sync_refresh_module
        self.skip_refresh_box = QCheckBox("免校对模式")
        self.skip_refresh_box.setToolTip(
            "开启后批量上传与右键单行上传不再要求先「同步前刷新」，"
            "直接用当前本地数据推送；资产页单条上传也会跳过自动刷新。\n"
            "适合只补少量已确认的新作品；写回以仅新增为主，"
            "线下活动已存在行只追加「宣传图」图片。")
        self.skip_refresh_box.setChecked(sync_refresh_module.bypass_enabled())
        self.skip_refresh_box.clicked.connect(self._on_skip_refresh_toggled)
        row2.addWidget(self.skip_refresh_box)

        # 直播记录 API 写回：只构造 payload 预览，不发送请求（默认不真实写入）
        self.api_preview_btn = QPushButton("预览 API 写回")
        self.api_preview_btn.setToolTip(
            "只把待补行编码成腾讯文档 OpenAPI 的 payload 供核对：不发送请求、"
            "不写入任何数据。真实写回需用户明确同意后在真机执行。")
        self.api_preview_btn.setVisible(False)
        self.api_preview_btn.clicked.connect(self._preview_live_api)
        row2.addWidget(self.api_preview_btn)

        # 单条试写：真实写入 1 条 + 读回校验（批量写回验证通过后再放开）
        self.api_write_btn = QPushButton("单条试写")
        self.api_write_btn.setToolTip(
            "把清单第一条真实写入直播记录表并立即读回校验：只写 1 条，"
            "写完后「同步前刷新」凭证作废，需要重新刷新才能再写。")
        self.api_write_btn.setVisible(False)
        self.api_write_btn.clicked.connect(self._write_single_live)
        row2.addWidget(self.api_write_btn)

        self.field_map_btn = QPushButton("字段映射")
        self.field_map_btn.setToolTip(
            "配置当前表「内部字段 → 腾讯列名」：改列名、换列或让某列不写；"
            "保存到 config.json 的 tcdoc_field_maps，立即生效。")
        self.field_map_btn.clicked.connect(self._open_field_map)
        row2.addWidget(self.field_map_btn)

        self.manual_btn = QPushButton("手动录入")
        self.manual_btn.setToolTip(
            "为当前表手动新增一行；只生成可复制的 TSV，不会直接写入腾讯文档。"
            "图片列请用「复制图片」或到腾讯文档里补。")
        self.manual_btn.clicked.connect(self._manual_entry)
        row2.addWidget(self.manual_btn)
        row2.addStretch(1)

        self.copy_btn = QPushButton("复制全部（TSV）")
        self.copy_btn.setToolTip("复制全部待同步行（不含表头），到腾讯文档表格末尾粘贴")
        self.copy_btn.setEnabled(False)
        self.copy_btn.clicked.connect(self._copy)
        row2.addWidget(self.copy_btn)

        self.history_btn = QPushButton("推送历史")
        self.history_btn.setToolTip(
            "查看程序每次写入腾讯文档的记录：按表汇总新增/跳过/图片与最近推送时间；"
            "只读，不影响表格数据")
        self.history_btn.clicked.connect(self._open_push_history)
        row2.addWidget(self.history_btn)

        self.upload_btn = QPushButton("上传到腾讯文档")
        self.upload_btn.setProperty("variant", "primary")
        self.upload_btn.setToolTip(
            "把清单中勾选的记录连同本地图片上传到腾讯文档（官方动态 / 直播记录可新增，"
            "完整歌曲仅新增、不改不删；线下活动新增行会写入本地封面/宣传图，"
            "已存在活动只追加「宣传图」图片；需在弹窗里确认勾选）")
        self.upload_btn.setEnabled(False)
        self.upload_btn.clicked.connect(self._open_upload)
        row2.addWidget(self.upload_btn)
        tb.addLayout(row2)
        layout.addWidget(toolbar)
        self._refresh_partner_btn()

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
        # B站没有歌曲实体，链接靠搜索匹配：自动填只认高置信，其余从这里就地选
        self.song_bili_combo = QComboBox()
        self.song_bili_combo.setToolTip(
            "B站候选：歌名、时长、演唱者三项都对上才会自动填入 B站链接；"
            "其余候选在这里就地选，选中即写入该行，不需要弹窗")
        self.song_bili_combo.setFixedWidth(300)
        self.song_bili_combo.setVisible(False)
        self.song_bili_combo.currentIndexChanged.connect(self._on_bili_pick)
        search_layout.addWidget(self.song_bili_combo)
        self.song_search_row.setVisible(False)
        layout.addWidget(self.song_search_row)

        # 结果摘要分两层：第一行给结论（待同步数量 + 比对范围），
        # 第二行灰色小字放图片/耗时等细节，长文本不再挤成一行。
        self.pending_label = QLabel("")
        self.pending_label.setStyleSheet("font-weight: 600; font-size: 13px;")
        self.pending_label.setVisible(False)
        self.summary = QLabel("加载中…")
        self.summary.setWordWrap(True)
        self.summary.setTextFormat(Qt.PlainText)
        self.summary_note = QLabel("")
        self.summary_note.setProperty("role", "caption")
        self.summary_note.setWordWrap(True)
        self.summary_note.setVisible(False)
        info_row = QHBoxLayout()
        info_row.setSpacing(10)
        info_row.addWidget(self.pending_label)
        info_row.addWidget(self.summary, 1)
        layout.addLayout(info_row)
        layout.addWidget(self.summary_note)

        # 「场馆地址」是 type 22 地理位置字段，OpenAPI 实测写不进去（含真实腾讯地图
        # POI 也 ret=22037），地址只写文本列「场馆地址（文本）」。这里给一句固定说明。
        self.address_hint = QLabel(
            "场馆地址写入「场馆地址（文本）」文本列；原「场馆地址」是地理位置字段，"
            "腾讯 OpenAPI 写不进去（ret=22037）")
        self.address_hint.setProperty("role", "caption")
        self.address_hint.setStyleSheet("color: #888;")
        self.address_hint.setWordWrap(True)
        self.address_hint.setVisible(False)
        layout.addWidget(self.address_hint)

        self.table = QTableView()
        self._model = _PendingModel(self.table)
        self.table.setModel(self._model)
        # 表头点击排序：交给 _PendingModel.sort()，排的是全部待同步行
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setToolTip("点击表头排序（对全部待同步行生效）")
        self.table.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setMinimumSectionSize(72)
        for index, name in enumerate(_DISPLAY_COLUMNS):
            self.table.setColumnWidth(index, _WIDTHS.get(name, 100))
        if _DISPLAY_COLUMNS:
            # 标题列吃掉剩余宽度，右侧不再靠「最后一列被拉宽」填满
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        self.table.selectionModel().selectionChanged.connect(self._update_copy_row_btn)
        self._copy_shortcut = QShortcut(QKeySequence.Copy, self.table)
        self._copy_shortcut.activated.connect(self._copy_selected_rows)
        self._table_stack = QStackedWidget()
        self._table_stack.addWidget(self.table)
        self._skeleton = LoadingSkeleton(text="正在读取目标表格…")
        self._table_stack.addWidget(self._skeleton)
        self._empty = self._build_empty_state()
        self._table_stack.addWidget(self._empty)
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
        bottom.setSpacing(8)
        self.status = _StatusLabel("选中行可单行复制；上传需点「上传到腾讯文档」并确认勾选。")
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

    def _build_empty_state(self) -> QWidget:
        """待同步为 0 时替代空表格：给出结论、原因和「重新比对」入口。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(6)
        v.addStretch(1)
        self._empty_title = QLabel("当前没有待同步条目")
        self._empty_title.setProperty("role", "sectionTitle")
        self._empty_title.setAlignment(Qt.AlignCenter)
        v.addWidget(self._empty_title)
        self._empty_hint = QLabel(
            "本地数据与腾讯表格一致时会停在这里；比对结果只读，不会修改腾讯文档。")
        self._empty_hint.setProperty("role", "muted")
        self._empty_hint.setAlignment(Qt.AlignCenter)
        self._empty_hint.setWordWrap(True)
        v.addWidget(self._empty_hint)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reload_btn = QPushButton("重新比对")
        reload_btn.clicked.connect(self._run_compare)
        buttons.addWidget(reload_btn)
        buttons.addStretch(1)
        v.addSpacing(6)
        v.addLayout(buttons)
        v.addStretch(1)
        return box

    def _set_empty(self, title: str, hint: str) -> None:
        self._empty_title.setText(title)
        self._empty_hint.setText(hint)

    def _set_summary(self, text: str, note: str = "", pending_text: str = "") -> None:
        """摘要第一行给结论，第二行灰色小字放图片统计与耗时。"""
        self.pending_label.setText(pending_text)
        self.pending_label.setVisible(bool(pending_text))
        self.summary.setText(text)
        self.summary.setToolTip(text)
        self.summary_note.setText(note)
        self.summary_note.setVisible(bool(note))

    def _refresh_partner_btn(self) -> None:
        """按钮直接显示过滤当前是否生效，避免用户点开才知道。"""
        try:
            enabled = bool(load_convert_config().get("tcdoc_bridge_partner_filter", True))
        except Exception:  # noqa: BLE001 - 配置读不到时按默认启用显示
            enabled = True
        self.partner_filter_btn.setText(
            "合作方过滤：已启用" if enabled else "合作方过滤：已关闭")

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
                self._set_empty("表格清单读取失败",
                                "点「重新比对」重试；若反复失败请查看 logs/app_*.log。")
                self._set_summary(f"表格清单读取失败：{err}；可重新比对重试")
                self._show_table()
                return
            self._targets, cfg = result
            self._saved_accounts = list(cfg.get("tcdoc_bridge_accounts") or [])
            self._apply_targets(cfg, prefer_doc_key)

        thread = run_bg(work, done, owner=self._task_owner)
        if thread is None:
            self._targets_loading = False
            self._set_summary("表格清单读取任务未启动，请重试")
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
            self._set_empty("还没有可用的表格数据",
                            "请先在「表格同步」页签同步一次表格，再回来比对。")
            self._set_summary(
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
        dialog = getattr(self, "_field_map_dialog", None)
        if dialog is not None:
            try:
                dialog.close()
            except RuntimeError:  # 已销毁
                pass
            self._field_map_dialog = None
        if target:
            self._save_cfg(tcdoc_bridge_doc_key=target["doc_key"],
                           tcdoc_bridge_tab_id=target["tab_id"])
        profile = str((target or {}).get("profile") or "posts")
        songs = profile == "songs"
        lives = profile == "lives"
        readonly = profile in ("events", "merch", "lives")
        self.manual_btn.setVisible(bool(target))
        self.field_map_btn.setVisible(bool(target))
        self.partner_filter_btn.setVisible(profile == "posts")
        self.live_account_btn.setVisible(lives)
        self.dup_combo.setVisible(lives)
        self.api_preview_btn.setVisible(lives)
        self.api_write_btn.setVisible(lives)
        if lives:
            self._load_live_accounts()
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
        self.address_hint.setVisible(profile == "events")
        if songs:
            self._show_song_search()
        else:
            self._update_account_btn()
            self._run_compare()

    # ==================== 直播记录：主播 / 重复策略 ====================

    def _load_live_accounts(self) -> None:
        """读取有直播场次的抖音主播（账号追踪 xlsx，读取量小，直接同步读）。"""
        try:
            self._live_rows = bridge.live_accounts(0)
        except Exception:  # noqa: BLE001 - 账号数据异常时退化为「未选主播」
            self._live_rows = []
        self._live_accounts_checked = {row["name"] for row in self._live_rows}
        self._rebuild_live_menu()

    def _rebuild_live_menu(self) -> None:
        self._live_account_menu.clear()
        if not self._live_rows:
            action = self._live_account_menu.addAction("（没有可用的抖音直播账号）")
            action.setEnabled(False)
            self.live_account_btn.setText("主播：无")
            return
        toggle = self._live_account_menu.addAction("全选 / 全不选")
        toggle.triggered.connect(self._toggle_all_live_accounts)
        self._live_account_menu.addSeparator()
        for row in self._live_rows:
            name = row["name"]
            action = self._live_account_menu.addAction(f"{name}（{row['count']} 场）")
            action.setCheckable(True)
            action.setData(name)
            action.setChecked(name in self._live_accounts_checked)
            action.toggled.connect(
                lambda checked, n=name: self._on_live_account_toggled(n, checked))
        self._update_live_account_btn()

    def _toggle_all_live_accounts(self) -> None:
        target = set() if self._live_accounts_checked else {
            row["name"] for row in self._live_rows}
        self._live_accounts_checked = target
        for action in self._live_account_menu.actions():
            if not action.isCheckable():
                continue
            action.blockSignals(True)
            action.setChecked(action.data() in target)
            action.blockSignals(False)
        self._update_live_account_btn()
        self._run_compare()

    def _on_live_account_toggled(self, name: str, checked: bool) -> None:
        if checked:
            self._live_accounts_checked.add(name)
        else:
            self._live_accounts_checked.discard(name)
        self._update_live_account_btn()
        self._run_compare()

    def _selected_live_accounts(self) -> list[str]:
        return [row["name"] for row in self._live_rows
                if row["name"] in self._live_accounts_checked]

    def _update_live_account_btn(self) -> None:
        total = len(self._live_rows)
        chosen = len(self._live_accounts_checked)
        if total and chosen == total:
            self.live_account_btn.setText(f"主播：全部（{total}）")
        elif chosen:
            self.live_account_btn.setText(f"主播：{chosen}/{total}")
        else:
            self.live_account_btn.setText("主播：未选")

    def _on_dup_mode_changed(self, *_args) -> None:
        target = self.target_combo.currentData() or {}
        if str(target.get("profile") or "") == "lives":
            self._run_compare()

    def _show_song_search(self) -> None:
        """完整歌曲模式：不做表格比对，提示输入歌曲走五平台检索。"""
        self._acc_timer.stop()
        self._busy = False
        self._compare_owner = 0
        self._compare_pending = False
        self._compare_req += 1
        self._clear_result()
        self._set_empty("还没有待录入歌曲",
                        "在上方输入歌名，回车或点「多平台检索」生成与表头对齐的录入行。")
        self._fill_table([], list(bridge.song_columns()), append_images=False)
        self._show_table()
        self.compare_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)
        self.copy_images_btn.setEnabled(False)
        self.copy_row_btn.setEnabled(False)
        self.account_btn.setText("全部")
        self._set_summary("完整歌曲：输入歌名，五平台 + B站检索后生成按表头对齐的录入行（不读写腾讯表格）")
        self.status.setText(
            "输入歌曲名后点「多平台检索」：生成一行与完整歌曲表头完全一致的数据，"
            "QQ音乐 / 网易云音乐 / 酷狗音乐 / 酷我音乐 / 5sing 链接分别填入对应列；"
            "B站没有歌曲实体，按「歌名 + 时长 + 演唱者」搜索匹配，三项都对上才自动填 "
            "B站链接，其余候选在右侧下拉里就地选。复制后从表格第一列粘贴。")

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
        self._clear_bili_combo()
        self._set_summary(f"正在检索五平台 + B站：{singer} - {title} …")
        self.status.setText("QQ音乐 / 网易云音乐 / 酷狗音乐 / 酷我音乐 / 5sing 并行检索中，"
                            "随后按歌名+时长+演唱者匹配 B站视频…")

        def work():
            from toolbox.music_complete_songs import fetch_platform_songs
            from toolbox.tcdoc import song_bili
            results = fetch_platform_songs(
                {"title": title, "singer": singer, "links": {}}, limit=8)
            matches = bridge.best_platform_songs(results, title, singer)
            duration = bridge._most_common_int(
                bridge._item_value(item, "duration") for item in matches.values())
            bili = song_bili.search_song_bili(title, singer, duration)
            row = bridge.song_row_from_matches(matches, title, singer, bili=bili)
            return matches, row, bili

        def done(result, err):
            if self._closed:
                return
            self.song_search_btn.setEnabled(True)
            if err:
                self._set_summary(f"检索失败：{err}")
                self.status.setText("可稍后重试；不会读写腾讯表格。")
                self.copy_btn.setEnabled(bool(self._rows))
                self._update_copy_row_btn()
                return
            matches, row, bili = result
            self._show_song_row(row, matches, bili)

        thread = run_bg(work, done, owner=self._task_owner)
        if thread is None:
            self.song_search_btn.setEnabled(True)
            self._set_summary("检索任务未启动，请重试")

    def _clear_bili_combo(self) -> None:
        combo = getattr(self, "song_bili_combo", None)
        if combo is None:      # 初始化早期 _clear_result 先于检索行创建
            return
        self._bili_filling = True
        combo.clear()
        combo.setVisible(False)
        self._bili_filling = False

    def _on_bili_pick(self, index: int) -> None:
        """下拉就地选 B站候选 → 直接改这一行的「B站链接」，不弹窗。"""
        if getattr(self, "_bili_filling", False) or not self._result:
            return
        rows = self._result.get("rows") or []
        if not rows or not isinstance(rows[0], dict):
            return
        url = str(self.song_bili_combo.itemData(index) or "")
        rows[0][bridge.SONG_BILI_COLUMN] = url
        self._fill_table([rows[0]], list(bridge.song_columns()), append_images=False)
        self.table.selectRow(0)
        self.status.setText("B站链接已改为：" + (url or "留空"))

    def _fill_bili_combo(self, bili: dict, row: dict) -> None:
        candidates = list((bili or {}).get("candidates") or [])
        self._bili_filling = True
        self.song_bili_combo.clear()
        if not candidates:
            self.song_bili_combo.setVisible(False)
            self._bili_filling = False
            return
        self.song_bili_combo.addItem("B站：不填（留空）", "")
        current = -1
        for index, item in enumerate(candidates[:8], start=1):
            cand = item.get("candidate") or {}
            label = (f"B站 {index}. {str(cand.get('title') or '')[:26]} · "
                     f"{cand.get('author') or '?'} · "
                     f"{bridge._mmss(int(cand.get('duration') or 0)) or '时长?'}")
            self.song_bili_combo.addItem(label, str(cand.get("url") or ""))
            if row.get(bridge.SONG_BILI_COLUMN) and \
                    row.get(bridge.SONG_BILI_COLUMN) == cand.get("url"):
                current = index
        self.song_bili_combo.setToolTip("\n".join(
            f"{item.get('candidate', {}).get('url', '')}  {'；'.join(item.get('reasons') or [])}"
            for item in candidates[:8]))
        self.song_bili_combo.setCurrentIndex(max(current, 0))
        self.song_bili_combo.setVisible(True)
        self._bili_filling = False

    def _show_song_row(self, row: dict, matches: dict, bili: dict | None = None) -> None:
        columns = list(bridge.song_columns())
        self._result = {"profile": "songs_row", "rows": [row], "columns": columns,
                        "link_columns": list(bridge.SONG_LINK_COLUMNS)}
        self._fill_table([row], columns, append_images=False)
        self.table.selectRow(0)
        self._show_table()
        self._fill_bili_combo(bili or {}, row)
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
        bili_state = str((bili or {}).get("state") or "")
        bili_url = str(row.get(bridge.SONG_BILI_COLUMN) or "")
        bili_note = {"matched": "B站链接已填入", "ambiguous": "B站多条候选待确认",
                     "none": "B站无达标候选", "error": "B站检索失败"}.get(
                         bili_state, "B站未检索")
        self._set_summary(
            f"已生成 1 行，{len(columns)} 列与完整歌曲表头一致；"
            f"链接 {links}/5 已填入对应列，"
            f"{bili_note}；平台匹配 {len(present)}/5，{duration_note}，{date_note}"
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
        lines.append(f"B站：{bili_note}" + (f" {bili_url}" if bili_url else ""))
        if not bili_url:
            for index, item in enumerate(((bili or {}).get("candidates") or [])[:3], start=1):
                cand = item.get("candidate") or {}
                lines.append(
                    f"B站候选 {index}：{str(cand.get('title') or '')[:30]} · "
                    f"{cand.get('author') or '?'} · "
                    f"{bridge._mmss(int(cand.get('duration') or 0)) or '时长?'} · "
                    f"{cand.get('url') or ''}（{'；'.join(item.get('reasons') or [])}）")
        self.status.setText("\n".join(lines))

    def _on_days_changed(self, *_args) -> None:
        self._save_cfg(tcdoc_bridge_days=int(self.days_combo.currentData() or 0))
        self._run_compare()

    def _edit_partner_filter(self) -> None:
        """合作方过滤设置：开关 + 关键词，保存后立即重新比对。"""
        cfg = load_convert_config()
        dialog = _PartnerFilterDialog(
            cfg.get("tcdoc_bridge_partner_filter", True),
            cfg.get("tcdoc_bridge_subject_keywords") or bridge.DEFAULT_SUBJECT_KEYWORDS,
            self)
        if dialog.exec() != QDialog.Accepted:
            return
        enabled, keywords = dialog.values()
        if enabled and not keywords:
            QMessageBox.warning(self, "合作方过滤", "启用过滤时至少填一个关键词。")
            return
        self._save_cfg(tcdoc_bridge_partner_filter=enabled,
                       tcdoc_bridge_subject_keywords=keywords)
        self._refresh_partner_btn()
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

    def _on_skip_refresh_toggled(self, checked: bool) -> None:
        """免校对模式开关：持久化到配置，上传门禁即时生效（提示走勾选框 toast）。"""
        from toolbox import sync_refresh
        try:
            sync_refresh.set_bypass(bool(checked))
        except Exception:  # noqa: BLE001 - 保存失败只影响下次启动，不阻断当前模式
            pass

    def _upload_target_ok(self) -> bool:
        """只允许白名单里的表（官方动态 / 直播记录 / 完整歌曲 / 线下活动 / 周边图鉴）。"""
        target = self.target_combo.currentData()
        if not target:
            return False
        try:
            from toolbox.tcdoc.import_local import validate_target
            validate_target(str(target.get("doc_key") or ""), str(target.get("tab_id") or ""))
        except Exception:  # noqa: BLE001 - 校验失败一律视为不可上传
            return False
        return True

    def _prepare_records(self, rows: list[dict], target: dict, days: int,
                         collected) -> dict:
        """待同步行 → 可上传记录（本地图片、字段预检、需新建的下拉选项）。

        GUI 批量上传与右键单行上传共用，避免两条链路的构造规则出现偏差。
        """
        from toolbox.tcdoc import import_pending as pending
        from toolbox.tcdoc.import_local import LocalImage, LocalImportClient, ManagedAuth
        import auth as auth_module

        profile = str(target.get("profile") or "posts")
        missing = []
        if profile == "songs":
            records = pending.song_records_from_rows(pending.ROOT, rows)
        elif profile in ("events", "merch"):
            builder = (pending.event_records_from_rows if profile == "events"
                       else pending.merch_records_from_rows)
            records = builder(rows)
        else:
            sources = bridge.collect_local_items(days, collected)
            catalogue = pending.build_catalogue(pending.ROOT, sources)
            records, missing = pending.records_from_rows(pending.ROOT, rows, catalogue)
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
        if profile == "events":
            records = self._filter_event_append_records(records, client)
        for record in records:
            try:
                if profile == "songs":
                    pending.make_song_plan(record, fields,
                                           target["doc_key"], target["tab_id"])
                elif profile in ("events", "merch"):
                    pending.make_candidate_plan(record, fields, profile,
                                                target["doc_key"], target["tab_id"])
                else:
                    # 新名字（如新 UP 主）不在这里拦下：留到上传前统一确认是否新建选项
                    pending.make_plan(record, fields, available_only=False,
                                      allow_new_options=True,
                                      doc_key=target["doc_key"],
                                      tab_id=target["tab_id"])
            except pending.ImportErrorDetail as exc:
                record["blocked"] = str(exc)
        if profile == "posts":
            for record in records:
                if not record.get("blocked"):
                    pending.prepare_comment_images(record)
        new_options = {}
        if profile == "posts":
            new_options = pending.new_choice_options(
                [r for r in records if not r.get("blocked")], fields,
                target["doc_key"], target["tab_id"])
        return {"records": records, "new_options": new_options, "missing": missing}

    @staticmethod
    def _filter_event_append_records(records: list[dict], client) -> list[dict]:
        """已存在活动只保留还有本地图片可追加的行；已补全的不再展示。"""
        from toolbox.tcdoc import field_maps
        from toolbox.tcdoc import import_pending as pending

        existing = [record for record in records if record.get("_existing")]
        if not existing:
            return records
        doc_key, tab_id = client.doc_key, client.tab_id
        live = pending._event_live_index(client.get_records(), doc_key, tab_id)
        promo_title = field_maps.target(doc_key, tab_id, "宣传图") or "宣传图"
        kept = []
        for record in existing:
            row = live.get(record["key"])
            if row is None:
                record["blocked"] = "目标活动行已不存在，请重新比对"
                kept.append(record)
                continue
            values = row.get("values") or {}
            present = set(pending._image_titles(values, promo_title))
            missing = [image for image in record.get("_images") or []
                       if image.upload_name not in present]
            if not missing:
                continue
            record["_images"] = missing
            record["images"] = [str(image.path) for image in missing]
            record["_record_id"] = str(row.get("recordID") or "")
            record["_existing_images"] = len(present)
            kept.append(record)
        fresh = [record for record in records if not record.get("_existing")]
        return fresh + kept

    def _open_upload(self) -> None:
        if self._closed or self._busy or self._uploading:
            return
        target = self.target_combo.currentData()
        if not target:
            return
        from toolbox import sync_refresh
        allowed, why = sync_refresh.upload_allowed()
        if not allowed:
            QMessageBox.information(
                self, "需要先刷新",
                f"{why}\n\n请先点「同步前刷新」，完成后再上传；"
                "或在工具条勾选「免校对模式」跳过刷新。")
            return
        if not self._upload_target_ok():
            QMessageBox.warning(
                self, "范围限制",
                "只允许上传到《封茗囧菌数据汇总库》的「官方动态」「直播记录」"
                "「完整歌曲」「线下活动」「周边图鉴」；完整歌曲/周边图鉴仅新增，"
                "线下活动仅新增并可为已存在行追加「宣传图」图片，不改其他字段。")
            return
        rows = list((self._result or {}).get("rows") or []) + list(
            (self._result or {}).get("existing") or [])
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
        self.status.setText("正在准备待上传清单、封面图与评论区图片…")
        self._progress_start()

        def work():
            return self._prepare_records(rows, target, days, collected)

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
            records = result["records"]
            new_options = result.get("new_options") or {}
            if not records:
                self.status.setText("没有可上传内容：本地图片已在表格中，或目标行已不存在。")
                self._maybe_run_pending_compare()
                return
            dialog = _UploadDialog(records, self, profile=profile)
            if dialog.exec() != QDialog.Accepted:
                self.status.setText("已取消上传。")
                self._maybe_run_pending_compare()
                return
            chosen = dialog.selected_records()
            if not chosen:
                self.status.setText("未勾选任何记录，已取消上传。")
                self._maybe_run_pending_compare()
                return
            allow_new_options = False
            if new_options:
                lines = "\n".join(f"{title}：{'、'.join(names)}"
                                 for title, names in new_options.items())
                answer = QMessageBox.question(
                    self, "新建下拉选项",
                    "这批记录里有腾讯表格下拉选项还没有的名字：\n"
                    f"{lines}\n\n"
                    "上传时会自动在对应列新建这些选项并写入（只影响这两张表的选项，"
                    "不改字段类型）。是否继续？",
                    QMessageBox.Yes | QMessageBox.No)
                if answer != QMessageBox.Yes:
                    self.status.setText("已取消上传（未新建选项）。")
                    self._maybe_run_pending_compare()
                    return
                allow_new_options = True
            self._start_upload(target, chosen, allow_new_options=allow_new_options,
                               fetch_missing=dialog.fetch_missing,
                               missing=result.get("missing") or [])

        thread = run_bg(work, done, owner=self)
        if thread is None:
            self._busy = False
            self._hide_progress()
            self._restore_buttons()
            self.status.setText("上传准备任务未启动，请重试")
            self._maybe_run_pending_compare()

    def _start_upload(self, target: dict, records: list[dict],
                      allow_new_options: bool = False, fetch_missing: bool = False,
                      missing: list[dict] | None = None,
                      verify_each: bool = False, source: str = "批量上传") -> None:
        if self._uploading:
            return
        self._uploading = True
        self._busy = True
        self._compare_owner = 0
        total_images = sum(len(r.get("_images") or []) + len(r.get("_comment_images") or [])
                           for r in records)
        self._upload_records_total = len(records)
        self._upload_images_total = total_images
        self._progress_start(len(records) + total_images)
        self.status.setText(f"正在上传 {len(records)} 条记录 / {total_images} 张图片…")
        for button in (self.upload_btn, self.copy_btn, self.compare_btn):
            button.setEnabled(False)

        def emit(event):
            phase = event.get("phase")
            if phase == "comment_prepare":
                common.post_to_main(
                    self.status.setText,
                    f"正在生成评论区图片：{str(event.get('title') or '')[:40]}")
            elif phase == "comment_prepared":
                count = int(event.get("count") or 0)
                note = str(event.get("note") or "")
                if count:
                    text = f"评论区图片 {count} 张已准备"
                elif note:
                    text = f"评论区图片已跳过：{note}"
                else:
                    text = "评论区图片已跳过"
                common.post_to_main(self.status.setText, text)
            elif phase == "image_uploaded":
                common.post_to_main(self._progress_tick, 0, 1)
                kind = {"comment": "评论区图片", "promo": "宣传图"}.get(
                    event.get("kind"), "封面图")
                common.post_to_main(self.status.setText,
                                    f"上传{kind}：{event.get('name')}")
            elif phase == "images_appended":
                common.post_to_main(self._progress_tick, 1, 0)
                common.post_to_main(
                    self.status.setText,
                    f"已追加宣传图：{str(event.get('title') or '')[:40]}"
                    f"（{int(event.get('image_count') or 0)} 张）")
            elif phase == "record_created":
                common.post_to_main(self._progress_tick, 1, 0)
                cover_n = int(event.get("cover_image_count") or event.get("image_count") or 0)
                comment_n = int(event.get("comment_image_count") or 0)
                common.post_to_main(
                    self.status.setText,
                    f"已新增：{str(event.get('title') or '')[:40]}"
                    f"（封面 {cover_n} / 评论 {comment_n}）")
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
            if fetch_missing and missing:
                common.post_to_main(self.status.setText, "正在尝试在线补下缺失图片…")
                fetched = pending.fetch_missing_images(records, missing)
                common.post_to_main(
                    self.status.setText, f"在线补图完成：补上 {fetched} 张，其余留空")
                if fetched:
                    # 补图后总张数变大，否则进度会显示「图片 2/1」
                    common.post_to_main(
                        self._set_upload_image_total,
                        sum(len(r.get("_images") or []) + len(r.get("_comment_images") or [])
                            for r in records))
            client = LocalImportClient(
                ManagedAuth(lambda: auth_module.get_cookie("tcdoc_openapi")),
                str(target["doc_key"]), str(target["tab_id"]))
            client.connect()
            return pending.run(client, records, apply=True, available_only=False,
                               verify_each=verify_each, emit=emit,
                               profile=str(target.get("profile") or "posts"),
                               allow_new_options=allow_new_options, source=source)

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
            updated = int(receipt.get("updated") or 0)
            # 上传已写入远端：刷新记录作废，下一次上传必须重新「同步前刷新」
            from toolbox import sync_refresh
            sync_refresh.invalidate()
            skipped = len(receipt.get("skipped") or [])
            failed = [item for item in receipt.get("added") or [] if not item.get("verified")]
            duplicates = receipt.get("duplicates") or []
            comment_images = int(receipt.get("uploaded_comment_images") or 0)
            self.status.setText(
                f"上传完成：新增 {added} 条 · 跳过 {skipped} 条 · "
                f"图片 {receipt.get('uploaded_images', 0)} 张"
                + (f"（评论区 {comment_images} 张）" if comment_images else "")
                + (f" · 追加宣传图 {updated} 条" if updated else "")
                + f" · 缺图 {receipt.get('missing_images', 0)} 张"
                + (f" · 评论区失败 {receipt.get('comment_image_failures')} 张"
                   if receipt.get("comment_image_failures") else "")
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
            comment_failed = [item for item in receipt.get("added") or []
                              if item.get("comment_failed_count")]
            if comment_failed:
                QMessageBox.warning(
                    self, "评论区图片未全部写入",
                    "以下记录的主行已新增，但评论区图片没有全部写入；"
                    "可重新抓取评论后再补传：\n"
                    + "\n".join(
                        f"{str(item.get('title') or '')[:40]}："
                        f"失败 {item.get('comment_failed_count')} 张"
                        + (f"（{str(item.get('comment_note') or '')[:120]}）"
                           if item.get("comment_note") else "")
                        for item in comment_failed[:10]))
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

    def _set_upload_image_total(self, total: int) -> None:
        """在线补图后更新图片总数，避免进度标签出现「图片 2/1」。"""
        self._upload_images_total = max(self._upload_images_total, int(total))
        self._upload_steps_total = max(
            self._upload_steps_total, self._upload_records_total + self._upload_images_total)
        if self._upload_steps_total:
            self.upload_progress.setRange(0, self._upload_steps_total)

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
        result = self._result or {}
        has_rows = bool(result.get("rows") or result.get("existing"))
        self.compare_btn.setEnabled(True)
        self.copy_btn.setEnabled(bool(result.get("rows")))
        self.upload_btn.setEnabled(has_rows and self._upload_target_ok())

    def _maybe_run_pending_compare(self) -> None:
        """其它后台任务结束后，补跑等待中的比对请求。"""
        if (self._closed or self._songs_mode or self._busy or self._targets_loading
                or not self._compare_pending):
            return
        self._compare_pending = False
        self._run_compare()

    # ==================== 比对 ====================

    _MANUAL_SKIP_TYPES = {5, 10, 11, 13, 19, 21}   # 图片/人员/公式/关联

    def _manual_entry(self) -> None:
        """手动新增一行：按当前表头生成输入框，只生成可复制 TSV。

        所有表都能用（官方动态 / 完整歌曲 / 线下活动 / 周边图鉴 / 直播记录）；
        输入框按腾讯表的真实列名显示，落库时换成程序内部字段名，
        这样字段映射改了列名也能正确显示与复制。
        """
        from toolbox.tcdoc import field_maps

        target = self.target_combo.currentData()
        if not target:
            return
        profile = str(target.get("profile") or "posts")
        doc_key = str(target.get("doc_key") or "")
        tab_id = str(target.get("tab_id") or "")
        pairs: list[tuple[str, str]] = []   # (腾讯列名, 内部字段名)
        seen: set[str] = set()
        for col in target.get("columns") or []:
            shown = str(col.get("name") or "")
            if not shown or int(col.get("type") or 1) in self._MANUAL_SKIP_TYPES:
                continue
            internal = field_maps.internal_of(doc_key, tab_id, shown)
            if internal in seen or not field_maps.target(doc_key, tab_id, internal):
                continue
            seen.add(internal)
            pairs.append((shown, internal))
        if not pairs:
            pairs = [(name, field_maps.internal_of(doc_key, tab_id, name))
                     for name in (c.get("name") for c in target.get("columns") or [])
                     if name]
        dlg = QDialog(self)
        dlg.setWindowTitle(f"手动录入 · {target.get('tab_name') or ''}")
        dlg.resize(640, 620)
        root = QVBoxLayout(dlg)
        root.setContentsMargins(12, 12, 12, 10)
        tip = QLabel("只生成可复制的 TSV，不会写入腾讯文档；图片列请单独处理。")
        tip.setProperty("role", "muted")
        root.addWidget(tip)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        form = QFormLayout(body)
        form.setContentsMargins(4, 4, 4, 4)
        edits: dict[str, QLineEdit] = {}
        for shown, internal in pairs:
            edit = QLineEdit()
            edit.setPlaceholderText(shown)
            edits[internal] = edit
            form.addRow(shown if shown == internal else f"{shown}（← {internal}）", edit)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(dlg.reject)
        buttons.addWidget(cancel)
        ok = QPushButton("加入待复制行")
        ok.setProperty("variant", "primary")
        buttons.addWidget(ok)
        root.addLayout(buttons)

        def save():
            row = {name: edit.text().strip() for name, edit in edits.items()}
            if not any(row.values()):
                QMessageBox.warning(dlg, "手动录入", "至少填写一个字段。")
                return
            row["_images"] = []
            row["_source"] = ""
            row["_needs_review"] = True
            if self._result is None:
                names = [internal for _shown, internal in pairs]
                self._result = bridge.manual_target_result(profile, names)
            elif not self._result.get("columns"):
                self._result["columns"] = [internal for _shown, internal in pairs]
            self._result["rows"].append(row)
            # 手动行可能填了自动比对结果里没有的列（如表里额外的人工列）
            columns = list(self._result.get("columns") or [])
            for key in row:
                if not str(key).startswith("_") and key not in columns:
                    columns.append(key)
            self._result["columns"] = columns
            self._fill_table(self._result["rows"], self._result.get("columns"))
            self._show_table()
            self.copy_btn.setEnabled(True)
            self.upload_btn.setEnabled(False)
            self.status.setText(
                f"已手动新增 1 行（共 {len(self._result['rows'])} 行）；"
                "点「复制全部（TSV）」后到腾讯文档对应表末尾粘贴。")
            dlg.accept()

        ok.clicked.connect(save)
        dlg.exec()

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
            self._set_summary("正在重新比对本地数据与腾讯表格…")
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
            # 抖音直播历史 → 直播记录待补行（含重复处理策略）
            columns = [str(col.get("name") or "")
                       for col in target.get("columns") or []
                       if str(col.get("name") or "")] or list(bridge.LIVE_FALLBACK_COLUMNS)
            days = int(self.days_combo.currentData() or 0)
            accounts = self._selected_live_accounts()
            mode = str(self.dup_combo.currentData() or "skip")
            if not accounts and self._live_rows:
                # 有主播但一个都没勾：直接提示，避免误把所有主播都跑一遍
                self._busy = False
                self._compare_owner = 0
                self._compare_watchdog.stop()
                self._result = bridge.manual_target_result("lives", columns)
                self._fill_table([], columns, append_images=False)
                self._show_table()
                self.copy_btn.setEnabled(False)
                self.upload_btn.setEnabled(False)
                self._set_summary("未勾选任何主播：请在上方「主播」里选择要同步的抖音账号。")
                return
            self._busy = True
            self._compare_pending = False
            req = self._compare_req
            self._compare_owner = req
            self._set_summary("正在比对抖音直播历史与直播记录表…")
            self.status.setText(
                f"只读比对：重复场次按「{bridge.LIVE_DUP_MODES.get(mode, mode)}」处理，"
                "AI 摘要 / 截图 / 标签 / 备注等人工列不会被覆盖。")

            def work():
                return bridge.build_live_pending(days=days, accounts=accounts,
                                                 duplicate_mode=mode, columns=columns)

            def done(result, err):
                self._finish_live_compare(req, result, err)

            thread = run_bg(work, done, owner=self._task_owner)
            if thread is None:
                self._busy = False
                self._compare_owner = 0
                self._set_summary("比对任务未启动，请重试")
            return
        self._busy = True
        self._compare_pending = False
        req = self._compare_req
        self._compare_owner = req

        try:
            self._set_summary("正在比对本地数据与腾讯表格…")
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
                    result = bridge.build_pending(doc_key, tab_id, days=days,
                                                  accounts=accounts, collected=collected)
                    _annotate_local_images(result, days, collected)
                    return result

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
                self._set_summary(f"比对启动失败（保留上次结果）：{exc}")
            else:
                self._clear_result()
                self._set_empty("比对启动失败", "点「重新比对」重试；若反复失败请查看 logs/app_*.log。")
                self._set_summary(f"比对启动失败：{exc}")
            self._restore_buttons()
            return
        if thread is None:
            self._busy = False
            self._compare_owner = 0
            self._compare_watchdog.stop()
            if self._rows:
                self._show_table()
            self._set_summary("比对任务未启动，请重试")
            self._restore_buttons()
            return
        self._compare_watchdog.start(self.COMPARE_TIMEOUT_MS)

    def _preview_live_api(self) -> None:
        """直播记录写回预演：构造 OpenAPI payload 供核对，不发送请求、不写数据。"""
        rows = (self._result or {}).get("rows") or []
        target = self.target_combo.currentData() or {}
        if not rows or not target:
            return
        from toolbox.tcdoc import import_pending

        fields = [{"fieldTitle": str(col.get("name") or ""),
                   "fieldType": int(col.get("type") or 1)}
                  for col in target.get("columns") or []
                  if str(col.get("name") or "")]
        remote_keys = []
        doc_key = str(target.get("doc_key") or "")
        tab_id = str(target.get("tab_id") or "")
        try:
            remote = bridge.load_remote(doc_key, tab_id)
            remote_keys = [import_pending.live_key_from_values(
                row.get("values") or {}, doc_key, tab_id) for row in remote]
        except Exception:  # noqa: BLE001 - 读不到快照就按「全部新增」预演
            remote_keys = []
        preview = import_pending.preview_lives(rows, fields, remote_keys=remote_keys,
                                               doc_key=doc_key, tab_id=tab_id)

        dlg = QDialog(self)
        dlg.setWindowTitle("预览 API 写回（不会写入）")
        dlg.resize(720, 560)
        layout = QVBoxLayout(dlg)
        head = QLabel(
            f"可新增 {len(preview['records'])} 条 · 已存在跳过 {len(preview['duplicates'])} 条 · "
            f"无法编码 {len(preview['errors'])} 条。\n"
            "以下只是 payload 预览：本功能不发送请求、不写入腾讯文档。")
        head.setWordWrap(True)
        layout.addWidget(head)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(json.dumps(preview, ensure_ascii=False, indent=2))
        layout.addWidget(view, 1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(dlg.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close_btn)
        layout.addLayout(row)
        dlg.exec()

    def _write_single_live(self) -> None:
        """单条试写：真实写入 1 条直播记录并读回校验。"""
        if self._closed or self._busy or self._uploading:
            return
        target = self.target_combo.currentData() or {}
        rows = (self._result or {}).get("rows") or []
        if str(target.get("profile") or "") != "lives" or not rows:
            return
        from toolbox import sync_refresh

        fresh, why = sync_refresh.is_fresh()
        if not fresh:
            QMessageBox.information(
                self, "需要先刷新",
                f"{why}\n\n请先点「同步前刷新」，完成后再试写。")
            return
        if not self._upload_target_ok():
            QMessageBox.warning(
                self, "范围限制",
                "只允许写入《封茗菌数据汇总库》的「直播记录」。")
            return
        row = rows[0]
        link = bridge.tables.link_url(row.get("直播链接1")) or "-"
        if QMessageBox.question(
                self, "单条试写",
                f"将真实写入 1 条到「{target.get('tab_name') or '直播记录'}」：\n\n"
                f"标题：{row.get('直播标题') or '-'}\n"
                f"开始：{row.get('开始时间') or '-'}\n"
                f"链接：{link}\n\n"
                "写入后立即读回校验；成功后刷新凭证作废，需要重新「同步前刷新」。\n"
                "确定写入这 1 条吗？") != QMessageBox.Yes:
            return

        self._busy = True
        self._uploading = True
        self.api_write_btn.setEnabled(False)
        self.status.setText("正在写入 1 条直播记录并读回校验…")

        def work():
            import auth as auth_module
            from toolbox.tcdoc import import_pending as pending
            from toolbox.tcdoc.import_local import LocalImportClient, ManagedAuth
            client = LocalImportClient(
                ManagedAuth(lambda: auth_module.get_cookie("tcdoc_openapi")),
                str(target["doc_key"]), str(target["tab_id"]))
            client.connect()
            return pending.write_lives(client, [row], limit=1, apply=True,
                                       verify_each=True)

        def done(result, err):
            self._busy = False
            self._uploading = False
            self.api_write_btn.setEnabled(True)
            if self._closed:
                return
            if err:
                self.status.setText(f"试写失败：{err}")
                QMessageBox.warning(self, "试写失败", str(err))
                return
            added = (result or {}).get("added") or []
            item = added[0] if added else {}
            if item.get("verified"):
                sync_refresh.invalidate("单条试写完成，需重新刷新后再写")
                self.status.setText(
                    f"试写成功并读回一致（record_id={item.get('record_id')}）；"
                    "刷新凭证已作废，请重新「同步前刷新」。")
                QMessageBox.information(
                    self, "试写成功",
                    f"已写入 1 条并读回一致：\n"
                    f"标题：{item.get('title') or '-'}\n"
                    f"record_id：{item.get('record_id') or '-'}\n\n"
                    "建议回腾讯文档核对这一行，再决定是否放开批量写回。")
                self._refresh_archive(target)
            else:
                self.status.setText("试写返回但未通过读回校验，请到表格里核对。")
                QMessageBox.warning(self, "未通过校验",
                                    "写入已提交但读回校验未通过，请人工核对。")

        thread = run_bg(work, done, owner=self)
        if thread is None:
            self._busy = False
            self._uploading = False
            self.api_write_btn.setEnabled(True)
            self.status.setText("试写任务未启动，请重试")

    def _finish_live_compare(self, req, result, err) -> None:
        """直播记录比对结果：只读展示 + 复制 TSV；不走上传（上传只支持动态结构）。"""
        self._busy = False
        self._compare_owner = 0
        self._compare_watchdog.stop()
        if self._closed or req != self._compare_req:
            return
        if err or not result:
            self._set_summary(f"比对失败：{err or '未知错误'}")
            if not self._rows:
                self._clear_result()
                self._set_empty("比对失败", "点「重新比对」重试；若反复失败请查看 logs/app_*.log。")
            return
        self._result = result
        self._fill_table(result["rows"], result.get("columns"), append_images=False)
        self._show_table()
        stats = result.get("stats") or {}
        mode_label = bridge.LIVE_DUP_MODES.get(str(stats.get("mode")), "跳过重复")
        scope = stats.get("since") or "全部历史"
        self._set_summary(
            f"抖音直播历史 {scope} 共 {stats.get('local_items', 0)} 场 → "
            f"重复 {stats.get('duplicates', 0)} 场（{mode_label}）、"
            f"待补 {stats.get('pending', 0)} 行"
            f"（含回放 {stats.get('with_replay', 0)} / 缺回放 {stats.get('missing_replay', 0)}）")
        self.copy_btn.setEnabled(bool(result["rows"]))
        self.upload_btn.setEnabled(False)
        self.copy_images_btn.setEnabled(False)
        self.status.setText(
            "只读比对：点「复制全部（TSV）」到直播记录表末尾粘贴；直播链接1 填 B 站投稿回放"
            "（按录制日期 + 平台匹配，同一天取开播时间最近的一条，没有投稿就留空）；"
            "直播时长 / 直播距今是公式列，不写；AI 摘要 / 截图 / 标签 / 初编 / 终编属人工列，"
            "任何模式都不会覆盖；新增行的「备注信息」会追加「自动推送程序上传+当前时间」，"
            "表里已有行不改。")
        if stats.get("duplicates") and str(stats.get("mode")) == "skip":
            self.status.setText(
                self.status.text()
                + " 当前为「跳过重复」；如需合并请把上方切到「只填空」或「覆盖自动字段」。")

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
                self._set_summary(f"比对失败（保留上次结果）：{err}")
                self._restore_buttons()
            else:
                self._clear_result()
                self._set_empty("比对失败", "点「重新比对」重试；若反复失败请查看 logs/app_*.log。")
                self._set_summary(f"比对失败：{err}")
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
            elif profile == "posts":
                rows_now = result["rows"]
                local_known = any(row.get("_local_images") is not None for row in rows_now)
                if local_known:
                    local_total = sum(int(row.get("_local_images") or 0) for row in rows_now)
                    missing_total = sum(int(row.get("_missing_images") or 0) for row in rows_now)
                    image_note = (f"（图片链接 {stats['with_images']} 行 / "
                                  f"{stats['image_count']} 张，本地已下载 {local_total} 张、"
                                  f"缺 {missing_total} 张，无图片 {stats['without_images']} 行）")
                else:
                    image_note = (f"（图片链接 {stats['with_images']} 行 / "
                                  f"{stats['image_count']} 张，无图片 {stats['without_images']} 行）")
            else:
                image_note = (f"（有图片 {stats['with_images']} 行 / "
                              f"{stats['image_count']} 张，无图片 {stats['without_images']} 行）")
        note_parts = []
        if image_note:
            note_parts.append(image_note.strip("（）"))
        note_parts.append(f"用时 {float(timing.get('total') or 0):.2f} 秒")
        note = " · ".join(note_parts)
        if profile == "songs":
            self._set_summary(
                f"本地音乐信息导出 {stats['local_items']} 首 → 去重 {stats['local_groups']} 首 · "
                f"已同步 {stats['synced_groups']} 首",
                note, f"待同步 {stats['pending']} 首")
        elif profile in ("events", "merch"):
            label = "线下活动" if profile == "events" else "周边图鉴"
            scope = stats["since"] or "全部历史"
            source_label = {"ticket": "票务解析", "shop": "购物解析"}.get(
                stats.get("source"), "本地动态")
            extra = (f" · 可追加宣传图 {stats['existing_promo']} 场"
                     if stats.get("existing_promo") else "")
            self._set_summary(
                f"{source_label} {scope} 共 {stats['local_items']} 条 → 识别 "
                f"{stats['local_groups']} 个{label}候选 · 已存在 {stats['synced_groups']} 个",
                note, f"待补 {stats['pending']} 行{extra}")
            if profile == "events":
                self._set_address_hint(stats)
        else:
            scope = stats["since"] or "全部历史"
            self._set_summary(
                f"本地 {scope} 共 {stats['local_items']} 条 → 合并 {stats['local_groups']} 个作品 · "
                f"已同步 {stats['synced_groups']} 个",
                note, f"待同步 {stats['pending']} 行")
        if timing:
            self.summary_note.setToolTip(
                f"读取腾讯存档 {timing.get('remote', 0):.2f}s · "
                f"读取本地条目 {timing.get('local', 0):.2f}s · "
                f"分组去重 {timing.get('match', 0):.2f}s")
        self.copy_btn.setEnabled(bool(result["rows"]))
        self.upload_btn.setEnabled(
            bool(result["rows"] or result.get("existing")) and self._upload_target_ok())
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
                append_note = (f"另有 {stats['existing_promo']} 场已存在活动可追加宣传图，"
                               "上传时在选择清单里勾选；"
                               if stats.get("existing_promo") else "")
                image_rule = ("上传会把本地封面/宣传图写入「宣传图」列，已存在活动只追加"
                              "该列图片、不改其他字段；" if profile == "events"
                              else "图片列仍不随程序上传；")
                addr_rule = ""
                if profile == "events":
                    addr_rule = ("地址只写「场馆地址（文本）」文本列"
                                 "（原地址列是地理位置字段，OpenAPI 写不进去）；")
                self.status.setText(
                    f"共 {len(result['rows'])} 行候选：由{source_label}自动识别，"
                    "请人工核对；可点「上传到腾讯文档」仅新增（不改不删），"
                    f"{image_rule}{addr_rule}{append_note}备注信息会追加"
                    "「自动推送程序上传+当前时间」。")
            else:
                self.status.setText(
                    f"共 {len(result['rows'])} 行待同步（不含表头；"
                    "发布距今/封面图留空占位）；选中行可单行复制，"
                    "右键行可「上传这一行到腾讯文档」或复制；"
                    "缺图会在上传时尝试在线补图，补不到则留空。")
        else:
            append_n = int(stats.get("existing_promo") or 0)
            if profile in ("events", "merch"):
                hint = (f"有 {append_n} 场已存在活动带本地封面/宣传图，点「上传到腾讯文档」"
                        "在选择清单里勾选后可只追加「宣传图」；也可先同步一次腾讯表格再比对。"
                        if append_n else
                        "本地没有新的候选；可先同步一次腾讯表格再重新比对。")
                self.status.setText(
                    f"没有待补候选；{append_n} 场已存在活动可追加宣传图。"
                    if append_n else
                    "没有待补候选；可先同步一次腾讯表格再重新比对。")
            else:
                hint = ("已同步 {synced} 首歌曲；如果刚上传过，请先同步一次腾讯表格再比对。"
                        if profile == "songs" else
                        "已同步 {synced} 个作品；如果刚上传过，请先同步一次腾讯表格再比对。")
                hint = hint.format(synced=stats["synced_groups"])
                self.status.setText(
                    "没有待同步歌曲；如果刚上传过，请先同步一次腾讯表格再比对。"
                    if profile == "songs" else
                    "没有待同步条目；如果刚上传过，请先同步一次腾讯表格再比对。")
            self._set_empty(
                "当前没有待同步歌曲" if profile == "songs" else
                "当前没有待补候选" if profile in ("events", "merch") else
                "当前没有待同步条目",
                hint)
        self._show_table()

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
            self._set_summary("读取表格清单超时，已保留上次结果；请稍后重试")
        else:
            self._clear_result()
            self._set_empty("读取表格清单超时", "点「重新比对」重试；若反复超时请查看 logs/app_*.log。")
            self._set_summary("读取表格清单超时；请稍后重新比对重试")
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
            self._set_summary(
                f"比对超时（{seconds} 秒未完成），已保留上次结果，请稍后重试")
            self._restore_buttons()
        else:
            self._clear_result()
            self._set_empty("比对超时", "点「重新比对」重试；若反复超时请查看 logs/app_*.log。")
            self._set_summary(f"比对超时（{seconds} 秒未完成），请稍后重试")
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
        # 新出现的合作方作者（如新加的合作方空间）默认勾选，否则比对结果里看不到它们的作品
        partners = set(result.get("partner_accounts") or []) & set(names)
        checked = set(checked) | (partners - self._partner_seen)
        self._partner_seen |= partners
        self._rebuild_account_menu(names, checked)
        selected = self._selected_accounts()
        if (previous or saved) and selected != used_accounts:
            self._acc_timer.start()

    def _fill_table(self, rows: list[dict], columns=None,
                    append_images: bool = True) -> None:
        if columns:
            from toolbox.tcdoc import field_maps
            target = self.target_combo.currentData() or {}
            doc_key = str(target.get("doc_key") or "")
            tab_id = str(target.get("tab_id") or "")
            sources = field_maps.source_map(doc_key, tab_id, columns)
            display = field_maps.display_columns(doc_key, tab_id, columns)
            if append_images:
                display = display + ["图片"]
            if display != self._model.columns or sources != self._model.sources:
                self._model.set_columns(display, sources)
                header = self.table.horizontalHeader()
                header.setSectionResizeMode(QHeaderView.Interactive)
                for index, name in enumerate(display):
                    if index == 0:
                        header.setSectionResizeMode(0, QHeaderView.Stretch)
                        continue
                    self.table.setColumnWidth(
                        index, _WIDTHS.get(sources.get(name, name), 100))
        self._rows = list(rows)
        self._model.replace(self._rows)
        self._update_copy_row_btn()
        self.copy_images_btn.setEnabled(
            any(row.get("_images") for row in self._rows) and not self._image_busy)

    def _clear_result(self):
        self._result = None
        self._clear_bili_combo()
        self._fill_table([])
        self._set_empty("当前没有待同步条目",
                        "本地数据与腾讯表格一致时会停在这里；比对结果只读，不会修改腾讯文档。")
        self.copy_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)

    def _set_loading(self, text):
        self._skeleton.setText(text)
        self._table_stack.setCurrentWidget(self._skeleton)
        self.pending_label.setVisible(False)
        self.summary_note.setVisible(False)
        self.compare_btn.setEnabled(False)
        self.copy_btn.setEnabled(False)
        self.upload_btn.setEnabled(False)
        self.copy_row_btn.setEnabled(False)
        self.copy_images_btn.setEnabled(False)

    def _show_table(self):
        self._table_stack.setCurrentWidget(self.table if self._rows else self._empty)
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

    def _open_field_map(self) -> None:
        """打开字段映射窗口（非模态）：内部字段 → 当前表的真实列名。"""
        if self._closed:
            return
        target = self.target_combo.currentData()
        if not target:
            return
        dialog = getattr(self, "_field_map_dialog", None)
        if dialog is not None:
            try:
                dialog.close()
            except RuntimeError:  # 已销毁
                pass
            self._field_map_dialog = None
        from gui_qt.pages.tcdoc_field_map import FieldMapDialog

        rows = list(self._rows or [])
        if not rows and self._result:
            rows = list(self._result.get("rows") or [])
        dialog = FieldMapDialog(self, target, rows=rows,
                                on_saved=lambda: self._on_field_map_saved(target))
        dialog.destroyed.connect(lambda *_: setattr(self, "_field_map_dialog", None))
        self._field_map_dialog = dialog
        dialog.show()

    def _on_field_map_saved(self, target) -> None:
        """映射保存后按新列名重跑比对（上传/复制都会用它）。"""
        if self._closed:
            return
        if self._songs_mode:
            # 完整歌曲是检索行，不重新比对；按新列名重画当前结果即可
            self._fill_table(self._rows, (self._result or {}).get("columns"))
            self.status.setText("字段映射已保存，立即生效。")
            return
        self.status.setText("字段映射已保存，正在按新列名重新比对…")
        self._run_compare()

    def _open_push_history(self) -> None:
        """打开推送历史（非模态，可一边查看一边操作主界面）。"""
        if self._closed:
            return
        dialog = getattr(self, "_history_dialog", None)
        if dialog is not None:
            try:
                dialog.refresh()
                dialog.raise_()
                dialog.activateWindow()
                return
            except RuntimeError:  # 已关闭并销毁：重建
                self._history_dialog = None
        dialog = _PushHistoryDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.destroyed.connect(lambda *_: setattr(self, "_history_dialog", None))
        self._history_dialog = dialog
        dialog.show()

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
        target = self.target_combo.currentData() or {}
        table_columns = [str(col.get("name") or "")
                         for col in target.get("columns") or []
                         if str(col.get("name") or "")]
        QApplication.clipboard().setText(bridge.to_tsv(
            rows, columns, str(target.get("doc_key") or ""),
            str(target.get("tab_id") or ""), table_columns=table_columns))
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
        act_upload = None
        # 直播记录走独立的 payload 预演链路（列结构与官方动态不同），这里开放
        # 官方动态、完整歌曲与两张候选表（线下活动 / 周边图鉴）的单行上传。
        if profile in ("posts", "songs", "events", "merch") and self._upload_target_ok():
            menu.addSeparator()
            act_upload = menu.addAction("上传这一行到腾讯文档")
            act_upload.setEnabled(not self._busy and not self._uploading)
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
        elif act_upload is not None and chosen == act_upload:
            self._upload_single(row_index)

    def _upload_single(self, row_index: int) -> None:
        """右键单行上传：只准备并写入这一行，缺图会先尝试在线补下。

        与批量上传同一条写回链路，差别是只写一条、逐条读回校验，
        不走「上传到腾讯文档」按钮的勾选清单。
        """
        if self._closed or self._busy or self._uploading:
            return
        target = self.target_combo.currentData()
        rows = list((self._result or {}).get("rows") or [])
        if not target or not (0 <= row_index < len(rows)):
            return
        if str((self._result or {}).get("profile") or "") not in (
                "posts", "songs", "events", "merch"):
            return
        if not self._upload_target_ok():
            QMessageBox.warning(
                self, "范围限制",
                "只允许上传到《封茗囧菌数据汇总库》的「官方动态」「直播记录」"
                "「完整歌曲」「线下活动」「周边图鉴」；完整歌曲/周边图鉴仅新增，"
                "线下活动仅新增并可为已存在行追加「宣传图」图片，不改其他字段。")
            return
        from toolbox import sync_refresh
        allowed, why = sync_refresh.upload_allowed()
        if not allowed:
            QMessageBox.information(
                self, "需要先刷新",
                f"{why}\n\n请先点「同步前刷新」，完成后再上传；"
                "或在工具条勾选「免校对模式」跳过刷新。")
            return

        row = rows[row_index]
        days = int(self.days_combo.currentData() or 0)
        collected = list(common.COLLECTED)
        self._busy = True
        self.upload_btn.setEnabled(False)
        self.status.setText("正在准备这一行的字段与本地图片…")
        self._progress_start()

        def work():
            return self._prepare_records([row], target, days, collected)

        def done(result, err):
            self._busy = False
            self._hide_progress()
            if self._closed:
                return
            self._restore_buttons()
            if err:
                self.status.setText(f"这一行准备失败：{err}")
                QMessageBox.warning(self, "准备失败", str(err))
                return
            record = (result.get("records") or [None])[0]
            if not record:
                self.status.setText("这一行没有可上传内容。")
                return
            if record.get("blocked"):
                self.status.setText(f"这一行不能上传：{record['blocked']}")
                QMessageBox.warning(self, "这一行不能上传", str(record["blocked"]))
                return
            columns = record.get("columns") or {}
            title = str(record.get("title") or columns.get("作品标题")
                        or columns.get("歌曲名称") or "")[:60]
            local = len(record.get("_images") or [])
            gaps = len(record.get("missing_slots") or [])
            profile = str((self._result or {}).get("profile") or "posts")
            if profile == "events":
                image_note = (f"宣传图：本地 {local} 张会写入「宣传图」列"
                              if local else "宣传图：本地没有封面/宣传图")
                if record.get("_existing"):
                    image_note += "（该活动已存在，只追加这一列图片）"
            elif profile == "merch":
                image_note = "图片：不随程序上传，可先用「复制全部图片」到表格里补"
            else:
                image_note = (f"图片：本地 {local} 张，缺 {gaps} 张"
                              + ("（会先尝试在线补下，补不到留空）" if gaps else ""))
            answer = QMessageBox.question(
                self, "上传这一行到腾讯文档",
                f"标题：{title}\n"
                f"账号：{columns.get('发布账号') or columns.get('演唱者') or columns.get('城市') or columns.get('来源渠道') or ''} · "
                f"平台：{columns.get('发布平台') or record.get('platform') or '多平台'}\n"
                f"{image_note}\n\n"
                "只新增这一条，已存在会自动跳过；不会修改或删除表内其它行。\n"
                "备注信息会追加一行「自动推送程序上传+当前时间」。\n"
                "是否继续？",
                QMessageBox.Yes | QMessageBox.No)
            if answer != QMessageBox.Yes:
                self.status.setText("已取消上传。")
                return
            new_options = result.get("new_options") or {}
            allow_new_options = False
            if new_options:
                lines = "\n".join(f"{name}：{'、'.join(names)}"
                                  for name, names in new_options.items())
                confirm = QMessageBox.question(
                    self, "新建下拉选项",
                    f"这一行有腾讯表格下拉选项还没有的名字：\n{lines}\n\n"
                    "上传时会自动在对应列新建这些选项并写入（只影响这两张表的选项，"
                    "不改字段类型）。是否继续？",
                    QMessageBox.Yes | QMessageBox.No)
                if confirm != QMessageBox.Yes:
                    self.status.setText("已取消上传（未新建选项）。")
                    return
                allow_new_options = True
            self._start_upload(target, [record], allow_new_options=allow_new_options,
                               fetch_missing=True,
                               missing=result.get("missing") or [],
                               verify_each=True, source="单行上传")

        thread = run_bg(work, done, owner=self)
        if thread is None:
            self._busy = False
            self._hide_progress()
            self._restore_buttons()
            self.status.setText("单行上传任务未启动，请重试")
