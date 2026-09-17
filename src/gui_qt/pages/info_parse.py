"""单个解析页（PySide6 版）：输入链接解析 + 直接下载（原「视频下载」页已合并进来）。

- 支持平台：B站/抖音/微博（完整解析器）+ 小红书/快手/好看/微视/梨视频/皮皮搞笑（multi_downloader）
- 画质下拉（全局共享）：B站 playurl 选档，抖音下载前按画质重解析
- 线程下拉（全局共享，2/4/8/16/32/64）：单文件大文件 IDM 风格分段下载，
  多文件（图集等）按线程数并行；下载时显示 IDM 风格进度面板
- 解析结果展示 + 下载按钮（B站走 playurl，其余走 media_downloader）+ 下载日志
"""

from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTextEdit, QVBoxLayout,
)

from gui_qt import common, download_ops
from gui_qt.common import _collect, _info_text, post_to_main, refresh_results_count, run_bg
from gui_qt.download_panel import DownloadPanel
from gui_qt.pages.base import BasePage

# 线程数选项（全局共享，与批量导入页一致）
THREAD_OPTIONS = ["2", "4", "8", "16", "32", "64"]


class InfoParsePage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._current_info = None  # 最近一次解析成功的 VideoInfo（下载按钮的目标）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("🔍 单个解析")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("https://www.bilibili.com/video/BV...")
        self.url_entry.returnPressed.connect(self._parse)
        row.addWidget(self.url_entry, 1)
        self.parse_btn = QPushButton("🔍 解析")
        self.parse_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.parse_btn.clicked.connect(self._parse)
        row.addWidget(self.parse_btn)
        layout.addLayout(row)

        # 下载行：下载当前 + 画质 + 线程数（全局共享，与已获取/批量导入同步）
        dl_row = QHBoxLayout()
        self.download_btn = QPushButton("⬇ 下载当前解析结果")
        self.download_btn.setEnabled(False)
        self.download_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.download_btn.setToolTip("下载最近一次解析成功的条目（B站按画质直下，其余走媒体下载）")
        self.download_btn.clicked.connect(self._download_current)
        dl_row.addWidget(self.download_btn)
        dl_row.addStretch(1)
        dl_row.addWidget(QLabel("画质:"))
        self.quality_sel = QComboBox()
        self.quality_sel.addItems(
            ["最高画质", "8K", "杜比视界", "HDR", "4K", "1080P60", "1080P", "720P", "480P"])
        self.quality_sel.setFixedWidth(110)
        self.quality_sel.setCurrentText(common.CURRENT_QUALITY)
        self.quality_sel.currentTextChanged.connect(self._on_quality_change)
        dl_row.addWidget(self.quality_sel)
        dl_row.addWidget(QLabel("线程:"))
        self.thread_sel = QComboBox()
        self.thread_sel.addItems(THREAD_OPTIONS)
        self.thread_sel.setToolTip(
            "下载线程数：单个大文件按线程数分段并行下载（IDM 风格）；\n"
            "图集/多文件按线程数并行下载多个文件")
        self.thread_sel.setCurrentText(str(common.CURRENT_THREADS))
        self.thread_sel.setFixedWidth(64)
        self.thread_sel.currentTextChanged.connect(self._on_threads_change)
        dl_row.addWidget(self.thread_sel)
        layout.addLayout(dl_row)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("解析结果将显示在这里")
        layout.addWidget(self.result_text, 1)

        # IDM 风格下载进度面板（单文件：文件名 + 分段条；多文件：文件名槽位）
        self.dl_panel = DownloadPanel()
        layout.addWidget(self.dl_panel)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(110)
        self.log_text.setPlaceholderText("下载日志")
        layout.addWidget(self.log_text)

    # ==================== 解析 ====================

    def open_url(self, url: str):
        """供其他页跳转：填入 URL 并自动解析（如 B站空间综合搜索的视频结果）。"""
        self.url_entry.setText(url)
        self._parse()

    def _parse(self):
        url = self.url_entry.text().strip()
        if not url:
            self.app.toast("请输入要解析的 URL", "warning")
            return
        self.parse_btn.setEnabled(False)
        self.result_text.setPlainText("⏳ 解析中...\n")

        def work():
            info = download_ops.parse_url_info(url)
            _collect([info])
            return info

        def done(result, err):
            self.parse_btn.setEnabled(True)
            if err:
                self._current_info = None
                self.download_btn.setEnabled(False)
                self.result_text.setPlainText(f"❌ 失败: {err}")
                return
            self._current_info = result
            self.download_btn.setEnabled(True)
            self.result_text.setPlainText("✅ 获取成功\n" + "=" * 40 + "\n" + _info_text(result))
            refresh_results_count(self.app)

        run_bg(work, done)

    # ==================== 下载 ====================

    def _on_quality_change(self, value):
        """清晰度改变时同步到全局（已获取/资产管理/批量导入页共用）。"""
        common.CURRENT_QUALITY = value

    def _on_threads_change(self, value):
        """线程数改变时同步到全局（批量导入页共用）。"""
        try:
            common.CURRENT_THREADS = int(value)
        except ValueError:
            pass

    def _threads(self) -> int:
        try:
            return int(self.thread_sel.currentText())
        except ValueError:
            return common.CURRENT_THREADS

    def _download_current(self):
        info = self._current_info
        if info is None:
            self.app.toast("请先解析出结果再下载", "warning")
            return
        self.download_btn.setEnabled(False)
        self._log(f"⏳ 开始下载 [{info.platform}] {(info.title or info.video_id)[:40]}"
                  f"（{self._threads()} 线程）...")
        self.dl_panel.begin()
        bili_items, non_bili = download_ops.resolve_items([info])
        if bili_items:
            self._download_bilibili(bili_items, non_bili)
        else:
            self._download_media([info])

    def _download_media(self, items):
        quality_label = self.quality_sel.currentText()
        threads = self._threads()

        def work():
            # 抖音：按所选画质重新解析拿新直链（存量直链有时效性）
            download_ops.refresh_douyin_urls(items, quality_label)
            return download_ops.download_media_items(
                items, log=self._log, threads=threads,
                on_task_event=self.dl_panel.handle_event)

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.dl_panel.finish(f"❌ 下载失败: {str(err)[:60]}")
                self._log(f"❌ 下载失败: {err}")
                return
            stats = result or {}
            msg = (f"✅ 下载完成: 图片 {stats.get('images', 0)} | "
                   f"封面 {stats.get('covers', 0)} | "
                   f"Live图 {stats.get('live', 0)} | 视频 {stats.get('videos', 0)}")
            if stats.get("skipped", 0):
                msg += f" | 跳过已下载 {stats['skipped']}"
            self._log(msg)
            self.dl_panel.finish(msg if len(msg) <= 80 else msg[:79] + "…")

        run_bg(work, done)

    def _download_bilibili(self, bili_items, non_bili):
        ck = download_ops.get_bilibili_cookie()
        bv_items, draw_items = download_ops.classify_bili(bili_items)
        quality = download_ops.quality_to_qn(self.quality_sel.currentText())
        quality_label = self.quality_sel.currentText()
        threads = self._threads()

        def work():
            return download_ops.do_bilibili_download(
                bv_items, draw_items, non_bili, quality, ck,
                quality_label=quality_label, log=self._log,
                threads=threads, on_task_event=self.dl_panel.handle_event)

        def done(result, err):
            self.download_btn.setEnabled(True)
            if err:
                self.dl_panel.finish(f"❌ 下载失败: {str(err)[:60]}")
                self._log(f"❌ 下载失败: {err}")
                return
            paths, draw_stats, other_stats, errors = result
            msg = download_ops.summarize_download(paths, draw_stats, other_stats, errors)
            self._log(f"✅ {msg}")
            self.dl_panel.finish(f"✅ {msg}" if len(msg) <= 80 else "✅ " + msg[:79] + "…")
            if errors:
                self._log("失败列表:\n" + "\n".join(errors[:5]))

        run_bg(work, done)

    # ==================== 日志 ====================

    def _log(self, text):
        """线程安全地往日志框追加一行（后台线程经 post_to_main 回主线程）。"""
        def apply():
            try:
                self.log_text.append(text)
                self.log_text.ensureCursorVisible()
            except Exception:
                pass
        post_to_main(apply)
