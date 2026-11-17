# -*- coding: utf-8 -*-
"""夸克网盘下载页。

浏览夸克网盘目录，把选中的文件或整个文件夹下载到本地：

- 单线程 + Range 断点续传：直链过期自动重新申请，网络中断后从 `.part` 接着下；
- 已存在的同名同大小文件自动跳过，不重复消耗流量；
- 可选下载完成后用内置 7-Zip 解压 rar / 7z / zip 等压缩包。

登录走「Cookie 管理 → 夸克网盘」（项目约定 5），本页不保存任何凭据。
视觉全部使用主题语义角色（ui-visual-spec），不写死颜色。
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QFrame, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.toolbox_common import ToolPageHeader, ToolSectionCard
from toolbox import sevenzip
from toolbox.quark import backup as qbackup
from toolbox.quark import download as qdownload


def _fmt_time(value) -> str:
    """夸克返回的更新时间可能是秒 / 毫秒时间戳或字符串。"""
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

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        root.addWidget(ToolPageHeader(
            "网盘下载",
            "浏览夸克网盘，勾选文件或整个文件夹下载到本地。大文件单线程断点续传："
            "直链失效会自动换链，网络中断后重新下载会从断点接着来。"))
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
        self.up_btn = QPushButton("⬆ 上一级")
        self.up_btn.setProperty("variant", "secondary")
        self.up_btn.setToolTip("返回上一层云端目录")
        self.up_btn.clicked.connect(self._go_up)
        path_row.addWidget(self.up_btn)

        self.path_label = QLabel("根目录")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_row.addWidget(self.path_label, 1)

        self.refresh_btn = QPushButton("🔄 刷新")
        self.refresh_btn.setProperty("variant", "secondary")
        self.refresh_btn.setToolTip("重新读取当前云端目录")
        self.refresh_btn.clicked.connect(self._refresh)
        path_row.addWidget(self.refresh_btn)

        self.login_btn = QPushButton("🔑 Cookie 管理")
        self.login_btn.setProperty("variant", "ghost")
        self.login_btn.setToolTip("夸克网盘登录 / 重新登录（统一由 Cookie 管理负责）")
        self.login_btn.clicked.connect(self._open_cookie_manager)
        path_row.addWidget(self.login_btn)
        layout.addLayout(path_row)

        local_row = QHBoxLayout()
        local_row.setSpacing(6)
        local_row.addWidget(QLabel("保存到"))
        self.local_edit = QLineEdit(self._default_local_dir())
        self.local_edit.setPlaceholderText("选择下载保存的本地文件夹")
        local_row.addWidget(self.local_edit, 1)
        browse = QPushButton("📁 选择")
        browse.setProperty("variant", "secondary")
        browse.setToolTip("选择下载保存的本地文件夹")
        browse.clicked.connect(self._pick_local_dir)
        local_row.addWidget(browse)

        self.select_all = QCheckBox("全选")
        self.select_all.setToolTip("勾选 / 取消当前目录的全部文件（文件夹不参与）")
        self.select_all.toggled.connect(self._toggle_all)
        local_row.addWidget(self.select_all)

        self.download_btn = QPushButton("⬇ 下载选中")
        self.download_btn.setProperty("variant", "primary")
        self.download_btn.setToolTip("下载已勾选的文件；同名同大小文件会自动跳过")
        self.download_btn.clicked.connect(self._download_selected)
        local_row.addWidget(self.download_btn)

        self.folder_btn = QPushButton("⬇ 下载本文件夹")
        self.folder_btn.setProperty("variant", "secondary")
        self.folder_btn.setToolTip("递归下载当前目录及其全部子目录")
        self.folder_btn.clicked.connect(self._download_current_folder)
        local_row.addWidget(self.folder_btn)

        self.stop_btn = QPushButton("⏹ 停止")
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

        left = ToolSectionCard("☁ 云端文件", "双击文件夹进入；勾选文件后点「下载选中」。")
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
        self.table.cellDoubleClicked.connect(self._on_double_click)
        left.content_layout.addWidget(self.table, 1)
        body.addWidget(left, 3)

        right = ToolSectionCard("⬇ 下载进度", "中断后重新下载同一个文件会自动从 .part 续传。")
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

    def _open_cookie_manager(self):
        try:
            from gui_qt.cookie_manager import CookieManagerDialog
            CookieManagerDialog(self, on_changed=self._refresh).exec()
        except Exception as exc:  # noqa: BLE001 - 管理入口异常不阻断页面
            QMessageBox.warning(self, "打开 Cookie 管理失败", str(exc))

    # ==================== 目录浏览 ====================

    def _refresh(self):
        if self._running or self._loading:
            return
        self._loading = True
        self.refresh_btn.setEnabled(False)
        fid = self._crumbs[-1][0]

        def work():
            client = qbackup.build_client("quark")
            return client.list_dir(fid)

        def done(entries, err):
            self._loading = False
            self.refresh_btn.setEnabled(True)
            if err:
                self.status_label.setText("列目录失败")
                self._append_log(f"列目录失败：{err}")
                self.app.toast(f"读取云端目录失败：{err}", "error")
                return
            self._entries = list(entries or [])
            self._render_entries()

        run_bg(work, done, owner=self)

    def _render_entries(self):
        entries = sorted(
            self._entries,
            key=lambda e: (0 if e.get("dir") else 1,
                           str(e.get("file_name") or "").casefold()))
        self.table.setRowCount(0)
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            is_dir = bool(entry.get("dir"))
            check_item = QTableWidgetItem("📁" if is_dir else "")
            check_item.setFlags(Qt.ItemIsEnabled)
            if not is_dir:
                check_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                check_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, check_item)

            name_item = QTableWidgetItem(str(entry.get("file_name") or ""))
            name_item.setData(Qt.UserRole, entry)
            self.table.setItem(row, 1, name_item)

            size_item = QTableWidgetItem(
                "" if is_dir else qdownload.human_size(int(entry.get("size") or 0)))
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
        self.progress.setValue(0)
        self.status_label.setText("准备中…")
        self._append_log(f"开始{title}")
        auto_extract = self.extract_cb.isChecked()
        stop_event = self._stop_event

        def work():
            client = qbackup.build_client("quark")
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
                self.app.toast(f"✅ 下载完成：{len(ok)} 个文件，{qdownload.human_size(total)}",
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
            f"⬇ {name} · {qdownload.human_size(done)} / "
            f"{qdownload.human_size(total) if total else '未知'} · "
            f"{qdownload.human_speed(speed)}")

    def _on_extract_progress(self, name: str, percent: int):
        self.progress.setValue(max(0, min(100, int(percent))))
        self.status_label.setText(f"📦 解压中：{name} · {int(percent)}%")

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
