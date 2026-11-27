"""信息获取 · 腾讯文档监控页：浏览量 / 文档成员数 每日自动记录。

与工具箱其它页面保持同一套视觉语言：标题行 + 白卡片 + 表格 +
底部设置行，功能按钮使用统一的线形图标。

功能：
- 文档下拉切换（每个文档独立记录），登录态统一由管理层持有（平台：腾讯文档）
- 立即采集（纯 HTTP 接口），每次采集追加一行并算增量，各自出 Excel
- 子页签顺序：中间层同步 → 表格同步 → 历史记录 → 采集明细 → 修订记录 →
  成员名单 → 文档管理 → 映射回写
- 中间层同步：比对本地已获取与当前表格，列出未上传条目，一键复制 TSV（不自动写入）；
  OpenAPI 凭证由统一管理入口维护（Client-Id / Access-Token / Open-Id）
- 映射回写：本地 CSV / Excel / JSON 按字段映射写入官方动态 / 直播记录，
  先预览差异再执行，只新增 / 更新，不删除记录
- 内容 Tab 自动切换：表格类型显示「表格同步」；文档 / AI 文档等显示「正文预览」
  （AI 文档自动抓取全部子页面，支持导出 Markdown/TXT 并自动存档）
- 表格图片字段同步时登记 URL 并增量下载到 图片/，也可手动「采集图片」补采
- 修订记录：纯 HTTP 增量翻页（按上次拉取位置停止，可全量重拉），导出 JSON + Excel
- 成员名单：纯 HTTP 游标翻页，导出 JSON + Excel
- 定时采集：统一在「任务」页面配置计划任务（本页不再内嵌定时设置）
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from threading import Event

from PySide6.QtCore import QEvent, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QProgressBar, QPushButton, QStackedWidget, QTableWidget,
    QTextBrowser,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt import table_columns, table_sort
from gui_qt.common import post_to_main, run_bg, TASKS
from gui_qt.pages.base import BasePage
from gui_qt.pages.tcdoc_bridge import TcDocBridgePanel
from gui_qt.skeleton import LoadingSkeleton
from toolbox import tcdoc as tc_engine
from toolbox.tcdoc import config as tc_cfg, storage, tables
from toolbox.tcdoc import preview_content, table_preview

DAILY_COLS = [("采集时间", 170), ("浏览量", 110), ("浏览增量", 110),
              ("成员数", 110), ("成员增量", 110), ("已查看人数", 110)]
SNAP_COLS = [("采集时间", 170), ("状态", 90), ("浏览量", 110), ("成员数", 110),
             ("说明", 420)]
DOC_COLS = [("名称", 260), ("类型", 100), ("记录数", 90), ("最近采集", 170), ("链接", 300)]
REV_COLS = [("时间", 165), ("版本", 90), ("编辑人", 140), ("修订号", 100), ("类型", 90)]
MEMBER_COLS = [("昵称", 220), ("权限等级", 110), ("账号来源", 100), ("UID", 210),
               ("企业", 160)]


def _cell(text, center=True, tip=""):
    item = QTableWidgetItem(str(text))
    if center:
        item.setTextAlignment(Qt.AlignCenter)
    if tip:
        item.setToolTip(tip)
    return item


def _fmt_delta(value) -> str:
    if value is None:
        return "-"
    return f"+{value}" if value > 0 else str(value)


class _DocDialog(QDialog):
    """添加 / 编辑文档：名称 + 链接，保存前校验并可后续自定义任意文档。"""

    def __init__(self, parent, doc: dict | None = None, exists=()):
        super().__init__(parent)
        self.setWindowTitle("编辑文档" if doc else "添加文档")
        self.setModal(True)
        self.setMinimumWidth(520)
        self._exists = list(exists)      # 已有文档的 (key, url)，用于查重
        self._edit_key = (doc or {}).get("key")
        self.result_doc: dict | None = None

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)
        v.addWidget(QLabel("文档名称（显示在列表，并作为报表文件名）"))
        self.name_edit = QLineEdit((doc or {}).get("name", ""))
        self.name_edit.setPlaceholderText("如：封茗囧菌数据汇总库")
        v.addWidget(self.name_edit)
        v.addSpacing(4)
        v.addWidget(QLabel("文档链接（支持 /sheet/、/smartsheet/、/aio/、/doc/ 等）"))
        self.url_edit = QLineEdit((doc or {}).get("url", ""))
        self.url_edit.setPlaceholderText("https://docs.qq.com/sheet/文档ID")
        v.addWidget(self.url_edit)
        hint = QLabel("保存后写入配置，数量不设上限；同一链接不能重复添加。")
        hint.setStyleSheet("color: #888;")
        v.addWidget(hint)

        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("保存")
        ok.setStyleSheet("background: #2e7d32; color: white; padding: 5px 18px;")
        ok.clicked.connect(self._submit)
        btns.addWidget(ok)
        v.addLayout(btns)
        self.url_edit.returnPressed.connect(self._submit)
        self.name_edit.setFocus()

    def _submit(self):
        parsed = tc_cfg.parse_url(self.url_edit.text())
        if not parsed:
            QMessageBox.warning(self, "链接格式",
                                "请填写腾讯文档链接，例如：\nhttps://docs.qq.com/sheet/文档ID")
            return
        key, kind, url = parsed
        for other_key, other_url in self._exists:
            same = (other_key == key or other_url == url)
            if same and key != self._edit_key:
                QMessageBox.warning(self, "文档已存在", "该链接已在清单中，无需重复添加。")
                return
        name = self.name_edit.text().strip() or key
        self.result_doc = {"key": key, "name": name, "url": url, "kind": kind}
        self.accept()


class _DocPreviewPanel(QWidget):
    """正文预览页：内容 Tab 在「表格同步」和「正文预览」之间自动切换，
    但正文抓取完全手动（避免进入页面时自动启动浏览器）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_owner = parent
        self._doc: dict | None = None
        self._loading = False
        self._loading_key = ""
        self._req = 0
        self._pages: list[dict] = []
        self._full_text = ""
        self._summary = ""
        self._cancel_fetch = Event()
        self._preview_width = 0
        self._image_sizes: dict[str, tuple[int, int]] = {}
        self._resizing_images = False

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("页面"))
        self.page_combo = QComboBox()
        self.page_combo.setMinimumWidth(220)
        self.page_combo.setEditable(True)
        self.page_combo.setInsertPolicy(QComboBox.NoInsert)
        self.page_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.page_combo.setMinimumContentsLength(8)
        self.page_combo.setToolTip("切换该 AI 文档里的子页面；默认「全部」合并显示")
        self.page_combo.currentIndexChanged.connect(self._on_page_changed)
        self.page_combo.hide()
        bar.addWidget(self.page_combo)
        self.kind_label = QLabel("")
        self.kind_label.setProperty("role", "muted")
        bar.addWidget(self.kind_label)
        bar.addStretch(1)
        self.fetch_btn = QPushButton("抓取正文")
        self.fetch_btn.setProperty("variant", "primary")
        self.fetch_btn.setToolTip("读取全部子页面并下载正文图片，保存后可离线预览")
        self.fetch_btn.clicked.connect(lambda *_: self.load())
        bar.addWidget(self.fetch_btn)
        self.export_btn = QPushButton("导出")
        self.export_btn.setToolTip(
            "导出当前预览正文（Markdown / TXT）；多篇 AI 文档可合并或每篇一个文件")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        bar.addWidget(self.export_btn)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setToolTip("取消本次正文与图片抓取，保留已有存档")
        self.cancel_btn.clicked.connect(self._cancel_download)
        self.cancel_btn.hide()
        bar.addWidget(self.cancel_btn)
        v.addLayout(bar)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        v.addWidget(self.progress)

        self.text_view = QTextBrowser()
        self.text_view.setReadOnly(True)
        self.text_view.setOpenLinks(False)
        self.text_view.setOpenExternalLinks(False)
        self.text_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_view.anchorClicked.connect(self._open_body_image)
        self.text_view.setPlaceholderText("本地暂无正文存档，点「抓取正文」开始")
        self._resize_timer = QTimer(self)
        self._resize_timer.setSingleShot(True)
        self._resize_timer.setInterval(100)
        self._resize_timer.timeout.connect(self._resize_preview_images)
        self.text_view.viewport().installEventFilter(self)
        self._text_stack = QStackedWidget()
        self._text_stack.addWidget(self.text_view)
        self._skeleton = LoadingSkeleton(text="正在读取本地正文…")
        self._text_stack.addWidget(self._skeleton)
        v.addWidget(self._text_stack, 1)

        self.status = QLabel("")
        self.status.setProperty("role", "muted")
        self.status.setTextFormat(Qt.PlainText)
        v.addWidget(self.status)

    # ==================== 文档切换 / 本地存档 ====================

    def set_doc(self, doc: dict | None):
        """切文档：只读本地存档，不自动启动浏览器；抓取必须手动点按钮。"""
        same = bool(self._doc and doc and self._doc.get("key") == doc.get("key"))
        if same and (self._pages or self._loading):
            self._doc = dict(doc)
            return
        self._cancel_fetch.set()
        self.cancel_btn.hide()
        self.progress.hide()
        self._req += 1
        self._loading = False
        self._loading_key = ""
        self._doc = dict(doc) if doc else None
        self._pages = []
        self._full_text = ""
        self._summary = ""
        self._resize_timer.stop()
        self._image_sizes = {}
        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        self.page_combo.blockSignals(False)
        self.page_combo.hide()
        self.export_btn.setEnabled(False)
        self.status.setToolTip("")
        self.text_view.setPlaceholderText("本地暂无正文存档，点「抓取正文」开始")
        self.text_view.clear()
        self._text_stack.setCurrentWidget(self.text_view)
        if not doc:
            self.kind_label.setText("")
            self.fetch_btn.setEnabled(False)
            self.status.setText("尚未配置文档")
            return
        self.kind_label.setText(tc_cfg.kind_label(doc.get("kind")))
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("抓取正文")
        req = self._req
        self._loading = True
        self.fetch_btn.setEnabled(False)
        self._skeleton.setText("正在读取本地正文…")
        self._text_stack.setCurrentWidget(self._skeleton)

        def work():
            cached = tc_engine.archive.pages_of(doc["key"])
            pages = [{**p, "error": ""} for p in cached]
            last = max((p.get("fetched_at") or "" for p in cached), default="")
            snap = tc_engine.archive.snapshot_path(doc)
            return pages, tc_engine.collector.join_pages(pages), last, str(snap) if snap.exists() else ""

        def done(result, err):
            if req != self._req or getattr(self._page_owner, "_closed", False):
                return
            self._loading = False
            self.fetch_btn.setEnabled(True)
            self._text_stack.setCurrentWidget(self.text_view)
            if err:
                self.status.setText(f"本地正文读取失败：{err}"[:200])
                return
            pages, text, last, tip = result
            if not pages:
                self.status.setText("本地暂无存档")
                return
            self._apply_pages(pages, text,
                              f"本地存档于 {last} · 共 {len(pages)} 篇 · 点「重新抓取」更新", tip)

        run_bg(work, done, owner=self._page_owner)

    def load(self):
        if self._loading or not self._doc:
            return
        self._req += 1
        req = self._req
        doc = dict(self._doc)
        key = doc.get("key") or ""
        self._loading = True
        self._loading_key = key
        self._cancel_fetch = Event()
        cancel_fetch = self._cancel_fetch
        self.fetch_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.cancel_btn.show()
        self.progress.show()
        self.status.setText("正在抓取正文与图片…")
        self.status.setToolTip("")
        self.text_view.setPlaceholderText("正在抓取正文……")
        self._skeleton.setText("正在抓取正文…")
        self._text_stack.setCurrentWidget(self._skeleton)

        def work():
            from toolbox.tcdoc.body_images import BodyImagesCancelled
            try:
                return tc_engine.collector.preview(
                    doc, on_status=lambda s: post_to_main(self._on_status, s, req),
                    is_cancelled=cancel_fetch.is_set)
            except BodyImagesCancelled as exc:
                try:
                    saved_pages = (tc_engine.archive.pages_of(key)
                                   if tc_cfg.pages_db_path().is_file() else [])
                except Exception:  # noqa: BLE001 - 取消不得被本地读回错误替换
                    saved_pages = []
                return {"cancelled": True, "pages": saved_pages,
                        "saved_pages": int(getattr(exc, "saved_pages", 0)),
                        "save_error": bool(getattr(exc, "save_error", ""))}

        def done(result, err):
            if req != self._req or getattr(self._page_owner, "_closed", False):
                return
            self._loading = False
            self._loading_key = ""
            self.cancel_btn.hide()
            self.progress.hide()
            if not self._doc or self._doc.get("key") != key:
                return
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("重新抓取")
            self._text_stack.setCurrentWidget(self.text_view)
            if result and result.get("cancelled"):
                count = result.get("saved_pages") or 0
                note = f"已取消（本轮保存 {count} 篇），已有存档保留"
                if result.get("save_error"):
                    note = "已取消，本轮完成的正文未能保存；已有存档保留"
                pages = result.get("pages") or []
                if pages:
                    text = (tc_engine.collector.join_pages(pages) if len(pages) > 1
                            else pages[0].get("text") or "")
                    self._apply_pages(pages, text, note)
                else:
                    self.status.setText(note)
                self.export_btn.setEnabled(bool(self._full_text.strip()))
                return
            if err:
                from toolbox.tcdoc.body_images import BodyImagesCancelled
                cancelled = cancel_fetch.is_set() or isinstance(err, BodyImagesCancelled)
                completed = int(getattr(err, "saved_pages", 0))
                cancel_note = (f"已取消（完成 {completed} 篇），已有存档保留" if completed
                               else "已取消，已有存档保留")
                self.status.setText(cancel_note if cancelled else "抓取正文失败，请重新抓取后重试")
                if not self._pages:
                    self.text_view.setPlaceholderText("抓取失败，可点「重新抓取」重试")
                self.export_btn.setEnabled(bool(self._full_text.strip()))
                return
            text = result.get("text") or ""
            pages = result.get("pages") or []
            save = result.get("save") or {}
            if save:
                added, updated, removed = (save.get("added", 0), save.get("updated", 0),
                                           save.get("removed", 0))
                if added or updated or removed:
                    save_note = f" · 已存档 +{added} ~{updated} -{removed}"
                else:
                    save_note = " · 已存档（无变化）"
                if save.get("skipped"):
                    save_note += f" · {save['skipped']} 篇抓取失败，保留旧存档"
                if save.get("complete") is False:
                    save_note += " · 页面列表未完整读取，缺失页面旧存档保留"
                if save.get("file_error"):
                    save_note += " · Markdown 快照未能更新"
                if save.get("version_error"):
                    save_note += " · 采集版本保存失败，请检查磁盘空间后重新抓取"
            else:
                save_note = " · 自动存档失败" if result.get("save_error") else ""
            count = f"共 {len(pages)} 篇 · " if len(pages) > 1 else ""
            summary = (f"{count}{len(text)} 字 · "
                       f"{result.get('title') or doc['name']} · "
                       f"{time.strftime('%H:%M:%S')} 抓取{save_note}")
            tip = (f"自动存档：{save['file']}" if save.get("file") else
                   (f"自动存档失败：{result['save_error']}"
                    if result.get("save_error") else ""))
            self._apply_pages(pages, text, summary, tip)

        run_bg(work, done, owner=self._page_owner)

    def _apply_pages(self, pages: list[dict], text: str, summary: str, tooltip: str = ""):
        self._pages = pages
        self._full_text = text
        self._summary = summary + " · " + preview_content.image_summary(pages)
        if len(pages) > 1:
            self.page_combo.blockSignals(True)
            self.page_combo.clear()
            self.page_combo.addItem(f"全部（{len(pages)} 篇）", -1)
            for i, item in enumerate(pages):
                indent = "　" * int(item.get("level") or 0)
                self.page_combo.addItem(
                    f"{indent}{item.get('title') or f'第 {i + 1} 篇'}", i)
            self.page_combo.setCurrentIndex(0)
            self.page_combo.blockSignals(False)
            self.page_combo.show()
        else:
            self.page_combo.blockSignals(True)
            self.page_combo.clear()
            self.page_combo.blockSignals(False)
            self.page_combo.hide()
        self._render_current()
        self.text_view.verticalScrollBar().setValue(0)
        self.status.setText(self._summary)
        self.status.setToolTip(tooltip or "")
        self.export_btn.setEnabled(bool(text.strip()))
        self.fetch_btn.setText("重新抓取")

    def set_enabled(self, enabled: bool):
        self.fetch_btn.setEnabled(bool(enabled and self._doc and not self._loading))
        self.export_btn.setEnabled(bool(enabled and self._full_text.strip()))

    def _on_page_changed(self, _index):
        idx = self.page_combo.currentData()
        if idx is None or idx < 0 or idx >= len(self._pages):
            self.status.setText(self._summary)
        else:
            item = self._pages[idx]
            body = item.get("error") or item.get("text") or "（空白页）"
            if item.get("error"):
                note = "抓取失败"
            elif not (item.get("text") or "").strip() and not item.get("images"):
                note = "空白页"
            else:
                note = f"{len(item.get('text') or '')} 字"
            self.status.setText(f"第 {idx + 1}/{len(self._pages)} 篇 · {note} · "
                                + preview_content.image_summary([item]))
        self._render_current()
        self.text_view.verticalScrollBar().setValue(0)

    def _on_status(self, text: str, request: int | None = None):
        if (self._loading and (request is None or request == self._req)
                and self._loading_key == (self._doc or {}).get("key", "")):
            self.status.setText(f"{text}")

    def _cancel_download(self):
        self._cancel_fetch.set()
        self.cancel_btn.setEnabled(False)
        self.status.setText("正在取消…")

    def _visible_pages(self):
        index = self.page_combo.currentData()
        return ([self._pages[index]] if index is not None and 0 <= index < len(self._pages)
                else self._pages)

    def _render_current(self, preserve_scroll=False):
        if not self._pages:
            return
        pages = self._visible_pages()
        self._image_sizes = {}
        for page in pages:
            for image in page.get("images") or []:
                try:
                    uri = preview_content.local_image_path(image).as_uri()
                except (OSError, ValueError):
                    continue
                width = max(1, int(image.get("width") or 780))
                height = max(1, int(image.get("height") or width))
                self._image_sizes[uri] = (width, height)
        scrollbar = self.text_view.verticalScrollBar()
        fraction = scrollbar.value() / max(1, scrollbar.maximum())
        self._preview_width = max(80, self.text_view.viewport().width() - 24)
        self.text_view.setHtml(preview_content.html_for_pages(
            pages, max_width=self._preview_width))
        if preserve_scroll:
            scrollbar.setValue(round(fraction * scrollbar.maximum()))

    def _resize_preview_images(self):
        """只调整现有图片尺寸；重建全文会反复触发滚动条显隐并打断阅读。"""
        width = max(80, self.text_view.viewport().width() - 24)
        if width == self._preview_width or self._resizing_images:
            return
        self._preview_width = width
        scrollbar = self.text_view.verticalScrollBar()
        fraction = scrollbar.value() / max(1, scrollbar.maximum())
        document = self.text_view.document()
        changes = []
        block = document.begin()
        while block.isValid():
            fragments = block.begin()
            while not fragments.atEnd():
                fragment = fragments.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    fmt = fragment.charFormat().toImageFormat()
                    size = self._image_sizes.get(fmt.name())
                    if size:
                        shown_width = min(size[0], width)
                        shown_height = max(1, round(size[1] * shown_width / size[0]))
                        if fmt.width() != shown_width or fmt.height() != shown_height:
                            fmt.setWidth(shown_width)
                            fmt.setHeight(shown_height)
                            changes.append((fragment.position(), fmt))
                fragments += 1
            block = block.next()
        if not changes:
            return
        self._resizing_images = True
        try:
            cursor = QTextCursor(document)
            cursor.beginEditBlock()
            for position, fmt in changes:
                cursor.setPosition(position)
                cursor.setPosition(position + 1, QTextCursor.KeepAnchor)
                cursor.setCharFormat(fmt)
            cursor.endEditBlock()
            scrollbar.setValue(round(fraction * scrollbar.maximum()))
        finally:
            self._resizing_images = False

    def _open_body_image(self, url):
        for page in self._visible_pages():
            for image in page.get("images") or []:
                try:
                    path = preview_content.local_image_path(image)
                except (OSError, ValueError):
                    continue
                if url == QUrl.fromLocalFile(str(path)):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
                    return

    def eventFilter(self, obj, event):
        if (obj is self.text_view.viewport() and event.type() == QEvent.Resize
                and event.size().width() != event.oldSize().width()
                and not self._resizing_images):
            self._resize_timer.start()
        return super().eventFilter(obj, event)

    # ==================== 导出 ====================

    def _export(self):
        """导出当前预览；多篇 AI 文档提供「合并 / 每篇一个文件」两种方式。"""
        if not self._pages or not self._doc:
            return
        idx = self.page_combo.currentData()
        if len(self._pages) > 1:
            menu = QMenu(self)
            menu.addAction("导出全部（合并为一个文件）…", self._export_merged)
            menu.addAction("导出全部（每篇一个 .md 文件）…", self._export_each_page)
            if idx is not None and 0 <= idx < len(self._pages):
                menu.addAction("仅导出当前篇…", self._export_current)
            menu.exec(self.export_btn.mapToGlobal(self.export_btn.rect().bottomLeft()))
        else:
            self._export_current()

    def _export_current(self):
        idx = self.page_combo.currentData()
        if idx is not None and 0 <= idx < len(self._pages):
            item = self._pages[idx]
            title = item.get("title") or self._doc["name"]
            body = item.get("error") or item.get("text") or "（空白页）"
            name = f"{self._doc['name']}_{title}"
        else:
            title = self._doc["name"]
            body = self._full_text
            name = self._doc["name"]
        self._save_text(name, title, body)

    def _export_merged(self):
        self._save_text(f"{self._doc['name']}_全部", self._doc["name"], self._full_text)

    def _export_each_page(self):
        folder = QFileDialog.getExistingDirectory(
            self, "选择导出文件夹", str(tc_cfg.data_dir()))
        if not folder:
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        out_dir = Path(folder) / f"{tc_cfg.safe_name(self._doc['name'])}_正文_{stamp}"
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            for i, item in enumerate(self._pages, 1):
                title = item.get("title") or f"第 {i} 篇"
                body = item.get("error") or item.get("text") or "（空白页）"
                path = out_dir / f"{i:02d}_{tc_cfg.safe_name(title, f'第{i}篇')}.md"
                path.write_text(self._export_body(title, body, ".md"), encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.status.setText(f"已导出 {len(self._pages)} 篇到 {out_dir.name}")
        self.status.setToolTip(str(out_dir))

    def _save_text(self, name: str, heading: str, text: str):
        if not (text or "").strip():
            self.status.setText("当前没有可导出的正文")
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        default = tc_cfg.data_dir() / f"{tc_cfg.safe_name(name)}_{stamp}.md"
        path, selected = QFileDialog.getSaveFileName(
            self, "导出正文", str(default), "Markdown (*.md);;文本文件 (*.txt)")
        if not path:
            return
        if not path.lower().endswith((".md", ".txt")):
            path += ".md" if "Markdown" in (selected or "") else ".txt"
        ext = ".md" if path.lower().endswith(".md") else ".txt"
        try:
            Path(path).write_text(self._export_body(heading, text, ext), encoding="utf-8-sig")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        self.status.setText(f"已导出：{Path(path).name}")
        self.status.setToolTip(path)

    def _export_body(self, heading: str, text: str, ext: str) -> str:
        url = self._doc.get("url") or ""
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        if ext == ".md":
            return f"# {heading}\n\n> 来源：{url}  \n> 导出时间：{ts}\n\n{text.strip()}\n"
        return f"{heading}\n\n来源：{url}\n导出时间：{ts}\n\n{text.strip()}\n"


class TcDocPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.docs: list[dict] = []
        self._doc_rows: list[dict] = []   # 文档表格当前显示顺序（排序后与 docs 不同）
        self._busy = False
        self._ready_done = False
        self._ready_loading = False
        self._page_visible = False
        self._closed = False
        self._loaded_tabs = set()
        self._panels = {}
        self._skeletons = {}
        self._local_requests = {}
        self._local_errors = {}
        self._local_running = set()
        self._data_revision = 0
        self._reload_pending = False
        self._reload_doc_key = ""
        self.mapping_panel = None
        self._sheets = []
        self._rev_meta = None
        self._migrate_note = ""
        self._last_reload_at = 0.0
        self._preload_requested = False
        self._preload_indexes = []
        self._version_dialog = None
        self._changes_dialog = None
        self._preload_timer = QTimer(self)
        self._preload_timer.setSingleShot(True)
        self._preload_timer.timeout.connect(self._preload_next_panel)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ---------- 顶部卡片：文档切换 + 操作 ----------
        card = QFrame()
        card.setProperty("variant", "card")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(8)
        doc_label = QLabel("文档")
        doc_label.setProperty("role", "caption")
        row.addWidget(doc_label)
        self.doc_combo = QComboBox()
        self.doc_combo.setMinimumWidth(260)
        self.doc_combo.setToolTip("切换文档：每个文档的历史记录、报表互相独立")
        self.doc_combo.currentIndexChanged.connect(self._on_doc_changed)
        row.addWidget(self.doc_combo)
        self.link_btn = QPushButton("打开链接")
        self.link_btn.setProperty("variant", "ghost")
        self.link_btn.setToolTip("在浏览器打开当前文档")
        self.link_btn.setVisible(False)
        self.link_btn.clicked.connect(self._open_doc_url)
        row.addWidget(self.link_btn)
        row.addStretch(1)
        cv.addLayout(row)

        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.versions_btn = QPushButton("采集版本")
        self.versions_btn.setToolTip("按采集时间和版本号选择并打开本地历史快照")
        self.versions_btn.clicked.connect(self._open_versions)
        actions.addWidget(self.versions_btn)

        self.sync_btn = QPushButton("同步表格数据")
        self.sync_btn.setToolTip(
            "抓取当前文档的表格内容并更新本地快照；不采集浏览量等指标")
        self.sync_btn.clicked.connect(self._sync_tables)
        self.sync_btn.setVisible(False)
        actions.addWidget(self.sync_btn)

        self.images_btn = QPushButton("补采表格图片")
        self.images_btn.setToolTip(
            "为当前文档补采尚未下载的表格图片；已保存图片会自动跳过")
        self.images_btn.clicked.connect(self._collect_images)
        self.images_btn.setVisible(False)
        actions.addWidget(self.images_btn)

        self.dir_btn = QPushButton("打开数据目录")
        self.dir_btn.setToolTip(
            "打开 download/腾讯文档数据/（报表/、表格数据/、正文存档/、图片/、数据库与日志）")
        self.dir_btn.clicked.connect(self._open_data_dir)
        actions.addWidget(self.dir_btn)

        actions.addStretch(1)
        self.collect_btn = QPushButton("采集指标")
        self.collect_btn.setProperty("variant", "primary")
        self.collect_btn.setToolTip(
            "采集全部文档的浏览量、成员数与已查看浏览人，写入指标历史和报表；不抓取表格内容")
        self.collect_btn.clicked.connect(self._collect)
        actions.addWidget(self.collect_btn)
        cv.addLayout(actions)

        # 自动维护：单独一条浅色带，开关在左、作用范围在右，不再和按钮挤一行
        auto_bar = QFrame()
        auto_bar.setProperty("variant", "subtle")
        auto_row = QHBoxLayout(auto_bar)
        auto_row.setContentsMargins(10, 6, 10, 6)
        auto_row.setSpacing(8)
        self.auto_cb = QCheckBox("自动维护")
        self.auto_cb.setToolTip(
            "开启后定时任务按勾选的子表同步；关闭后定时任务不再同步任何子表。"
            "计划任务本身保留，时间仍在「任务」页统一管理。"
            "手动「采集指标 / 同步表格数据」不受影响。")
        self.auto_cb.toggled.connect(self._on_auto_toggled)
        auto_row.addWidget(self.auto_cb)
        auto_row.addStretch(1)
        self.sync_tabs_label = QLabel("")
        self.sync_tabs_label.setProperty("role", "caption")
        self.sync_tabs_label.setWordWrap(False)
        auto_row.addWidget(self.sync_tabs_label)
        self.sync_tabs_btn = QPushButton("选择同步子表…")
        self.sync_tabs_btn.setToolTip(
            "勾选本子文档里参与自动同步的子表（官方动态 / 直播记录 / 完整歌曲…）；"
            "未勾选的子表不拉取、不下图片，本地旧数据保留不删")
        self.sync_tabs_btn.clicked.connect(self._choose_sync_tabs)
        auto_row.addWidget(self.sync_tabs_btn)
        cv.addWidget(auto_bar)
        self._refresh_auto_state()

        self.latest_label = QLabel("")
        self.latest_label.setProperty("role", "subtitle")
        self.latest_label.setWordWrap(True)
        cv.addWidget(self.latest_label)
        layout.addWidget(card)

        # ---------- 子页签：中间层同步 → 表格同步 → 历史/采集/修订/成员/文档 ----------
        self.tabs = QTabWidget()
        self._panel_builders = (
            self._build_bridge_panel, self._build_content_panel,
            self._build_daily_panel, self._build_snapshot_panel,
            self._build_revision_tab, self._build_members_tab,
            self._build_doc_tab, self._build_mapping_panel,
        )
        for title in ("中间层同步", "表格同步", "指标历史", "采集明细",
                      "修订记录", "成员名单", "文档管理", "映射回写"):
            stack = QStackedWidget()
            skeleton = LoadingSkeleton(text="正在准备页面…")
            stack.addWidget(skeleton)
            self._skeletons[self.tabs.count()] = skeleton
            self.tabs.addTab(stack, title)
        (self.bridge_tab_index, self.content_tab_index, self.daily_tab_index,
         self.snapshot_tab_index, self.revision_tab_index, self.members_tab_index,
         self.doc_tab_index, self.mapping_tab_index) = range(8)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        # ---------- 底部提示：定时设置已统一到「任务」页 ----------
        hint = QLabel("定时采集已统一到「任务」页面，可在那里修改计划并安装 / 删除 Windows 计划任务。")
        hint.setProperty("role", "caption")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.status_label = QLabel("")
        self.status_label.setProperty("role", "caption")
        layout.addWidget(self.status_label)

    # ==================== 界面拼装辅助 ====================

    def _open_versions(self):
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先选择腾讯文档", "warning")
            return
        from gui_qt.tcdoc_versions import TcDocVersionsDialog
        from shiboken6 import isValid
        dialog = self._version_dialog
        if dialog is None or not isValid(dialog) or dialog._closed:
            dialog = TcDocVersionsDialog(doc, self)
            self._version_dialog = dialog
        elif dialog._doc.get("key") != doc.get("key"):
            # 已打开的旧文档历史保持独立，新的入口只切换到所选文档。
            dialog = TcDocVersionsDialog(doc, self)
            self._version_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _open_changes(self):
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先选择腾讯文档", "warning")
            return
        from gui_qt.tcdoc_changes import TcDocChangesDialog
        from shiboken6 import isValid
        dialog = self._changes_dialog
        if dialog is None or not isValid(dialog) or dialog._doc.get("key") != doc.get("key"):
            dialog = TcDocChangesDialog(doc, self)
            self._changes_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        dialog.refresh()

    def _ensure_panel(self, index):
        if index not in self._panels:
            panel = self._panel_builders[index]()
            self._panels[index] = panel
            self.tabs.widget(index).addWidget(panel)
            from gui_qt import common, theme
            common.bind_checkbox_toasts(panel)
            theme.adapt(panel)
        return self._panels[index]

    def _show_panel(self, index):
        if index in self._panels:
            self.tabs.widget(index).setCurrentWidget(self._panels[index])

    def _defer(self, callback):
        """主线程下一轮执行，并为排队期间保留页面宿主。"""
        TASKS.acquire((self,))

        def invoke():
            try:
                if not self._closed and not self._disposed:
                    callback()
            finally:
                TASKS.release((self,))

        QTimer.singleShot(0, invoke)

    def _load_local(self, index, work, apply):
        """每个页签最多一个读任务，连续刷新只保留最新请求。"""
        request = (self._data_revision, work, apply)
        self._loaded_tabs.discard(index)
        self._local_requests[index] = request
        self._skeletons[index].setText("正在读取本地数据…")
        self.tabs.widget(index).setCurrentWidget(self._skeletons[index])
        if index not in self._local_running:
            self._start_local(index, request)

    def _start_local(self, index, request):
        self._local_running.add(index)
        revision, work, apply = request

        def valid():
            return (not self._closed and revision == self._data_revision
                    and self._local_requests.get(index) is request)

        def finish(error=None):
            self._local_running.discard(index)
            latest = self._local_requests.get(index)
            if valid():
                self._local_requests.pop(index, None)
                if error:
                    self._local_error(index, f"显示失败，可刷新重试：{error}")
                else:
                    self._loaded_tabs.add(index)
                    message = self._local_errors.pop(index, "")
                    if message and self.status_label.text() == message:
                        self.status_label.clear()
                self._show_panel(index)
            elif not self._closed and latest is not None and latest is not request:
                self._start_local(index, latest)
            else:
                self._local_requests.pop(index, None)

        def done(result, err):
            if not valid():
                finish()
                return
            if err:
                self._local_requests.pop(index, None)
                self._local_running.discard(index)
                self._show_panel(index)
                self._local_error(index, f"读取失败，可刷新重试：{err}")
                return
            try:
                apply(result, finish, valid)
            except Exception as exc:  # noqa: BLE001 - 失败后保留重试入口
                self._local_requests.pop(index, None)
                self._local_running.discard(index)
                self._show_panel(index)
                self._local_error(index, f"显示失败，可刷新重试：{exc}")

        run_bg(work, done, owner=self)

    def _local_error(self, index, message):
        self._loaded_tabs.discard(index)
        self._local_errors[index] = message[:200]
        self.status_label.setText(message[:200])

    def _render_table(self, table, rows, make_items, finish, valid, offset=0):
        """分批填充普通表格，把事件循环留给导航、重绘和输入。"""
        if not valid():
            finish()
            return
        table.setRowCount(offset + len(rows))
        position = 0

        def step():
            nonlocal position
            if not valid():
                finish()
                return
            end = time.perf_counter() + 0.006
            table.setUpdatesEnabled(False)
            try:
                while position < len(rows):
                    for column, item in enumerate(make_items(rows[position])):
                        table.setItem(offset + position, column, item)
                    position += 1
                    if time.perf_counter() >= end:
                        break
            except Exception as exc:  # noqa: BLE001 - 分批渲染也要结束加载状态
                finish(exc)
                return
            finally:
                table.setUpdatesEnabled(True)
            if position < len(rows):
                self._defer(step)
            else:
                self._apply_table_sort(table)
                finish()

        self._defer(step)

    def _apply_table_sort(self, table):
        """填充完成后按 sorter 记住的表头状态重排（未点过表头时不做任何事）。"""
        sorter = getattr(table, "_table_sorter", None)
        if table is getattr(self, "table_data", None):
            return  # 表格数据已在共享用例中整表排序后分页，不再次触发读取回调。
        if sorter is not None:
            sorter.apply()

    def _build_bridge_panel(self):
        self.bridge_panel = TcDocBridgePanel(self, self.app, defer_load=True)
        return self.bridge_panel

    def _build_mapping_panel(self):
        from gui_qt.pages.tcdoc_mapping import TcDocMappingPanel
        self.mapping_panel = TcDocMappingPanel(self, self.current_doc)
        return self.mapping_panel

    def _build_content_panel(self):
        self.table_panel = self._build_sheet_tab()
        self.preview_panel = _DocPreviewPanel(self)
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.table_panel)
        self.content_stack.addWidget(self.preview_panel)
        return self.content_stack

    def _build_daily_panel(self):
        self.table_daily = self._make_table(DAILY_COLS, stretch_all=True, fit_cols=(0,))
        self._daily_sorter = table_sort.install(self.table_daily, skip_columns=())
        self.table_daily.setToolTip("每次采集追加一行；增量相对该文档上一次采集")
        return self._wrap(self.table_daily)

    def _build_snapshot_panel(self):
        self.table_snap = self._make_table(SNAP_COLS)
        self._snap_sorter = table_sort.install(self.table_snap, skip_columns=())
        self.table_snap.setToolTip("每次采集的成功/失败明细，失败原因留档便于回溯")
        return self._wrap(self.table_snap)

    def _make_table(self, columns, stretch_all=False, fit_cols=()) -> QTableWidget:
        table = QTableWidget(0, len(columns))
        table.setHorizontalHeaderLabels([c[0] for c in columns])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        header = table.horizontalHeader()
        for idx, (_name, width) in enumerate(columns):
            table.setColumnWidth(idx, width)
            if idx in fit_cols:
                header.setSectionResizeMode(idx, QHeaderView.ResizeToContents)
            else:
                header.setSectionResizeMode(
                    idx, QHeaderView.Stretch if stretch_all else QHeaderView.Interactive)
        # 列宽均匀铺满（历史记录页数字列多，避免最后一列被拉成一大片空白色块）
        header.setStretchLastSection(not stretch_all)
        return table

    def _wrap(self, table: QTableWidget) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 8, 6, 6)
        v.addWidget(table)
        return box

    def _build_doc_tab(self) -> QWidget:
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(6)

        self.doc_table = QTableWidget(0, len(DOC_COLS))
        self.doc_table.setHorizontalHeaderLabels([c[0] for c in DOC_COLS])
        self.doc_table.verticalHeader().setVisible(False)
        self.doc_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.doc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.doc_table.setSelectionMode(QAbstractItemView.SingleSelection)
        for idx, (_name, width) in enumerate(DOC_COLS):
            self.doc_table.setColumnWidth(idx, width)
            self.doc_table.horizontalHeader().setSectionResizeMode(
                idx, QHeaderView.Stretch if idx == 0 else QHeaderView.Interactive)
        self._doc_sorter = table_sort.install(self.doc_table, skip_columns=())
        self.doc_table.itemSelectionChanged.connect(self._on_doc_row_selected)
        self.doc_table.cellDoubleClicked.connect(lambda _r, _c: self._edit_doc())
        v.addWidget(self.doc_table, 1)

        btns = QHBoxLayout()
        self.add_btn = QPushButton("添加文档")
        self.add_btn.setToolTip("添加任意腾讯文档（自定义文档，数量不设上限）")
        self.add_btn.clicked.connect(self._add_doc)
        self.edit_btn = QPushButton("编辑")
        self.edit_btn.setToolTip("修改文档名称 / 链接（双击行同效）")
        self.edit_btn.clicked.connect(lambda *_: self._edit_doc())
        self.del_btn = QPushButton("删除")
        self.del_btn.setToolTip("从清单移除文档（可选同时删除其历史记录）")
        self.del_btn.clicked.connect(lambda *_: self._delete_doc())
        self.probe_btn = QPushButton("生成诊断包")
        self.probe_btn.setToolTip("页面改版导致采集失败时，打开浏览器抓取入口信息存到 调试/")
        self.probe_btn.clicked.connect(self._probe)
        for btn in (self.add_btn, self.edit_btn, self.del_btn, self.probe_btn):
            btns.addWidget(btn)
        btns.addStretch(1)
        self.rebuild_btn = QPushButton("重建全部报表")
        self.rebuild_btn.setToolTip("按数据库重新生成所有文档的 Excel（历史增量一并重算）")
        self.rebuild_btn.clicked.connect(self._rebuild_reports)
        btns.addWidget(self.rebuild_btn)
        v.addLayout(btns)

        hint = QLabel("添加文档后可立即采集；切换文档在「指标历史」里查看各自曲线，"
                      "每个文档一份独立 Excel。")
        hint.setStyleSheet("color: #888;")
        v.addWidget(hint)
        return box

    def _build_sheet_tab(self) -> QWidget:
        """表格同步：工作表切换 + 数据预览 + 分页 + 本地变更入口。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("工作表"))
        self.sheet_combo = QComboBox()
        self.sheet_combo.setMinimumWidth(160)
        self.sheet_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sheet_combo.setMinimumContentsLength(8)
        self.sheet_combo.setToolTip("切换该文档下的工作表（普通表格 / 图表页不支持同步）")
        self.sheet_combo.currentIndexChanged.connect(self._on_sheet_changed)
        bar.addWidget(self.sheet_combo)
        self.sheet_info = QLabel("")
        self.sheet_info.setStyleSheet("color: #888;")
        self.sheet_info.setWordWrap(True)
        bar.addStretch(1)

        self.prev_btn = QPushButton("上一页")
        self.prev_btn.setToolTip("显示上一页表格记录")
        self.prev_btn.clicked.connect(lambda: self._load_sheet_page(delta=-1))
        self.page_label = QLabel("-/-")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setMinimumWidth(66)
        self.next_btn = QPushButton("下一页")
        self.next_btn.setToolTip("显示下一页表格记录")
        self.next_btn.clicked.connect(lambda: self._load_sheet_page(delta=1))

        self.newest_cb = QCheckBox("最新在前")
        self.newest_cb.setChecked(True)
        self.newest_cb.setToolTip(
            "默认按日期列倒序显示，空日期排在最后；没有日期列时按原行序倒序。"
            "取消勾选恢复表格原顺序；只改变本地预览，不重排云端。")
        self.newest_cb.toggled.connect(self._on_newest_changed)
        bar.addWidget(self.newest_cb)
        self.changes_btn = QPushButton("最近变更")
        self.changes_btn.setToolTip("查看当前文档的本地同步检测记录，不读取云端编辑历史")
        self.changes_btn.clicked.connect(self._open_changes)
        bar.addWidget(self.changes_btn)
        v.addLayout(bar)

        self.table_data = QTableWidget(0, 0)
        self.table_data.verticalHeader().setVisible(True)
        self.table_data.verticalHeader().setDefaultSectionSize(26)
        self.table_data.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_data.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table_data.setAlternatingRowColors(True)
        self.table_data.setWordWrap(False)
        self.table_data.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self._data_sorter = table_sort.install_callback(
            self.table_data, self._sheet_sort_changed, skip_columns=(), tri_state=True)
        self.table_data.setToolTip("本地存档的表格数据（每次同步按记录逐行比对增量）")

        v.addWidget(self.table_data, 1)
        paging = QHBoxLayout()
        paging.setSpacing(8)
        paging.addWidget(self.sheet_info, 1)
        paging.addWidget(self.prev_btn)
        paging.addWidget(self.page_label)
        paging.addWidget(self.next_btn)
        v.addLayout(paging)

        self.sheet_hint = QLabel("点「同步表格」把文档里的表格内容抓到本地；"
                                "图片字段会自动增量下载到 图片/。默认最新在前，"
                                "取消勾选可恢复原顺序；本地检测记录见「最近变更」。")
        self.sheet_hint.setStyleSheet("color: #888;")
        v.addWidget(self.sheet_hint)

        self._sheet_columns: list[str] = []
        self._sheet_offset = 0
        self._sheet_total = 0
        return box

    def _build_revision_tab(self) -> QWidget:
        """修订记录：按上次拉取位置增量，保留同版本不同编辑人。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.rev_fetch_btn = QPushButton("拉取修订记录（增量）")
        self.rev_fetch_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.rev_fetch_btn.setToolTip(
            "从第 1 页开始增量拉取：遇到上次已拉取且内容一致（修订号 + 编辑人 + 时间）"
            "的记录就停止翻页；同版本/同修订号不同编辑人全部保留")
        self.rev_fetch_btn.clicked.connect(lambda: self._fetch_revisions(False))
        bar.addWidget(self.rev_fetch_btn)
        self.rev_full_btn = QPushButton("全量重拉")
        self.rev_full_btn.setToolTip(
            "忽略上次拉取位置，翻完全部页面并逐条校验更新（数据怀疑不一致时使用）")
        self.rev_full_btn.clicked.connect(lambda: self._fetch_revisions(True))
        bar.addWidget(self.rev_full_btn)
        self.rev_dir_btn = QPushButton("打开修订记录目录")
        self.rev_dir_btn.clicked.connect(lambda: self._open_dir(tc_cfg.revisions_dir()))
        bar.addWidget(self.rev_dir_btn)
        self.rev_more_btn = QPushButton("加载更多")
        self.rev_more_btn.setToolTip("再加载 2000 条较早的修订记录（滚动到底也会自动加载）")
        self.rev_more_btn.clicked.connect(lambda: self._load_more_revisions())
        bar.addWidget(self.rev_more_btn)
        self.rev_all_btn = QPushButton("全部加载")
        self.rev_all_btn.setToolTip("一次性把本地全部修订记录加载进表格（数据量大时稍慢）")
        self.rev_all_btn.clicked.connect(self._load_all_revisions)
        bar.addWidget(self.rev_all_btn)
        bar.addStretch(1)
        v.addLayout(bar)
        self.rev_info = QLabel("")
        self.rev_info.setStyleSheet("color: #888;")
        self.rev_info.setWordWrap(True)
        v.addWidget(self.rev_info)

        self.rev_table = QTableWidget(0, len(REV_COLS))
        self.rev_table.setHorizontalHeaderLabels([c[0] for c in REV_COLS])
        self.rev_table.verticalHeader().setVisible(False)
        self.rev_table.verticalHeader().setDefaultSectionSize(24)
        self.rev_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.rev_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rev_table.setAlternatingRowColors(True)
        for idx, (_name, width) in enumerate(REV_COLS):
            self.rev_table.setColumnWidth(idx, width)
        self.rev_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch)
        self.rev_table.horizontalHeader().setStretchLastSection(True)
        self._rev_sorter = table_sort.install(self.rev_table, skip_columns=())
        self.rev_table.verticalScrollBar().valueChanged.connect(self._on_rev_scrolled)
        v.addWidget(self.rev_table, 1)

        self._rev_loaded = 0
        self._rev_total = 0
        self._rev_batch = 2000
        self._rev_loading = False

        self.rev_hint = QLabel(
            "点「拉取修订记录（增量）」从第 1 页开始，遇到上次已拉取且内容一致的"
            "记录就停止；同版本/同修订号不同编辑人会全部保留。"
            "需要重建时点「全量重拉」。每行对应一次编辑，编辑人昵称来自文档作者接口。"
            "表格默认加载最近 2000 条，滚动到底自动继续加载。")
        self.rev_hint.setStyleSheet("color: #888;")
        self.rev_hint.setWordWrap(True)
        v.addWidget(self.rev_hint)
        return box

    def _build_members_tab(self) -> QWidget:
        """成员名单：文档全部成员 + 权限等级。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.mem_fetch_btn = QPushButton("拉取全部成员")
        self.mem_fetch_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.mem_fetch_btn.setToolTip(
            "纯 HTTP 游标翻页拉取该文档的全部成员（每页 100 条，自动翻完）；"
            "含昵称、权限等级、账号来源、UID，自动存 成员名单/ JSON + Excel")
        self.mem_fetch_btn.clicked.connect(self._fetch_members)
        bar.addWidget(self.mem_fetch_btn)
        self.mem_dir_btn = QPushButton("打开成员名单目录")
        self.mem_dir_btn.clicked.connect(lambda: self._open_dir(tc_cfg.members_dir()))
        bar.addWidget(self.mem_dir_btn)
        bar.addStretch(1)
        v.addLayout(bar)
        self.mem_info = QLabel("")
        self.mem_info.setStyleSheet("color: #888;")
        self.mem_info.setWordWrap(True)
        v.addWidget(self.mem_info)

        self.mem_table = QTableWidget(0, len(MEMBER_COLS))
        self.mem_table.setHorizontalHeaderLabels([c[0] for c in MEMBER_COLS])
        self.mem_table.verticalHeader().setVisible(False)
        self.mem_table.verticalHeader().setDefaultSectionSize(24)
        self.mem_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.mem_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.mem_table.setAlternatingRowColors(True)
        for idx, (_name, width) in enumerate(MEMBER_COLS):
            self.mem_table.setColumnWidth(idx, width)
        self.mem_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch)
        self.mem_table.horizontalHeader().setStretchLastSection(True)
        self._mem_sorter = table_sort.install(self.mem_table, skip_columns=())
        v.addWidget(self.mem_table, 1)

        self.mem_hint = QLabel(
            "接口返回的记录会按 UID 去重（腾讯会重复返回同一成员）；"
            "权限等级：所有者 / 可编辑 / 可查看 等。需要该文档的管理权限。")
        self.mem_hint.setStyleSheet("color: #888;")
        v.addWidget(self.mem_hint)
        return box

    # ==================== 生命周期 ====================

    def preload_tasks(self):
        return (self._start_preload,)

    def preload_is_pending(self):
        bridge = self._panels.get(self.bridge_tab_index)
        return bool(self._preload_indexes or self._ready_loading or self._local_running
                    or self._reload_pending or (bridge is not None and
                    (bridge._busy or bridge._targets_loading)))

    def _start_preload(self):
        self._preload_requested = True
        self._preload_indexes = list(range(self.tabs.count()))
        self._ensure_ready()
        self._preload_timer.start(100)

    def _preload_next_panel(self):
        if self._closed or not self._preload_indexes:
            return
        if self._ready_loading or self._busy or self._reload_pending:
            self._preload_timer.start(100)
            return
        if not self._ready_done:  # 初始化失败留给显式进入/刷新重试。
            self._preload_indexes.clear()
            return
        index = self._preload_indexes.pop(0)
        monitor = getattr(self.app, "performance_monitor", None)
        token = None
        failed = False
        try:
            if monitor is not None:
                try:
                    token = monitor.page_started()
                except Exception:
                    pass
            self._refresh_tab(index)
        except Exception:
            failed = True
            logging.getLogger(__name__).exception("腾讯文档面板预载失败: %s", index)
        finally:
            if monitor is not None:
                try:
                    monitor.page_finished(token, f"tcdoc:{index}", failed, kind="page_preload")
                except Exception:
                    pass
            if self._preload_indexes:
                self._preload_timer.start(100)

    def on_show(self):
        self._page_visible = True
        if self._ready_done:
            if time.monotonic() - self._last_reload_at > 30:
                self.reload()
            else:
                self._refresh_active_tab()
        else:
            self._ensure_ready()

    def on_hide(self):
        self._page_visible = False

    def shutdown(self):
        self._closed = True
        self._preload_timer.stop()
        self._page_visible = False
        if self.bridge_tab_index in self._panels:
            self.bridge_panel.shutdown()
        if self.mapping_panel is not None:
            self.mapping_panel.shutdown()

    def _ensure_ready(self):
        """后台建库/迁移，让页面框架先显示；失败时下次进入可重试。"""
        if self._ready_done or self._ready_loading:
            return
        self._ready_loading = True
        self.tabs.setEnabled(False)
        self.doc_combo.setEnabled(False)
        self._set_busy(True, "正在读取本地数据…")

        def work():
            return tc_engine.job.ensure_ready()

        def done(note, err):
            if self._closed:
                return
            self._ready_loading = False
            self._ready_done = not bool(err)
            self.tabs.setEnabled(True)
            self.doc_combo.setEnabled(True)
            self._set_busy(False, f"初始化失败：{err}" if err else "", refresh=False)
            if err:
                self.app.toast(f"初始化失败：{err}"[:120], "error")
                return
            self._migrate_note = note or ""
            if note:
                self.app.toast(note, "info")
            if self._page_visible or self._preload_requested:
                self.reload()
                if self._preload_indexes:
                    self._preload_timer.start(100)

        run_bg(work, done)

    # ==================== 刷新 ====================

    def reload(self, prefer_doc_key=""):
        if prefer_doc_key:
            self._reload_doc_key = prefer_doc_key
        if not self._ready_done or self._closed or self._reload_pending:
            return
        self._reload_pending = True
        index = self.tabs.currentIndex()
        if index >= 0:
            self._skeletons[index].setText("正在刷新页面…")
            self.tabs.widget(index).setCurrentWidget(self._skeletons[index])
        self._defer(self._reload_now)

    def _reload_now(self):
        self._reload_pending = False
        if self._closed:
            return
        self._data_revision += 1
        self._last_reload_at = time.monotonic()
        self._reload_docs(self._reload_doc_key)
        self._reload_doc_key = ""
        self._refresh_summary()
        self._refresh_content_tab()
        self._loaded_tabs.clear()
        if self.mapping_panel is not None:
            self.mapping_panel.set_doc(self.current_doc())
        self._refresh_active_tab()

    def _bridge_loaded(self) -> bool:
        return getattr(self, "bridge_panel", None) is not None

    def _reload_docs(self, prefer_doc_key=""):
        current = self.current_doc()
        self.docs = tc_cfg.load_docs()
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        for doc in self.docs:
            self.doc_combo.addItem(doc["name"], doc["key"])
        if prefer_doc_key:
            idx = next((i for i, d in enumerate(self.docs) if d["key"] == prefer_doc_key), 0)
        elif current:
            idx = next((i for i, d in enumerate(self.docs) if d["key"] == current["key"]), 0)
        else:
            idx = 0
        if self.docs:
            self.doc_combo.setCurrentIndex(idx)
        self.doc_combo.blockSignals(False)
        self._refresh_auto_state()

    # ==================== 自动维护开关 / 子表范围 ====================

    def _refresh_auto_state(self):
        """回显总开关与当前文档的子表范围（只读配置，不触发同步）。"""
        self.auto_cb.blockSignals(True)
        self.auto_cb.setChecked(tc_cfg.auto_sync_enabled())
        self.auto_cb.blockSignals(False)
        doc = self.current_doc() or {}
        key = str(doc.get("key") or "")
        is_table_doc = bool(doc) and (doc.get("kind") or "").lower() in tables.SYNC_KINDS
        self.sync_btn.setVisible(is_table_doc)
        self.images_btn.setVisible(is_table_doc)
        self.sync_btn.setEnabled(is_table_doc and not self._busy)
        self.images_btn.setEnabled(is_table_doc and not self._busy)
        self.sync_tabs_btn.setEnabled(bool(key))
        if not key:
            self.sync_tabs_label.setText("子表范围：—")
            return
        names = tc_cfg.selected_tabs(key)
        if names is None:
            self.sync_tabs_label.setText("子表范围：全部子表（未限定）")
        else:
            preview = "、".join(names[:4]) + ("…" if len(names) > 4 else "")
            self.sync_tabs_label.setText(f"子表范围：{len(names)} 张（{preview}）")

    def _on_auto_toggled(self, checked):
        tc_cfg.set_auto_sync_enabled(checked)
        if checked:
            self.app.toast("自动维护已开启：定时任务只同步勾选的子表", "success")
        else:
            self.app.toast("自动维护已关闭：定时任务不再同步（计划任务保留）", "info")

    def _choose_sync_tabs(self):
        """勾选本子文档参与自动同步的子表；取消全部勾选 = 恢复「全部子表」。"""
        doc = self.current_doc() or {}
        key = str(doc.get("key") or "")
        if not key:
            return
        sheets = [s for s in tables.sheets_of(key) if not s.get("skipped")]
        names = [s["tab_name"] for s in sheets]
        if not names:
            QMessageBox.information(
                self, "还没有子表数据",
                "本地还没有这本文档的子表快照，请先在「表格同步」里同步一次，再来选择范围。")
            return
        current = tc_cfg.selected_tabs(key)
        checked = set(names if current is None else current)

        dlg = QDialog(self)
        dlg.setWindowTitle("选择自动同步的子表")
        dlg.resize(420, 460)
        v = QVBoxLayout(dlg)
        tip = QLabel("勾选的子表参与定时同步；未勾选的不拉取、不下图片，"
                     "本地已有数据保留不删。全部勾选等于「同步全部子表」。")
        tip.setProperty("role", "caption")
        tip.setWordWrap(True)
        v.addWidget(tip)
        boxes: dict[str, QCheckBox] = {}
        for name in names:
            cb = QCheckBox(name)
            cb.setChecked(name in checked)
            boxes[name] = cb
            v.addWidget(cb)
        v.addStretch(1)
        row = QHBoxLayout()
        all_btn = QPushButton("全选")
        all_btn.clicked.connect(lambda: [cb.setChecked(True) for cb in boxes.values()])
        row.addWidget(all_btn)
        none_btn = QPushButton("全不选")
        none_btn.clicked.connect(lambda: [cb.setChecked(False) for cb in boxes.values()])
        row.addWidget(none_btn)
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(dlg.reject)
        row.addWidget(cancel)
        ok = QPushButton("保存")
        ok.setProperty("variant", "primary")
        ok.clicked.connect(dlg.accept)
        row.addWidget(ok)
        v.addLayout(row)
        if dlg.exec() != QDialog.Accepted:
            return
        chosen = [name for name, cb in boxes.items() if cb.isChecked()]
        # 全选 = 不限定（None），这样以后新增的子表也会自动同步
        tc_cfg.set_selected_tabs(key, chosen if len(chosen) < len(names) else None)
        self._refresh_auto_state()
        self._loaded_tabs.discard(self.bridge_tab_index)
        bridge_panel = self._panels.get(self.bridge_tab_index)
        if bridge_panel is not None:
            bridge_panel.refresh(reload_targets=True, prefer_doc_key=key)
        self.app.toast(
            "已保存子表范围：全部" if len(chosen) == len(names)
            else f"已保存子表范围：{len(chosen)}/{len(names)} 张", "success")

    def _fill_doc_table(self):
        db = tc_cfg.db_path()
        docs = [dict(doc) for doc in self.docs]
        self._doc_rows = list(docs)
        self._doc_sorter.attach(self._doc_rows)

        def work():
            return [(doc, *storage.doc_stats(db, doc["key"])) for doc in docs]

        def items(row):
            doc, count, last = row
            return (QTableWidgetItem(doc["name"]), _cell(tc_cfg.kind_label(doc.get("kind"))),
                    _cell(count), _cell(last or "-"),
                    _cell(doc["url"], center=False, tip=doc["url"]))

        self._load_local(self.doc_tab_index, work,
                         lambda rows, done, valid: self._render_table(
                             self.doc_table, rows, items, done, valid))

    def _refresh_tables(self, index=None):
        """填指定历史/明细页；预载隐藏页时不改变当前页签。"""
        index = self.tabs.currentIndex() if index is None else index
        daily = index == self.daily_tab_index
        table = self.table_daily if daily else self.table_snap
        doc = self.current_doc()
        db = tc_cfg.db_path()

        def work():
            data = []
            if not doc:
                return data
            if daily:
                for row in reversed(storage.all_daily(db, doc["key"])):
                    data.append((row["ts"], "-" if row["views"] is None else row["views"],
                                 _fmt_delta(row["views_delta"]), row["members"],
                                 _fmt_delta(row["members_delta"]),
                                 "-" if row["readers"] is None else row["readers"]))
            else:
                for row in reversed(storage.all_snapshots(db, doc["key"], limit=2000)):
                    data.append((row["ts"], "成功" if row["ok"] else "失败",
                                 "-" if row["views"] is None else row["views"],
                                 "-" if row["members"] is None else row["members"],
                                 row["message"] or ""))
            return data

        def items(values):
            return [_cell(value, center=not (not daily and c == 4),
                          tip=str(value) if not daily and c == 4 else "")
                    for c, value in enumerate(values)]

        self._load_local(self.daily_tab_index if daily else self.snapshot_tab_index,
                         work, lambda rows, done, valid: self._render_table(
                             table, rows, items, done, valid))

    def _refresh_summary(self):
        doc = self.current_doc()
        if not doc:
            self.link_btn.setVisible(False)
            self.latest_label.setText("尚未配置文档，可在「文档管理」里添加")
            return
        url = str(doc.get("url") or "")
        self.link_btn.setVisible(bool(url))
        self.link_btn.setToolTip(f"在浏览器打开：{url}" if url else "当前文档没有链接")
        self.latest_label.setText("正在读取最新记录…")
        revision = self._data_revision
        db = tc_cfg.db_path()
        self.latest_label.setText("正在读取最新记录…")

        def done(row, err):
            if self._closed or revision != self._data_revision:
                return
            if err:
                self.latest_label.setText(f"最新记录读取失败：{err}"[:200])
            elif not row:
                self.latest_label.setText("该文档暂无指标记录，点「采集指标」开始")
            else:
                views = "-" if row["views"] is None else row["views"]
                self.latest_label.setText(
                    f"最新记录 {row['date']} · 浏览量 {views} · 成员 {row['members']}"
                    f" · 已查看 {row['readers'] if row['readers'] is not None else '-'}")

        run_bg(lambda: storage.latest(db, doc["key"]), done, owner=self)

    # ==================== 状态 ====================

    def current_doc(self) -> dict | None:
        if not self.docs:
            return None
        idx = self.doc_combo.currentIndex()
        if 0 <= idx < len(self.docs):
            return self.docs[idx]
        return self.docs[0]

    def _on_doc_changed(self, _index):
        self._refresh_auto_state()
        if not self._ready_done:
            return
        self._data_revision += 1
        self._refresh_summary()
        self._refresh_content_tab()
        self._loaded_tabs.clear()
        if self.mapping_panel is not None:
            self.mapping_panel.set_doc(self.current_doc())
        self._refresh_active_tab()

    def _refresh_content_tab(self):
        """内容 Tab 自动切换：表格类型=表格同步，文档类型=正文预览。"""
        doc = self.current_doc()
        syncable = (not doc) or (doc.get("kind") or "").lower() in tables.SYNC_KINDS
        self.tabs.setTabText(self.content_tab_index,
                             "表格同步" if syncable else "正文预览")
        if self.content_tab_index in self._panels:
            self.content_stack.setCurrentWidget(
                self.table_panel if syncable else self.preview_panel)
            if not syncable and self.tabs.currentIndex() == self.content_tab_index:
                self.preview_panel.set_doc(doc)

    def _on_tab_changed(self, index):
        self._refresh_active_tab()

    def _refresh_active_tab(self):
        self._refresh_tab(self.tabs.currentIndex())

    def _refresh_tab(self, index):
        if not self._ready_done or self._busy:
            return
        if index < 0:
            return
        if index in self._loaded_tabs:
            self._show_panel(index)
            return
        request = self._local_requests.get(index)
        if request is not None and request[0] == self._data_revision:
            return
        self._ensure_panel(index)
        if index == self.bridge_tab_index:
            self.bridge_panel.refresh(
                reload_targets=True, prefer_doc_key=(self.current_doc() or {}).get("key", ""))
            self._show_panel(index)
            self._loaded_tabs.add(index)
        elif index == self.revision_tab_index:
            self._refresh_revisions()
        elif index == self.members_tab_index:
            self._refresh_members()
        elif index == self.doc_tab_index:
            self._fill_doc_table()
        elif index == self.mapping_tab_index:
            self.mapping_panel.set_doc(self.current_doc())
            self._show_panel(index)
            self._loaded_tabs.add(index)
        elif index in (self.daily_tab_index, self.snapshot_tab_index):
            self._refresh_tables(index)
        elif index == self.content_tab_index:
            self._refresh_content_tab()
            doc = self.current_doc()
            if not doc or (doc.get("kind") or "").lower() in tables.SYNC_KINDS:
                self._refresh_sheet_tabs()
            else:
                self.preview_panel.set_doc(doc)
                self._show_panel(index)
                self._loaded_tabs.add(index)

    # ==================== 表格同步 ====================

    def _refresh_sheet_tabs(self):
        """按当前文档刷新工作表下拉（含被跳过的非表格页提示）。"""
        doc = self.current_doc()
        keep = self._current_tab_id()
        page_size = max(20, int(tc_cfg.load_cfg().get("tcdoc_table_page") or 200))
        newest = self._newest_first()

        def work():
            sheets = tables.sheets_of(doc["key"]) if doc else []
            available = [sheet for sheet in sheets if not sheet["skipped"]]
            sheet = next((s for s in available if s["tab_id"] == keep),
                         available[0] if available else {})
            data = self._read_sheet_page(doc, sheet, 0, page_size, newest)
            return sheets, data

        def apply(result, done, valid):
            sheets, data = result
            self._sheets = sheets
            self._apply_sheet_tabs(doc, sheets, keep)
            self._apply_sheet_page(data, page_size, done, valid)

        self._load_local(self.content_tab_index, work, apply)

    def _apply_sheet_tabs(self, doc, sheets, keep):
        self.sheet_combo.blockSignals(True)
        self.sheet_combo.clear()
        for sheet in sheets:
            if sheet["skipped"]:
                continue
            self.sheet_combo.addItem(f"{sheet['tab_name']}（{sheet['n_rows']} 行）",
                                     sheet["tab_id"])
        self.sheet_combo.blockSignals(False)

        skipped = [s for s in sheets if s["skipped"]]
        if keep:
            idx = next((i for i, s in enumerate(
                [x for x in sheets if not x["skipped"]]) if s["tab_id"] == keep), None)
            if idx is not None:
                self.sheet_combo.blockSignals(True)
                self.sheet_combo.setCurrentIndex(idx)
                self.sheet_combo.blockSignals(False)

        if not doc:
            self.sheet_hint.setText("尚未配置文档")
        elif (doc.get("kind") or "").lower() not in tables.SYNC_KINDS:
            self.sheet_hint.setText("该文档类型没有表格数据，内容 Tab 已自动切换为正文预览")
        elif not sheets:
            self.sheet_hint.setText("该文档还没有本地表格数据，点顶部「同步表格数据」开始")
        elif skipped:
            names = "、".join(s["tab_name"] for s in skipped[:6])
            more = " 等" if len(skipped) > 6 else ""
            self.sheet_hint.setText(f"已跳过 {len(skipped)} 个非表格页：{names}{more}"
                                    "（普通表格 / 图表页 / 文章页不支持同步）")
        else:
            self.sheet_hint.setText("点顶部「同步表格数据」把文档里的表格内容抓到本地；"
                                    "图片字段会自动增量下载到 图片/。")

    def _current_tab_id(self) -> str:
        data = self.sheet_combo.currentData()
        return str(data) if data else ""

    def _current_sheet(self) -> dict | None:
        doc, tab_id = self.current_doc(), self._current_tab_id()
        if not doc or not tab_id:
            return None
        return next((s for s in self._sheets if s["tab_id"] == tab_id), None)

    def _newest_first(self) -> bool:
        box = getattr(self, "newest_cb", None)
        return bool(box is not None and box.isChecked())

    def _reset_sheet_sort(self):
        self._data_sorter.col, self._data_sorter.ascending = -1, True
        self._data_sorter._update_indicator()

    def _on_sheet_changed(self, *_args):
        self._reset_sheet_sort()
        self._load_sheet_page(reset=True)

    def _on_newest_changed(self, *_args):
        self._reset_sheet_sort()
        self._load_sheet_page(reset=True)

    def _sheet_sort_changed(self, *_args):
        self._load_sheet_page(reset=True)

    def _load_sheet_page(self, reset: bool = False, delta: int = 0):
        doc = self.current_doc()
        sheet = self._current_sheet() or {}
        page_size = max(20, int(tc_cfg.load_cfg().get("tcdoc_table_page") or 200))
        offset = 0 if reset else self._sheet_offset
        offset = max(0, offset + delta * page_size)
        newest = self._newest_first()
        column = self._data_sorter.col
        sort_column = self._sheet_columns[column] if 0 <= column < len(self._sheet_columns) else ""
        descending = not self._data_sorter.ascending
        self._load_local(self.content_tab_index,
                         lambda: self._read_sheet_page(
                             doc, sheet, offset, page_size, newest, sort_column, descending),
                         lambda data, done, valid: self._apply_sheet_page(
                             data, page_size, done, valid))

    @classmethod
    def _read_sheet_page(cls, doc, sheet, offset, page_size, newest=True,
                         sort_column="", descending=False):
        tab_id = sheet.get("tab_id")
        if not doc or not tab_id:
            return sheet, [], 0, 0, {}
        rows, total, offset = table_preview.read_page(
            doc["key"], sheet, offset, page_size, newest, sort_column, descending)
        try:
            image_stats = tc_engine.images.stats(doc["key"], tab_id)
        except Exception:  # noqa: BLE001 - 图片统计异常不影响表格预览
            image_stats = {}
        return sheet, rows, total, offset, image_stats

    def _apply_sheet_page(self, data, page_size, done, valid):
        sheet, rows, total, self._sheet_offset, image_stats = data
        self._sheet_total = total
        if not sheet:
            self.table_data.setRowCount(0)
            self.table_data.setColumnCount(0)
            self.page_label.setText("-/-")
            self.sheet_info.setText("")
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
            done()
            return

        names = [c["name"] for c in sheet.get("columns", [])]
        columns_changed = names != self._sheet_columns
        self._sheet_columns = names
        self.table_data.setColumnCount(len(names))
        self.table_data.setHorizontalHeaderLabels(names)
        if columns_changed:
            table_columns.configure_table_columns(
                self.table_data, stretch=range(len(names)), auto_fit=True)
        self.table_data.setRowCount(len(rows))
        for r, row in enumerate(rows):
            self.table_data.setVerticalHeaderItem(r, QTableWidgetItem(str(row["row_no"])))

        def items(row):
            result = []
            for name in names:
                value = str(row["values"].get(name, "") or "")
                item = QTableWidgetItem(value.replace("\n", " ⏎ ")[:200])
                if value:
                    item.setToolTip(value[:1500])
                result.append(item)
            if result:
                # 行号挂在首列，排序后按新行序还原竖表头（排序只移动单元格，不动竖表头）
                result[0].setData(Qt.UserRole, str(row["row_no"]))
            return result
        pages = max(1, (total + page_size - 1) // page_size)
        current = self._sheet_offset // page_size + 1 if total else 0
        self.page_label.setText(f"{current}/{pages}")
        self.prev_btn.setEnabled(self._sheet_offset > 0)
        self.next_btn.setEnabled(self._sheet_offset + page_size < total)
        stamp = sheet.get("fetched_at") or "-"
        image_note = ""
        if image_stats.get("total"):
            image_note = (f" · 图片 {image_stats.get('done', 0)}/{image_stats['total']}"
                          + (f"（失败 {image_stats['failed']}）"
                             if image_stats.get("failed") else ""))
        self.sheet_info.setText(
            f"{len(names)} 列 · {total} 行{image_note} · 同步于 {stamp}")

        def rendered(error=None):
            if not error:
                self._restore_sheet_row_numbers()
            done(error)

        self._render_table(self.table_data, rows, items, rendered, valid)

    def _restore_sheet_row_numbers(self):
        """排序后重排竖表头的原始行号（单元格被整体搬行，竖表头不会跟随）。"""
        table = self.table_data
        for r in range(table.rowCount()):
            item = table.item(r, 0)
            row_no = item.data(Qt.UserRole) if item is not None else None
            if row_no:
                table.setVerticalHeaderItem(r, QTableWidgetItem(str(row_no)))

    def _sync_tables(self):
        if self._busy:
            return
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先添加腾讯文档", "warning")
            return
        if (doc.get("kind") or "").lower() not in tables.SYNC_KINDS:
            self.app.toast("只有「表格 / 智能表格」类型能同步数据；文档类型请看「正文预览」",
                           "warning")
            return
        self._set_busy(True, "正在同步表格数据……")

        def work():
            return tc_engine.job.sync_tables(
                [doc], on_status=lambda s: post_to_main(self.status_label.setText, f"{s}"))

        def done(results, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"同步失败：{err}"[:120], "error")
                return
            self._refresh_sheet_tabs()
            item = results[0] if results else {}
            if not item.get("ok"):
                QMessageBox.warning(self, "同步失败", item.get("error") or "未知错误")
                return
            if item.get("tables_error"):
                self.app.toast(item["tables_error"][:120], "warning")
                return
            if item.get("version_error"):
                self.status_label.setText(
                    "当前表格已保存，但采集版本保存失败，请检查磁盘空间后重新同步")
                self._refresh_bridge_hint()
                return
            if item.get("tables_unchanged"):
                image_stats = item.get("images") or {}
                note = (f"，补采图片 {image_stats['downloaded']} 张"
                        if image_stats.get("downloaded") else "")
                self.status_label.setText(f"文档未变化，已跳过同步{note}（rev 未变）")
                self.app.toast(f"文档未变化，已跳过同步{note}", "info")
                self._refresh_bridge_hint()
                return
            added = sum(t["added"] for t in item.get("tables_delta") or [])
            updated = sum(t["updated"] for t in item.get("tables_delta") or [])
            removed = sum(t["removed"] for t in item.get("tables_delta") or [])
            image_stats = item.get("images") or {}
            image_note = ""
            if image_stats.get("total"):
                image_note = (f"　图片 {image_stats.get('downloaded', 0)} 张新增"
                              f"/共 {image_stats['total']} 张")
                if image_stats.get("failed"):
                    image_note += f"（失败 {image_stats['failed']}）"
            self.status_label.setText(
                f"表格数据同步完成：新增 {added}　修改 {updated}　删除 {removed}"
                f"{image_note}　Excel 已更新到 表格数据/")
            if item.get("images_error"):
                self.status_label.setText(
                    self.status_label.text() + f"　{item['images_error']}")
            self.app.toast(f"表格数据同步完成：+{added} ~{updated} -{removed}"
                           + (f"，图片 +{image_stats.get('downloaded', 0)}"
                              if image_stats.get("downloaded") else ""), "success")
            self._refresh_bridge_hint()

        run_bg(work, done)

    def _refresh_bridge_hint(self):
        """表格同步完成后，在底部提示本地还有多少行尚未上传。"""
        doc = self.current_doc()
        if not doc:
            return
        from gui_qt import common
        collected = list(common.COLLECTED)  # 主线程快照，避免采集线程并发追加

        def work():
            from toolbox.tcdoc import bridge
            return bridge.default_pending_count(doc.get("key"), collected=collected)

        def done(count, err):
            if err or count < 0:
                return
            if count:
                self.sheet_hint.setText(
                    f"本地有 {count} 行尚未同步到当前腾讯表格 · "
                    "点上方「中间层同步」页签查看并一键复制")
            else:
                self.sheet_hint.setText(
                    "本地与当前腾讯表格已对齐（默认时间范围/账号）· "
                    "点上方「中间层同步」页签可查看明细")

        run_bg(work, done)

    def _collect_images(self):
        """补采本地已同步表格里的图片字段（不重新抓表格）。"""
        if self._busy:
            return
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先添加腾讯文档", "warning")
            return
        self._set_busy(True, "正在采集表格图片……")

        def work():
            return tc_engine.images.download_doc(
                doc["key"], doc["name"],
                on_status=lambda s: post_to_main(self.status_label.setText, f"{s}"))

        def done(result, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"图片采集失败：{err}"[:120], "error")
                return
            total = int((result or {}).get("total") or 0)
            downloaded = int((result or {}).get("downloaded") or 0)
            failed = int((result or {}).get("failed") or 0)
            if not total:
                self.status_label.setText("该文档的表格里没有图片字段")
                self.app.toast("该文档的表格里没有图片字段", "info")
                return
            if downloaded:
                message = (f"图片采集完成：共 {total} 张，本次下载 {downloaded} 张"
                           + (f"，失败 {failed} 张" if failed else ""))
            else:
                message = f"图片已是最新：共 {total} 张"
                if failed:
                    message += f"，失败 {failed} 张"
            self.status_label.setText(message + "　（图片/）")
            self.app.toast(message, "warning" if failed else "success")
            self._load_sheet_page()

        run_bg(work, done)

    # ==================== 修订记录 / 成员名单 ====================

    def _refresh_revisions(self):
        self._rev_loaded = 0
        self._rev_total = 0
        self.rev_table.setRowCount(0)
        if not self.current_doc():
            self.rev_info.setText("")
            self.rev_more_btn.setEnabled(False)
            self.rev_all_btn.setEnabled(False)
            self._show_panel(self.revision_tab_index)
            self._loaded_tabs.add(self.revision_tab_index)
            return
        self._load_more_revisions(reset=True)

    def _load_more_revisions(self, reset: bool = False, count: int | None = None):
        doc = self.current_doc()
        if not doc:
            return
        if not reset and self._rev_total and self._rev_loaded >= self._rev_total:
            self._update_rev_info()
            return
        limit = int(count or self._rev_batch)
        offset = 0 if reset else self._rev_loaded

        def work():
            rows, total = tc_engine.revision.load_rows(doc["key"], limit=limit, offset=offset)
            return rows, total, tc_engine.revision.meta(doc["key"])

        def items(row):
            return (_cell(row["ts"]), _cell(f"第{row['version']}版" if row["version"] else ""),
                    _cell(row["author"], center=False), _cell(row["rev"]), _cell(row["type"]))

        def apply(result, done, valid):
            rows, self._rev_total, self._rev_meta = result

            def rendered(error=None):
                if valid() and not error:
                    self._rev_loaded = offset + len(rows)
                    self._update_rev_info()
                done(error)

            self._render_table(self.rev_table, rows, items, rendered, valid, offset=offset)

        self._load_local(self.revision_tab_index, work, apply)

    def _on_rev_scrolled(self, value):
        bar = self.rev_table.verticalScrollBar()
        if (self._rev_loading or self.revision_tab_index in self._local_running
                or not self._rev_total):
            return
        if self._rev_loaded >= self._rev_total:
            return
        if bar.maximum() and value >= bar.maximum() - 4:
            self._rev_loading = True
            QTimer.singleShot(0, self._load_next_rev_batch)

    def _load_next_rev_batch(self):
        try:
            self._load_more_revisions()
        finally:
            self._rev_loading = False

    def _load_all_revisions(self):
        if not self._rev_total or self._rev_loaded >= self._rev_total:
            return
        self.setCursor(Qt.WaitCursor)
        try:
            self._load_more_revisions(count=self._rev_total - self._rev_loaded)
        finally:
            self.unsetCursor()

    def _update_rev_info(self):
        doc = self.current_doc()
        if not doc:
            self.rev_info.setText("")
            return
        meta_info = self._rev_meta
        if not meta_info:
            self.rev_info.setText("暂无本地修订记录，点「拉取全部修订记录」")
            self.rev_more_btn.setEnabled(False)
            self.rev_all_btn.setEnabled(False)
            return
        authors = meta_info.get("authors") or []
        names = "、".join(authors[:6]) + (
            f" 等 {len(authors)} 人" if len(authors) > 6 else "")
        note = (f"共 {self._rev_total} 条编辑记录 · {meta_info.get('versions', 0)} 个版本"
                f" · 编辑人：{names or '-'}"
                f" · 已加载 {self._rev_loaded}/{self._rev_total} 条")
        if self._rev_loaded < self._rev_total:
            note += "（滚动到底自动加载）"
        if not meta_info.get("complete", True):
            note += " · 上次未拉全"
        self.rev_info.setText(note)
        more = self._rev_loaded < self._rev_total
        self.rev_more_btn.setEnabled(more)
        self.rev_all_btn.setEnabled(more)

    def _fetch_revisions(self, full: bool = False):
        if self._busy:
            return
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先添加腾讯文档", "warning")
            return
        mode = "全量重拉" if full else "增量拉取"
        self._set_busy(True, f"正在{mode}修订记录……")

        def work():
            result = tc_engine.revision.collect(
                doc, on_status=lambda s: post_to_main(self.status_label.setText, f"{s}"),
                incremental=not full)
            return tc_engine.revision.save(result)

        def done(saved, err):
            self._set_busy(False)
            if err:
                QMessageBox.warning(self, "修订记录拉取失败", str(err))
                self.app.toast(f"拉取失败：{err}"[:120], "error")
                return
            self._refresh_revisions()
            detail = "本次新增"
            if saved.get("incremental"):
                detail += f" {saved.get('added', 0)} 条"
                if saved.get("stopped_at_page"):
                    detail += f"（第 {saved['stopped_at_page']} 页到上次位置）"
            else:
                detail += f" {saved.get('added', 0)} 条 · 全量校验更新 {saved.get('updated', 0)} 条"
            message = (f"修订记录：{saved.get('versions', 0)} 个版本 / "
                       f"{saved.get('total', 0)} 条编辑（{detail}）")
            if saved.get("skipped_export"):
                message += "；内容无变化，未重写导出文件"
            if saved.get("excel_error"):
                message += "；Excel 写入失败（文件可能被占用）"
            self.status_label.setText(message + "　（修订记录/）")
            self.app.toast(message, "warning" if saved.get("excel_error") else "success")

        run_bg(work, done)

    def _refresh_members(self):
        doc = self.current_doc()

        def work():
            if not doc:
                return [], 0, None
            users, total = tc_engine.members.load_users(doc["key"], limit=5000)
            return users, total, tc_engine.members.meta(doc["key"])

        def items(user):
            return (_cell(user["name"], center=False, tip=user["name"]),
                    _cell(user["role"]), _cell(user["source"]),
                    _cell(user["uid"], center=False, tip=user["uid"]),
                    _cell(user["corp_name"], center=False))

        def apply(result, done, valid):
            users, total, meta_info = result
            self._update_member_info(total, meta_info)
            self._render_table(self.mem_table, users, items, done, valid)

        self._load_local(self.members_tab_index, work, apply)

    def _update_member_info(self, total, meta_info):
        if not self.current_doc():
            self.mem_info.setText("")
            return
        if not meta_info:
            self.mem_info.setText("暂无本地成员名单，点「拉取全部成员」")
            return
        roles = meta_info.get("roles") or {}
        dist = " · ".join(f"{k} {v}" for k, v in roles.items())
        note = (f"共 {meta_info.get('records', total)} 条记录 / {total} 位成员（去重）")
        if dist:
            note += " · " + dist
        if not meta_info.get("complete", True):
            note += " · 上次未拉全"
        self.mem_info.setText(note)

    def _fetch_members(self):
        if self._busy:
            return
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先添加腾讯文档", "warning")
            return
        self._set_busy(True, "正在拉取全部成员……")

        def work():
            result = tc_engine.members.collect(
                doc, on_status=lambda s: post_to_main(self.status_label.setText, f"{s}"))
            return tc_engine.members.save(result)

        def done(saved, err):
            self._set_busy(False)
            if err:
                QMessageBox.warning(self, "成员名单拉取失败", str(err))
                self.app.toast(f"拉取失败：{err}"[:120], "error")
                return
            self._refresh_members()
            if not saved.get("records"):
                message = "该文档没有返回成员名单（需要文档管理权限）"
                self.status_label.setText(f"{message}")
                self.app.toast(message, "warning")
                return
            message = (f"成员名单：{saved.get('records', 0)} 条记录 / "
                       f"{saved.get('unique', 0)} 位成员（去重，本次新增 {saved.get('added', 0)}）")
            if saved.get("excel_error"):
                message += "；Excel 写入失败（文件可能被占用）"
            self.status_label.setText(message + "　（成员名单/）")
            self.app.toast(message, "warning" if saved.get("excel_error") else "success")

        run_bg(work, done)

    def _open_dir(self, path):
        path = Path(path)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.app.toast(f"打开失败：{path}", "warning")

    def _set_busy(self, busy: bool, text: str = "", refresh: bool = True):
        self._busy = busy
        for name in ("collect_btn",
                     "add_btn", "edit_btn", "del_btn", "probe_btn",
                     "rebuild_btn", "sync_btn", "images_btn",
                     "rev_fetch_btn", "rev_more_btn", "rev_all_btn", "mem_fetch_btn"):
            btn = getattr(self, name, None)
            if btn is not None:
                btn.setEnabled(not busy)
        if self.content_tab_index in self._panels:
            self.preview_panel.set_enabled(not busy)
            if busy:
                self.prev_btn.setEnabled(False)
                self.next_btn.setEnabled(False)
            elif refresh and self.tabs.currentIndex() == self.content_tab_index:
                self._load_sheet_page()
        if refresh and not busy and self._page_visible:
            self._refresh_active_tab()
        self.status_label.setText(text)

    # ==================== 采集 ====================

    def _collect(self):
        if self._busy:
            return
        if not self.docs:
            self.app.toast("请先添加至少一个腾讯文档", "warning")
            return
        self._set_busy(True, "正在采集历史记录……")

        def work():
            return tc_engine.job.collect_all(
                on_status=lambda s: post_to_main(self.status_label.setText, f"{s}"))

        def done(results, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"采集失败：{err}"[:120], "error")
                return
            ok = [r for r in results if r.get("ok")]
            failed = [r for r in results if not r.get("ok")]
            report_err = [r for r in ok if r.get("report_error")]
            self.reload()
            browse_total = sum((r.get("browse") or {}).get("total") or 0 for r in ok)
            browse_added = sum((r.get("browse") or {}).get("added") or 0 for r in ok)
            browse_note = (f"，浏览人 {browse_total}（新增 {browse_added}）"
                           if browse_total else "")
            self.status_label.setText(
                f"历史采集完成：成功 {len(ok)}/{len(results)}"
                f"，已写入数据库与报表{browse_note}")
            if failed:
                detail = "\n".join(f"· {r['name']}：{r.get('error') or '未知错误'}" for r in failed)
                QMessageBox.warning(self, "部分文档采集失败",
                                    f"成功 {len(ok)}/{len(results)}。\n\n{detail}")
                self.app.toast(f"{len(failed)} 个文档采集失败", "warning")
            elif report_err:
                self.app.toast("数据已入库，但报表写入失败（文件可能被打开）", "warning")
            else:
                self.app.toast(f"历史采集完成 {len(ok)}/{len(results)}", "success")

        run_bg(work, done)

    # ==================== 报表 / 目录 ====================

    def _rebuild_reports(self):
        if self._busy:
            return
        self._set_busy(True, "正在重建全部报表……")

        def done(count, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"重建失败：{err}"[:120], "error")
                return
            self.reload()
            self.app.toast(f"已重建 {count} 个文档的报表", "success")

        run_bg(tc_engine.job.rebuild_reports, done)

    def _open_data_dir(self):
        path = tc_cfg.data_dir()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.app.toast(f"打开失败：{path}", "warning")

    def _open_doc_url(self):
        doc = self.current_doc() or {}
        url = str(doc.get("url") or "")
        if url and not QDesktopServices.openUrl(QUrl(url)):
            self.app.toast(f"打开失败：{url}", "warning")

    # ==================== 文档管理 ====================

    def _selected_doc(self) -> dict | None:
        row = self.doc_table.currentRow()
        if 0 <= row < len(self._doc_rows):
            return self._doc_rows[row]
        return None

    def _on_doc_row_selected(self):
        doc = self._selected_doc()
        if doc is None:
            return
        idx = next((i for i, d in enumerate(self.docs) if d["key"] == doc["key"]), None)
        if idx is not None and idx != self.doc_combo.currentIndex():
            self.doc_combo.setCurrentIndex(idx)

    def _add_doc(self):
        dialog = _DocDialog(self, None, [(d["key"], d["url"]) for d in self.docs])
        if dialog.exec() != QDialog.Accepted or not dialog.result_doc:
            return
        doc = dialog.result_doc
        docs = self.docs + [doc]
        tc_cfg.save_docs(docs)
        self.reload(prefer_doc_key=doc["key"])
        self.app.toast(f"已添加文档「{doc['name']}」，可直接采集", "success")

    def _edit_doc(self):
        doc = self._selected_doc() or self.current_doc()
        if not doc:
            self.app.toast("请先选择要编辑的文档", "warning")
            return
        dialog = _DocDialog(self, doc, [(d["key"], d["url"]) for d in self.docs])
        if dialog.exec() != QDialog.Accepted or not dialog.result_doc:
            return
        new = dialog.result_doc
        docs = []
        for item in self.docs:
            if item["key"] == doc["key"]:
                docs.append(new)
            else:
                docs.append(item)
        tc_cfg.save_docs(docs)
        self.reload(prefer_doc_key=new["key"])
        self.app.toast(f"已保存「{new['name']}」", "success")

    def _delete_doc(self):
        doc = self._selected_doc() or self.current_doc()
        if not doc:
            self.app.toast("请先选择要删除的文档", "warning")
            return
        count, _last = storage.doc_stats(tc_cfg.db_path(), doc["key"])
        box = QMessageBox(self)
        box.setWindowTitle("删除文档")
        box.setIcon(QMessageBox.Question)
        box.setText(f"确定从清单中删除「{doc['name']}」？")
        box.setInformativeText(f"该文档现有 {count} 条历史记录；不勾选下面的选项时记录会保留"
                               "（重新添加同一文档即可找回）。")
        del_data = QCheckBox("同时删除该文档的历史记录与采集明细")
        box.setCheckBox(del_data)
        yes = box.addButton("删除", QMessageBox.YesRole)
        box.addButton("取消", QMessageBox.NoRole)
        box.exec()
        if box.clickedButton() is not yes:
            return
        tc_cfg.save_docs([d for d in self.docs if d["key"] != doc["key"]])
        if del_data.isChecked():
            removed = storage.clear_doc(tc_cfg.db_path(), doc["key"])
            table_rows = tables.clear_doc(doc["key"])
            page_rows = tc_engine.archive.clear_doc(doc["key"])
            try:
                tc_engine.archive.snapshot_path(doc).unlink(missing_ok=True)
            except OSError:
                pass
            self.app.toast(f"已删除文档与其 {removed} 条历史记录"
                           f"（表格数据 {table_rows} 行，正文存档 {page_rows} 页；"
                           "已下载图片文件保留）", "success")
        else:
            self.app.toast(f"已删除文档「{doc['name']}」", "success")
        self.reload()

    def _probe(self):
        doc = self._selected_doc() or self.current_doc()
        if not doc or self._busy:
            self.app.toast("请先选择要诊断的文档", "warning")
            return
        self._set_busy(True, "正在生成诊断包（会打开浏览器窗口）……")

        def work():
            return tc_engine.collector.probe(
                doc, on_status=lambda s: post_to_main(self.status_label.setText, f"{s}"))

        def done(path, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"诊断失败：{err}"[:120], "error")
                return
            self.status_label.setText(f"诊断包已生成：{path}")
            self.app.toast(f"诊断包已保存到 调试/{path.name}", "success")

        run_bg(work, done)
