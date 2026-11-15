"""微博超话页：解析超话 → 选择频道 → 访客态翻页采集 → 浏览结果并导出 Excel。

- 输入：超话链接（https://weibo.com/p/100808...）或 100808 开头的超话 ID
- 频道：从超话头部动态读取 flowId，每个频道可单独设置最大翻页数
- 采集：沿用游标翻页（moreInfo.params.since_id），跨频道按帖子 ID 去重
- 导出：download/微博超话/<超话名>超话_数据采集_时间戳.xlsx
- 边界：阅读量 / 影响力 / 榜单分数网页端不存在；总数只有「4.8万」这类缩略文案
"""

import os
import threading
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from gui_qt.common import post_to_main, run_bg
from gui_qt.pages.base import BasePage
from platforms import weibo_chaohua as wch


class WeiboChaohuaPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self._topic_id = ""
        self._info = None
        self._data = {}
        self._busy = False
        self._stop_event = threading.Event()
        self._channel_rows = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # ---- 标题 ----
        title = QLabel("🔴 微博超话")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        title_row = QHBoxLayout()
        title_row.addWidget(title)
        title_row.addStretch(1)
        from gui_qt.help_overlay import attach_help_overlay
        title_row.addWidget(attach_help_overlay(self, "❓ 微博超话 · 功能介绍", (
            "<p><b>微博超话访客态采集（免登录），支持任意超话 ID。</b></p>"
            "<p>· <b>解析</b>：输入超话链接（https://weibo.com/p/100808...）"
            "或 100808 开头的超话 ID → 点「🔎 解析超话」</p>"
            "<p>· <b>频道</b>：从超话头部动态读取 flowId；每个频道可单独勾选并设置最大翻页数</p>"
            "<p>· <b>采集</b>：游标翻页，跨频道按帖子 ID 去重；页面显示当前页数与累计条数</p>"
            "<p>· <b>导出</b>：汇总、频道明细、月度趋势、作者榜、设备/地区/星期/时段、全局 TOP50</p>"
            "<p>· <b>保存位置</b>：download/微博超话/&lt;超话名&gt;超话_数据采集_时间戳.xlsx</p>"
            "<p>· <b>已知边界</b>：阅读量 / 影响力 / 榜单分数网页端不存在；"
            "帖子数、粉丝数只有「4.8万 / 21万」缩略文案；"
            "「最新」频道深翻会混入零星老帖，时间趋势建议只取近期连续区间。</p>"
        )))
        layout.addLayout(title_row)

        # ---- 输入卡片 ----
        top = QFrame()
        top.setProperty("variant", "card")
        tv = QVBoxLayout(top)
        tv.setContentsMargins(12, 10, 12, 10)
        tv.setSpacing(6)
        row = QHBoxLayout()
        row.addWidget(QLabel("超话链接 / ID"))
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText(
            "https://weibo.com/p/10080857b3593b49a1cef3b2585f65295e0419 或 100808...")
        self.input_edit.returnPressed.connect(self._do_parse)
        row.addWidget(self.input_edit, 1)
        self.parse_btn = QPushButton("🔎 解析超话")
        self.parse_btn.clicked.connect(self._do_parse)
        row.addWidget(self.parse_btn)
        tv.addLayout(row)

        self.overview_label = QLabel("输入超话链接或 ID 后点「解析超话」，再选择频道开始采集。")
        self.overview_label.setProperty("role", "muted")
        self.overview_label.setWordWrap(True)
        tv.addWidget(self.overview_label)
        layout.addWidget(top)

        # ---- 频道 + 操作 ----
        ch_card = QFrame()
        ch_card.setProperty("variant", "card")
        cv = QVBoxLayout(ch_card)
        cv.setContentsMargins(12, 10, 12, 10)
        cv.setSpacing(6)

        ch_head = QHBoxLayout()
        ch_title = QLabel("采集频道")
        ch_title.setProperty("role", "sectionTitle")
        ch_head.addWidget(ch_title)
        ch_head.addStretch(1)
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        ch_head.addWidget(self.select_all_btn)
        self.select_none_btn = QPushButton("全不选")
        self.select_none_btn.clicked.connect(lambda: self._set_all_checked(False))
        ch_head.addWidget(self.select_none_btn)
        cv.addLayout(ch_head)

        self.ch_table = QTableWidget(0, 5)
        self.ch_table.setHorizontalHeaderLabels(["采集", "频道", "flowId", "最大页数", "本次采集"])
        self.ch_table.verticalHeader().setVisible(False)
        self.ch_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.ch_table.setSelectionMode(QTableWidget.NoSelection)
        self.ch_table.setAlternatingRowColors(True)
        self.ch_table.setMinimumHeight(118)
        self.ch_table.setMaximumHeight(220)
        header = self.ch_table.horizontalHeader()
        header.resizeSection(0, 56)
        header.resizeSection(1, 110)
        header.resizeSection(3, 90)
        header.resizeSection(4, 210)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        cv.addWidget(self.ch_table)

        ops = QHBoxLayout()
        self.start_btn = QPushButton("🚀 开始采集")
        self.start_btn.setStyleSheet(
            "background: #2e7d32; color: white; padding: 6px 16px; border-radius: 4px;")
        self.start_btn.clicked.connect(self._do_start)
        ops.addWidget(self.start_btn)
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._do_stop)
        ops.addWidget(self.stop_btn)
        ops.addStretch(1)
        self.export_btn = QPushButton("💾 导出 Excel")
        self.export_btn.setEnabled(False)
        self.export_btn.setStyleSheet(
            "background: #1565c0; color: white; padding: 5px 14px; border-radius: 4px;")
        self.export_btn.clicked.connect(self._do_export)
        ops.addWidget(self.export_btn)
        self.open_dir_btn = QPushButton("📂 打开导出目录")
        self.open_dir_btn.clicked.connect(self._open_export_dir)
        ops.addWidget(self.open_dir_btn)
        cv.addLayout(ops)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        cv.addWidget(self.progress)
        self.status_label = QLabel("等待解析超话")
        self.status_label.setProperty("role", "muted")
        self.status_label.setWordWrap(True)
        cv.addWidget(self.status_label)
        layout.addWidget(ch_card)

        # ---- 结果 ----
        self.result_tabs = QTabWidget()
        self.result_tabs.addTab(self._build_posts_tab(), "📋 帖子明细")
        self.result_tabs.addTab(self._build_stats_tab(), "📊 统计概览")
        layout.addWidget(self.result_tabs, 1)

    # ==================== UI 构建 ====================
    def _build_posts_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 6, 4, 4)
        v.setSpacing(4)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("频道"))
        self.channel_filter = QComboBox()
        self.channel_filter.addItem("全部频道", "")
        self.channel_filter.currentIndexChanged.connect(self._refresh_results)
        bar.addWidget(self.channel_filter)
        self.kw_edit = QLineEdit()
        self.kw_edit.setPlaceholderText("搜索作者 / 正文")
        self.kw_edit.textChanged.connect(self._refresh_results)
        bar.addWidget(self.kw_edit, 1)
        self.count_label = QLabel("0 条")
        self.count_label.setProperty("role", "muted")
        bar.addWidget(self.count_label)
        v.addLayout(bar)

        self.posts_table = QTableWidget(0, 10)
        self.posts_table.setHorizontalHeaderLabels(
            ["时间", "频道", "作者", "赞", "评论", "转发", "图片", "地区", "设备", "正文"])
        self.posts_table.verticalHeader().setVisible(False)
        self.posts_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.posts_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.posts_table.setAlternatingRowColors(True)
        head = self.posts_table.horizontalHeader()
        for column, width in enumerate([142, 86, 150, 58, 58, 58, 58, 100, 130]):
            head.resizeSection(column, width)
        head.setSectionResizeMode(9, QHeaderView.Stretch)
        v.addWidget(self.posts_table, 1)
        return page

    def _build_stats_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(4, 6, 4, 4)
        self.stats_view = QPlainTextEdit()
        self.stats_view.setReadOnly(True)
        self.stats_view.setPlaceholderText("完成一次采集后显示统计概览")
        v.addWidget(self.stats_view)
        return page

    # ==================== 解析 ====================
    def _do_parse(self):
        if self._busy:
            return
        raw = self.input_edit.text().strip()
        if not raw:
            self.status_label.setText("❌ 请先输入超话链接或 ID")
            return
        try:
            topic_id = wch.extract_topic_id(raw)
        except ValueError as exc:
            self.status_label.setText(f"❌ {exc}")
            return

        self._set_busy(True, f"⏳ 正在解析超话 {topic_id} ...")

        def work():
            return wch.fetch_overview(topic_id)

        def done(info, err):
            if err:
                self._set_busy(False)
                self.status_label.setText(f"❌ 解析失败：{err}")
                self.app.toast(f"超话解析失败：{err}", "warning")
                return
            self._topic_id = topic_id
            self._info = info
            self._data = {}
            self._set_busy(False)
            labels = " / ".join(info.get("labels") or [])
            fans = f'{info.get("fans_label") or "粉丝"} {info.get("fans_str") or "-"}'
            lines = [
                f'{info.get("nick") or "（未命名超话）"}'
                + (f" ｜ {labels}" if labels else ""),
                f'超话 ID：{topic_id}',
                f'帖子 {info.get("post_count_str") or "-"} ｜ {fans}',
            ]
            if info.get("desc"):
                lines.append(f'简介：{info["desc"]}')
            self.overview_label.setText("\n".join(lines))
            self._populate_channels(info.get("channels") or [])
            self._reset_results()
            self.status_label.setText(
                f'✅ 已解析「{info.get("nick") or topic_id}」，'
                f'共 {len(info.get("channels") or [])} 个频道，选择后点「开始采集」')
            self.app.toast(f'超话已解析：{info.get("nick") or topic_id}', "success")

        run_bg(work, done)

    # ==================== 频道表 ====================
    def _populate_channels(self, channels):
        self._channel_rows = []
        self.ch_table.setRowCount(len(channels))
        for row, channel in enumerate(channels):
            check = QCheckBox()
            check.setChecked(True)
            holder = QWidget()
            h = QHBoxLayout(holder)
            h.setContentsMargins(0, 0, 0, 0)
            h.setAlignment(Qt.AlignCenter)
            h.addWidget(check)
            self.ch_table.setCellWidget(row, 0, holder)

            self.ch_table.setItem(row, 1, QTableWidgetItem(channel.get("name") or "频道"))
            flow_item = QTableWidgetItem(channel.get("flow_id") or "")
            flow_item.setToolTip(channel.get("flow_id") or "")
            self.ch_table.setItem(row, 2, flow_item)

            spin = QSpinBox()
            spin.setRange(1, 500)
            spin.setValue(int(channel.get("max_pages") or 10))
            spin.setToolTip("该频道最大翻页数（每页约 15 条；1 秒/页，过大耗时较长）")
            self.ch_table.setCellWidget(row, 3, spin)

            status = QLabel("待采集")
            status.setProperty("role", "muted")
            self.ch_table.setCellWidget(row, 4, status)
            self._channel_rows.append({
                "name": channel.get("name") or "频道",
                "flow_id": channel.get("flow_id") or "",
                "check": check,
                "spin": spin,
                "status": status,
            })

    def _set_all_checked(self, checked):
        for row in self._channel_rows:
            row["check"].setChecked(bool(checked))

    # ==================== 采集 ====================
    def _do_start(self):
        if self._busy:
            return
        if not self._info or not self._channel_rows:
            self.status_label.setText("❌ 请先解析超话")
            return
        selected = []
        for row in self._channel_rows:
            if not row["check"].isChecked():
                row["status"].setText("未选择")
                continue
            row["status"].setText("等待中")
            selected.append({
                "name": row["name"],
                "flow_id": row["flow_id"],
                "max_pages": row["spin"].value(),
            })
        if not selected:
            self.status_label.setText("❌ 请至少勾选一个频道")
            return

        topic_id = self._topic_id
        self._stop_event.clear()
        self._set_busy(True, "⏳ 正在换取访客身份 ...", stoppable=True)

        def work():
            client = wch.ChaohuaClient(
                topic_id,
                progress=lambda message: post_to_main(self._on_progress, message),
                stop_event=self._stop_event,
            )
            return client.crawl(selected)

        def done(data, err):
            if err:
                self._set_busy(False)
                self.status_label.setText(f"❌ 采集失败：{err}")
                self.app.toast(f"超话采集失败：{err}", "warning")
                return
            self._data = data or {}
            self._set_busy(False)
            for row in self._channel_rows:
                posts = self._data.get(row["name"])
                if posts is not None:
                    row["status"].setText(f"{len(posts)} 条")
                elif row["check"].isChecked():
                    row["status"].setText("未采集")
                else:
                    row["status"].setText("未选择")
            self._refresh_channel_filter()
            self._refresh_results()
            self._refresh_stats()
            unique = len(wch.merge_posts(self._data))
            raw_total = sum(len(posts) for posts in self._data.values())
            stopped = self._stop_event.is_set()
            prefix = "⏹ 已停止" if stopped else "✅ 采集完成"
            self.status_label.setText(
                f"{prefix}：{raw_total} 条（跨频道去重后 {unique} 条），可导出 Excel")
            self.app.toast(
                f"超话采集{'已停止' if stopped else '完成'}：去重后 {unique} 条", "success")

        run_bg(work, done)

    def _on_progress(self, message):
        self.status_label.setText(f"⏳ {message}")

    def _do_stop(self):
        if not self._busy:
            return
        self._stop_event.set()
        self.stop_btn.setEnabled(False)
        self.status_label.setText("⏹ 正在停止（等待当前页结束）...")

    # ==================== 结果展示 ====================
    def _reset_results(self):
        self.channel_filter.blockSignals(True)
        self.channel_filter.clear()
        self.channel_filter.addItem("全部频道", "")
        self.channel_filter.blockSignals(False)
        self.posts_table.setRowCount(0)
        self.count_label.setText("0 条")
        self.stats_view.setPlainText("")
        self.export_btn.setEnabled(False)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)

    def _refresh_channel_filter(self):
        current = self.channel_filter.currentData()
        self.channel_filter.blockSignals(True)
        self.channel_filter.clear()
        self.channel_filter.addItem("全部频道", "")
        for name in self._data:
            self.channel_filter.addItem(name, name)
        index = self.channel_filter.findData(current) if current else 0
        self.channel_filter.setCurrentIndex(max(0, index))
        self.channel_filter.blockSignals(False)

    def _refresh_results(self):
        channel = self.channel_filter.currentData() or ""
        keyword = self.kw_edit.text().strip().casefold()
        rows = []
        for name, posts in self._data.items():
            if channel and name != channel:
                continue
            for post in posts:
                if keyword:
                    haystack = f'{post.get("author", "")} {post.get("text", "")}'.casefold()
                    if keyword not in haystack:
                        continue
                rows.append((name, post))
        rows.sort(key=lambda item: item[1].get("dt") or datetime.min, reverse=True)

        table = self.posts_table
        table.setUpdatesEnabled(False)
        table.setRowCount(len(rows))
        for index, (name, post) in enumerate(rows):
            values = [
                post.get("dt_str", ""), name, post.get("author", ""),
                post.get("likes", 0), post.get("comments", 0), post.get("reposts", 0),
                post.get("pics", 0), post.get("region", ""), post.get("source", ""),
                post.get("text", ""),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column in (3, 4, 5, 6):
                    item.setTextAlignment(Qt.AlignCenter)
                if column == 9:
                    item.setToolTip(str(value))
                table.setItem(index, column, item)
        table.setUpdatesEnabled(True)
        self.count_label.setText(f"{len(rows)} 条")

    def _refresh_stats(self):
        if not self._data:
            self.stats_view.setPlainText("")
            return
        info = self._info or {}
        summary = wch.build_summary(self._data)
        lines = [
            f'超话：{info.get("nick") or "-"}（{info.get("topic_id") or "-"}）',
            f'状态：{" / ".join(info.get("labels") or []) or "-"}',
            f'页面文案：帖子 {info.get("post_count_str") or "-"}'
            f'｜{info.get("fans_label") or "粉丝"} {info.get("fans_str") or "-"}',
            f'本次唯一帖子：{summary["unique"]} 条（含频道重复 {summary["raw_total"]} 条）',
            "",
            "【频道统计】",
        ]
        for channel in summary["channels"]:
            lines.append(
                f'{channel["name"]:<8} {channel["count"]:>5} 条  '
                f'均赞 {channel["avg_likes"]:>6.1f}  中位 {channel["median_likes"]:>5.0f}  '
                f'最高 {channel["max_likes"]:>5}  带图 {channel["pic_rate"]:>3.0f}%  '
                f'作者 {channel["authors"]:>4}  {channel["start"]} ~ {channel["end"]}'
            )

        lines.extend(["", "【发帖 TOP10】"])
        for rank, (author, count) in enumerate(summary["authors_top"], 1):
            lines.append(f"{rank:>2}. {author}  {count} 帖")

        lines.extend(["", "【获赞 TOP10】"])
        for rank, (author, likes, max_likes) in enumerate(summary["likes_top"], 1):
            lines.append(f"{rank:>2}. {author}  {likes} 赞（最高单帖 {max_likes}）")

        lines.extend(["", "【月度分布（最近 12 个月）】"])
        for month, item in summary["months"][-12:]:
            lines.append(f"{month}  {item['count']:>4} 帖  "
                         f"均赞 {item['likes'] / item['count']:.1f}  最高 {item['max']}")

        lines.extend(["", "【设备 TOP10】"])
        for name, count in summary["sources_top"]:
            lines.append(f"{name}  {count}")

        lines.extend(["", "【地区 TOP10】"])
        for name, count in summary["regions_top"]:
            lines.append(f"{name}  {count}")

        lines.extend(["", "【发帖时段（0-23 点）】"])
        lines.append("  ".join(f"{hour:02d}:{count}" for hour, count in enumerate(summary["hours"])))

        lines.extend(["", "【星期分布】"])
        lines.append("  ".join(f"{wch.WEEKDAY_NAMES[index]}:{count}"
                               for index, count in enumerate(summary["weekdays"])))

        lines.extend([
            "",
            "提示：阅读量 / 影响力 / 榜单分数网页端不存在；"
            "「最新」频道深翻会混入零星老帖，时间趋势建议只取近期连续区间。",
        ])
        self.stats_view.setPlainText("\n".join(lines))

    # ==================== 导出 ====================
    def _do_export(self):
        if self._busy:
            return
        if not self._info or not self._data:
            self.status_label.setText("❌ 请先完成一次采集")
            return
        info, data = self._info, self._data
        self._set_busy(True, "⏳ 正在生成 Excel ...")

        def work():
            return wch.export_xlsx(info, data)

        def done(path, err):
            self._set_busy(False)
            if err:
                self.status_label.setText(f"❌ 导出失败：{err}")
                self.app.toast(f"Excel 导出失败：{err}", "warning")
                return
            self.status_label.setText(f"✅ Excel 已导出：{path}")
            self.status_label.setToolTip(str(path))
            self.app.toast(f"超话数据已导出：{path.name}", "success")

        run_bg(work, done)

    def _open_export_dir(self):
        try:
            from toolbox.config_manager import get_export_subdir
            path = get_export_subdir("weibo_chaohua")
            os.startfile(str(path))
        except Exception as exc:
            self.app.toast(f"打开导出目录失败：{exc}", "warning")

    # ==================== 状态 ====================
    def _set_busy(self, busy, text="", stoppable=False):
        self._busy = busy
        self.input_edit.setEnabled(not busy)
        self.parse_btn.setEnabled(not busy)
        self.start_btn.setEnabled(not busy)
        self.select_all_btn.setEnabled(not busy)
        self.select_none_btn.setEnabled(not busy)
        self.ch_table.setEnabled(not busy)
        self.stop_btn.setEnabled(busy and stoppable)
        self.export_btn.setEnabled(not busy and bool(self._data))
        if busy:
            self.progress.setRange(0, 0)
        else:
            self.progress.setRange(0, 100)
            self.progress.setValue(100 if self._data else 0)
        if text:
            self.status_label.setText(text)

    def on_hide(self):
        self._stop_event.set()

    def shutdown(self):
        self._stop_event.set()
