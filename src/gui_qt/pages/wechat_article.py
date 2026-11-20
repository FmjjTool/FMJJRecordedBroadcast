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
import threading

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel,
    QPlainTextEdit, QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.config_manager import (
    get_export_subdir, load_convert_config, save_convert_config,
)

from gui_qt.common import post_to_main
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
        layout.addWidget(self._build_input_card())
        layout.addWidget(self._build_log_card(), 1)
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
        self.status_label = self._caption("就绪")
        h.addWidget(self.status_label, 1)

        open_btn = self._icon_button("打开导出文件夹", "folder")
        open_btn.setToolTip("在资源管理器中打开当前导出位置")
        open_btn.clicked.connect(self._open_export_dir)
        h.addWidget(open_btn)
        self.cancel_btn = self._icon_button("取消", "stop", "danger")
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
        cfg = self._cfg()
        if cfg.get("wechat_use_custom_dir") and cfg.get("wechat_custom_dir"):
            d = cfg["wechat_custom_dir"]
            os.makedirs(d, exist_ok=True)
            return d
        return str(get_export_subdir("wechat"))

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
        if not self.running:
            self._start()

    def _start(self):
        if self.running:
            return
        from platforms.wechat_article import extract_urls
        urls = extract_urls(self.input_edit.toPlainText())
        if not urls:
            self.app.toast("未识别到公众号文章链接（需 mp.weixin.qq.com 链接）", "warning")
            return
        formats = [k for k, _ in FORMATS if self.format_checks[k].isChecked()]
        if not formats:
            self.app.toast("请至少勾选一种导出格式", "warning")
            return

        self.running = True
        self._cancel = False
        self.start_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress.setValue(0)
        self.log_text.clear()
        self._log(f"开始解析 {len(urls)} 篇文章，导出格式: {'/'.join(FORMATS_DICT[f] for f in formats)}")
        self._log(f"导出位置: {self._current_out_dir()}")
        # 下载浮窗（主线程创建；文章间串行限速，文章内图片并发）
        from gui_qt.download_float import get_float
        card = get_float().add_batch("公众号解析", total=len(urls), threads=1,
                                     cancel_cb=self._cancel_run)
        from gui_qt.common import start_page_thread
        start_page_thread(self, self._worker,
                          (urls, formats, self._current_out_dir(), card))

    def _cancel_run(self):
        self._cancel = True
        self.status_label.setText("正在取消（完成当前请求后停止）...")

    def _worker(self, urls, formats, out_dir, card=None):
        from platforms.wechat_article import fetch_article, download_images
        from exporters import wechat_writer
        import time

        def _evt(*event):
            if card is not None:
                card.handle_event(*event)

        total = len(urls)
        ok, fail = 0, 0
        pdf_jobs = []  # (html, pdf_path, title)
        for i, url in enumerate(urls):
            if self._cancel:
                break
            post_to_main(self._set_status, f"批量解析 {i + 1}/{total}  抓取页面...")
            title = f"{i + 1}. {url[-24:]}"
            try:
                art = fetch_article(url)
                title = (f"{i + 1}. {art.title}")[:46] if art.title else title
                _evt("start", title)
                post_to_main(self._log,
                             f"[{i + 1}/{total}] {art.title}")
                post_to_main(self._log,
                             f"  公众号: {art.account or '-'}  发布: {art.publish_time or '-'}  图片: {len(art.images)} 张")

                def img_prog(n, m, t=title):
                    post_to_main(self._set_status,
                                 f"批量解析 {i + 1}/{total}  {t[:24]}  图片 {n}/{m}")
                    _evt("bytes", t, n, m)

                download_images(art, progress=img_prog,
                                should_cancel=lambda: self._cancel,
                                threads=6)

                rel_dir, stem = wechat_writer.build_article_path(art, out_dir)
                art_dir = os.path.join(out_dir, rel_dir)
                os.makedirs(art_dir, exist_ok=True)
                if "html" in formats:
                    wechat_writer.export_html(art, os.path.join(art_dir, stem + ".html"))
                if "docx" in formats:
                    wechat_writer.export_docx(art, os.path.join(art_dir, stem + ".docx"))
                if "md" in formats:
                    wechat_writer.export_markdown(art, art_dir)
                if "pdf" in formats:
                    pdf_jobs.append((wechat_writer.build_full_html(art),
                                     os.path.join(art_dir, stem + ".pdf"), art.title))
                ok += 1
                _evt("done", title, True)
                post_to_main(self._log,
                             "  导出完成 → "
                             + os.path.relpath(art_dir, out_dir).replace(os.sep, "/") + "/"
                             + ("（PDF 稍后生成）" if "pdf" in formats else ""))
            except Exception as e:
                fail += 1
                _evt("done", title, False)
                post_to_main(self._log, f"[{i + 1}/{total}] 失败: {str(e)[:120]}")
            post_to_main(self.progress.setValue, int((i + 1) / total * 100))
            if i < total - 1 and not self._cancel:
                time.sleep(1.2)  # 批量限速，降低微信风控概率

        if card is not None:
            tip = f"完成：成功 {ok} 篇" if not fail else f"完成：成功 {ok} / 失败 {fail}"
            card.finish(tip)
        post_to_main(self._finish_batch, pdf_jobs, ok, fail, formats)

    def _finish_batch(self, pdf_jobs, ok, fail, formats):
        """主线程收尾：渲染 PDF（Qt 必须主线程）+ 恢复按钮。"""
        if pdf_jobs and not self._cancel:
            from gui_qt.pdf_render import render_pdf
            total = len(pdf_jobs)
            for i, (html, path, title) in enumerate(pdf_jobs):
                self.status_label.setText(f"生成 PDF {i + 1}/{total}  {title[:24]}")
                QApplication.processEvents()
                try:
                    render_pdf(html, path)
                except Exception as e:
                    self._log(f"  PDF 生成失败: {e}")
        self.running = False
        self._cancel = False
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        tip = f"完成：成功 {ok} 篇" if not fail else f"完成：成功 {ok} / 失败 {fail}"
        if self._cancel:
            tip = f"已取消（成功 {ok} / 失败 {fail}）"
        self.status_label.setText(tip)
        self._log(f"\n{tip}  → {self._current_out_dir()}")

    def _set_status(self, text):
        self.status_label.setText(text)

    def _log(self, msg):
        self.log_text.append(str(msg))
