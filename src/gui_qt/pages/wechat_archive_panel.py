"""公众号资料和文章归档的 L2 表现层；数据与任务只经 wechat_library。"""
from __future__ import annotations

from PySide6.QtCore import QDate, QSignalBlocker, QTimer, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDateEdit,
    QFileDialog, QFrame, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt.common import run_bg
from gui_qt.table_columns import configure_table_columns, install as install_columns
from gui_qt.table_sort import install_callback

PAGE_SIZE = 200
ACCOUNT_COLUMNS = (
    ("name", "公众号"), ("remark", "备注"),
    ("article_count", "文章数"), ("last_sync", "最近同步"), ("biz", "账号标识"),
)
ARTICLE_COLUMNS = (
    ("", ""), ("title", "标题"), ("account", "公众号"),
    ("publish_time", "发布时间"), ("status", "状态"),
)


def _caption(text):
    label = QLabel(text)
    label.setProperty("role", "caption")
    label.setWordWrap(True)
    return label


def _card(parent):
    card = QFrame(parent)
    card.setProperty("variant", "card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(12)
    return card, layout


class WechatArchivePanel(QWidget):
    """两个页签共享宿主页的后台任务、格式、进度及取消状态。"""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._busy = False
        self._account_offset = self._article_offset = 0
        self._account_total = self._article_total = 0
        self._account_sort = self._article_sort = ("", "")
        self._accounts = []
        self._articles = []
        self._checked = set()
        self._versions = {1: 0, 2: 0}
        self._loading_timers = {}
        for index in (1, 2):
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda i=index: self._show_loading(i))
            self._loading_timers[index] = timer
        self._filling = False
        self._job_buttons = []
        install_columns()
        self.accounts_page = self._build_accounts()
        self.articles_page = self._build_articles()

    def _button(self, text, icon, slot, primary=False, job=False):
        button = self.owner._icon_button(text, icon, "primary" if primary else "secondary")
        button.clicked.connect(slot)
        if job:
            self._job_buttons.append(button)
        return button

    @staticmethod
    def _table(parent, columns):
        table = QTableWidget(0, len(columns), parent)
        table.setHorizontalHeaderLabels([label for _, label in columns])
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setWordWrap(False)
        table.setTextElideMode(Qt.ElideRight)
        table.verticalHeader().hide()
        table.horizontalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(32)
        return table

    def _pager(self, layout, kind):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addStretch(1)
        buttons = []
        for text, icon, delta in (("首页", "skip_prev", "first"),
                                  ("上一页", "arrow_left", -1)):
            button = self._button(text, icon, lambda checked=False, d=delta: self._page(kind, d))
            row.addWidget(button)
            buttons.append(button)
        label = _caption("第 1 / 1 页 · 共 0 条")
        row.addWidget(label)
        for text, icon, delta in (("下一页", "arrow", 1),
                                  ("末页", "skip_next", "last")):
            button = self._button(text, icon, lambda checked=False, d=delta: self._page(kind, d))
            row.addWidget(button)
            buttons.append(button)
        layout.addLayout(row)
        return label, buttons

    def _build_accounts(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(12)
        card, layout = _card(page)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("文章链接"))
        self.account_link = QLineEdit()
        self.account_link.setPlaceholderText("粘贴一篇文章，识别公众号并归档文章")
        row.addWidget(self.account_link, 1)
        row.addWidget(self._button("识别并归档", "plus", self._collect, True, True))
        layout.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("搜索"))
        self.account_keyword = QLineEdit()
        self.account_keyword.setPlaceholderText("公众号、备注或账号标识")
        self.account_keyword.returnPressed.connect(self._filter_accounts)
        row.addWidget(self.account_keyword, 1)
        row.addWidget(self._button("刷新", "refresh", self._filter_accounts))
        layout.addLayout(row)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        actions.addWidget(self._button("编辑备注", "edit", self._edit_remark, job=True))
        actions.addWidget(self._button("查看文章", "document", self._view_account))
        actions.addWidget(self._button("继续抓取", "skip_next", lambda: self._sync("continue"), job=True))
        actions.addWidget(self._button("同步最新", "refresh", lambda: self._sync("latest"), True, True))
        layout.addLayout(actions)
        self.accounts_table = self._table(card, ACCOUNT_COLUMNS)
        self.accounts_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.accounts_table.cellDoubleClicked.connect(lambda *_: self._view_account())
        install_callback(self.accounts_table, self._sort_accounts, skip_columns=(), tri_state=True)
        configure_table_columns(self.accounts_table, minimums={0: 110, 1: 90, 2: 64, 3: 120, 4: 90},
                                stretch=[0, 1], auto_fit=True)
        layout.addWidget(self.accounts_table, 1)
        footer = QHBoxLayout()
        self.account_empty = _caption("还没有公众号资料，粘贴文章链接后点「识别并归档」")
        footer.addWidget(self.account_empty, 1)
        layout.addLayout(footer)
        self.account_pager, self.account_pages = self._pager(layout, 1)
        outer.addWidget(card, 1)
        return page

    def _build_articles(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 12, 0, 0)
        outer.setSpacing(12)
        card, layout = _card(page)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("公众号"))
        self.account_filter = QComboBox()
        self.account_filter.setEditable(True)
        self.account_filter.setInsertPolicy(QComboBox.NoInsert)
        self.account_filter.addItem("全部", "")
        self.account_filter.setMinimumWidth(130)
        self.account_filter.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.account_filter.setMinimumContentsLength(8)
        self.account_filter.setToolTip("选择公众号；也可输入完整账号标识")
        row.addWidget(self.account_filter)
        row.addWidget(QLabel("状态"))
        self.status_filter = QComboBox()
        self.status_filter.setMinimumWidth(130)
        self.status_filter.addItem("全部", "")
        for key, label in (("archived", "已归档"), ("exported", "已导出"),
                           ("failed", "失败"), ("pdf_pending", "待生成 PDF")):
            self.status_filter.addItem(label, key)
        row.addWidget(self.status_filter)
        row.addWidget(QLabel("标题"))
        self.article_keyword = QLineEdit()
        self.article_keyword.setPlaceholderText("搜索文章标题")
        self.article_keyword.returnPressed.connect(self._filter_articles)
        row.addWidget(self.article_keyword, 1)
        row.addWidget(self._button("筛选", "search", self._filter_articles, True))
        layout.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.date_from_on = QCheckBox("起始日期")
        self.date_to_on = QCheckBox("结束日期")
        self.date_from = QDateEdit(QDate.currentDate().addMonths(-1))
        self.date_to = QDateEdit(QDate.currentDate())
        for check, date in ((self.date_from_on, self.date_from), (self.date_to_on, self.date_to)):
            date.setCalendarPopup(True)
            date.setDisplayFormat("yyyy-MM-dd")
            date.setEnabled(False)
            check.toggled.connect(date.setEnabled)
            row.addWidget(check)
            row.addWidget(date)
        row.addStretch(1)
        self.selection_label = _caption("已勾选 0 篇")
        row.addWidget(self.selection_label)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("导出格式"))
        for key, text in (("html", "HTML"), ("pdf", "PDF"), ("docx", "Word"), ("md", "MD")):
            original = self.owner.format_checks[key]
            check = QCheckBox(text)
            check.setChecked(original.isChecked())
            check.toggled.connect(original.setChecked)
            original.toggled.connect(check.setChecked)
            row.addWidget(check)
        row.addStretch(1)
        layout.addLayout(row)
        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        actions.addWidget(self._button("勾选本页", "select_all", self._check_page))
        actions.addWidget(self._button("清除勾选", "minus", self._clear_checked))
        actions.addWidget(self._button("导出清单", "list", self._export_index, job=True))
        actions.addWidget(self._button("重试失败", "refresh", self._retry_failed, job=True))
        actions.addWidget(self._button("导出勾选", "download", self._export_selected, True, True))
        layout.addLayout(actions)
        self.articles_table = self._table(card, ARTICLE_COLUMNS)
        self.articles_table.itemChanged.connect(self._article_checked)
        self.articles_table.cellDoubleClicked.connect(self._open_article)
        install_callback(self.articles_table, self._sort_articles, skip_columns=(0,), tri_state=True)
        configure_table_columns(self.articles_table, fixed={0: 36},
                                minimums={1: 150, 2: 100, 3: 120, 4: 90}, stretch=[1], auto_fit=True)
        layout.addWidget(self.articles_table, 1)
        footer = QHBoxLayout()
        self.article_empty = _caption("还没有文章记录，可在「公众号资料」识别文章或同步最新")
        footer.addWidget(self.article_empty, 1)
        layout.addLayout(footer)
        self.article_pager, self.article_pages = self._pager(layout, 2)
        outer.addWidget(card, 1)
        self.account_filter.currentIndexChanged.connect(self._filter_articles)
        self.account_filter.lineEdit().editingFinished.connect(self._filter_articles)
        self.status_filter.currentIndexChanged.connect(self._filter_articles)
        self.date_from_on.toggled.connect(self._filter_articles)
        self.date_to_on.toggled.connect(self._filter_articles)
        self.date_from.dateChanged.connect(self._filter_articles)
        self.date_to.dateChanged.connect(self._filter_articles)
        return page

    def set_busy(self, busy):
        self._busy = bool(busy)
        for button in self._job_buttons:
            button.setEnabled(not busy)

    def refresh_all(self):
        self.refresh(1)
        self.refresh(2)

    def refresh(self, index):
        if index not in (1, 2):
            return
        from toolbox import wechat_library as library
        self._versions[index] += 1
        version = self._versions[index]
        self._loading_timers[index].start(1000)
        if index == 1:
            keyword = self.account_keyword.text().strip()
            sort, order = self._account_sort
            offset = self._account_offset
            query = lambda: library.list_accounts(keyword=keyword, sort=sort, order=order,
                                                  offset=offset, limit=PAGE_SIZE)
        else:
            filters = self._article_filters()
            offset = self._article_offset
            query = lambda: library.list_articles(**filters, offset=offset, limit=PAGE_SIZE)
        run_bg(query, lambda result, error: self._show_result(index, version, result, error), owner=self.owner)

    def _show_loading(self, index):
        empty = self.account_empty if index == 1 else self.article_empty
        empty.setText("读取中…")

    def _show_result(self, index, version, result, error):
        if version != self._versions[index]:
            return
        self._loading_timers[index].stop()
        table = self.accounts_table if index == 1 else self.articles_table
        empty = self.account_empty if index == 1 else self.article_empty
        if error:
            empty.setText("读取失败，请点刷新或筛选重试")
            return
        result = result or {}
        rows = result.get("items") or []
        total = int(result.get("total") or 0)
        selected_biz = (self._account_record() or {}).get("biz") if index == 1 else None
        self._filling = True
        table.setRowCount(len(rows))
        columns = ACCOUNT_COLUMNS if index == 1 else ARTICLE_COLUMNS
        for row_index, record in enumerate(rows):
            for col, (key, _) in enumerate(columns):
                item = QTableWidgetItem()
                if index == 2 and col == 0:
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                    item.setCheckState(Qt.Checked if record["id"] in self._checked else Qt.Unchecked)
                    item.setData(Qt.UserRole, record["id"])
                else:
                    value = record.get(key, "")
                    if key == "status":
                        from toolbox.wechat_library import STATUS_LABELS
                        value = STATUS_LABELS.get(value, value)
                    text = str(value if value is not None else "")
                    item.setText(text or "—")
                    item.setToolTip(str(record.get("error") or text) if key == "status" else text)
                table.setItem(row_index, col, item)
            if selected_biz and record.get("biz") == selected_biz:
                table.selectRow(row_index)
        self._filling = False
        table.horizontalHeader().setVisible(bool(rows))
        if index == 1:
            self._accounts = rows
            self._account_total = total
            empty.setText("" if rows else "没有匹配的公众号，可清空搜索或粘贴文章链接后识别")
            self._update_account_options(rows)
        else:
            self._articles = rows
            self._article_total = total
            empty.setText("" if rows else "没有匹配的文章，可调整筛选，或在「公众号资料」同步最新")
        self._update_pager(index)

    def _update_account_options(self, rows):
        selected = self._filter_biz()
        combo = self.account_filter
        blocker = QSignalBlocker(combo)
        try:
            current = {combo.itemData(i): i for i in range(combo.count())}
            for record in rows:
                biz = record["biz"]
                label = record.get("name") or biz
                if biz in current:
                    combo.setItemText(current[biz], label)
                else:
                    current[biz] = combo.count()
                    combo.addItem(label, biz)
            if selected and selected in current:
                combo.setCurrentIndex(current[selected])
        finally:
            del blocker

    def _update_pager(self, kind):
        offset = self._account_offset if kind == 1 else self._article_offset
        total = self._account_total if kind == 1 else self._article_total
        label = self.account_pager if kind == 1 else self.article_pager
        buttons = self.account_pages if kind == 1 else self.article_pages
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        label.setText(f"第 {offset // PAGE_SIZE + 1} / {pages} 页 · 共 {total:,} 条".replace(",", " "))
        for button in buttons[:2]:
            button.setEnabled(offset > 0)
        for button in buttons[2:]:
            button.setEnabled(offset + PAGE_SIZE < total)

    def _page(self, kind, delta):
        attr = "_account_offset" if kind == 1 else "_article_offset"
        total = self._account_total if kind == 1 else self._article_total
        offset = getattr(self, attr)
        maximum = max(0, ((total - 1) // PAGE_SIZE) * PAGE_SIZE)
        offset = 0 if delta == "first" else maximum if delta == "last" else offset + delta * PAGE_SIZE
        setattr(self, attr, min(maximum, max(0, offset)))
        self.refresh(kind)

    def _filter_accounts(self):
        self._account_offset = 0
        self.refresh(1)

    def _filter_articles(self):
        self._article_offset = 0
        self.refresh(2)

    def _sort_accounts(self, column, ascending):
        self._account_sort = (ACCOUNT_COLUMNS[column][0], "asc" if ascending else "desc") if column >= 0 else ("", "")
        self._filter_accounts()

    def _sort_articles(self, column, ascending):
        self._article_sort = (ARTICLE_COLUMNS[column][0], "asc" if ascending else "desc") if column >= 0 else ("", "")
        self._filter_articles()

    def _filter_biz(self):
        combo = self.account_filter
        if combo.currentIndex() >= 0 and combo.currentText() == combo.itemText(combo.currentIndex()):
            return str(combo.currentData() or "")
        return combo.currentText().strip()

    def _article_filters(self):
        sort, order = self._article_sort
        return dict(biz=self._filter_biz(), keyword=self.article_keyword.text().strip(),
                    date_from=self.date_from.date().toString("yyyy-MM-dd") if self.date_from_on.isChecked() else "",
                    date_to=self.date_to.date().toString("yyyy-MM-dd") if self.date_to_on.isChecked() else "",
                    status=self.status_filter.currentData() or "", sort=sort, order=order)

    def _account_record(self):
        row = self.accounts_table.currentRow()
        return self._accounts[row] if 0 <= row < len(self._accounts) else None

    def _require_account(self):
        record = self._account_record()
        if not record:
            self.owner.app.toast("请先选择一个公众号", "warning")
        return record

    def _collect(self):
        from toolbox import wechat_library as library
        text = self.account_link.text().strip()
        if not library.extract_urls(text):
            self.owner.app.toast("请填写有效的公众号文章链接", "warning")
            return
        self.owner.run_library_job(lambda **callbacks: library.collect_links(text, **callbacks))

    def _edit_remark(self):
        if self.owner.running:
            return
        record = self._require_account()
        if not record:
            return
        text, accepted = QInputDialog.getText(self.owner, "编辑备注", "备注", text=record.get("remark") or "")
        if not accepted:
            return
        from toolbox import wechat_library as library
        self.owner.run_library_job(lambda **callbacks: library.save_account(
            record["biz"], name=record.get("name") or "", remark=text))

    def _view_account(self):
        record = self._require_account()
        if not record:
            return
        index = self.account_filter.findData(record["biz"])
        if index < 0:
            self.account_filter.addItem(record.get("name") or record["biz"], record["biz"])
            index = self.account_filter.count() - 1
        self.account_filter.setCurrentIndex(index)
        self.owner.tabs.setCurrentIndex(2)
        self._filter_articles()

    def _sync(self, mode):
        record = self._require_account()
        if record:
            from toolbox import wechat_library as library
            self.owner.run_library_job(lambda **callbacks: library.sync_account(record["biz"], mode=mode, **callbacks))

    def _article_checked(self, item):
        if self._filling or item.column() != 0:
            return
        article_id = item.data(Qt.UserRole)
        if item.checkState() == Qt.Checked:
            self._checked.add(article_id)
        else:
            self._checked.discard(article_id)
        self.selection_label.setText(f"已勾选 {len(self._checked)} 篇")

    def _check_page(self):
        for row in range(self.articles_table.rowCount()):
            self.articles_table.item(row, 0).setCheckState(Qt.Checked)

    def _clear_checked(self):
        self._checked.clear()
        for row in range(self.articles_table.rowCount()):
            self.articles_table.item(row, 0).setCheckState(Qt.Unchecked)
        self.selection_label.setText("已勾选 0 篇")

    def _open_article(self, row, column):
        if 0 <= row < len(self._articles) and column != 0:
            QDesktopServices.openUrl(QUrl(self._articles[row]["url"]))

    def _export_selected(self):
        if not self._checked:
            self.owner.app.toast("请先勾选文章", "warning")
            return
        formats = self.owner.selected_formats()
        if not formats:
            self.owner.app.toast("请至少勾选一种导出格式", "warning")
            return
        from toolbox import wechat_library as library
        ids = sorted(self._checked)
        out_dir = self.owner._current_out_dir()
        self.owner.run_library_job(lambda **callbacks: library.export_articles(ids, formats, out_dir, **callbacks))

    def _retry_failed(self):
        formats = self.owner.selected_formats()
        if not formats:
            self.owner.app.toast("请至少勾选一种导出格式", "warning")
            return
        from toolbox import wechat_library as library
        biz = self._filter_biz()
        out_dir = self.owner._current_out_dir()
        self.owner.run_library_job(lambda **callbacks: library.retry_failed(biz, formats, out_dir, **callbacks))

    def _export_index(self):
        path, _ = QFileDialog.getSaveFileName(self.owner, "导出文章清单", "公众号文章清单.csv", "CSV 文件 (*.csv)")
        if not path:
            return
        from toolbox import wechat_library as library
        filters = self._article_filters()
        self.owner.run_library_job(lambda **callbacks: library.export_index(filters, path))
