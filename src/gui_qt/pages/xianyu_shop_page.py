"""购物页中的闲鱼结果面板；网络、落盘与去重全部交给 L3。"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gui_qt import table_sort
from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.table_columns import configure_table_columns
from toolbox import xianyu_shopping


class XianyuShopPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._rows = []
        self._checks = set()
        self._busy = False
        self._drawing = False
        self._cancel = threading.Event()
        self._search_keyword = ""
        self._search_page = 0
        self._search_pages = {}
        self._retry_action = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("闲鱼关键词或完整商品链接")
        self.parse_btn = QPushButton("搜索并解析")
        self.parse_btn.clicked.connect(lambda: self.submit_auto(self.input_edit.text()))
        layout.addWidget(self.input_edit)
        layout.addWidget(self.parse_btn)

        card = QFrame()
        card.setProperty("variant", "card")
        body = QVBoxLayout(card)
        body.setContentsMargins(12, 12, 12, 12)
        body.setSpacing(8)
        self.empty_label = QLabel("输入周边关键词或粘贴商品链接后点「搜索并解析」")
        self.empty_label.setWordWrap(True)
        body.addWidget(self.empty_label)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["选", "商品标题", "价格", "卖家", "地区", "资料状态"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemChanged.connect(self._checked)
        self.table.itemSelectionChanged.connect(self._sync_actions)
        self.table.cellDoubleClicked.connect(lambda *_: self._open_item())
        self._sorter = table_sort.install_callback(self.table, self._sort_changed,
                                                   skip_columns=(0,), tri_state=True)
        self._columns = configure_table_columns(self.table, fixed={0: 36}, minimums={1: 140},
                                                stretch=(1,), auto_fit=True)
        body.addWidget(self.table, 1)
        self.table.hide()

        actions = QHBoxLayout()
        self.all_btn = QPushButton("本页全选")
        self.all_btn.clicked.connect(lambda: self._set_checks(True))
        self.clear_btn = QPushButton("清除勾选")
        self.clear_btn.clicked.connect(lambda: self._set_checks(False))
        self.local_btn = QPushButton("本地资料")
        self.local_btn.clicked.connect(lambda: self._start(xianyu_shopping.list_saved))
        self.open_btn = QPushButton("打开商品")
        self.open_btn.clicked.connect(self._open_item)
        self.save_btn = QPushButton("入库并下载")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.clicked.connect(self._save)
        for button in (self.all_btn, self.clear_btn):
            actions.addWidget(button)
        self.selection_label = QLabel()
        actions.addWidget(self.selection_label)
        actions.addStretch(1)
        body.addLayout(actions)
        self.pagination = QFrame()
        paging = QHBoxLayout(self.pagination)
        paging.setContentsMargins(0, 0, 0, 0)
        paging.setSpacing(8)
        paging.addStretch(1)
        self.first_btn = QPushButton("首页")
        self.first_btn.clicked.connect(lambda: self._go_page(1))
        self.previous_btn = QPushButton("上一页")
        self.previous_btn.clicked.connect(lambda: self._go_page(self._search_page - 1))
        self.page_label = QLabel()
        self.next_btn = QPushButton("下一页")
        self.next_btn.clicked.connect(lambda: self._go_page(self._search_page + 1))
        for widget in (self.first_btn, self.previous_btn, self.page_label, self.next_btn):
            paging.addWidget(widget)
        body.addWidget(self.pagination)
        output_actions = QHBoxLayout()
        output_actions.addWidget(self.local_btn)
        output_actions.addWidget(self.open_btn)
        output_actions.addStretch(1)
        output_actions.addWidget(self.save_btn)
        body.addLayout(output_actions)
        layout.addWidget(card, 1)

        feedback = QHBoxLayout()
        self.status_label = QLabel("粘贴单品链接后自动保存商品资料与图片；关键词搜索结果可勾选后批量入库")
        self.status_label.setWordWrap(True)
        feedback.addWidget(self.status_label, 1)
        self.retry_btn = QPushButton("重试")
        self.retry_btn.clicked.connect(self._retry)
        feedback.addWidget(self.retry_btn)
        self.cancel_btn = QPushButton("停止")
        self.cancel_btn.clicked.connect(self._stop)
        feedback.addWidget(self.cancel_btn)
        layout.addLayout(feedback)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        layout.addWidget(self.progress)
        self.progress.hide()
        self._sync_actions()

    def embed_input(self):
        self.input_edit.hide()
        self.parse_btn.hide()

    def submit_text(self, text):
        self._start(lambda: xianyu_shopping.parse(text, on_log=self._log,
                                                is_cancelled=self._cancel.is_set))

    def submit_auto(self, text):
        """统一入口：商品链接解析，普通文本搜索。"""
        if not xianyu_shopping.is_item_reference(text):
            return self.submit_search(text)
        return self.submit_text(text)

    def submit_search(self, text):
        text = str(text or "").strip()
        self._start(lambda: xianyu_shopping.search(text, on_log=self._log,
                                                 is_cancelled=self._cancel.is_set, page=1),
                    search=(text, 1, True))

    def _go_page(self, page):
        if self._busy or not self._search_page or page < 1:
            return
        if page in self._search_pages:
            self._show_search_page(page)
            return
        current = self._search_pages[self._search_page]
        if page != self._search_page + 1 or not current.get("has_more"):
            return
        keyword = self._search_keyword
        self._start(lambda: xianyu_shopping.search(keyword, on_log=self._log,
                                                 is_cancelled=self._cancel.is_set, page=page),
                    search=(keyword, page, False))

    def _show_search_page(self, page):
        result = self._search_pages[page]
        self._search_page = page
        self._rows = result["items"]
        self.status_label.setText(result.get("summary", "已读取缓存商品"))
        self._retry_action = None
        self._draw()

    def _retry(self):
        if not self._busy and self._retry_action:
            self._retry_action()

    def _start(self, operation, *, saving=False, search=None):
        if self._busy:
            return
        self._cancel.clear()
        self._retry_action = None
        self._busy = True
        self.parse_btn.setEnabled(False)  # 合并页监听此状态，锁定平台与输入操作。
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status_label.setText(
            "正在保存闲鱼商品资料与图片" if saving else
            (f"正在抓取闲鱼第 {search[1]} 页商品" if search else "正在抓取闲鱼商品资料"))
        self._sync_actions()

        def done(result, error):
            self._busy = False
            self.parse_btn.setEnabled(True)
            self.progress.hide()
            self._sync_actions()
            if error:
                self.status_label.setText(error)
                self._retry_action = lambda: self._start(operation, saving=saving, search=search)
                self._sync_actions()
                return
            self.status_label.setText(result.get("summary", "已完成"))
            if saving:
                completed = {row["item_id"] for row in result.get("items", []) if row.get("saved")}
                self._checks.difference_update(completed)
                for row in self._known_rows():
                    if row["item_id"] in completed:
                        row["saved"] = True
                errors = result.get("failures", [])
                if errors:
                    self.status_label.setText(result["summary"] + "；" + errors[0]["error"])
                self._draw()
            elif search is not None:
                keyword, page, reset = search
                if reset:
                    self._search_pages.clear()
                    self._checks.clear()
                self._search_keyword = keyword
                self._search_pages[page] = dict(result, items=list(result.get("items", [])))
                self._show_search_page(page)
            else:
                self._search_keyword = ""
                self._search_page = 0
                self._search_pages.clear()
                self._rows = list(result.get("items", []))
                self._checks.clear()
                self._draw()

        run_bg(operation, done, owner=self)

    def _log(self, message):
        post_to_main(lambda: self.status_label.setText(str(message)))

    def _stop(self):
        self._cancel.set()
        self.cancel_btn.setEnabled(False)
        self.status_label.setText("已请求停止，将保留已保存的商品资料")

    def _current(self):
        item = self.table.item(self.table.currentRow(), 1)
        return item.data(Qt.UserRole) if item else None

    def _open_item(self):
        if row := self._current():
            # URL 仅来自 L3 规范化的官方商品链接。
            QDesktopServices.openUrl(QUrl(row["url"]))

    def _checked(self, item):
        if self._drawing or item.column() != 0:
            return
        row = item.data(Qt.UserRole)
        if row:
            if item.checkState() == Qt.Checked:
                if row["item_id"] not in self._checks and len(self._checks) >= 30:
                    self._drawing = True
                    try:
                        item.setCheckState(Qt.Unchecked)
                    finally:
                        self._drawing = False
                    self.status_label.setText("每次最多勾选30件，可先入库后继续勾选")
                else:
                    self._checks.add(row["item_id"])
            else:
                self._checks.discard(row["item_id"])
        self._sync_actions()

    def _set_checks(self, checked):
        if checked:
            ids = [row["item_id"] for row in self._rows if row["item_id"] not in self._checks]
            available = max(0, 30 - len(self._checks))
            self._checks.update(ids[:available])
            if len(ids) > available:
                self.status_label.setText("每次最多勾选30件，可先入库后继续勾选")
        else:
            self._checks.clear()
        self._draw()

    def _known_rows(self):
        """当前搜索的全部已访问页；只用于展示状态和跨页勾选。"""
        if self._search_page:
            return [row for result in self._search_pages.values() for row in result["items"]]
        return self._rows

    def _sort_changed(self, *_):
        self._draw()

    def _draw(self):
        current = self._current()
        current_id = current.get("item_id") if current else ""
        rows = list(self._rows)
        keys = {1: "title", 2: "price", 3: "seller", 4: "location", 5: "saved"}
        if self._sorter.col in keys:
            key = keys[self._sorter.col]
            rows = table_sort.sort_records(rows, lambda row: table_sort.sort_key(row.get(key)),
                                            self._sorter.ascending)
        self._drawing = True
        try:
            self.table.setRowCount(len(rows))
            for index, row in enumerate(rows):
                check = QTableWidgetItem()
                check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
                check.setData(Qt.UserRole, row)
                check.setCheckState(Qt.Checked if row["item_id"] in self._checks else Qt.Unchecked)
                self.table.setItem(index, 0, check)
                values = (row.get("title"), row.get("price"), row.get("seller"),
                          row.get("location"), "已入库" if row.get("saved") else "待入库")
                for col, value in enumerate(values, 1):
                    cell = QTableWidgetItem(str(value or "—"))
                    cell.setToolTip(str(value or "") + ("\n" + row["url"] if col == 1 else ""))
                    cell.setData(Qt.UserRole, row)
                    self.table.setItem(index, col, cell)
                if row["item_id"] == current_id:
                    self.table.selectRow(index)
        finally:
            self._drawing = False
        self.table.setVisible(bool(rows))
        self.empty_label.setVisible(not rows)
        if not rows:
            self.empty_label.setText("当前没有商品，可更换关键词重试，或点「本地资料」查看已保存商品")
        self._sync_actions()

    def _sync_actions(self):
        self.save_btn.setEnabled(not self._busy and bool(self._checks))
        self.save_btn.setText(f"入库并下载 {len(self._checks)} 件" if self._checks else "入库并下载")
        self.selection_label.setText(f"已勾选 {len(self._checks)} 件" if self._checks else "")
        self.all_btn.setEnabled(not self._busy and bool(self._rows))
        self.clear_btn.setEnabled(not self._busy and bool(self._checks))
        self.input_edit.setEnabled(not self._busy)
        self.local_btn.setEnabled(not self._busy)
        self.open_btn.setEnabled(not self._busy and bool(self._current()))
        self.cancel_btn.setEnabled(self._busy and not self._cancel.is_set())
        self.retry_btn.setVisible(bool(self._retry_action))
        self.retry_btn.setEnabled(not self._busy)
        self.pagination.setVisible(bool(self._search_page))
        self.first_btn.setEnabled(not self._busy and self._search_page > 1)
        self.previous_btn.setEnabled(not self._busy and self._search_page > 1)
        current = self._search_pages.get(self._search_page, {})
        self.next_btn.setEnabled(not self._busy and bool(current.get("has_more")))
        self.page_label.setText(f"第 {self._search_page} 页 · 本页 {len(self._rows)} 件")

    def _save(self):
        items = [{"item_id": item_id} for item_id in dict.fromkeys(
            row["item_id"] for row in self._known_rows() if row["item_id"] in self._checks)]
        if self._busy or not items:
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle("商品入库")
        confirm.setText(f"将 {len(items)} 件闲鱼商品保存到本地资料库？")
        confirm.setInformativeText("将读取商品详情并下载图片到「闲鱼商品」目录；已完整保存的商品会跳过。")
        cancel = confirm.addButton("取消", QMessageBox.RejectRole)
        confirm.addButton("入库并下载", QMessageBox.AcceptRole)
        confirm.setDefaultButton(cancel)
        confirm.setEscapeButton(cancel)
        confirm.exec()
        clicked = confirm.clickedButton()
        if clicked is not None and confirm.buttonRole(clicked) == QMessageBox.ButtonRole.AcceptRole:
            self._start(lambda: xianyu_shopping.save_items(items, on_log=self._log,
                                                          is_cancelled=self._cancel.is_set()), saving=True)

    def has_active_tasks(self):
        return self._busy

    def on_theme_changed(self):
        # 主题切换会改变表格边框占用，沿用统一列宽控制器重新适配视口。
        self._columns.resume()
        self._columns.timer.start(0)
