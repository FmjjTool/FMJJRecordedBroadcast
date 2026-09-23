"""公众号文章解析页（PySide6）。

- 链接输入区：支持单个 / 批量（每行一个 mp.weixin.qq.com 链接，自动提取去重）
- 导出格式：HTML（自包含）/ PDF / Word / 图片文本(Markdown)，可多选
- 导出位置：默认位置（统一导出目录/公众号文章）与固定位置双选项（持久化）
- 后台线程逐篇解析下载图片，进度按「第 N/M 篇 + 当前图片 K/L」显示；
  PDF 由 Qt 主线程渲染（QTextDocument → QPdfWriter）
- 取消按钮：打断当前文章（网络请求间检测），已完成的导出保留
"""
import os
import threading

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QPlainTextEdit,
    QProgressBar, QPushButton, QTextEdit, QVBoxLayout,
)

from toolbox.config_manager import (
    get_export_subdir, load_convert_config, save_convert_config,
)

from gui_qt.common import post_to_main
from gui_qt.pages.base import BasePage

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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        # 标题 + 使用说明
        title_row = QHBoxLayout()
        title = QLabel("📰 公众号文章解析")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(self._build_help())
        layout.addLayout(title_row)

        # 链接输入区
        self.input_edit = QPlainTextEdit()
        self.input_edit.setPlaceholderText(
            "输入公众号文章链接，每行一个（支持单个和批量）：\n"
            "https://mp.weixin.qq.com/s/xxxxxxxx\n"
            "https://mp.weixin.qq.com/s?__biz=xxx&mid=xxx&idx=1&sn=xxx")
        self.input_edit.setMaximumHeight(110)
        layout.addWidget(self.input_edit)

        # 格式选择 + 导出位置
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("导出格式:"))
        self.format_checks = {}
        for key, label in FORMATS:
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.format_checks[key] = cb
            opt_row.addWidget(cb)
        opt_row.addStretch(1)
        layout.addLayout(opt_row)

        loc_row = QHBoxLayout()
        loc_row.addWidget(QLabel("导出到:"))
        self.loc_combo = QComboBox()
        self.loc_combo.addItem("默认位置")
        self.loc_combo.addItem("固定位置")
        self.loc_combo.currentIndexChanged.connect(self._on_loc_changed)
        loc_row.addWidget(self.loc_combo)
        pick_btn = QPushButton("📁")
        pick_btn.setFixedSize(30, 26)
        pick_btn.setToolTip("选择固定位置（文件夹）")
        pick_btn.clicked.connect(self._pick_custom_dir)
        loc_row.addWidget(pick_btn)
        self.loc_label = QLabel("")
        self.loc_label.setStyleSheet("color: #888; font-size: 11px;")
        loc_row.addWidget(self.loc_label, 1)
        layout.addLayout(loc_row)

        # 进度条 + 状态
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.status_label = QLabel("就绪")
        self.status_label.setStyleSheet("color: #888;")
        layout.addWidget(self.status_label)

        # 操作按钮
        btn_row = QHBoxLayout()
        self.start_btn = QPushButton("🔍 解析并导出")
        self.start_btn.setStyleSheet("background: #2e7d32; color: white; padding: 5px 14px; border-radius: 4px;")
        self.start_btn.clicked.connect(self._start)
        btn_row.addWidget(self.start_btn)
        self.cancel_btn = QPushButton("⏹ 取消")
        self.cancel_btn.setStyleSheet("background: #c62828; color: white;")
        self.cancel_btn.clicked.connect(self._cancel_run)
        self.cancel_btn.setEnabled(False)
        btn_row.addWidget(self.cancel_btn)
        open_btn = QPushButton("📂 打开导出文件夹")
        open_btn.clicked.connect(self._open_export_dir)
        btn_row.addWidget(open_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        # 日志
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text, 1)

        self._refresh_loc_label()

    def _build_help(self):
        from gui_qt.help_overlay import attach_help_overlay
        html = (
            "<p><b>功能说明</b></p>"
            "<p>· 输入公众号文章链接（每行一个），点「解析并导出」自动抓取正文与图片</p>"
            "<p>· <b>HTML</b>：自包含单文件，图片内嵌，双击即可打开</p>"
            "<p>· <b>PDF</b>：A4 排版，适合打印与存档</p>"
            "<p>· <b>Word</b>：docx 格式，可继续编辑</p>"
            "<p>· <b>图片文本(MD)</b>：Markdown 文件 + 图片文件夹，文字与图片分离存放</p>"
            "<p>· 导出位置可选「默认位置」（统一导出目录/公众号文章）或「固定位置」</p>"
            "<p style='color:#888'>仅支持 mp.weixin.qq.com 图文消息；付费/加密文章、"
            "已被删除文章无法解析；批量解析会自动限速避免微信风控</p>"
        )
        return attach_help_overlay(self, "❓ 使用说明", html, btn_text="❓ 使用说明")

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
        self._log(f"📁 固定导出位置: {picked}")

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
        self._log(f"📰 开始解析 {len(urls)} 篇文章，导出格式: {'/'.join(FORMATS_DICT[f] for f in formats)}")
        self._log(f"📂 导出位置: {self._current_out_dir()}")
        threading.Thread(
            target=self._worker, args=(urls, formats, self._current_out_dir()),
            daemon=True).start()

    def _cancel_run(self):
        self._cancel = True
        self.status_label.setText("⏳ 正在取消（完成当前请求后停止）...")

    def _worker(self, urls, formats, out_dir):
        from platforms.wechat_article import fetch_article, download_images
        from exporters import wechat_writer
        import time

        total = len(urls)
        ok, fail = 0, 0
        pdf_jobs = []  # (html, pdf_path, title)
        for i, url in enumerate(urls):
            if self._cancel:
                break
            post_to_main(self._set_status, f"批量解析 {i + 1}/{total}  ⏳ 抓取页面...")
            try:
                art = fetch_article(url)
                post_to_main(self._log,
                             f"[{i + 1}/{total}] 📄 {art.title}")
                post_to_main(self._log,
                             f"  公众号: {art.account or '-'}  发布: {art.publish_time or '-'}  图片: {len(art.images)} 张")

                def img_prog(n, m, title):
                    post_to_main(self._set_status,
                                 f"批量解析 {i + 1}/{total}  {title[:24]}  ⬇ 图片 {n}/{m}")
                download_images(art, progress=img_prog,
                                should_cancel=lambda: self._cancel)

                stem = wechat_writer.build_stem(art)
                if "html" in formats:
                    wechat_writer.export_html(art, os.path.join(out_dir, stem + ".html"))
                if "docx" in formats:
                    wechat_writer.export_docx(art, os.path.join(out_dir, stem + ".docx"))
                if "md" in formats:
                    wechat_writer.export_markdown(art, out_dir)
                if "pdf" in formats:
                    pdf_jobs.append((wechat_writer.build_full_html(art),
                                     os.path.join(out_dir, stem + ".pdf"), art.title))
                ok += 1
                post_to_main(self._log, "  ✅ 导出完成" + ("（PDF 稍后生成）" if "pdf" in formats else ""))
            except Exception as e:
                fail += 1
                post_to_main(self._log, f"[{i + 1}/{total}] ❌ 失败: {str(e)[:120]}")
            post_to_main(self.progress.setValue, int((i + 1) / total * 100))
            if i < total - 1 and not self._cancel:
                time.sleep(1.2)  # 批量限速，降低微信风控概率

        post_to_main(self._finish_batch, pdf_jobs, ok, fail, formats)

    def _finish_batch(self, pdf_jobs, ok, fail, formats):
        """主线程收尾：渲染 PDF（Qt 必须主线程）+ 恢复按钮。"""
        if pdf_jobs and not self._cancel:
            from exporters.wechat_writer import render_pdf
            total = len(pdf_jobs)
            for i, (html, path, title) in enumerate(pdf_jobs):
                self.status_label.setText(f"生成 PDF {i + 1}/{total}  {title[:24]}")
                QApplication.processEvents()
                try:
                    render_pdf(html, path)
                except Exception as e:
                    self._log(f"  ❌ PDF 生成失败: {e}")
        self.running = False
        self._cancel = False
        self.start_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        tip = f"✅ 完成：成功 {ok} 篇" if not fail else f"⚠️ 完成：成功 {ok} / 失败 {fail}"
        if self._cancel:
            tip = f"⏹ 已取消（成功 {ok} / 失败 {fail}）"
        self.status_label.setText(tip)
        self._log(f"\n{tip}  → {self._current_out_dir()}")

    def _set_status(self, text):
        self.status_label.setText(text)

    def _log(self, msg):
        self.log_text.append(str(msg))
