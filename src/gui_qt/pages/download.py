"""视频下载页（PySide6 版）：从「已获取列表」勾选下载，或上方粘贴链接解析后追加到列表。

对应原版 gui/download_page.py：
- 顶部：链接输入行（解析后加入已获取列表，支持多平台 URL）
- 平台筛选（全部/B站/抖音/微博）+ 全选/反选/清除 + 清晰度下拉（全局共享）+ 下载选中
- 已获取列表（复选行）
- 统一日志框（解析 + 下载）
B站视频走 playurl 下载（画质可选，默认最高），抖音/微博走 media_downloader。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QListWidgetItem,
    QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

from gui_qt import common
from gui_qt.common import _collect, _fmt, post_to_main, refresh_results_count, run_bg
from gui_qt import download_ops
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList


class VideoDownloadPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.check_items = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # 1. 顶部：链接输入行（解析后加入已获取列表）
        head = QHBoxLayout()
        title = QLabel("📥 视频下载 · 粘贴链接解析后加入列表")
        title.setStyleSheet("font-size: 15px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        layout.addLayout(head)

        link_row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText(
            "粘贴视频链接（B站/抖音/小红书/快手/好看/微视/梨视频/皮皮搞笑/微博），解析后加入列表")
        self.url_entry.returnPressed.connect(self._parse_url_to_collected)
        link_row.addWidget(self.url_entry, 1)
        self.parse_btn = QPushButton("🔍 解析加入")
        self.parse_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.parse_btn.clicked.connect(self._parse_url_to_collected)
        link_row.addWidget(self.parse_btn)
        layout.addLayout(link_row)

        # 2. 平台筛选 + 全选/反选/清除 + 下载选中 + 清晰度（右）
        bar = QHBoxLayout()
        self.dl_platform = QComboBox()
        self.dl_platform.addItems(["全部平台", "B站", "抖音", "微博"])
        self.dl_platform.setFixedWidth(92)
        self.dl_platform.currentTextChanged.connect(lambda _: self.refresh())
        bar.addWidget(self.dl_platform)
        for text, mode in (("全选", "all"), ("反选", "invert"), ("清除", "none")):
            b = QPushButton(text)
            b.setFixedSize(48, 24)
            b.setStyleSheet("font-size: 11px;")
            b.clicked.connect(lambda _=False, m=mode: self._select(m))
            bar.addWidget(b)
            bar.addSpacing(4)
        bar.addStretch(1)
        bar.addWidget(QLabel("清晰度:"))
        self.quality_sel = QComboBox()
        self.quality_sel.addItems(
            ["最高画质", "8K", "杜比视界", "HDR", "4K", "1080P60", "1080P", "720P", "480P"])
        self.quality_sel.setFixedWidth(110)
        self.quality_sel.setCurrentText(common.CURRENT_QUALITY)
        self.quality_sel.currentTextChanged.connect(self._on_quality_change)
        bar.addWidget(self.quality_sel)
        self.download_btn = QPushButton("⬇ 下载选中")
        self.download_btn.setFixedSize(96, 24)
        self.download_btn.setStyleSheet(
            "background: #2e7d32; color: white; font-size: 11px;")
        self.download_btn.clicked.connect(self._download_selected)
        bar.addWidget(self.download_btn)
        layout.addLayout(bar)

        # 3. 已获取列表
        self.list_widget = RowCheckList()
        layout.addWidget(self.list_widget, 1)

        # 4. 统一日志框（解析 + 下载）
        self.list_log_text = QTextEdit()
        self.list_log_text.setReadOnly(True)
        self.list_log_text.setFixedHeight(130)
        self.list_log_text.setPlainText("解析/下载日志将显示在这里\n")
        layout.addWidget(self.list_log_text)

        self.refresh()

    # ==================== 解析加入 ====================

    def _parse_url_to_collected(self):
        """解析链接 → 构造 VideoInfo → 加入 COLLECTED → 刷新列表 → 日志记录解析过程。

        多平台（抖音/小红书/快手/好看/微视/梨视频/皮皮搞笑）走 multi_downloader，
        构造最小 VideoInfo；B站/微博走 platforms.get_parser().parse() 返回完整 VideoInfo。
        """
        url = self.url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入视频链接")
            return
        self.parse_btn.setEnabled(False)
        self._log(f"🔗 开始解析: {url[:80]}")

        def work():
            from platforms.base import VideoInfo, now_str

            # 优先尝试多平台下载器（7 个平台）
            try:
                from multi_downloader import parse_video
                info_dict = parse_video(url)
                info = VideoInfo(
                    platform=info_dict.get("platform", "unknown"),
                    kind="video",
                    video_id=url[:64],
                    title=info_dict.get("title", ""),
                    cover_url=info_dict.get("cover_url", ""),
                    video_url=info_dict.get("video_url", ""),
                    raw_url=url,
                    fetched_at=now_str(),
                )
                if info_dict.get("images"):
                    info.images = info_dict["images"]
                _collect([info])
                return info
            except ValueError:
                pass  # 非多平台 URL，回退到 B站/微博

            # 回退：B站 / 微博
            from platforms import URLRecognizer, get_parser
            platform = URLRecognizer.recognize(url)
            if not platform:
                raise ValueError(
                    "无法识别 URL 平台（支持：B站/抖音/小红书/快手/好看/微视/梨视频/皮皮搞笑/微博）")
            parser = get_parser(platform)
            if not parser:
                raise ValueError(f"平台 '{platform}' 解析器未实现")
            info = parser.parse(url)
            if not info:
                raise ValueError("获取失败（可能反爬，需 Cookie）")
            _collect([info])
            return info

        def done(result, err):
            self.parse_btn.setEnabled(True)
            if err:
                self._log(f"❌ {err}")
                return
            title = (result.title or result.video_id or url)[:40]
            self._log(f"✅ 解析成功 [{result.platform}]: {title} → 已加入列表")
            self.refresh()
            refresh_results_count(self.app)

        run_bg(work, done)

    # ==================== 列表渲染/选择 ====================

    def _select(self, mode):
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if not (item.flags() & Qt.ItemIsUserCheckable):
                continue
            if mode == "all":
                item.setCheckState(Qt.Checked)
            elif mode == "none":
                item.setCheckState(Qt.Unchecked)
            else:
                item.setCheckState(
                    Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    def refresh(self):
        self.list_widget.clear()
        self.check_items = []
        plat_map = {"B站": "bilibili", "抖音": "douyin", "微博": "weibo"}
        target = plat_map.get(self.dl_platform.currentText(), "")
        items = [i for i in common.COLLECTED if not target or i.platform == target]
        if not items:
            item = QListWidgetItem(
                "（已获取列表暂无数据，可在顶部粘贴链接解析加入，或到「信息获取」页采集）")
            item.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(item)
            return
        for i, info in enumerate(items, 1):
            self.check_items.append(info)
            title = (info.title or "-")[:32]
            author = info.author or "-"
            plat = info.platform or "-"
            text = (f"[{i}] [{plat}] {title}  @{author}  "
                    f"播放{_fmt(info.play_count)} 赞{_fmt(info.like_count)}")
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            self.list_widget.addItem(item)

    def on_show(self):
        self.refresh()

    def _selected(self):
        return [self.check_items[row] for row in range(self.list_widget.count())
                if self.list_widget.item(row).checkState() == Qt.Checked]

    # ==================== 日志 ====================

    def _log(self, text):
        """线程安全地往日志框追加一行（后台线程经 post_to_main 回主线程）。"""
        def apply():
            try:
                self.list_log_text.append(text)
                self.list_log_text.ensureCursorVisible()
            except Exception:
                pass
        post_to_main(apply)

    # ==================== 下载执行 ====================

    def _on_quality_change(self, value):
        """清晰度改变时同步到全局（供资产管理/已获取页复用）。"""
        common.CURRENT_QUALITY = value

    def _download_selected(self):
        items = self._selected()
        if not items:
            QMessageBox.warning(self, "提示", "请先勾选要下载的条目")
            return
        self.list_log_text.clear()
        self._log(f"⏳ 准备下载 {len(items)} 个条目...")
        self._download_items(items)

    def _download_items(self, items):
        bili_items, non_bili = download_ops.resolve_items(items)
        if bili_items:
            self._download_bilibili(bili_items, non_bili)
        else:
            self._download_media(items)

    def _download_media(self, items):
        def work():
            return download_ops.download_media_items(items, log=self._log)

        def done(result, err):
            if err:
                self._log(f"❌ 下载失败: {err}")
                return
            stats = result or {}
            msg = (f"✅ 下载完成: 图片 {stats.get('images', 0)} | "
                   f"Live图 {stats.get('live', 0)} | 视频 {stats.get('videos', 0)}")
            if stats.get("skipped", 0):
                msg += f" | 跳过已下载 {stats['skipped']}"
            self._log(msg)

        run_bg(work, done)

    def _download_bilibili(self, bili_items, non_bili):
        """B站批量下载：按类型分发（BV→视频，DRAW→图片，AV→archive.bvid视频）。

        不弹窗，直接按顶部清晰度下拉下载。
        """
        ck = download_ops.get_bilibili_cookie()
        bv_items, draw_items = download_ops.classify_bili(bili_items)
        quality = download_ops.quality_to_qn(self.quality_sel.currentText())
        quality_label = self.quality_sel.currentText()

        def work():
            return download_ops.do_bilibili_download(
                bv_items, draw_items, non_bili, quality, ck,
                quality_label=quality_label, log=self._log)

        def done(result, err):
            if err:
                self._log(f"❌ 下载失败: {err}")
                return
            paths, draw_stats, other_stats, errors = result
            lines = []
            if paths:
                lines.append(f"B站视频: {len(paths)} 个")
            if draw_stats:
                lines.append(f"动态图片: {draw_stats.get('images', 0)} 张")
            if other_stats:
                lines.append(f"其他: 图 {other_stats.get('images', 0)} / "
                             f"视频 {other_stats.get('videos', 0)}")
            msg = "\n".join(lines) or "无内容下载"
            if errors:
                msg += f"\n\n失败 {len(errors)}:\n" + "\n".join(errors[:5])
            self._log(f"✅ {msg}")

        run_bg(work, done)
