"""公众号文章解析页（PySide6）。

- 链接输入区：支持单个 / 批量（每行一个 mp.weixin.qq.com 链接，自动提取去重）
- 导出格式：HTML（自包含）/ PDF / Word / 图片文本(Markdown)，可多选
- 导出位置：默认位置（统一导出目录/公众号文章）与固定位置双选项（持久化），
  两者都按 公众号名/文章名 建两级文件夹，HTML/PDF/Word/MD 与图片统一存放在文章文件夹内
- 后台线程逐篇解析下载图片，进度按「第 N/M 篇 + 当前图片 K/L」显示；
  PDF 由 Qt 主线程渲染（QTextDocument → QPdfWriter）
- 取消按钮：打断当前文章（网络请求间检测），已完成的导出保留
"""
import os

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
    QPlainTextEdit, QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
    QTabWidget, QWidget,
)

from toolbox.config_manager import (
    get_export_subdir, load_convert_config, save_convert_config,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.navigation import line_icon
from gui_qt.pages.base import BasePage
from gui_qt.theme import current_palette

FORMATS = [
    ("html", "HTML"),
    ("pdf", "PDF"),
    ("docx", "Word"),
    ("md", "图片文本(MD)"),
]
FORMATS_DICT = {k: label for k, label in FORMATS}


class WechatArticlePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.running = False
        self._cancel = False
        self._icons: list = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("wechatSections")
        self.setStyleSheet("""
            QTabWidget#wechatSections::pane { border: 0; }
            QTabWidget#wechatSections > QTabBar::tab {
                background: transparent; border: 0;
                border-bottom: 2px solid transparent; padding: 6px 12px;
            }
            QTabWidget#wechatSections > QTabBar::tab:selected {
                color: palette(link); border-bottom-color: palette(highlight);
            }
        """)
        links = QWidget(self.tabs)
        link_layout = QVBoxLayout(links)
        link_layout.setContentsMargins(0, 12, 0, 0)
        link_layout.setSpacing(12)
        link_layout.addWidget(self._build_input_card())
        link_layout.addWidget(self._build_log_card(), 1)
        self.tabs.addTab(links, "链接导出")
        from gui_qt.pages.wechat_archive_panel import WechatArchivePanel
        self.library_panel = WechatArchivePanel(self)
        self.tabs.addTab(self.library_panel.accounts_page, "公众号资料")
        self.tabs.addTab(self.library_panel.articles_page, "文章归档")
        self.tabs.currentChanged.connect(self._tab_changed)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self._build_action_bar())

        self._refresh_loc_label()

    # ===================== 布局片段 =====================
    @staticmethod
    def _caption(text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("role", "caption")
        return label

    @staticmethod
    def _icon_color(role: str = "button") -> str:
        pal = current_palette()
        if role == "primary":
            return pal.color(QPalette.ColorRole.HighlightedText).name()
        if role == "danger":
            return pal.color(QPalette.ColorRole.ButtonText).name()
        return pal.color(QPalette.ColorRole.ButtonText).name()

    def _icon_button(self, text: str, icon_name: str, variant: str = "secondary") -> QPushButton:
        btn = QPushButton(text)
        btn.setProperty("variant", variant)
        role = "primary" if variant == "primary" else "button"
        btn.setIcon(line_icon(icon_name, self._icon_color(role), 16))
        btn.setIconSize(QSize(16, 16))
        self._icons.append((btn, icon_name, role))
        return btn

    def on_theme_changed(self):
        for btn, name, role in self._icons:
            btn.setIcon(line_icon(name, self._icon_color(role), 16))

    def has_active_tasks(self):
        return self.running

    def on_show(self):
        self._tab_changed(self.tabs.currentIndex())

    def _tab_changed(self, index):
        if index > 0:
            self.library_panel.refresh(index)

    def _build_input_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(10)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("文章链接", card)
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self._caption("每行一个链接，可批量；自动去重"))
        v.addLayout(head)

        self.input_edit = QPlainTextEdit(card)
        self.input_edit.setPlaceholderText(
            "输入公众号文章链接，每行一个（支持单个和批量）：\n"
            "https://mp.weixin.qq.com/s/xxxxxxxx\n"
            "https://mp.weixin.qq.com/s?__biz=xxx&mid=xxx&idx=1&sn=xxx")
        self.input_edit.setMaximumHeight(110)
        v.addWidget(self.input_edit)

        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(12)
        fmt_row.addWidget(self._caption("导出格式"))
        self.format_checks = {}
        for key, label in FORMATS:
            cb = QCheckBox(label, card)
            cb.setChecked(True)
            self.format_checks[key] = cb
            fmt_row.addWidget(cb)
        fmt_row.addStretch(1)
        v.addLayout(fmt_row)

        loc_row = QHBoxLayout()
        loc_row.setSpacing(10)
        loc_row.addWidget(self._caption("导出到"))
        self.loc_combo = QComboBox(card)
        self.loc_combo.addItem("默认位置")
        self.loc_combo.addItem("固定位置")
        self.loc_combo.setFixedWidth(112)
        self.loc_combo.currentIndexChanged.connect(self._on_loc_changed)
        loc_row.addWidget(self.loc_combo)
        pick_btn = self._icon_button("", "folder")
        pick_btn.setFixedSize(30, 26)
        pick_btn.setToolTip("选择固定位置（文件夹）")
        pick_btn.clicked.connect(self._pick_custom_dir)
        loc_row.addWidget(pick_btn)
        self.loc_label = self._caption("")
        self.loc_label.setWordWrap(True)
        loc_row.addWidget(self.loc_label, 1)
        v.addLayout(loc_row)
        return card

    def _build_log_card(self) -> QFrame:
        card = QFrame(self)
        card.setProperty("variant", "card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 12)
        v.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        title = QLabel("日志", card)
        title.setProperty("role", "sectionTitle")
        head.addWidget(title)
        head.addStretch(1)
        v.addLayout(head)

        self.log_text = QTextEdit(card)
        self.log_text.setReadOnly(True)
        v.addWidget(self.log_text, 1)
        return card

    def _build_action_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setProperty("variant", "toolbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(8)

        self.progress = QProgressBar(bar)
        self.progress.setRange(0, 100)
        self.progress.setFixedWidth(220)
        h.addWidget(self.progress)
        self.status_label = self._caption("未开始")
        h.addWidget(self.status_label, 1)

        open_btn = self._icon_button("打开导出文件夹", "folder")
        open_btn.setToolTip("在资源管理器中打开当前导出位置")
        open_btn.clicked.connect(self._open_export_dir)
        h.addWidget(open_btn)
        self.cancel_btn = self._icon_button("停止", "stop", "danger")
        self.cancel_btn.setToolTip("打断当前文章（请求间检测），已完成的导出保留")
        self.cancel_btn.clicked.connect(self._cancel_run)
        self.cancel_btn.setEnabled(False)
        h.addWidget(self.cancel_btn)
        self.start_btn = self._icon_button("解析并导出", "play", "primary")
        self.start_btn.setToolTip("解析全部链接并按勾选格式导出")
        self.start_btn.clicked.connect(self._start)
        h.addWidget(self.start_btn)
        return bar

    # ===================== 导出位置（默认/固定 双选项） =====================

    def _cfg(self):
        return load_convert_config()

    def _on_loc_changed(self, index):
        cfg = self._cfg()
        cfg["wechat_use_custom_dir"] = index == 1
        save_convert_config(cfg)
        self._refresh_loc_label()

    def _pick_custom_dir(self):
        from PySide6.QtWidgets import QFileDialog
        cur = self._cfg().get("wechat_custom_dir", "") or os.path.expanduser("~")
        picked = QFileDialog.getExistingDirectory(self, "选择固定导出位置", cur)
        if not picked:
            return
        cfg = self._cfg()
        cfg["wechat_custom_dir"] = picked
        save_convert_config(cfg)
        self.loc_combo.setCurrentIndex(1)
        self._refresh_loc_label()
        self._log(f"固定导出位置: {picked}")

    def _current_out_dir(self):
        from toolbox import wechat_library
        return wechat_library.current_export_dir()

    def _refresh_loc_label(self):
        self.loc_label.setText("→ " + self._current_out_dir())

    def _open_export_dir(self):
        d = self._current_out_dir()
        if os.path.isdir(d):
            os.startfile(d)
        else:
            self.app.toast("导出文件夹不存在", "warning")

    # ===================== 解析与导出 =====================

    def on_return_press(self):
        if not self.running and self.tabs.currentIndex() == 0:
            self._start()

    def _start(self):
        if self.running:
            return
        from toolbox import wechat_library
        urls = wechat_library.extract_urls(self.input_edit.toPlainText())
        if not urls:
            self.app.toast("未识别到公众号文章链接（需 mp.weixin.qq.com 链接）", "warning")
            return
        formats = [k for k, _ in FORMATS if self.format_checks[k].isChecked()]
        if not formats:
            self.app.toast("请至少勾选一种导出格式", "warning")
            return

        self.log_text.clear()
        self._log(f"开始解析 {len(urls)} 篇文章，导出格式: {'/'.join(FORMATS_DICT[f] for f in formats)}")
        out_dir = self._current_out_dir()
        self.run_library_job(
            lambda **callbacks: wechat_library.export_links(
                '\n'.join(urls), formats, out_dir, **callbacks))

    def _cancel_run(self):
        self._cancel = True
        self.status_label.setText("正在停止（当前请求结束后保留已完成部分）")

    def selected_formats(self):
        return [key for key, _ in FORMATS if self.format_checks[key].isChecked()]

    def run_library_job(self, operation):
        """仅将 L3 的任务与进度桥接至界面，所有业务编排留在服务层。"""
        if self.running:
            self.app.toast("已有任务进行中，请等待或停止后重试", "warning")
            return
        self.running = True
        self._cancel = False
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.library_panel.set_busy(True)
        self.progress.setRange(0, 0)
        self.status_label.setText("进行中")

        def worker():
            return operation(
                on_progress=lambda done, total, text: post_to_main(
                    self._job_progress, done, total, text),
                on_log=lambda text: post_to_main(self._log, text),
                is_cancelled=lambda: self._cancel)

        run_bg(worker, self._finish_library_job, owner=self)

    def _job_progress(self, done, total, text):
        if total:
            self.progress.setRange(0, 100)
            self.progress.setValue(min(100, max(0, int(done / total * 100))))
        else:
            self.progress.setRange(0, 0)
        self.status_label.setText(str(text))

    def _finish_library_job(self, result, error):
        """主线程处理 Qt PDF；PDF 结果通过服务层登记。"""
        from toolbox import wechat_library
        result = result or {}
        pdf_failed = 0
        pdf_jobs = result.get("pdf_jobs") or []
        if pdf_jobs and not self._cancel:
            from gui_qt.pdf_render import render_pdf
            for index, job in enumerate(pdf_jobs, 1):
                if self._cancel:
                    break
                self.status_label.setText(f"生成 PDF {index}/{len(pdf_jobs)}")
                QApplication.processEvents()
                if self._cancel:
                    break
                try:
                    render_pdf(job["html"], job["path"])
                    wechat_library.finish_pdf(job["id"], True)
                except Exception:
                    pdf_failed += 1
                    wechat_library.finish_pdf(job["id"], False, "PDF 生成失败，请重试")
                    self._log("PDF 生成失败，请检查导出位置后重试")
        cancelled = self._cancel or bool(result.get("cancelled"))
        self.running = False
        self._cancel = False
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        self.library_panel.set_busy(False)
        self.progress.setRange(0, 100)
        if error:
            # 底层异常可能带 URL 或路径；页面只显示服务认可的通用提示。
            tip = "任务失败，请检查输入与网络后重试"
            self._log(tip)
        elif cancelled:
            tip = f"已取消（成功 {result.get('ok', 0)} / 失败 {result.get('failed', 0)}）"
        else:
            self.progress.setValue(100)
            tip = str(result.get("summary") or "已完成，结果已写入文章归档")
            if pdf_failed:
                tip += f" · PDF 失败 {pdf_failed} 篇"
        self.status_label.setText(tip)
        self.library_panel.refresh_all()

    def _set_status(self, text):
        self.status_label.setText(text)

    def _log(self, msg):
        self.log_text.append(str(msg))
