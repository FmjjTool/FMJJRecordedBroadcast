# -*- coding: utf-8 -*-
"""网盘下载页（夸克 / 百度 / 移动云盘）。

浏览三大网盘目录，把选中的文件或整个文件夹下载到本地：

- 单线程 + Range 断点续传：直链过期自动重新申请，网络中断后从 `.part` 接着下；
- 已存在的同名同大小文件自动跳过，不重复消耗流量；
- 可选下载完成后用内置 7-Zip 解压 rar / 7z / zip 等压缩包。

登录状态由软件统一管理，本页不保存任何凭据。
视觉全部使用主题语义角色（ui-visual-spec），不写死颜色。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolSectionCard
from toolbox import sevenzip
from toolbox.quark import backup as qbackup
from toolbox.quark import config as qconfig
from toolbox.quark import download as qdownload

# 各网盘根目录标识：夸克用 fid "0"，百度 / 移动云盘用 "/"
ROOT_IDS = {"quark": "0", "baidu": "/", "yun139": "/"}


def _fmt_time(value) -> str:
    """更新时间可能是秒 / 毫秒时间戳，或移动云盘的 ISO 字符串。"""
    if isinstance(value, str) and "T" in value:
        try:
            return datetime.fromisoformat(
                value.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return value[:16].replace("T", " ")
    try:
        num = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    if num > 10 ** 12:
        num //= 1000
    try:
        return datetime.fromtimestamp(num).strftime("%Y-%m-%d %H:%M")
    except (OSError, OverflowError, ValueError):
        return str(value or "")


def _sort_time(value):
    """排序键：时间戳用数字、ISO 字符串用文本，首元素区分类型避免混比。"""
    if isinstance(value, str) and "T" in value:
        return (1, value)
    try:
        num = int(value)
    except (TypeError, ValueError):
        return (1, str(value or ""))
    if num > 10 ** 12:
        num //= 1000
    return (0, num)


class ShareDialog(QDialog):
    """分享云端文件 / 文件夹：名称、有效期、是否带提取码。"""

    def __init__(self, parent=None, title: str = ""):
        super().__init__(parent)
        self.setWindowTitle("分享云端文件")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        layout.addWidget(QLabel("分享名称"))
        self.title_edit = QLineEdit(title)
        self.title_edit.setPlaceholderText("显示在夸克「我的分享」里的名称")
        layout.addWidget(self.title_edit)

        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("有效期"))
        self.expire_combo = QComboBox()
        self.expire_combo.addItems(qconfig.SHARE_EXPIRES)
        row.addWidget(self.expire_combo)
        row.addSpacing(8)
        self.passcode_cb = QCheckBox("需要提取码（4 位随机）")
        row.addWidget(self.passcode_cb)
        row.addStretch(1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("创建分享")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def options(self) -> dict:
        return {"title": self.title_edit.text().strip() or "分享",
                "expire": self.expire_combo.currentText(),
                "passcode": qbackup.share_passcode() if self.passcode_cb.isChecked() else ""}


class QuarkDownloadPage(BasePage):
    COLS = ("", "名称", "大小", "修改时间")

    def __init__(self, app):
        super().__init__(app)
        self._crumbs: list[tuple[str, str]] = [("0", "根目录")]
        self._entries: list[dict] = []
        self._loaded = False
        self._loading = False
        self._running = False
        self._stop_event: threading.Event | None = None
        # 列头排序：文件夹恒在最前，组内按 名称 / 大小 / 修改时间
        self._sort_column = 1
        self._sort_desc = False

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(self._build_toolbar())
        root.addLayout(self._build_body(), 1)

    # ==================== 界面 ====================

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setProperty("variant", "toolbar")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        path_row = QHBoxLayout()
        path_row.setSpacing(6)
        path_row.addWidget(QLabel("网盘"))
        self.provider_combo = QComboBox()
        for key, label in qconfig.PROVIDERS.items():
            self.provider_combo.addItem(label, key)
        self.provider_combo.setFixedWidth(112)
        self.provider_combo.setToolTip("切换要浏览的网盘")
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        path_row.addWidget(self.provider_combo)

        self.up_btn = QPushButton("上一级")
        self.up_btn.setProperty("variant", "secondary")
        self.up_btn.setToolTip("返回上一层云端目录")
        self.up_btn.clicked.connect(self._go_up)
        path_row.addWidget(self.up_btn)

        self.path_label = QLabel("根目录")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_row.addWidget(self.path_label, 1)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setProperty("variant", "secondary")
        self.refresh_btn.setToolTip("重新读取当前云端目录")
        self.refresh_btn.clicked.connect(self._refresh)
        path_row.addWidget(self.refresh_btn)
        layout.addLayout(path_row)

        local_row = QHBoxLayout()
        local_row.setSpacing(6)
        local_row.addWidget(QLabel("保存到"))
        self.local_edit = QLineEdit(self._default_local_dir())
        self.local_edit.setPlaceholderText("选择下载保存的本地文件夹")
        local_row.addWidget(self.local_edit, 1)
        browse = QPushButton("选择")
        browse.setProperty("variant", "secondary")
        browse.setToolTip("选择下载保存的本地文件夹")
        browse.clicked.connect(self._pick_local_dir)
        local_row.addWidget(browse)

        self.select_all = QCheckBox("全选")
        self.select_all.setToolTip("勾选 / 取消当前目录的全部文件（文件夹不参与）")
        self.select_all.toggled.connect(self._toggle_all)
        local_row.addWidget(self.select_all)

        self.download_btn = QPushButton("下载选中")
        self.download_btn.setProperty("variant", "primary")
        self.download_btn.setToolTip("下载已勾选的文件；同名同大小文件会自动跳过")
        self.download_btn.clicked.connect(self._download_selected)
        local_row.addWidget(self.download_btn)

        self.folder_btn = QPushButton("下载本文件夹")
        self.folder_btn.setProperty("variant", "secondary")
        self.folder_btn.setToolTip("递归下载当前目录及其全部子目录")
        self.folder_btn.clicked.connect(self._download_current_folder)
        local_row.addWidget(self.folder_btn)

        self.share_btn = QPushButton("分享选中")
        self.share_btn.setProperty("variant", "secondary")
        self.share_btn.setToolTip("把勾选的文件（没勾选时用当前选中的文件夹）生成夸克分享链接")
        self.share_btn.clicked.connect(self._share_selected)
        local_row.addWidget(self.share_btn)

        self.stop_btn = QPushButton("停止")
        self.stop_btn.setProperty("variant", "secondary")
        self.stop_btn.setToolTip("停止当前下载（已下载部分保留为 .part，下次自动续传）")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._stop)
        local_row.addWidget(self.stop_btn)
        layout.addLayout(local_row)

        option_row = QHBoxLayout()
        option_row.setSpacing(6)
        self.extract_cb = QCheckBox("下载完成后自动解压压缩包")
        self.extract_cb.setToolTip("用内置 7-Zip 解开 rar / 7z / zip 等，解到压缩包同名的文件夹；"
                                   "加密包会跳过，需手动解压")
        option_row.addWidget(self.extract_cb)
        option_row.addStretch(1)
        layout.addLayout(option_row)

        progress_row = QHBoxLayout()
        progress_row.setSpacing(8)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(16)
        progress_row.addWidget(self.progress, 1)
        self.status_label = QLabel("空闲")
        self.status_label.setProperty("role", "caption")
        self.status_label.setMinimumWidth(260)
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        progress_row.addWidget(self.status_label)
        layout.addLayout(progress_row)
        return bar

    def _build_body(self) -> QHBoxLayout:
        body = QHBoxLayout()
        body.setSpacing(12)

        left = ToolSectionCard("云端文件", "双击文件夹进入；勾选文件后点「下载选中」。")
        self.count_label = QLabel("0 项")
        self.count_label.setProperty("role", "caption")
        left.heading_layout.addWidget(self.count_label)

        self.table = QTableWidget(0, len(self.COLS))
        self.table.setHorizontalHeaderLabels(list(self.COLS))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.resizeSection(0, 36)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.resizeSection(2, 96)
        header.setSectionResizeMode(3, QHeaderView.Fixed)
        header.resizeSection(3, 150)
        header.setSectionsClickable(True)
        header.setSortIndicatorShown(True)
        header.setSortIndicator(1, Qt.AscendingOrder)
        header.setToolTip("点击「名称 / 大小 / 修改时间」列头排序，文件夹始终排在最前")
        header.sectionClicked.connect(self._on_header_clicked)
        self.table.cellDoubleClicked.connect(self._on_double_click)
        left.content_layout.addWidget(self.table, 1)
        body.addWidget(left, 3)

        right = ToolSectionCard("下载进度", "中断后重新下载同一个文件会自动从 .part 续传。")
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        self.log_view.setPlaceholderText("下载开始后，这里显示每个文件的结果")
        self.log_view.setStyleSheet(
            'QPlainTextEdit { font-family: Consolas, "Cascadia Mono", monospace;'
            " font-size: 11px; background: palette(base); color: palette(text);"
            " border: 1px solid palette(shadow); border-radius: 8px; padding: 6px; }")
        right.content_layout.addWidget(self.log_view, 1)
        wrapper = QWidget()
        wrap_layout = QVBoxLayout(wrapper)
        wrap_layout.setContentsMargins(0, 0, 0, 0)
        wrap_layout.addWidget(right, 1)
        body.addWidget(wrapper, 2)
        return body

    # ==================== 生命周期 ====================

    def on_show(self):
        if not self._loaded:
            self._loaded = True
            self._refresh()

    def _default_local_dir(self) -> str:
        work = str(getattr(self.app, "work_dir", "") or "")
        if work and Path(work).is_dir():
            return work
        fallback = Path.home() / "Downloads"
        return str(fallback if fallback.is_dir() else Path.home())

    # ==================== 网盘切换 ====================

    def _current_provider(self) -> str:
        return str(self.provider_combo.currentData() or "quark")

    def _provider_root(self) -> str:
        return ROOT_IDS.get(self._current_provider(), "0")

    def _on_provider_changed(self, _index: int = 0):
        """切换网盘：目录栈回到该网盘的根，再重新列目录。"""
        self._crumbs = [(self._provider_root(), "根目录")]
        self._entries = []
        self.table.setRowCount(0)
        self.count_label.setText("0 项")
        self.path_label.setText("根目录")
        self.up_btn.setEnabled(False)
        self._refresh()

    # ==================== 目录浏览 ====================

    def _refresh(self):
        if self._running or self._loading:
            return
        self._loading = True
        self.refresh_btn.setEnabled(False)
        self.provider_combo.setEnabled(False)
        fid = self._crumbs[-1][0]
        provider = self._current_provider()

        def work():
            client = qbackup.build_client(provider)
            return client.list_entries(fid)

        def done(entries, err):
            self._loading = False
            self.refresh_btn.setEnabled(True)
            self.provider_combo.setEnabled(True)
            if err:
                self.status_label.setText("列目录失败")
                self._append_log(f"列目录失败：{err}")
                self.app.toast(f"读取云端目录失败：{err}", "error")
                return
            self._entries = list(entries or [])
            self._render_entries()

        run_bg(work, done, owner=self)

    def _render_entries(self):
        entries = self._ordered_entries()
        self.table.setRowCount(0)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            is_dir = bool(entry.get("dir"))
            check_item = QTableWidgetItem("")
            check_item.setFlags(Qt.ItemIsEnabled)
            if not is_dir:
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                check_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check_item)

            name_item = QTableWidgetItem(str(entry.get("file_name") or ""))
            name_item.setData(Qt.UserRole, entry)
            self.table.setItem(row, 1, name_item)

            size = int(entry.get("size") or 0)
            size_item = QTableWidgetItem(
                "" if is_dir else qdownload.human_size(size))
            size_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(row, 2, size_item)

            time_item = QTableWidgetItem(_fmt_time(entry.get("updated_at")))
            self.table.setItem(row, 3, time_item)

        self.count_label.setText(f"{len(entries)} 项")
        self.path_label.setText(" / ".join(name for _fid, name in self._crumbs))
        self.up_btn.setEnabled(len(self._crumbs) > 1)
        self.select_all.blockSignals(True)
        self.select_all.setChecked(False)
        self.select_all.blockSignals(False)

    def _on_header_clicked(self, section: int):
        """列头点击：第 0 列是勾选框，不参与排序。"""
        if section <= 0 or section >= len(self.COLS):
            return
        if section == self._sort_column:
            self._sort_desc = not self._sort_desc
        else:
            # 换列时默认正序；「修改时间」更常见的是最新在前
            self._sort_column, self._sort_desc = section, section == 3
        self.table.horizontalHeader().setSortIndicator(
            self._sort_column,
            Qt.DescendingOrder if self._sort_desc else Qt.AscendingOrder)
        self._render_entries()

    def _entry_sort_key(self, entry: dict):
        if self._sort_column == 2:
            return int(entry.get("size") or 0)
        if self._sort_column == 3:
            return _sort_time(entry.get("updated_at"))
        return str(entry.get("file_name") or "").casefold()

    def _ordered_entries(self) -> list[dict]:
        """文件夹恒在最前（正倒序都一样），组内按当前列排序。"""
        dirs = [entry for entry in self._entries if entry.get("dir")]
        files = [entry for entry in self._entries if not entry.get("dir")]
        for group in (dirs, files):
            group.sort(key=self._entry_sort_key, reverse=self._sort_desc)
        return dirs + files

    def _on_double_click(self, row: int, _column: int):
        item = self.table.item(row, 1)
        if item is None:
            return
        entry = item.data(Qt.UserRole) or {}
        if entry.get("dir"):
            self._enter_dir(str(entry.get("fid") or ""), str(entry.get("file_name") or ""))
            return
        check = self.table.item(row, 0)
        if check is not None and check.flags() & Qt.ItemIsUserCheckable:
            check.setCheckState(
                Qt.Unchecked if check.checkState() == Qt.Checked else Qt.Checked)

    def _enter_dir(self, fid: str, name: str):
        self._crumbs.append((fid, name))
        self._refresh()

    def _go_up(self):
        if len(self._crumbs) <= 1:
            return
        self._crumbs.pop()
        self._refresh()

    def _toggle_all(self, checked: bool):
        for row in range(self.table.rowCount()):
            check = self.table.item(row, 0)
            if check is not None and check.flags() & Qt.ItemIsUserCheckable:
                check.setCheckState(Qt.Checked if checked else Qt.Unchecked)

    def _checked_files(self) -> list[dict]:
        files = []
        for row in range(self.table.rowCount()):
            check = self.table.item(row, 0)
            name_item = self.table.item(row, 1)
            if check is None or name_item is None:
                continue
            if not (check.flags() & Qt.ItemIsUserCheckable):
                continue
            if check.checkState() != Qt.Checked:
                continue
            entry = name_item.data(Qt.UserRole)
            if entry:
                files.append(entry)
        return files

    def _pick_local_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "选择下载保存的文件夹", self.local_edit.text().strip())
        if chosen:
            self.local_edit.setText(chosen)

    # ==================== 分享 ====================

    def _share_selected(self):
        if self._running:
            self.app.toast("正在下载，等下载结束后再分享", "info")
            return
        if self._current_provider() != "quark":
            self.app.toast("分享目前只支持夸克网盘", "info")
            return
        entries = self._checked_files()
        if not entries:
            row = self.table.currentRow()
            item = self.table.item(row, 1) if row >= 0 else None
            entry = item.data(Qt.UserRole) if item is not None else None
            entries = [entry] if entry else []
        if not entries:
            self.app.toast("请先勾选文件，或选中要分享的文件夹", "info")
            return
        fids = [str(entry.get("fid") or "") for entry in entries if entry.get("fid")]
        if not fids:
            self.app.toast("选中的项目没有文件标识，无法分享", "error")
            return
        default = (str(entries[0].get("file_name") or "") if len(entries) == 1
                   else f"分享 {len(entries)} 项")
        dialog = ShareDialog(self, default)
        if dialog.exec() != QDialog.Accepted:
            return
        options = dialog.options()
        self.share_btn.setEnabled(False)
        self._append_log(f"正在创建分享：{len(fids)} 项")

        def work():
            client = qbackup.build_client("quark")
            return client.create_share(fids, options["title"],
                                       expire=options["expire"], passcode=options["passcode"])

        def done(result, err):
            self.share_btn.setEnabled(True)
            if err:
                self._append_log(f"分享失败：{err}")
                self.app.toast(f"分享失败：{err}", "error")
                return
            url, code = result
            self._append_log(f"分享链接：{url}" + (f"  提取码：{code}" if code else ""))
            QGuiApplication.clipboard().setText(url)
            self.app.toast("分享链接已复制到剪贴板", "success")

        run_bg(work, done, owner=self)

    # ==================== 下载 ====================

    def _download_selected(self):
        entries = self._checked_files()
        if not entries:
            self.app.toast("请先勾选要下载的文件", "info")
            return
        items = [qdownload.DownloadItem(
            fid=str(entry.get("fid") or ""),
            name=str(entry.get("file_name") or ""),
            size=int(entry.get("size") or 0),
            updated=str(entry.get("updated_at") or ""),
            download_id=str(entry.get("download_id") or entry.get("fid") or ""),
        ) for entry in entries]
        self._start_download(lambda _downloader: items,
                             f"下载选中 {len(items)} 个文件")

    def _download_current_folder(self):
        fid, name = self._crumbs[-1]
        self._start_download(lambda downloader: downloader.walk(fid),
                             f"下载文件夹：{name}")

    def _start_download(self, items_provider, title: str):
        if self._running:
            self.app.toast("下载正在进行中", "info")
            return
        local = self.local_edit.text().strip()
        if not local:
            self.app.toast("请先选择保存到的本地文件夹", "info")
            return
        try:
            Path(local).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "保存目录不可用", str(exc))
            return

        self._running = True
        self._stop_event = threading.Event()
        self.download_btn.setEnabled(False)
        self.folder_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.provider_combo.setEnabled(False)
        self.progress.setValue(0)
        self.status_label.setText("准备中…")
        self._append_log(f"开始{title}")
        auto_extract = self.extract_cb.isChecked()
        stop_event = self._stop_event
        provider = self._current_provider()

        def work():
            client = qbackup.build_client(provider)
            downloader = qdownload.Downloader(
                client, local,
                on_progress=lambda *args: post_to_main(self._on_progress, *args),
                on_log=lambda message: post_to_main(self._append_log, message),
                stop_event=stop_event)
            items = items_provider(downloader)
            if not items:
                post_to_main(self._append_log, "没有可下载的文件")
                return []
            results = downloader.run(items)
            if auto_extract:
                self._extract_results(results, stop_event)
            return results

        def done(results, err):
            self._running = False
            self.download_btn.setEnabled(True)
            self.folder_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.provider_combo.setEnabled(True)
            if err:
                self.status_label.setText("失败")
                self._append_log(f"下载异常终止：{err}")
                self.app.toast(f"下载失败：{err}", "error")
                return
            results = results or []
            ok = [r for r in results if r.ok]
            failed = [r for r in results if not r.ok]
            total = sum(r.size for r in ok)
            self.progress.setValue(100 if results else 0)
            self.status_label.setText(
                f"完成：成功 {len(ok)} · 失败 {len(failed)} · {qdownload.human_size(total)}")
            if failed:
                self.app.toast(f"下载完成，但有 {len(failed)} 个文件失败", "error")
            else:
                self.app.toast(f"下载完成：{len(ok)} 个文件，{qdownload.human_size(total)}",
                               "success")

        run_bg(work, done, owner=self)

    def _extract_results(self, results, stop_event: threading.Event):
        """下载完成后按需解压压缩包（在后台线程里执行）。"""
        if not sevenzip.available():
            post_to_main(self._append_log, "未找到 7-Zip，跳过自动解压")
            return
        for result in results:
            if not result.ok or result.skipped:
                continue
            path = result.local_path
            if not path or not sevenzip.is_archive(path):
                continue
            post_to_main(self._append_log, f"解压：{result.name}")
            try:
                out_dir = sevenzip.default_extract_dir(path)
                sevenzip.extract(
                    path, out_dir, overwrite=True, stop_event=stop_event,
                    on_progress=lambda pct, _text, name=result.name:
                    post_to_main(self._on_extract_progress, name, pct))
                post_to_main(self._append_log, f"解压完成：{out_dir.name}")
            except sevenzip.SevenZipError as exc:
                post_to_main(self._append_log, f"解压失败：{result.name}：{exc}")

    def _on_progress(self, name: str, done: int, total: int, speed: float):
        total = total or 0
        percent = int(done * 100 / total) if total else 0
        self.progress.setValue(max(0, min(100, percent)))
        self.status_label.setText(
            f"{name} · {qdownload.human_size(done)} / "
            f"{qdownload.human_size(total) if total else '未知'} · "
            f"{qdownload.human_speed(speed)}")

    def _on_extract_progress(self, name: str, percent: int):
        self.progress.setValue(max(0, min(100, int(percent))))
        self.status_label.setText(f"解压中：{name} · {int(percent)}%")

    def _stop(self):
        if self._stop_event is not None:
            self._stop_event.set()
            self.status_label.setText("正在停止…")
            self.stop_btn.setEnabled(False)

    def _append_log(self, message: str):
        if not message:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"{stamp}  {message}")
