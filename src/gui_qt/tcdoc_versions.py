"""腾讯文档采集版本的只读窗口；文件访问统一经 version_service。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QFont, QImage, QTextCharFormat, QTextCursor, QTextDocument, QTextImageFormat
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QCompleter, QDialog, QDialogButtonBox,
    QHBoxLayout, QLabel, QProgressBar, QPushButton, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from gui_qt import table_columns, table_sort
from gui_qt.common import run_bg
from toolbox.tcdoc import version_service

PAGE_SIZE = 200


def _display(value):
    return "" if value is None else str(value)


class _VersionText(QTextBrowser):
    """只显示纯文字和已校验的内置资源，不加载 URL 或本地文件。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self._sizes = {}

    def loadResource(self, kind, url):
        return None

    def show_content(self, title, text, images):
        self.clear()
        self._sizes = {}
        cursor = self.textCursor()
        heading = QTextCharFormat()
        heading.setFontWeight(QFont.Bold)
        cursor.insertText(str(title or ""), heading)
        cursor.insertBlock()
        cursor.setCharFormat(QTextCharFormat())
        cursor.insertText(str(text or ""))
        for index, (name, data) in enumerate(images):
            cursor.insertBlock()
            cursor.insertText(str(name or "图片"))
            cursor.insertBlock()
            picture = QImage.fromData(data) if data else QImage()
            if picture.isNull():
                cursor.insertText("该图片未保存在此版本中")
                continue
            url = QUrl(f"tcdoc-version:/{index}")
            self.document().addResource(QTextDocument.ImageResource, url, picture)
            self._sizes[url.toString()] = (picture.width(), picture.height())
            fmt = QTextImageFormat()
            fmt.setName(url.toString())
            width = min(picture.width(), max(120, self.viewport().width() - 32))
            fmt.setWidth(width)
            fmt.setHeight(width * picture.height() / max(1, picture.width()))
            cursor.insertImage(fmt)
        self.moveCursor(QTextCursor.Start)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._sizes:
            return
        selection = self.textCursor()
        scroll = self.verticalScrollBar().value()
        block = self.document().firstBlock()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    fmt = fragment.charFormat().toImageFormat()
                    size = self._sizes.get(fmt.name())
                    if size:
                        width = min(size[0], max(120, self.viewport().width() - 32))
                        fmt.setWidth(width)
                        fmt.setHeight(width * size[1] / max(1, size[0]))
                        cursor = QTextCursor(self.document())
                        cursor.setPosition(fragment.position())
                        cursor.setPosition(fragment.position() + fragment.length(), QTextCursor.KeepAnchor)
                        cursor.setCharFormat(fmt)
                iterator += 1
            block = block.next()
        self.setTextCursor(selection)
        self.verticalScrollBar().setValue(scroll)


def _combo():
    box = QComboBox()
    box.setEditable(True)
    box.setInsertPolicy(QComboBox.NoInsert)
    box.setMinimumWidth(160)
    box.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    box.setMinimumContentsLength(8)
    box.setFocusPolicy(Qt.StrongFocus)
    box.completer().setFilterMode(Qt.MatchContains)
    box.completer().setCaseSensitivity(Qt.CaseInsensitive)
    box.completer().setCompletionMode(QCompleter.PopupCompletion)
    box.currentTextChanged.connect(box.setToolTip)
    return box


def _combo_data(box):
    index, text = box.currentIndex(), box.currentText()
    if index >= 0 and box.itemText(index) == text:
        return box.itemData(index)
    matches = [i for i in range(box.count()) if box.itemText(i) == text]
    return box.itemData(matches[0]) if len(matches) == 1 else None


class TcDocVersionsDialog(QDialog):
    """一个文档的独立历史预览，关闭后继续使用主页面的最新数据。"""

    def __init__(self, doc: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("采集版本")
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose, False)
        self.setProperty("windowSurface", True)
        self.setMinimumSize(720, 520)
        self.resize(1040, 720)
        self._doc = dict(doc)
        self._closed = False
        self._loading = False
        self._request = 0
        self._asset_request = 0
        self._version = ""
        self._content = {}
        self._ordered_rows = []
        self._shown_rows = []
        self._page = self._pages = 1
        self._render = 0
        self._positioned = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        identity = QLabel(str(doc.get("name") or "腾讯文档"))
        identity.setTextFormat(Qt.PlainText)
        identity.setWordWrap(True)
        layout.addWidget(identity)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("采集版本"))
        self.version_combo = _combo()
        self.version_combo.lineEdit().setPlaceholderText("按采集时间或版本号搜索")
        self.version_combo.currentTextChanged.connect(self._selection_changed)
        self.version_combo.lineEdit().returnPressed.connect(self.open_selected)
        bar.addWidget(self.version_combo, 1)
        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self.refresh_btn)
        self.open_btn = QPushButton("打开版本")
        self.open_btn.setProperty("variant", "primary")
        self.open_btn.clicked.connect(self.open_selected)
        bar.addWidget(self.open_btn)
        layout.addLayout(bar)

        self.status = QLabel("正在读取采集版本…")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.PlainText)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setMaximumHeight(6)
        layout.addWidget(self.progress)

        self.stack = QStackedWidget()
        self.empty = QLabel("还没有采集版本，请先同步表格或抓取正文，再点「刷新」")
        self.empty.setWordWrap(True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setProperty("role", "muted")
        self.stack.addWidget(self.empty)
        self.table_panel = self._build_table()
        self.stack.addWidget(self.table_panel)
        self.body_panel = QWidget()
        bv = QVBoxLayout(self.body_panel)
        bv.setContentsMargins(0, 0, 0, 0)
        bv.setSpacing(8)
        page_bar = QHBoxLayout()
        page_bar.addWidget(QLabel("页面"))
        self.page_combo = _combo()
        self.page_combo.activated.connect(self._show_body)
        self.page_combo.lineEdit().returnPressed.connect(self._show_body)
        page_bar.addWidget(self.page_combo, 1)
        bv.addLayout(page_bar)
        self.body_view = _VersionText()
        bv.addWidget(self.body_view, 1)
        self.stack.addWidget(self.body_panel)
        layout.addWidget(self.stack, 1)
        footer = QDialogButtonBox()
        back = footer.addButton("返回最新", QDialogButtonBox.RejectRole)
        back.setToolTip("关闭历史预览，返回主页面查看最新数据")
        footer.rejected.connect(self.close)
        layout.addWidget(footer)
        QTimer.singleShot(0, self.refresh)

    def _build_table(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        bar = QHBoxLayout()
        bar.addWidget(QLabel("工作表"))
        self.sheet_combo = _combo()
        self.sheet_combo.activated.connect(self._show_sheet)
        self.sheet_combo.lineEdit().returnPressed.connect(self._show_sheet)
        bar.addWidget(self.sheet_combo, 1)
        layout.addLayout(bar)
        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)
        host = QWidget()
        table_layout = QVBoxLayout(host)
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(8)
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.cellDoubleClicked.connect(self._show_row)
        self.table.setToolTip("双击一行查看完整字段及该行已保存的图片")
        self.sorter = table_sort.install_callback(
            self.table, self._sort_changed, skip_columns=(), tri_state=True)
        table_layout.addWidget(self.table, 1)
        paging = QHBoxLayout()
        paging.setSpacing(8)
        self.page_label = QLabel("")
        paging.addWidget(self.page_label)
        paging.addStretch(1)
        self.first_btn = QPushButton("首页")
        self.prev_btn = QPushButton("上一页")
        self.next_btn = QPushButton("下一页")
        self.last_btn = QPushButton("末页")
        for button, delta in ((self.first_btn, "first"), (self.prev_btn, "previous"),
                              (self.next_btn, "next"), (self.last_btn, "last")):
            button.clicked.connect(lambda _checked=False, action=delta: self._turn_page(action))
            paging.addWidget(button)
        table_layout.addLayout(paging)
        split.addWidget(host)
        self.detail = _VersionText()
        self.detail.setPlaceholderText("双击一行查看完整字段及历史图片")
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 130])
        layout.addWidget(split, 1)
        return panel

    def _alive(self, request):
        return not self._closed and isValid(self) and request == self._request

    def _selected_id(self):
        return _combo_data(self.version_combo) or ""

    def _selection_changed(self, *_args):
        self.open_btn.setEnabled(bool(self._selected_id()) and not self._loading)

    def _busy(self, value):
        self._loading = bool(value)
        self.progress.setVisible(value)
        self.refresh_btn.setEnabled(not value)
        self.version_combo.setEnabled(not value)
        self.open_btn.setEnabled(not value and bool(self._selected_id()))

    def refresh(self):
        self._request += 1
        request = self._request
        keep = self._selected_id()
        self._busy(True)
        self.status.setText("正在读取采集版本…")

        def done(rows, error):
            if not self._alive(request):
                return
            self._busy(False)
            if error:
                self.status.setText("读取采集版本失败，请点「刷新」重试")
                return
            self.version_combo.blockSignals(True)
            self.version_combo.clear()
            for row in rows or []:
                source = "表格" if row.get("source") == "tables" else "正文"
                note = " · 部分采集" if row.get("complete") is False else ""
                self.version_combo.addItem(f"{row.get('label') or row['version_id']} · {source}{note}", row["version_id"])
            index = self.version_combo.findData(keep)
            self.version_combo.setCurrentIndex(index if index >= 0 else (0 if rows else -1))
            self.version_combo.blockSignals(False)
            self._selection_changed()
            if rows:
                self.status.setText(f"共 {len(rows)} 个采集版本，选择后点「打开版本」")
            else:
                self.stack.setCurrentWidget(self.empty)
                self.status.setText("当前文档还没有采集版本，完成一次采集后可在此打开")

        run_bg(lambda: version_service.list_versions(self._doc["key"]), done)

    def open_selected(self):
        version = self._selected_id()
        if not version:
            self.status.setText("请从列表选择采集版本后再打开")
            return
        self._request += 1
        self._asset_request += 1
        request = self._request
        self._busy(True)
        self.status.setText("正在打开采集版本…")

        def done(content, error):
            if not self._alive(request):
                return
            self._busy(False)
            if error:
                self.status.setText("打开采集版本失败：文件可能损坏或已移走，请刷新后重试")
                return
            self._version = version
            self._content = content
            meta = content.get("meta") or {}
            state = "部分采集，含此前保留内容" if meta.get("complete") is False else "采集已完成"
            missing = int(meta.get("missing_images") or 0)
            self.status.setText(f"历史只读 · {meta.get('label') or version} · {state}"
                                + (f" · 缺图 {missing} 张" if missing else ""))
            self.sheet_combo.clear()
            self.page_combo.clear()
            for index, sheet in enumerate(content.get("sheets") or []):
                if not sheet.get("skipped"):
                    self.sheet_combo.addItem(str(sheet.get("tab_name") or "工作表"), index)
            for index, page in enumerate(content.get("pages") or []):
                self.page_combo.addItem(str(page.get("title") or f"第 {index + 1} 篇"), index)
            if self.sheet_combo.count():
                self.stack.setCurrentWidget(self.table_panel)
                self._show_sheet()
            elif self.page_combo.count():
                self.stack.setCurrentWidget(self.body_panel)
                self._show_body()
            else:
                self.empty.setText("该次采集没有可预览的表格或正文，请选择其他版本")
                self.stack.setCurrentWidget(self.empty)

        run_bg(lambda: version_service.read_version(self._doc["key"], version), done)

    def _sheet(self):
        number = _combo_data(self.sheet_combo)
        sheets = self._content.get("sheets") or []
        return sheets[number] if isinstance(number, int) and 0 <= number < len(sheets) else {}

    def _show_sheet(self, *_args):
        self._asset_request += 1
        self.detail.clear()
        self.sorter.col, self.sorter.ascending = -1, True
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self._sort_changed(-1, True)

    def _sort_changed(self, column, ascending):
        sheet = self._sheet()
        columns = sheet.get("columns") or []
        rows = list(sheet.get("rows") or [])
        if 0 <= column <= len(columns):
            name = (columns[column - 1].get("name") if isinstance(columns[column - 1], dict)
                    else str(columns[column - 1])) if column else None
            rows = table_sort.sort_records(rows, lambda row: table_sort.sort_key(
                (row.get("values") or {}).get(name) if column else row.get("row_no")), ascending)
        self._ordered_rows = rows
        self._pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
        self._page = 1
        self._fill_table()

    def _turn_page(self, action):
        self._page = {"first": 1, "previous": max(1, self._page - 1),
                      "next": min(self._pages, self._page + 1), "last": self._pages}[action]
        self._fill_table()

    def _fill_table(self):
        self._render += 1
        self._asset_request += 1
        self.detail.clear()
        serial = self._render
        columns = self._sheet().get("columns") or []
        names = [str(c.get("name") or "") if isinstance(c, dict) else str(c) for c in columns]
        self._shown_rows = self._ordered_rows[(self._page - 1) * PAGE_SIZE:self._page * PAGE_SIZE]
        self.table.setColumnCount(len(names) + 1)
        self.table.setHorizontalHeaderLabels(["行号"] + names)
        self.table.clearContents()
        self.table.setRowCount(len(self._shown_rows))
        self.table.horizontalHeader().setVisible(bool(self._shown_rows))
        self.page_label.setText(f"第 {self._page} / {self._pages} 页 · 共 {len(self._ordered_rows):,} 条".replace(",", " "))
        for button in (self.first_btn, self.prev_btn):
            button.setEnabled(self._page > 1)
        for button in (self.next_btn, self.last_btn):
            button.setEnabled(self._page < self._pages)
        table_columns.configure_table_columns(self.table, fixed={0: 48}, stretch=range(1, len(names) + 1), auto_fit=True)

        def render(start=0):
            if self._closed or serial != self._render:
                return
            for row_index in range(start, min(start + 20, len(self._shown_rows))):
                row = self._shown_rows[row_index]
                values = [_display(row.get("row_no"))] + [_display((row.get("values") or {}).get(name, "")) for name in names]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value.replace("\n", " / "))
                    item.setToolTip(value)
                    self.table.setItem(row_index, column, item)
            if start + 20 < len(self._shown_rows):
                QTimer.singleShot(0, lambda: render(start + 20))
        render()

    def _show_row(self, row, _column):
        if not 0 <= row < len(self._shown_rows):
            return
        record = self._shown_rows[row]
        text = "\n\n".join(f"{key}\n{value}" for key, value in (record.get("values") or {}).items())
        self._load_images(self.detail, f"第 {record.get('row_no') or row + 1} 行", text, record.get("images") or [])

    def _show_body(self, *_args):
        number = _combo_data(self.page_combo)
        pages = self._content.get("pages") or []
        if isinstance(number, int) and 0 <= number < len(pages):
            page = pages[number]
            self._load_images(self.body_view, page.get("title"), page.get("text"), page.get("images") or [])

    def _load_images(self, view, title, text, entries):
        self._asset_request += 1
        request, version = self._asset_request, self._version
        view.show_content(title, text, [])
        if not entries:
            return

        def work():
            images = []
            for entry in entries:
                if self._closed or request != self._asset_request:
                    break
                name = str(entry.get("name") or entry.get("field") or "图片")
                data = b""
                if entry.get("asset_id"):
                    try:
                        data, _mime = version_service.read_asset(self._doc["key"], version, entry["asset_id"])
                    except (OSError, ValueError):
                        pass
                images.append((name, data))
            return images

        def done(images, error):
            if self._closed or not isValid(self) or request != self._asset_request or version != self._version:
                return
            if error:
                view.show_content(title, text, [("图片读取失败，请重新打开此版本", b"")])
            else:
                view.show_content(title, text, images)
        run_bg(work, done)

    def _retire(self):
        if self._closed:
            return
        self._closed = True
        self._request += 1
        self._asset_request += 1
        self._render += 1
        # 全局列宽组件有最长 400ms 的显示回调；先停本窗口渲染，等回调收尾再释放。
        QTimer.singleShot(500, lambda: self.deleteLater() if isValid(self) else None)

    def reject(self):
        self._retire()
        super().reject()

    def closeEvent(self, event):
        self._retire()
        super().closeEvent(event)

    def showEvent(self, event):
        if not self._positioned:
            self._positioned = True
            available = self.screen().availableGeometry()
            self.resize(min(self.width(), available.width() - 32),
                        min(self.height(), available.height() - 32))
            parent = self.parentWidget()
            center = parent.window().frameGeometry().center() if parent else available.center()
            frame = self.frameGeometry()
            frame.moveCenter(center)
            frame.moveLeft(max(available.left(), min(frame.left(), available.right() - frame.width() + 1)))
            frame.moveTop(max(available.top(), min(frame.top(), available.bottom() - frame.height() + 1)))
            self.move(frame.topLeft())
        super().showEvent(event)
