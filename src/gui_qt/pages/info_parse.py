"""单个解析页（PySide6 版）：输入链接解析 + 直接下载（原「视频下载」页已合并进来）。

- 两种模式（输入行前下拉切换）：
  · 链接模式：输入视频/帖子链接解析（B站/抖音/微博 + 小红书/快手等 multi_downloader）
  · 搜索模式：输入关键词全局搜索（B站/抖音/微博，可全部平台并发），
    结果弹窗双击/选中条目即填回链接模式自动解析
- 画质下拉（全局共享）：B站 playurl 选档，抖音下载前按画质重解析
- 线程下拉（全局共享，2/4/8/16/32/64）：单文件大文件 IDM 风格分段下载，
  多文件（图集等）按线程数并行；下载时显示 IDM 风格进度面板
- 解析结果展示 + 下载按钮（B站走 playurl，其余走 media_downloader）+ 下载日志
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout,
)

from gui_qt import common, download_ops
from gui_qt.common import _collect, _info_text, post_to_main, refresh_results_count, run_bg
from gui_qt.download_panel import DownloadPanel
from gui_qt.pages.base import BasePage

# 线程数选项（全局共享，与批量导入页一致）
THREAD_OPTIONS = ["2", "4", "8", "16", "32", "64"]

# 搜索模式平台下拉值 → video_search 平台名
SEARCH_PLATFORMS = [("全部平台", "all"), ("B站", "bilibili"), ("抖音", "douyin"), ("微博", "weibo")]


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
        # 模式切换：链接模式 / 搜索模式
        self.mode_sel = QComboBox()
        self.mode_sel.addItems(["🔗 链接模式", "🔎 搜索模式"])
        self.mode_sel.setFixedWidth(104)
        self.mode_sel.setToolTip("链接模式：粘贴链接解析；搜索模式：输入关键词全局搜索视频")
        self.mode_sel.currentIndexChanged.connect(self._on_mode_change)
        row.addWidget(self.mode_sel)
        # 搜索平台下拉（仅搜索模式可见）
        self.search_plat_sel = QComboBox()
        self.search_plat_sel.addItems([label for label, _ in SEARCH_PLATFORMS])
        self.search_plat_sel.setFixedWidth(90)
        self.search_plat_sel.setToolTip("搜索的平台范围（全部=三平台并发）")
        self.search_plat_sel.setVisible(False)
        row.addWidget(self.search_plat_sel)
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("https://www.bilibili.com/video/BV...")
        self.url_entry.returnPressed.connect(self._submit)
        row.addWidget(self.url_entry, 1)
        self.parse_btn = QPushButton("🔍 解析")
        self.parse_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.parse_btn.clicked.connect(self._submit)
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

    # ==================== 模式切换 ====================

    def _is_search_mode(self) -> bool:
        return self.mode_sel.currentIndex() == 1

    def _on_mode_change(self, _idx):
        if self._is_search_mode():
            self.search_plat_sel.setVisible(True)
            self.url_entry.setPlaceholderText("输入关键词搜索视频，如：封茗囧菌 原创歌曲")
            self.parse_btn.setText("🔎 搜索")
        else:
            self.search_plat_sel.setVisible(False)
            self.url_entry.setPlaceholderText("https://www.bilibili.com/video/BV...")
            self.parse_btn.setText("🔍 解析")

    def _submit(self):
        """按钮/回车统一入口：按当前模式分流到解析或搜索。"""
        if self._is_search_mode():
            self._search()
        else:
            self._parse()

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

    # ==================== 搜索模式 ====================

    def _search(self):
        kw = self.url_entry.text().strip()
        if not kw:
            self.app.toast("请输入搜索关键词", "warning")
            return
        plat_val = dict(SEARCH_PLATFORMS).get(self.search_plat_sel.currentText(), "all")
        self.parse_btn.setEnabled(False)
        self.result_text.setPlainText(f"⏳ 搜索中：{kw}（{self.search_plat_sel.currentText()}）...\n")

        def work():
            from platforms.video_search import search_videos

            def notify(msg, level):
                post_to_main(self.app.toast, msg, level)

            return search_videos(plat_val, kw, 20, notify=notify)

        def done(result, err):
            self.parse_btn.setEnabled(True)
            if err:
                self.result_text.setPlainText(f"❌ 搜索失败: {err}")
                self.app.toast(f"搜索失败：{str(err)[:60]}", "error")
                return
            results = result or []
            added = _collect(results)
            self.result_text.setPlainText(
                f"✅ 搜索「{kw}」获取 {len(results)} 条（{added} 条新存入「已获取」）\n"
                + "=" * 40 + "\n双击结果弹窗中的条目即可自动解析下载")
            refresh_results_count(self.app)
            if results:
                self._show_search_results(kw, results)
            else:
                self.app.toast("没有搜到结果", "warning")

        run_bg(work, done)

    def _show_search_results(self, kw, results):
        """搜索结果弹窗：双击/选中+解析 → 填回链接模式自动解析。"""
        from platforms.video_search import parse_url_of
        win = QDialog(self)
        win.setWindowTitle(f"🔎 搜索「{kw}」 · 共 {len(results)} 条")
        win.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        win.resize(860, 520)
        v = QVBoxLayout(win)
        v.setContentsMargins(12, 12, 12, 12)
        head = QLabel("双击条目（或选中后点「解析选中」）→ 自动填入链接并解析")
        head.setStyleSheet("font-size: 12px; color: #5a6072;")
        v.addWidget(head)
        table = QTableWidget(len(results), 7)
        table.setHorizontalHeaderLabels(["平台", "标题", "作者", "播放量", "时长", "发布时间", "链接"])
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        plat_label = {"bilibili": "B站", "douyin": "抖音", "weibo": "微博"}
        for i, r in enumerate(results):
            table.setItem(i, 0, QTableWidgetItem(plat_label.get(r.platform, r.platform)))
            table.setItem(i, 1, QTableWidgetItem((r.title or "")[:60]))
            table.setItem(i, 2, QTableWidgetItem(r.author or ""))
            table.setItem(i, 3, QTableWidgetItem(f"{r.play_count:,}" if r.play_count else ""))
            m, s = divmod(r.duration or 0, 60)
            table.setItem(i, 4, QTableWidgetItem(f"{m}:{s:02d}" if r.duration else ""))
            table.setItem(i, 5, QTableWidgetItem(r.publish_time or ""))
            table.setItem(i, 6, QTableWidgetItem(parse_url_of(r)))
        table.setColumnWidth(0, 48)
        table.setColumnWidth(1, 300)
        table.setColumnWidth(2, 110)
        table.setColumnWidth(3, 80)
        table.setColumnWidth(4, 60)
        table.setColumnWidth(5, 110)
        table.horizontalHeader().setStretchLastSection(True)
        v.addWidget(table, 1)

        def _parse_selected():
            rows = sorted({idx.row() for idx in table.selectedIndexes()})
            if not rows:
                self.app.toast("请先选中一条结果", "warning")
                return
            _parse_row(rows[0])

        def _parse_row(row):
            url = table.item(row, 6).text()
            win.accept()
            # 切回链接模式 → 填入链接 → 自动解析（闭环到下载）
            self.mode_sel.setCurrentIndex(0)
            self.url_entry.setText(url)
            self._parse()

        table.doubleClicked.connect(lambda idx: _parse_row(idx.row()))
        btn_row = QHBoxLayout()
        parse_btn = QPushButton("🔍 解析选中")
        parse_btn.setStyleSheet("background: #2e7d32; color: white;")
        parse_btn.clicked.connect(_parse_selected)
        btn_row.addWidget(parse_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(win.reject)
        btn_row.addWidget(close_btn)
        v.addLayout(btn_row)
        win.exec()

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
