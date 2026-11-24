"""QQ群人数订阅弹窗。

入口：粉丝群页的「QQ群订阅」按钮。
数据源：QQ群资料分享页（每个群一条分享链接，无需登录）。
定时刷新复用「任务 → 粉丝群快照」，本弹窗只负责订阅名单、手动刷新与导出。
"""

from __future__ import annotations

import os
import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from gui_qt import table_sort
from gui_qt.common import post_to_main, run_bg
from toolbox import qq_groups_service as service

_COLUMNS = ["群名", "群号", "人数", "变化", "状态", "来源"]
_HINT = ("粘贴群号即可订阅（需先完成 QQ 群登录，任意群都能按群号查人数）；"
         "也可以粘贴 QQ 群资料分享链接，分享链接免登录但每个群要各自一条。")


class _BulkImportDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("批量导入分享链接")
        self.resize(760, 500)
        self.setMinimumSize(520, 360)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        hint = QLabel("每行粘贴一个 QQ 群号或群资料分享链接；按群号自动匹配官方目录，"
                      "匹配不到就追加到名单末尾。群号需要先完成 QQ 群登录。")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(
            "https://qun.qq.com/universal-share/share?ac=1&authKey=...\n"
            "https://qun.qq.com/universal-share/share?ac=1&authKey=...")
        self.edit.setMinimumHeight(240)
        layout.addWidget(self.edit, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("导入")
        buttons.button(QDialogButtonBox.Ok).setProperty("variant", "primary")
        buttons.button(QDialogButtonBox.Ok).setDefault(True)
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self):
        if not self.urls():
            QMessageBox.warning(self, "没有链接", "请至少粘贴一条 QQ 群分享链接。")
            return
        self.accept()

    def urls(self) -> list[str]:
        return [line.strip() for line in self.edit.toPlainText().splitlines()
                if line.strip()]


class QQGroupsDialog(QDialog):
    def __init__(self, page):
        super().__init__(page)
        self.page = page
        self.setWindowTitle("QQ群订阅")
        self.setModal(False)
        self.resize(1040, 720)
        self.setMinimumSize(760, 520)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        self._rows: list[service.QQGroupRow] = []
        self._deltas: dict[str, tuple[int | None, bool]] = {}
        self._busy = False
        self._cancel: threading.Event | None = None
        self._positioned = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        hint = QLabel(_HINT)
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        toolbar = QFrame()
        toolbar.setProperty("variant", "toolbar")
        tv = QVBoxLayout(toolbar)
        tv.setContentsMargins(12, 12, 12, 12)
        tv.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText(
            "粘贴 QQ 群号或分享链接（群号需先完成 QQ 群登录）")
        self.input.returnPressed.connect(self._save_link)
        row.addWidget(self.input, 1)

        self.bulk_btn = QPushButton("批量导入")
        self.bulk_btn.clicked.connect(self._bulk_import)
        row.addWidget(self.bulk_btn)

        self.refresh_btn = QPushButton("刷新全部")
        self.refresh_btn.clicked.connect(self._refresh)
        row.addWidget(self.refresh_btn)

        # 一键下载头像：摆在工具条上，不再藏进「更多」
        self.avatar_btn = QPushButton("下载头像")
        self.avatar_btn.setToolTip("把全部已订阅群的群头像下载到各自群目录的 头像/ 里")
        self.avatar_btn.clicked.connect(self._download_avatars)
        row.addWidget(self.avatar_btn)

        self.history_btn = QPushButton("人数历史")
        self.history_btn.setToolTip("查看选中群的历史人数（存在 qq_groups.db 里）")
        self.history_btn.clicked.connect(self._show_history)
        row.addWidget(self.history_btn)

        self.more_btn = QPushButton("更多")
        self.more_btn.setProperty("menuButton", True)
        more = QMenu(self)
        export_action = more.addAction("导出 Excel")
        export_action.triggered.connect(self._export)
        dir_action = more.addAction("打开保存目录")
        dir_action.triggered.connect(self._open_dir)
        avatar_action = more.addAction("重下群头像（覆盖已有）")
        avatar_action.triggered.connect(self._refresh_avatars)
        more.addSeparator()
        self.login_action = more.addAction("按群号订阅全部（需 QQ 群登录）")
        self.login_action.triggered.connect(self._subscribe_catalog_by_login)
        self.delete_action = more.addAction("删除订阅")
        self.delete_action.setEnabled(False)
        self.delete_action.triggered.connect(self._delete)
        self.more_btn.setMenu(more)
        row.addWidget(self.more_btn)

        self.save_btn = QPushButton("保存链接")
        self.save_btn.setProperty("variant", "primary")
        self.save_btn.clicked.connect(self._save_link)
        row.addWidget(self.save_btn)
        tv.addLayout(row)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(True)
        progress_row.addWidget(self.progress, 1)
        self.stop_btn = QPushButton("停止")
        self.stop_btn.setVisible(False)
        self.stop_btn.clicked.connect(self._stop)
        progress_row.addWidget(self.stop_btn)
        tv.addLayout(progress_row)

        self.status = QLabel("")
        self.status.setProperty("role", "caption")
        self.status.setWordWrap(True)
        tv.addWidget(self.status)
        layout.addWidget(toolbar)

        self.table = QTableWidget(0, len(_COLUMNS))
        self.table.setHorizontalHeaderLabels(_COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for index, width in ((1, 110), (2, 80), (3, 70), (4, 190), (5, 90)):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
            self.table.setColumnWidth(index, width)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self._sorter = table_sort.install(self.table, rows=self._rows,
                                          skip_columns=())
        layout.addWidget(self.table, 1)

        self.footnote = QLabel("定时刷新在「任务 → 粉丝群快照」设置；QQ 群与粉丝群共用该任务。")
        self.footnote.setProperty("role", "caption")
        self.footnote.setWordWrap(True)
        layout.addWidget(self.footnote)

        self._reload_rows()

    # ---------- 数据 ----------
    def _reload_rows(self):
        rows = service.catalog()
        for row in rows:
            if row.no in self._deltas:
                row.change, row.is_new = self._deltas[row.no]
        self._apply_rows(rows)
        subscribed = sum(1 for row in rows if row.share_url)
        if not self.status.text():
            self.status.setText(
                f"共 {len(rows)} 个群，已订阅 {subscribed} 个；未订阅的群不会抓人数。"
                "登录后点「更多 → 按群号订阅全部」可一次订阅 8 个预置群。")
        sync = getattr(self.page, "_refresh_task_label", None)
        if callable(sync):
            sync()
        view = getattr(self.page, "_refresh_qq_view", None)
        if callable(view):
            view()

    def _apply_rows(self, rows: list[service.QQGroupRow]):
        self._rows = rows
        self._sorter.attach(self._rows)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            name = QTableWidgetItem(row.name or "-")
            name.setToolTip(row.name or "")
            self.table.setItem(index, 0, name)
            number = QTableWidgetItem(row.no)
            number.setTextAlignment(Qt.AlignCenter)
            self.table.setItem(index, 1, number)
            member = QTableWidgetItem(row.member_text)
            member.setTextAlignment(Qt.AlignCenter)
            if row.member_count is not None:
                member.setData(table_sort.SORT_KEY_ROLE, row.member_count)
            self.table.setItem(index, 2, member)
            delta = QTableWidgetItem(row.delta_text)
            delta.setTextAlignment(Qt.AlignCenter)
            if row.change is not None:
                delta.setData(table_sort.SORT_KEY_ROLE, row.change)
            self.table.setItem(index, 3, delta)
            status = QTableWidgetItem(row.status or "-")
            status.setTextAlignment(Qt.AlignCenter)
            status.setToolTip(f"{row.status} {row.fetched_at}".strip())
            self.table.setItem(index, 4, status)
            if row.source == "share":
                link_text = "分享链接"
                link_tip = "已保存该群的资料分享链接（不显示链接内容）"
            elif row.source == "login":
                link_text = "登录查询"
                link_tip = "已按群号订阅，刷新时使用 QQ 群登录态读取群资料"
            else:
                link_text = "未订阅"
                link_tip = "粘贴该群群号（需 QQ 群登录）或资料分享链接即可订阅"
            link = QTableWidgetItem(link_text)
            link.setTextAlignment(Qt.AlignCenter)
            link.setToolTip(link_tip)
            self.table.setItem(index, 5, link)
        self._sorter.apply()
        self._on_selection()

    def _selected(self) -> service.QQGroupRow | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def _on_selection(self):
        row = self._selected()
        self.delete_action.setEnabled((not self._busy) and row is not None)
        self.history_btn.setEnabled((not self._busy) and row is not None)

    # ---------- 状态 ----------
    def _set_busy(self, busy: bool, text: str = "", indeterminate: bool = False,
                  total: int = 0):
        self._busy = busy
        for widget in (self.input, self.save_btn, self.bulk_btn,
                       self.refresh_btn, self.avatar_btn, self.more_btn):
            widget.setEnabled(not busy)
        self.history_btn.setEnabled(not busy and self._selected() is not None)
        self.progress.setVisible(busy)
        if busy:
            self.progress.setRange(0, 0 if indeterminate else max(1, total))
            self.progress.setValue(0)
        self.stop_btn.setVisible(busy and self._cancel is not None)
        self.delete_action.setEnabled((not busy) and self._selected() is not None)
        if text:
            self.status.setText(text)

    def _set_progress(self, index: int, total: int, label: str):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(index)
        self.status.setText(f"正在处理 {index}/{total}：{label}")

    def _stop(self):
        if self._cancel is not None:
            self._cancel.set()
            self.stop_btn.setEnabled(False)
            self.status.setText("正在停止，当前请求完成后结束...")

    # ---------- 操作 ----------
    def _save_link(self):
        if self._busy:
            return
        text = self.input.text().strip()
        if not text:
            self.status.setText("请先粘贴 QQ 群分享链接")
            return
        self._cancel = None
        self._set_busy(True, "正在读取分享链接...", indeterminate=True)

        def done(result, err):
            if err:
                self._set_busy(False, f"保存失败：{err}")
                return
            self.input.clear()
            self._deltas[result.no] = (result.change, result.is_new)
            self._reload_rows()
            for index, row in enumerate(self._rows):
                if row.no == result.no:
                    self.table.selectRow(index)
                    break
            self._set_busy(False, f"已更新：{result.name}（{result.member_text} 人）")

        run_bg(lambda: service.save_link(text), done, owner=self)

    def _bulk_import(self):
        if self._busy:
            return
        dialog = _BulkImportDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return
        urls = dialog.urls()
        if not urls:
            return
        self._cancel = threading.Event()
        self._set_busy(True, f"正在导入 {len(urls)} 条分享链接...",
                       total=len(urls))

        def work():
            results, errors = [], []
            for index, url in enumerate(urls, 1):
                if self._cancel is not None and self._cancel.is_set():
                    break
                post_to_main(self._set_progress, index, len(urls),
                             f"第 {index} 条")
                try:
                    results.append(service.save_link(url))
                except Exception as exc:  # noqa: BLE001 - 逐条返回错误
                    errors.append((index, str(exc)))
            return {"results": results, "errors": errors,
                    "cancelled": bool(self._cancel and self._cancel.is_set())}

        def done(result, err):
            if err:
                self._set_busy(False, f"批量导入失败：{err}")
                return
            for row in result["results"]:
                self._deltas[row.no] = (row.change, row.is_new)
            self._reload_rows()
            errors = result["errors"]
            if result["cancelled"]:
                text = f"已取消（完成 {len(result['results'])}/{len(urls)} 条）"
            elif errors:
                text = (f"已导入 {len(result['results'])}/{len(urls)} 条，"
                        f"失败 {len(errors)} 条：{errors[0][1]}")
            else:
                text = f"已导入 {len(result['results'])} 条分享链接"
            self._set_busy(False, text)

        run_bg(work, done, owner=self)

    def _subscribe_catalog_by_login(self):
        if self._busy:
            return
        self._cancel = threading.Event()
        total = len(service.catalog())
        self._set_busy(True, "正在按群号订阅预置群...", total=total)

        def progress(index, count, label):
            post_to_main(self._set_progress, index, count, label)

        def work():
            return service.subscribe_catalog_by_login(
                progress=progress, cancel=self._cancel)

        def done(result, err):
            if err:
                self._set_busy(False, f"按群号订阅失败：{err}")
                return
            for row in result["results"]:
                self._deltas[row.no] = (row.change, row.is_new)
            self._reload_rows()
            errors = result["errors"]
            if result["cancelled"]:
                text = f"已取消（完成 {result['ok']}/{result['total']} 个群）"
            elif errors and not result["ok"]:
                text = f"按群号订阅失败：{errors[0][1]}"
            elif errors:
                text = (f"已按群号订阅 {result['ok']}/{result['total']} 个群，"
                        f"失败 {len(errors)} 个：{errors[0][1]}")
            else:
                text = f"已按群号订阅 {result['ok']}/{result['total']} 个群"
            self._set_busy(False, text)

        run_bg(work, done, owner=self)

    def _download_avatars(self):
        """一键下载：只补缺失的头像，已有文件直接复用。"""
        if self._busy:
            return
        total = sum(1 for row in self._rows if row.share_url)
        if not total:
            self.status.setText("还没有订阅的群，先按群号或分享链接订阅")
            return
        self._cancel = None
        self._set_busy(True, "正在下载群头像...", total=total)

        def progress(index, count, label):
            post_to_main(self._set_progress, index, count, label)

        def done(result, err):
            if err:
                self._set_busy(False, f"下载头像失败：{err}")
                return
            self._reload_rows()
            if result["errors"]:
                self._set_busy(False, f"已下载 {result['ok']}/{result['total']} 个头像，"
                                      f"失败 {len(result['errors'])} 个")
            else:
                self._set_busy(False, f"已下载 {result['ok']}/{result['total']} 个群头像")

        run_bg(lambda: service.refresh_avatars(force=False, progress=progress),
               done, owner=self)

    def _show_history(self):
        row = self._selected()
        if row is None:
            self.status.setText("请先选中一个群")
            return
        items = service.history(row.no)
        dialog = QDialog(self)
        dialog.setWindowTitle(f"{row.name or row.no} · 人数历史")
        dialog.setModal(False)
        dialog.setAttribute(Qt.WA_DeleteOnClose, True)
        dialog.resize(720, 520)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        head = QLabel(
            f"群号 {row.no} · 共 {len(items)} 条记录（库文件 {service.db_file().name}，最新在前）")
        head.setProperty("role", "caption")
        head.setWordWrap(True)
        layout.addWidget(head)

        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels(["采集时间", "人数", "上限", "变化", "来源"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for index, width in ((1, 80), (2, 80), (3, 80), (4, 90)):
            header.setSectionResizeMode(index, QHeaderView.Interactive)
            table.setColumnWidth(index, width)
        table.setRowCount(len(items))
        source_text = {"login": "按群号", "share": "分享链接",
                       "task": "定时任务", "import": "导入历史"}
        for r, item in enumerate(items):
            count = item.get("member_count")
            prev = items[r + 1].get("member_count") if r + 1 < len(items) else None
            if isinstance(count, int) and isinstance(prev, int):
                change = count - prev
                change_text = f"+{change}" if change > 0 else str(change)
            else:
                change_text = "-"
            table.setItem(r, 0, QTableWidgetItem(str(item.get("fetched_at") or "")))
            for col, value in ((1, count), (2, item.get("member_limit")), (3, change_text)):
                cell = QTableWidgetItem("-" if value is None else str(value))
                cell.setTextAlignment(Qt.AlignCenter)
                table.setItem(r, col, cell)
            src = QTableWidgetItem(source_text.get(str(item.get("source") or ""),
                                                  item.get("source") or "-"))
            src.setTextAlignment(Qt.AlignCenter)
            table.setItem(r, 4, src)
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.button(QDialogButtonBox.Close).setText("关闭")
        buttons.rejected.connect(dialog.close)
        layout.addWidget(buttons)
        dialog.show()
        dialog.raise_()

    def _refresh_avatars(self):
        if self._busy:
            return
        total = sum(1 for row in self._rows if row.share_url)
        if not total:
            self.status.setText("还没有订阅的群，先按群号或分享链接订阅")
            return
        self._cancel = None
        self._set_busy(True, "正在重新下载群头像...", total=total)

        def progress(index, count, label):
            post_to_main(self._set_progress, index, count, label)

        def done(result, err):
            if err:
                self._set_busy(False, f"下载头像失败：{err}")
                return
            self._reload_rows()
            if result["errors"]:
                self._set_busy(False, f"已重下 {result['ok']}/{result['total']} 个头像，"
                                      f"失败 {len(result['errors'])} 个")
            else:
                self._set_busy(False, f"已重下 {result['ok']}/{result['total']} 个群头像")

        run_bg(lambda: service.refresh_avatars(progress=progress), done, owner=self)

    def _refresh(self):
        if self._busy:
            return
        total = sum(1 for row in self._rows if row.share_url)
        if not total:
            self.status.setText(
                "还没有配置群号或分享链接；可点「更多 → 按群号订阅全部」登录后批量订阅。")
            return
        self._cancel = threading.Event()
        self._set_busy(True, "正在刷新 QQ 群人数...", total=total)

        def progress(index, count, label):
            post_to_main(self._set_progress, index, count, label)

        def work():
            return service.refresh_all(progress=progress, cancel=self._cancel)

        def done(result, err):
            if err:
                self._set_busy(False, f"刷新失败：{err}")
                return
            for row in result["results"]:
                self._deltas[row.no] = (row.change, row.is_new)
            self._reload_rows()
            errors = result["errors"]
            if result["cancelled"]:
                text = f"已取消（完成 {result['ok']}/{result['total']} 个群）"
            elif errors:
                text = (f"已刷新 {result['ok']}/{result['total']} 个群，"
                        f"失败 {len(errors)} 个：{errors[0][1]}")
            else:
                text = f"已刷新 {result['ok']}/{result['total']} 个群"
            self._set_busy(False, text)

        run_bg(work, done, owner=self)

    def _delete(self):
        if self._busy:
            return
        row = self._selected()
        if row is None:
            self.status.setText("请先选中一行")
            return
        if not row.share_url:
            self.status.setText("该群还没有订阅来源")
            return
        box = QMessageBox(self)
        box.setWindowTitle("取消订阅")
        box.setText(f"取消订阅「{row.name}」？")
        box.setInformativeText("将停止定时刷新人数，已有快照保留。")
        cancel = box.addButton("取消", QMessageBox.RejectRole)
        confirm = box.addButton("取消订阅", QMessageBox.DestructiveRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() is not confirm:
            return
        if service.remove(row.no):
            self._deltas.pop(row.no, None)
            self._reload_rows()
            self.status.setText(f"已取消订阅：{row.name}（快照保留）")
        else:
            self.status.setText("取消失败：这条订阅已不存在")

    def _export(self):
        if self._busy:
            return
        rows = list(self._rows)
        if not any(row.share_url for row in rows):
            self.status.setText("还没有可导出的群")
            return
        self._cancel = None
        self._set_busy(True, "正在导出 Excel...", indeterminate=True)

        def done(path, err):
            if err:
                self._set_busy(False, f"导出失败：{err}")
                return
            self.status.setText(f"已导出：{os.path.basename(str(path))}")
            self.status.setToolTip(str(path))
            self._set_busy(False)

        run_bg(lambda: service.export_rows(rows), done, owner=self)

    def _open_dir(self):
        path = service.export_dir()
        try:
            if not path.exists():
                self.status.setText("保存目录尚未创建（先刷新一次人数）")
                return
            os.startfile(str(path))
        except OSError as exc:
            self.status.setText(f"打开目录失败：{exc}")

    def showEvent(self, event):
        super().showEvent(event)
        if not self._positioned:
            self._positioned = True
            screen = self.screen()
            if screen is not None:
                available = screen.availableGeometry()
                self.resize(min(self.width(), max(760, available.width() - 40)),
                            min(self.height(), max(520, available.height() - 40)))
            parent = self.parentWidget()
            if parent is not None:
                geometry = parent.window().frameGeometry()
                self.move(geometry.center().x() - self.width() // 2,
                          geometry.center().y() - self.height() // 2)
        if not self._busy:
            self._reload_rows()
