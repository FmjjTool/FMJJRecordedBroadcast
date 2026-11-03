"""信息获取 · 腾讯文档监控页：浏览量 / 文档成员数 每日自动记录。

与工具箱其它页面保持同一套视觉语言：标题行 + ❓功能介绍浮层 + 白卡片 + 表格 +
底部设置行，功能按钮带 emoji 图标。

功能：
- 文档下拉切换（每个文档独立记录），登录态统一由 Cookie 管理器持有（平台：腾讯文档）
- 立即采集（接口优先、界面兜底），每次采集追加一行并算增量，各自出 Excel
- 内容 Tab 自动切换：表格类型显示「表格同步」；文档 / AI 文档等显示「正文预览」
  （AI 文档自动抓取全部子页面，支持导出 Markdown/TXT 并自动存档）
- 其余 Tab：历史记录 / 采集明细 / 文档管理（自定义文档增删改 + 诊断包）
- 底部：每日定时采集时间 + 计划任务安装/删除 + 采集时隐藏浏览器窗口 + 表格同步开关
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTableWidget,
    QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from toolbox.tcdoc import (
    archive, collector, config as tc_cfg, job, storage, tables, task)

DAILY_COLS = [("采集时间", 170), ("浏览量", 110), ("浏览增量", 110),
              ("成员数", 110), ("成员增量", 110), ("已查看人数", 110)]
SNAP_COLS = [("采集时间", 170), ("状态", 90), ("浏览量", 110), ("成员数", 110),
             ("说明", 420)]
DOC_COLS = [("名称", 260), ("类型", 100), ("记录数", 90), ("最近采集", 170), ("链接", 300)]
CHANGE_COLS = [("时间", 160), ("工作表", 150), ("变更", 70), ("记录", 100), ("内容", 300)]


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
    """正文预览页：内容 Tab 在「表格同步」和「正文预览」之间自动切换。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._doc: dict | None = None
        self._loading = False
        self._loading_key = ""
        self._req = 0
        self._pages: list[dict] = []
        self._full_text = ""
        self._summary = ""

        v = QVBoxLayout(self)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("页面"))
        self.page_combo = QComboBox()
        self.page_combo.setMinimumWidth(220)
        self.page_combo.setToolTip("切换该 AI 文档里的子页面；默认「全部」合并显示")
        self.page_combo.currentIndexChanged.connect(self._on_page_changed)
        self.page_combo.hide()
        bar.addWidget(self.page_combo)
        self.kind_label = QLabel("")
        self.kind_label.setStyleSheet("color: #888;")
        bar.addWidget(self.kind_label)
        bar.addStretch(1)
        self.fetch_btn = QPushButton("🔄 抓取正文")
        self.fetch_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.fetch_btn.setToolTip("从腾讯文档读取全部子页面并自动存档（AI 文档约需 1 分钟）")
        self.fetch_btn.clicked.connect(lambda *_: self.load())
        bar.addWidget(self.fetch_btn)
        self.export_btn = QPushButton("💾 导出")
        self.export_btn.setToolTip(
            "导出当前预览正文（Markdown / TXT）；多篇 AI 文档可合并或每篇一个文件")
        self.export_btn.setEnabled(False)
        self.export_btn.clicked.connect(self._export)
        bar.addWidget(self.export_btn)
        v.addLayout(bar)

        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setPlaceholderText("本地暂无正文存档，点「🔄 抓取正文」开始")
        v.addWidget(self.text_view, 1)

        self.status = QLabel("")
        self.status.setStyleSheet("color: #888;")
        self.status.setTextFormat(Qt.PlainText)
        v.addWidget(self.status)

    # ==================== 文档切换 / 本地存档 ====================

    def set_doc(self, doc: dict | None):
        """切文档：优先显示本地存档；没有存档时等 Tab 真正显示再自动抓取。"""
        same = bool(self._doc and doc and self._doc.get("key") == doc.get("key"))
        if same and self._pages:
            self._doc = dict(doc)
            return
        self._req += 1
        self._loading = False
        self._loading_key = ""
        self._doc = dict(doc) if doc else None
        self._pages = []
        self._full_text = ""
        self._summary = ""
        self.page_combo.blockSignals(True)
        self.page_combo.clear()
        self.page_combo.blockSignals(False)
        self.page_combo.hide()
        self.export_btn.setEnabled(False)
        self.status.setToolTip("")
        self.text_view.setPlaceholderText("本地暂无正文存档，点「🔄 抓取正文」开始")
        self.text_view.clear()
        if not doc:
            self.kind_label.setText("")
            self.fetch_btn.setEnabled(False)
            self.status.setText("尚未配置文档")
            return
        self.kind_label.setText(tc_cfg.kind_label(doc.get("kind")))
        self.fetch_btn.setEnabled(True)
        self.fetch_btn.setText("🔄 抓取正文")
        try:
            cached = archive.pages_of(doc["key"])
        except Exception:  # noqa: BLE001 - 存档库异常不应挡住页面
            cached = []
        if not cached:
            self.status.setText("本地暂无存档")
            return
        pages = [{"id": p["id"], "title": p["title"], "level": p["level"],
                  "text": p["text"], "error": ""} for p in cached]
        last = max((p.get("fetched_at") or "" for p in cached), default="")
        try:
            snap = archive.snapshot_path(doc)
            snap_tip = str(snap) if snap.exists() else ""
        except Exception:  # noqa: BLE001
            snap_tip = ""
        self._apply_pages(
            pages, collector.join_pages(pages),
            f"📂 本地存档于 {last} · 共 {len(pages)} 篇 · 点「🔄 重新抓取」更新",
            snap_tip)

    def ensure_loaded(self):
        """Tab 真正显示时才抓取；已有本地存档则直接看存档。"""
        if self._doc and not self._pages and not self._loading:
            self.load()

    def load(self):
        if self._loading or not self._doc:
            return
        self._req += 1
        req = self._req
        doc = dict(self._doc)
        key = doc.get("key") or ""
        self._loading = True
        self._loading_key = key
        self.fetch_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.status.setText("⏳ 正在打开文档并抓取正文……")
        self.status.setToolTip("")
        self.text_view.setPlaceholderText("正在抓取正文……")

        def work():
            return collector.preview(
                doc, on_status=lambda s: post_to_main(self._on_status, s))

        def done(result, err):
            if req != self._req:
                return
            self._loading = False
            self._loading_key = ""
            if not self._doc or self._doc.get("key") != key:
                return
            self.fetch_btn.setEnabled(True)
            self.fetch_btn.setText("🔄 重新抓取")
            if err:
                self.status.setText(f"❌ {err}"[:200])
                if not self._pages:
                    self.text_view.setPlaceholderText("抓取失败，可点「🔄 重新抓取」重试")
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
            else:
                save_note = " · ⚠ 自动存档失败" if result.get("save_error") else ""
            count = f"共 {len(pages)} 篇 · " if len(pages) > 1 else ""
            summary = (f"✅ {count}{len(text)} 字 · "
                       f"{result.get('title') or doc['name']} · "
                       f"{time.strftime('%H:%M:%S')} 抓取{save_note}")
            tip = (f"自动存档：{save['file']}" if save.get("file") else
                   (f"自动存档失败：{result['save_error']}"
                    if result.get("save_error") else ""))
            self._apply_pages(pages, text, summary, tip)

        run_bg(work, done)

    def _apply_pages(self, pages: list[dict], text: str, summary: str, tooltip: str = ""):
        self._pages = pages
        self._full_text = text
        self._summary = summary
        if len(pages) > 1:
            self.page_combo.blockSignals(True)
            self.page_combo.clear()
            self.page_combo.addItem(f"📚 全部（{len(pages)} 篇）", -1)
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
        self.text_view.setPlainText(text)
        self.text_view.verticalScrollBar().setValue(0)
        self.status.setText(summary)
        self.status.setToolTip(tooltip or "")
        self.export_btn.setEnabled(bool(text.strip()))
        self.fetch_btn.setText("🔄 重新抓取")

    def set_enabled(self, enabled: bool):
        self.fetch_btn.setEnabled(bool(enabled and self._doc and not self._loading))
        self.export_btn.setEnabled(bool(enabled and self._full_text.strip()))

    def _on_page_changed(self, _index):
        idx = self.page_combo.currentData()
        if idx is None or idx < 0 or idx >= len(self._pages):
            self.text_view.setPlainText(self._full_text)
            self.status.setText(self._summary)
        else:
            item = self._pages[idx]
            body = item.get("error") or item.get("text") or "（空白页）"
            self.text_view.setPlainText(body)
            if item.get("error"):
                note = "抓取失败"
            elif not (item.get("text") or "").strip():
                note = "空白页"
            else:
                note = f"{len(body)} 字"
            self.status.setText(f"第 {idx + 1}/{len(self._pages)} 篇 · {note}")
        self.text_view.verticalScrollBar().setValue(0)

    def _on_status(self, text: str):
        if self._loading and self._loading_key == (self._doc or {}).get("key", ""):
            self.status.setText(f"⏳ {text}")

    # ==================== 导出 ====================

    def _export(self):
        """导出当前预览；多篇 AI 文档提供「合并 / 每篇一个文件」两种方式。"""
        if not (self._full_text or "").strip() or not self._doc:
            return
        idx = self.page_combo.currentData()
        if len(self._pages) > 1:
            menu = QMenu(self)
            menu.addAction("📄 导出全部（合并为一个文件）…", self._export_merged)
            menu.addAction("🗂 导出全部（每篇一个 .md 文件）…", self._export_each_page)
            if idx is not None and 0 <= idx < len(self._pages):
                menu.addAction("📃 仅导出当前篇…", self._export_current)
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
        self.status.setText(f"✅ 已导出 {len(self._pages)} 篇到 {out_dir.name}")
        self.status.setToolTip(str(out_dir))

    def _save_text(self, name: str, heading: str, text: str):
        if not (text or "").strip():
            self.status.setText("❌ 当前没有可导出的正文")
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
        self.status.setText(f"✅ 已导出：{Path(path).name}")
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
        self._busy = False
        self._ready_done = False
        self._task_checked_at = 0.0
        self._migrate_note = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ---------- 标题行 ----------
        title = QLabel("📄 腾讯文档监控")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ 腾讯文档监控 · 功能介绍", (
            "<p><b>自动记录腾讯文档的浏览量、文档成员数（附已查看人数），"
            "每个文档独立存档并生成 Excel 报表。</b></p>"
            "<p>· <b>登录</b>：在 <b>Cookie 管理器</b> 的「腾讯文档」一行点「浏览器」扫码即可。"
            "登录态与其它平台统一存在 Cookie 管理器里，也能在那边清除或重新登录，"
            "之后不必重复登录（直到失效）</p>"
            "<p>· <b>立即采集</b>：只采集全部文档的浏览量 / 成员数历史，每次追加一行，"
            "增量相对上一次采集计算；不包含表格同步，速度更快</p>"
            "<p>· <b>管理</b>：顶部「管理」菜单可快速添加 / 编辑 / 删除当前文档的名称与链接；"
            "「🗂 文档管理」Tab 提供完整列表、诊断包和重建报表</p>"
            "<p>· <b>表格同步</b>：与历史采集相互独立，在内容 Tab 里单独点「🔄 同步表格」"
            "把智能表格数据抓到本地库，按记录比对新增/修改/删除，每次同步自动生成 Excel"
            "（每张工作表一个 sheet，存在 表格数据/，可点「📂 打开数据目录」查看）。"
            "普通表格、图表页、文章页不是同一套数据协议，会自动跳过并提示</p>"
            "<p>· <b>内容 Tab</b>：表格类型显示「📋 表格同步」，文档 / AI 文档 / "
            "幻灯片等自动切换为「📖 正文预览」。AI 文档会展开并抓取全部子页面，"
            "可下拉切换单篇或合并查看，支持导出 Markdown / TXT；"
            "每次抓取自动存档：正文逐页写 pages.db（新增/修改/删除留痕），"
            "并更新 正文存档/文档名.md</p>"
            "<p>· <b>定时采集</b>：底部填时间后「🕓 安装/更新定时任务」，"
            "由 Windows 计划任务每天自动跑历史记录（任务名 FengMingTCDocMonitor）；"
            "表格数据需在内容 Tab 里手动同步</p>"
            "<p>· <b>数据位置</b>：download/腾讯文档数据/"
            "（tcdoc.db 浏览量库 + tables.db 表格库 + pages.db 正文库 + 报表/ + "
            "表格数据/ + 正文存档/ + 日志）</p>"
            "<p>· AI 文档（/aio/）本身不提供浏览量，只记录成员数；"
            "Excel 若正被 WPS/Office 打开会写入失败，采集前请先关闭</p>")))
        layout.addLayout(title_row)

        # ---------- 顶部卡片：文档切换 + 操作 ----------
        card = QFrame()
        card.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #d0d6df; border-radius: 8px; }")
        cv = QVBoxLayout(card)
        cv.setContentsMargins(10, 8, 10, 8)
        cv.setSpacing(6)

        row = QHBoxLayout()
        self.manage_btn = QPushButton("管理")
        self.manage_btn.setToolTip("添加 / 编辑 / 删除文档（名称与链接）")
        manage_menu = QMenu(self.manage_btn)
        manage_menu.addAction("➕ 添加文档…", lambda *_: self._add_doc())
        manage_menu.addAction("✏️ 编辑当前文档…", lambda *_: self._edit_current_doc())
        manage_menu.addAction("🗑 删除当前文档…", lambda *_: self._delete_current_doc())
        manage_menu.addSeparator()
        manage_menu.addAction("📋 打开文档管理", lambda *_: self._show_doc_tab())
        self.manage_btn.setMenu(manage_menu)
        row.addWidget(self.manage_btn)
        self.doc_combo = QComboBox()
        self.doc_combo.setMinimumWidth(230)
        self.doc_combo.setToolTip("切换文档：每个文档的历史记录、报表互相独立")
        self.doc_combo.currentIndexChanged.connect(self._on_doc_changed)
        row.addWidget(self.doc_combo)
        row.addStretch(1)

        self.collect_btn = QPushButton("🔄 立即采集")
        self.collect_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.collect_btn.setToolTip(
            "采集全部文档的浏览量 / 成员数并写入历史记录与报表（不含表格同步）")
        self.collect_btn.clicked.connect(self._collect)
        self.dir_btn = QPushButton("📂 打开数据目录")
        self.dir_btn.setToolTip(
            "打开 download/腾讯文档数据/（报表/、表格数据/、正文存档/、数据库与日志）")
        self.dir_btn.clicked.connect(self._open_data_dir)
        for btn in (self.collect_btn, self.dir_btn):
            row.addWidget(btn)
        cv.addLayout(row)

        self.url_label = QLabel("")
        self.url_label.setStyleSheet("color: #888;")
        self.url_label.setWordWrap(True)
        cv.addWidget(self.url_label)
        self.latest_label = QLabel("")
        self.latest_label.setStyleSheet("color: #2f3440;")
        cv.addWidget(self.latest_label)
        layout.addWidget(card)

        # ---------- 三个 Tab ----------
        self.tabs = QTabWidget()
        self.table_daily = self._make_table(DAILY_COLS, stretch_all=True, fit_cols=(0,))
        self.table_daily.setToolTip("每次采集追加一行；增量相对该文档上一次采集")
        self.tabs.addTab(self._wrap(self.table_daily), "📈 历史记录")

        self.table_snap = self._make_table(SNAP_COLS)
        self.table_snap.setToolTip("每次采集的成功/失败明细，失败原因留档便于回溯")
        self.tabs.addTab(self._wrap(self.table_snap), "🧾 采集明细")

        self.table_panel = self._build_sheet_tab()
        self.preview_panel = _DocPreviewPanel(self)
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.table_panel)
        self.content_stack.addWidget(self.preview_panel)
        self.tabs.addTab(self.content_stack, "📋 表格同步")
        self.content_tab_index = self.tabs.count() - 1
        self.doc_tab = self._build_doc_tab()
        self.tabs.addTab(self.doc_tab, "🗂 文档管理")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        # ---------- 底部设置行 ----------
        bottom = QHBoxLayout()
        bottom.setSpacing(6)
        bottom.addWidget(QLabel("定时采集：每天"))
        self.time_edit = QLineEdit(str(tc_cfg.load_cfg().get("tcdoc_collect_time") or "23:50"))
        self.time_edit.setFixedWidth(66)
        self.time_edit.setAlignment(Qt.AlignCenter)
        self.time_edit.setToolTip("24 小时制，如 23:50")
        bottom.addWidget(self.time_edit)
        self.install_btn = QPushButton("🕓 安装/更新定时任务")
        self.install_btn.setToolTip("注册 Windows 每日计划任务（任务名 FengMingTCDocMonitor）")
        self.install_btn.clicked.connect(self._install_task)
        bottom.addWidget(self.install_btn)
        self.uninstall_btn = QPushButton("🗑 删除定时任务")
        self.uninstall_btn.clicked.connect(self._uninstall_task)
        bottom.addWidget(self.uninstall_btn)
        self.headless_cb = QCheckBox("采集时隐藏浏览器窗口")
        self.headless_cb.setToolTip("勾选：后台静默采集；取消：弹出浏览器窗口便于观察")
        self.headless_cb.setChecked(bool(tc_cfg.load_cfg().get("tcdoc_headless", True)))
        self.headless_cb.toggled.connect(self._toggle_headless)
        bottom.addWidget(self.headless_cb)
        self.task_label = QLabel("")
        self.task_label.setStyleSheet("color: #888;")
        bottom.addWidget(self.task_label, 1)
        layout.addLayout(bottom)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

    # ==================== 界面拼装辅助 ====================

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
        self.doc_table.itemSelectionChanged.connect(self._on_doc_row_selected)
        self.doc_table.cellDoubleClicked.connect(lambda _r, _c: self._edit_doc())
        v.addWidget(self.doc_table, 1)

        btns = QHBoxLayout()
        self.add_btn = QPushButton("➕ 添加文档")
        self.add_btn.setToolTip("添加任意腾讯文档（自定义文档，数量不设上限）")
        self.add_btn.clicked.connect(self._add_doc)
        self.edit_btn = QPushButton("✏️ 编辑")
        self.edit_btn.setToolTip("修改文档名称 / 链接（双击行同效）")
        self.edit_btn.clicked.connect(lambda *_: self._edit_doc())
        self.del_btn = QPushButton("🗑 删除")
        self.del_btn.setToolTip("从清单移除文档（可选同时删除其历史记录）")
        self.del_btn.clicked.connect(lambda *_: self._delete_doc())
        self.probe_btn = QPushButton("🩺 生成诊断包")
        self.probe_btn.setToolTip("页面改版导致采集失败时，打开浏览器抓取入口信息存到 调试/")
        self.probe_btn.clicked.connect(self._probe)
        for btn in (self.add_btn, self.edit_btn, self.del_btn, self.probe_btn):
            btns.addWidget(btn)
        btns.addStretch(1)
        self.rebuild_btn = QPushButton("🧾 重建全部报表")
        self.rebuild_btn.setToolTip("按数据库重新生成所有文档的 Excel（历史增量一并重算）")
        self.rebuild_btn.clicked.connect(self._rebuild_reports)
        btns.addWidget(self.rebuild_btn)
        v.addLayout(btns)

        hint = QLabel("添加文档后可立即采集；切换文档在「历史记录」里查看各自曲线，"
                      "每个文档一份独立 Excel。")
        hint.setStyleSheet("color: #888;")
        v.addWidget(hint)
        return box

    def _build_sheet_tab(self) -> QWidget:
        """表格同步：工作表切换 + 数据预览 + 分页 + 变更记录。"""
        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(6, 8, 6, 6)
        v.setSpacing(6)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(QLabel("工作表"))
        self.sheet_combo = QComboBox()
        self.sheet_combo.setMinimumWidth(220)
        self.sheet_combo.setToolTip("切换该文档下的工作表（普通表格 / 图表页不支持同步）")
        self.sheet_combo.currentIndexChanged.connect(lambda _i: self._load_sheet_page(reset=True))
        bar.addWidget(self.sheet_combo)
        self.sheet_info = QLabel("")
        self.sheet_info.setStyleSheet("color: #888;")
        bar.addWidget(self.sheet_info)
        bar.addStretch(1)

        self.sync_btn = QPushButton("🔄 同步表格")
        self.sync_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.sync_btn.setToolTip("只同步当前文档的表格数据（不动浏览量），可免登录读公开文档")
        self.sync_btn.clicked.connect(self._sync_tables)
        bar.addWidget(self.sync_btn)

        self.prev_btn = QPushButton("◀")
        self.prev_btn.setToolTip("上一页")
        self.prev_btn.setFixedWidth(34)
        self.prev_btn.clicked.connect(lambda: self._load_sheet_page(delta=-1))
        bar.addWidget(self.prev_btn)
        self.page_label = QLabel("-/-")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setMinimumWidth(66)
        bar.addWidget(self.page_label)
        self.next_btn = QPushButton("▶")
        self.next_btn.setToolTip("下一页")
        self.next_btn.setFixedWidth(34)
        self.next_btn.clicked.connect(lambda: self._load_sheet_page(delta=1))
        bar.addWidget(self.next_btn)
        v.addLayout(bar)

        self.table_data = QTableWidget(0, 0)
        self.table_data.verticalHeader().setVisible(True)
        self.table_data.verticalHeader().setDefaultSectionSize(26)
        self.table_data.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_data.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table_data.setAlternatingRowColors(True)
        self.table_data.setWordWrap(False)
        self.table_data.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table_data.setToolTip("本地存档的表格数据（每次同步按记录逐行比对增量）")

        change_box = QWidget()
        cv = QVBoxLayout(change_box)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(3)
        cv.addWidget(QLabel("最近变更（新增 / 修改 / 删除）"))
        self.table_changes = QTableWidget(0, len(CHANGE_COLS))
        self.table_changes.setHorizontalHeaderLabels([c[0] for c in CHANGE_COLS])
        self.table_changes.verticalHeader().setVisible(False)
        self.table_changes.verticalHeader().setDefaultSectionSize(24)
        self.table_changes.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_changes.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table_changes.setWordWrap(False)
        for idx, (_name, width) in enumerate(CHANGE_COLS):
            self.table_changes.setColumnWidth(idx, width)
        self.table_changes.horizontalHeader().setStretchLastSection(True)
        cv.addWidget(self.table_changes, 1)

        # 上下可拖动分栏：预览表与变更记录各自独立伸缩，避免预览把变更区压成一条缝
        body = QSplitter(Qt.Vertical)
        body.setChildrenCollapsible(False)
        body.addWidget(self.table_data)
        body.addWidget(change_box)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 1)
        body.setSizes([430, 170])
        v.addWidget(body, 1)

        self.sheet_hint = QLabel("点「🔄 同步表格」把文档里的表格内容抓到本地；"
                                "「立即采集」时也会顺带同步（看底部开关）。")
        self.sheet_hint.setStyleSheet("color: #888;")
        v.addWidget(self.sheet_hint)

        self._sheet_columns: list[str] = []
        self._sheet_offset = 0
        self._sheet_total = 0
        return box

    # ==================== 生命周期 ====================

    def on_show(self):
        self._ensure_ready()
        self.reload()
        if time.time() - self._task_checked_at > 8:
            self._refresh_task()

    def _ensure_ready(self):
        """首次进入时建库 + 导入旧版历史数据（只跑一次）。"""
        if self._ready_done:
            return
        try:
            self._migrate_note = job.ensure_ready()
        except Exception as exc:  # noqa: BLE001
            self.app.toast(f"初始化失败：{exc}"[:120], "error")
            self._migrate_note = ""
        self._ready_done = True
        if self._migrate_note:
            self.app.toast(self._migrate_note, "info")

    # ==================== 刷新 ====================

    def reload(self):
        self._reload_docs()
        self._refresh_tables()
        self._refresh_summary()
        self._refresh_sheet_tabs()
        self._refresh_content_tab()

    def _reload_docs(self):
        current = self.current_doc()
        self.docs = tc_cfg.load_docs()
        self.doc_combo.blockSignals(True)
        self.doc_combo.clear()
        for doc in self.docs:
            self.doc_combo.addItem(doc["name"], doc["key"])
        if current:
            idx = next((i for i, d in enumerate(self.docs) if d["key"] == current["key"]), 0)
        else:
            idx = 0
        if self.docs:
            self.doc_combo.setCurrentIndex(idx)
        self.doc_combo.blockSignals(False)
        self._fill_doc_table()

    def _fill_doc_table(self):
        db = tc_cfg.db_path()
        self.doc_table.setRowCount(0)
        for doc in self.docs:
            count, last = storage.doc_stats(db, doc["key"])
            r = self.doc_table.rowCount()
            self.doc_table.insertRow(r)
            self.doc_table.setItem(r, 0, QTableWidgetItem(doc["name"]))
            self.doc_table.setItem(r, 1, _cell(tc_cfg.kind_label(doc.get("kind"))))
            self.doc_table.setItem(r, 2, _cell(count))
            self.doc_table.setItem(r, 3, _cell(last or "-"))
            self.doc_table.setItem(r, 4, _cell(doc["url"], center=False, tip=doc["url"]))

    def _refresh_tables(self):
        doc = self.current_doc()
        self.table_daily.setRowCount(0)
        self.table_snap.setRowCount(0)
        if not doc:
            return
        db = tc_cfg.db_path()
        for row in reversed(storage.all_daily(db, doc["key"])):
            r = self.table_daily.rowCount()
            self.table_daily.insertRow(r)
            self.table_daily.setItem(r, 0, _cell(row["ts"]))
            self.table_daily.setItem(r, 1, _cell("-" if row["views"] is None else row["views"]))
            self.table_daily.setItem(r, 2, _cell(_fmt_delta(row["views_delta"])))
            self.table_daily.setItem(r, 3, _cell(row["members"]))
            self.table_daily.setItem(r, 4, _cell(_fmt_delta(row["members_delta"])))
            self.table_daily.setItem(
                r, 5, _cell("-" if row["readers"] is None else row["readers"]))

        for row in reversed(storage.all_snapshots(db, doc["key"], limit=2000)):
            r = self.table_snap.rowCount()
            self.table_snap.insertRow(r)
            self.table_snap.setItem(r, 0, _cell(row["ts"]))
            self.table_snap.setItem(r, 1, _cell("✅ 成功" if row["ok"] else "❌ 失败"))
            self.table_snap.setItem(r, 2, _cell("-" if row["views"] is None else row["views"]))
            self.table_snap.setItem(r, 3, _cell("-" if row["members"] is None else row["members"]))
            self.table_snap.setItem(r, 4, _cell(row["message"] or "", center=False,
                                                tip=row["message"] or ""))

    def _refresh_summary(self):
        doc = self.current_doc()
        if not doc:
            self.url_label.setText("尚未配置文档，可在「🗂 文档管理」里添加")
            self.latest_label.setText("")
            return
        self.url_label.setText(f"链接：{doc['url']}")
        row = storage.latest(tc_cfg.db_path(), doc["key"])
        if not row:
            self.latest_label.setText("该文档暂无记录，点「🔄 立即采集」开始")
            return
        views = "-" if row["views"] is None else row["views"]
        self.latest_label.setText(
            f"最新记录：{row['date']}　浏览量 {views}　成员 {row['members']}"
            f"　已查看 {row['readers'] if row['readers'] is not None else '-'}")

    def _refresh_task(self):
        self._task_checked_at = time.time()

        def work():
            return task.next_run_text()

        def done(text, err):
            self.task_label.setText(text if not err else f"定时任务状态未知：{err}")

        run_bg(work, done)

    # ==================== 状态 ====================

    def current_doc(self) -> dict | None:
        if not self.docs:
            return None
        idx = self.doc_combo.currentIndex()
        if 0 <= idx < len(self.docs):
            return self.docs[idx]
        return self.docs[0]

    def _on_doc_changed(self, _index):
        self._refresh_tables()
        self._refresh_summary()
        self._refresh_sheet_tabs()
        self._refresh_content_tab()

    def _refresh_content_tab(self):
        """内容 Tab 自动切换：表格类型=表格同步，文档类型=正文预览。"""
        doc = self.current_doc()
        syncable = (not doc) or (doc.get("kind") or "").lower() in tables.SYNC_KINDS
        self.content_stack.setCurrentWidget(
            self.table_panel if syncable else self.preview_panel)
        self.tabs.setTabText(self.content_tab_index,
                             "📋 表格同步" if syncable else "📖 正文预览")
        if not syncable:
            self.preview_panel.set_doc(doc)
            if self.tabs.currentIndex() == self.content_tab_index:
                self.preview_panel.ensure_loaded()

    def _on_tab_changed(self, index):
        if index != self.content_tab_index:
            return
        doc = self.current_doc()
        if doc and (doc.get("kind") or "").lower() not in tables.SYNC_KINDS:
            self.preview_panel.set_doc(doc)
            self.preview_panel.ensure_loaded()

    # ==================== 表格同步 ====================

    def _refresh_sheet_tabs(self):
        """按当前文档刷新工作表下拉（含被跳过的非表格页提示）。"""
        doc = self.current_doc()
        sheets = tables.sheets_of(doc["key"]) if doc else []
        keep = self._current_tab_id()
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
            self.sheet_hint.setText("该文档还没有本地表格数据，点「🔄 同步表格」开始")
        elif skipped:
            names = "、".join(s["tab_name"] for s in skipped[:6])
            more = " 等" if len(skipped) > 6 else ""
            self.sheet_hint.setText(f"已跳过 {len(skipped)} 个非表格页：{names}{more}"
                                    "（普通表格 / 图表页 / 文章页不支持同步）")
        else:
            self.sheet_hint.setText("点「🔄 同步表格」把文档里的表格内容抓到本地；"
                                    "「立即采集」时也会顺带同步（看底部开关）。")
        self._sheet_offset = 0
        self._load_sheet_page(reset=True)
        self._refresh_changes()

    def _current_tab_id(self) -> str:
        data = self.sheet_combo.currentData()
        return str(data) if data else ""

    def _current_sheet(self) -> dict | None:
        doc, tab_id = self.current_doc(), self._current_tab_id()
        if not doc or not tab_id:
            return None
        return next((s for s in tables.sheets_of(doc["key"]) if s["tab_id"] == tab_id), None)

    def _load_sheet_page(self, reset: bool = False, delta: int = 0):
        doc, tab_id = self.current_doc(), self._current_tab_id()
        if not doc or not tab_id:
            self.table_data.setRowCount(0)
            self.table_data.setColumnCount(0)
            self.page_label.setText("-/-")
            self.sheet_info.setText("")
            return
        page_size = max(20, int(tc_cfg.load_cfg().get("tcdoc_table_page") or 200))
        sheet = self._current_sheet() or {}
        if reset:
            self._sheet_offset = 0
        if delta:
            self._sheet_offset = max(0, self._sheet_offset + delta * page_size)
        rows, total = tables.rows_page(doc["key"], tab_id, self._sheet_offset, page_size)
        if not rows and total and self._sheet_offset:
            self._sheet_offset = max(0, (total - 1) // page_size * page_size)
            rows, total = tables.rows_page(doc["key"], tab_id, self._sheet_offset, page_size)
        self._sheet_total = total

        names = [c["name"] for c in sheet.get("columns", [])]
        self._sheet_columns = names
        self.table_data.setColumnCount(len(names))
        self.table_data.setHorizontalHeaderLabels(names)
        self.table_data.setRowCount(0)
        for row in rows:
            r = self.table_data.rowCount()
            self.table_data.insertRow(r)
            self.table_data.setVerticalHeaderItem(r, QTableWidgetItem(str(row["row_no"])))
            for c, name in enumerate(names):
                value = str(row["values"].get(name, "") or "")
                item = QTableWidgetItem(value.replace("\n", " ⏎ ")[:200])
                if value:
                    item.setToolTip(value[:1500])
                self.table_data.setItem(r, c, item)
        pages = max(1, (total + page_size - 1) // page_size)
        current = self._sheet_offset // page_size + 1 if total else 0
        self.page_label.setText(f"{current}/{pages}")
        self.prev_btn.setEnabled(self._sheet_offset > 0)
        self.next_btn.setEnabled(self._sheet_offset + page_size < total)
        stamp = sheet.get("fetched_at") or "-"
        self.sheet_info.setText(f"{len(names)} 列 · {total} 行 · 同步于 {stamp}")

    def _refresh_changes(self):
        doc = self.current_doc()
        self.table_changes.setRowCount(0)
        if not doc:
            return
        for row in tables.recent_changes(doc["key"], limit=60):
            r = self.table_changes.rowCount()
            self.table_changes.insertRow(r)
            self.table_changes.setItem(r, 0, _cell(row["ts"]))
            self.table_changes.setItem(r, 1, QTableWidgetItem(row["tab_name"] or ""))
            self.table_changes.setItem(r, 2, _cell(row["kind"]))
            self.table_changes.setItem(r, 3, _cell(row["record_id"] or ""))
            self.table_changes.setItem(r, 4, QTableWidgetItem(row["summary"] or ""))

    def _sync_tables(self):
        if self._busy:
            return
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先添加腾讯文档", "warning")
            return
        if (doc.get("kind") or "").lower() not in tables.SYNC_KINDS:
            self.app.toast("只有「表格 / 智能表格」类型能同步数据；文档类型请看「📖 正文预览」",
                           "warning")
            return
        self._set_busy(True, "⏳ 正在同步表格数据……")

        def work():
            return job.sync_tables(
                [doc], on_status=lambda s: post_to_main(self.status_label.setText, f"⏳ {s}"))

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
            added = sum(t["added"] for t in item.get("tables_delta") or [])
            updated = sum(t["updated"] for t in item.get("tables_delta") or [])
            removed = sum(t["removed"] for t in item.get("tables_delta") or [])
            self.status_label.setText(
                f"✅ 表格同步完成：新增 {added}　修改 {updated}　删除 {removed}"
                "　Excel 已更新到 表格数据/")
            self.app.toast(f"表格同步完成：+{added} ~{updated} -{removed}", "success")

        run_bg(work, done)

    def _set_busy(self, busy: bool, text: str = ""):
        self._busy = busy
        for btn in (self.collect_btn, self.manage_btn, self.install_btn,
                    self.uninstall_btn, self.add_btn, self.edit_btn, self.del_btn,
                    self.probe_btn, self.rebuild_btn, self.sync_btn):
            btn.setEnabled(not busy)
        self.preview_panel.set_enabled(not busy)
        if busy:
            self.prev_btn.setEnabled(False)
            self.next_btn.setEnabled(False)
        else:
            self._load_sheet_page()
        self.status_label.setText(text)

    # ==================== 采集 ====================

    def _collect(self):
        if self._busy:
            return
        if not self.docs:
            self.app.toast("请先添加至少一个腾讯文档", "warning")
            return
        self._set_busy(True, "⏳ 正在采集历史记录……")

        def work():
            return job.collect_all(
                on_status=lambda s: post_to_main(self.status_label.setText, f"⏳ {s}"),
                debug=True)

        def done(results, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"采集失败：{err}"[:120], "error")
                return
            ok = [r for r in results if r.get("ok")]
            failed = [r for r in results if not r.get("ok")]
            report_err = [r for r in ok if r.get("report_error")]
            self.reload()
            self.status_label.setText(
                f"✅ 历史采集完成：成功 {len(ok)}/{len(results)}，已写入数据库与报表")
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
        self._set_busy(True, "⏳ 正在重建全部报表……")

        def done(count, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"重建失败：{err}"[:120], "error")
                return
            self.reload()
            self.app.toast(f"已重建 {count} 个文档的报表", "success")

        run_bg(job.rebuild_reports, done)

    def _open_data_dir(self):
        path = tc_cfg.data_dir()
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.app.toast(f"打开失败：{path}", "warning")

    # ==================== 文档管理 ====================

    def _show_doc_tab(self):
        self.tabs.setCurrentWidget(self.doc_tab)

    def _edit_current_doc(self):
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先选择文档", "warning")
            return
        self._edit_doc(doc)

    def _delete_current_doc(self):
        doc = self.current_doc()
        if not doc:
            self.app.toast("请先选择文档", "warning")
            return
        self._delete_doc(doc)

    def _selected_doc(self) -> dict | None:
        row = self.doc_table.currentRow()
        if 0 <= row < len(self.docs):
            return self.docs[row]
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
        self.reload()
        idx = next((i for i, d in enumerate(self.docs) if d["key"] == doc["key"]), None)
        if idx is not None:
            self.doc_combo.setCurrentIndex(idx)
        self.app.toast(f"已添加文档「{doc['name']}」，可直接采集", "success")

    def _edit_doc(self, doc=None):
        doc = doc or self._selected_doc() or self.current_doc()
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
        self.reload()
        self.app.toast(f"已保存「{new['name']}」", "success")

    def _delete_doc(self, doc=None):
        doc = doc or self._selected_doc() or self.current_doc()
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
            page_rows = archive.clear_doc(doc["key"])
            try:
                archive.snapshot_path(doc).unlink(missing_ok=True)
            except OSError:
                pass
            self.app.toast(f"已删除文档与其 {removed} 条历史记录"
                           f"（表格数据 {table_rows} 行，正文存档 {page_rows} 页）", "success")
        else:
            self.app.toast(f"已删除文档「{doc['name']}」", "success")
        self.reload()

    def _probe(self):
        doc = self._selected_doc() or self.current_doc()
        if not doc or self._busy:
            self.app.toast("请先选择要诊断的文档", "warning")
            return
        self._set_busy(True, "⏳ 正在生成诊断包（会打开浏览器窗口）……")

        def work():
            return collector.probe(
                doc, on_status=lambda s: post_to_main(self.status_label.setText, f"⏳ {s}"))

        def done(path, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"诊断失败：{err}"[:120], "error")
                return
            self.status_label.setText(f"✅ 诊断包已生成：{path}")
            self.app.toast(f"诊断包已保存到 调试/{path.name}", "success")

        run_bg(work, done)

    # ==================== 定时任务 ====================

    def _toggle_headless(self, checked):
        tc_cfg.save_cfg(tcdoc_headless=bool(checked))

    def _install_task(self):
        text = self.time_edit.text().strip()
        parts = text.split(":")
        valid = (len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit()
                 and 0 <= int(parts[0]) < 24 and 0 <= int(parts[1]) < 60)
        if not valid:
            QMessageBox.warning(self, "时间格式", "请填写 24 小时制时间，例如 23:50")
            return
        hhmm = f"{int(parts[0]):02d}:{int(parts[1]):02d}"
        if self._busy:
            return
        self._set_busy(True, f"⏳ 正在安装定时任务（每天 {hhmm}）……")

        def work():
            ok, msg = task.install(hhmm)
            if ok:
                tc_cfg.save_cfg(tcdoc_collect_time=hhmm)
            return ok, msg

        def done(result, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"安装失败：{err}"[:120], "error")
                return
            ok, msg = result
            if ok:
                self.time_edit.setText(hhmm)
                self.app.toast(f"定时任务已安装：{msg}", "success")
                self._refresh_task()
            else:
                QMessageBox.warning(self, "安装失败", msg)

        run_bg(work, done)

    def _uninstall_task(self):
        if self._busy:
            return
        self._set_busy(True, "⏳ 正在删除定时任务……")

        def done(result, err):
            self._set_busy(False)
            if err:
                self.app.toast(f"删除失败：{err}"[:120], "error")
                return
            ok, msg = result
            if ok:
                self.app.toast(msg, "success")
                self._refresh_task()
            else:
                QMessageBox.warning(self, "删除失败", msg)

        run_bg(task.uninstall, done)
