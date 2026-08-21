"""已获取页（PySide6 版）：三栏分平台展示 COLLECTED，按栏全选/反选/清除、导出、下载。

对应原版 gui/info_pages.py 的 InfoResultsPage：
- 顶部：画质下拉（全局共享）+ 🗑 全部清空 + 📤 全部导出 + 📥 全部下载 + 导出格式复选
- 三栏（抖音/B站/微博）：计数标签 + 全选/反选/清除/📤 导出/📥 下载 + 复选行 + 🔗 打开链接
- 每行显示发布日期，默认按发布日期倒序（最新在前）
- 下载：B站按画质直下（视频走 playurl、DRAW 动态走图片），非 B站走 media_downloader
"""

import webbrowser
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QHBoxLayout, QLabel, QListWidgetItem,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from gui_qt import common
from gui_qt.common import _fmt, run_bg
from gui_qt import download_ops
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList

PLATFORMS_3 = [
    ("douyin", "📕 抖音"),
    ("bilibili", "📺 B站"),
    ("weibo", "📝 微博"),
]

_PUB_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d")


def _parse_pub(info):
    """解析发布时间 → (datetime, 显示文本)。无法解析时返回 (datetime.min, '无日期')。"""
    raw = (getattr(info, "publish_time", "") or "").strip()
    if raw:
        for fmt in _PUB_FORMATS:
            try:
                dt = datetime.strptime(raw[:19] if " " in raw else raw[:10], fmt)
                return dt, raw[:16] if " " in raw else raw[:10]
            except ValueError:
                continue
        # 纯数字时间戳
        if raw.isdigit():
            try:
                ts = int(raw)
                if ts > 10 ** 12:  # 毫秒
                    ts //= 1000
                dt = datetime.fromtimestamp(ts)
                return dt, dt.strftime("%Y-%m-%d %H:%M")
            except (ValueError, OSError):
                pass
    return datetime.min, "无日期"


class InfoResultsPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        self.cols = {key: {"songs": [], "list": None, "count_label": None}
                     for key, _ in PLATFORMS_3}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        # 顶部：标题 + 全部清空 + 画质选择
        head = QHBoxLayout()
        title = QLabel("📦 已获取（按平台分栏，按发布日期倒序）")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch(1)
        export_all_btn = QPushButton("📤 全部导出")
        export_all_btn.setStyleSheet("background: #2e7d32; color: white;")
        export_all_btn.setToolTip("导出三栏内全部勾选条目（一次导出三平台）")
        export_all_btn.clicked.connect(self._export_all)
        head.addWidget(export_all_btn)
        dl_all_btn = QPushButton("📥 全部下载")
        dl_all_btn.setStyleSheet("background: #2e7d32; color: white;")
        dl_all_btn.setToolTip("下载三栏内全部勾选条目（一次下载三平台）")
        dl_all_btn.clicked.connect(self._download_all)
        head.addWidget(dl_all_btn)
        clear_btn = QPushButton("🗑 全部清空")
        clear_btn.setStyleSheet("background: #c62828; color: white;")
        clear_btn.clicked.connect(self._clear)
        head.addWidget(clear_btn)
        head.addWidget(QLabel("画质:"))
        self.quality_sel = QComboBox()
        self.quality_sel.addItems(
            ["最高画质", "8K", "杜比视界", "HDR", "4K", "1080P60", "1080P", "720P", "480P"])
        self.quality_sel.setFixedWidth(110)
        self.quality_sel.setCurrentText(common.CURRENT_QUALITY)
        self.quality_sel.currentTextChanged.connect(self._on_quality_change)
        head.addWidget(self.quality_sel)
        layout.addLayout(head)

        # 导出格式（对所有栏生效）
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("导出格式:"))
        self.fmt_json = QCheckBox("JSON")
        self.fmt_json.setChecked(True)
        self.fmt_xlsx = QCheckBox("XLSX")
        self.fmt_xlsx.setChecked(True)
        self.fmt_md = QCheckBox("MD")
        self.fmt_md.setChecked(True)
        for cb in (self.fmt_json, self.fmt_xlsx, self.fmt_md):
            fmt_row.addWidget(cb)
            fmt_row.addSpacing(6)
        fmt_row.addStretch(1)
        layout.addLayout(fmt_row)

        # 三栏
        cols = QHBoxLayout()
        for key, name in PLATFORMS_3:
            cols.addWidget(self._build_column(key, name), 1)
        layout.addLayout(cols, 1)

        self.refresh()

    def _build_column(self, key, name):
        col = QWidget()
        col.setStyleSheet("QWidget { background: #ffffff; border-radius: 8px; }")
        v = QVBoxLayout(col)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(4)

        head = QHBoxLayout()
        head.addWidget(QLabel(name))
        count = QLabel("0")
        count.setStyleSheet("color: #1976d2;")
        head.addStretch(1)
        head.addWidget(count)
        self.cols[key]["count_label"] = count
        v.addLayout(head)

        btns = QHBoxLayout()
        btns.setSpacing(2)
        for text, mode in (("全选", "all"), ("反选", "invert"), ("清除", "none")):
            b = QPushButton(text)
            b.setFixedSize(40, 22)
            b.setStyleSheet("font-size: 10px;")
            b.clicked.connect(lambda _=False, k=key, m=mode: self._col_select(k, m))
            btns.addWidget(b)
        export_btn = QPushButton("📤 导出")
        export_btn.setFixedSize(46, 22)
        export_btn.setStyleSheet("font-size: 10px;")
        export_btn.clicked.connect(lambda _=False, k=key: self._col_export(k))
        btns.addWidget(export_btn)
        dl_btn = QPushButton("📥 下载")
        dl_btn.setFixedSize(46, 22)
        dl_btn.setStyleSheet("font-size: 10px;")
        dl_btn.clicked.connect(lambda _=False, k=key: self._col_download(k))
        btns.addWidget(dl_btn)
        v.addLayout(btns)

        lst = RowCheckList()
        lst.itemDoubleClicked.connect(
            lambda item: self._open_url(item.data(Qt.UserRole) or ""))
        self.cols[key]["list"] = lst
        v.addWidget(lst, 1)
        return col

    # ==================== 渲染 ====================

    def refresh(self):
        # 按平台分到三栏，默认按发布日期倒序（最新在前）
        grouped = {key: [] for key, _ in PLATFORMS_3}
        for info in common.COLLECTED:
            plat = info.platform or "other"
            if plat in grouped:
                grouped[plat].append(info)
            # 抖音/b站/微博以外的平台不展示
        for key, _name in PLATFORMS_3:
            grouped[key].sort(key=_parse_pub, reverse=True)
            self._render_column(key, grouped[key])

    def _render_column(self, key, songs):
        col = self.cols[key]
        lst = col["list"]
        lst.clear()
        col["songs"] = songs
        col["count_label"].setText(f"{len(songs)} 条")
        if not songs:
            item = QListWidgetItem("（无数据）")
            item.setFlags(Qt.NoItemFlags)
            lst.addItem(item)
            return
        for i, info in enumerate(songs, 1):
            title = (info.title or "-")[:30]
            author = info.author or "-"
            kind_tag = "动态" if getattr(info, "kind", "") == "dynamic" else "视频"
            _dt, date_str = _parse_pub(info)
            text = (f"[{i}] {date_str} [{kind_tag}] {title}  @{author}  "
                    f"播放{_fmt(info.play_count)} 赞{_fmt(info.like_count)}")
            url = info.video_url or info.raw_url
            if url:
                text += "   🔗"
            item = QListWidgetItem(text)
            item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, url or "")
            lst.addItem(item)

    def refresh_count(self):
        self.refresh()

    def on_show(self):
        self.refresh()

    def _selected(self, key):
        col = self.cols[key]
        result = []
        for row in range(col["list"].count()):
            item = col["list"].item(row)
            if item.checkState() == Qt.Checked and row < len(col["songs"]):
                result.append(col["songs"][row])
        return result

    def _col_select(self, key, mode):
        lst = self.cols[key]["list"]
        for row in range(lst.count()):
            item = lst.item(row)
            if not (item.flags() & Qt.ItemIsUserCheckable):
                continue
            if mode == "all":
                item.setCheckState(Qt.Checked)
            elif mode == "none":
                item.setCheckState(Qt.Unchecked)
            else:
                item.setCheckState(
                    Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)

    # ==================== 操作 ====================

    def _open_url(self, url):
        """双击行（行尾 🔗）打开对应链接。"""
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception as e:
            QMessageBox.critical(self, "打开失败", str(e))

    def _get_formats(self):
        fmts = []
        if self.fmt_json.isChecked():
            fmts.append("json")
        if self.fmt_xlsx.isChecked():
            fmts.append("xlsx")
        if self.fmt_md.isChecked():
            fmts.append("md")
        return fmts

    def _all_selected(self):
        """聚合三栏内全部勾选条目。"""
        out = []
        for key, _name in PLATFORMS_3:
            out.extend(self._selected(key))
        return out

    def _export_items(self, items, warn_empty="暂无勾选条目"):
        if not items:
            QMessageBox.warning(self, "提示", warn_empty)
            return
        fmts = self._get_formats()
        if not fmts:
            QMessageBox.warning(self, "提示", "请至少选择一种导出格式")
            return

        def work():
            from exporters import export_per_video_flat
            from toolbox.config_manager import get_export_subdir
            import os
            out_dir = str(get_export_subdir("video_data"))
            paths, skipped = export_per_video_flat(items, output_dir=out_dir,
                                                   formats=fmts, skip_existing=True)
            return paths, skipped, os.path.abspath(out_dir)

        def done(result, err):
            if err:
                self.app.toast(str(err), "error")
                return
            paths, skipped, out_dir = result
            if skipped and not paths:
                self.app.toast("文件已存在，请勿重复保存", "info")
                return
            msg = f"已导出 {len(paths)} 个文件到 {out_dir}"
            if skipped:
                msg += f"（跳过 {len(skipped)} 个已存在）"
            self.app.toast(msg, "success")

        run_bg(work, done)

    def _col_export(self, key):
        self._export_items(self._selected(key), "该栏暂无勾选条目")

    def _export_all(self):
        """三平台一次导出：聚合三栏勾选项统一导出。"""
        self._export_items(self._all_selected(), "三栏均无勾选条目，请先勾选要导出的内容")

    def _download_items(self, items):
        """按条目下载（不弹任何二级窗）：B站图文动态直接下图片，视频按 head 画质下拉下载。"""
        if not items:
            return
        bili_items = [i for i in items if i.platform == "bilibili"]
        non_bili = [i for i in items if i.platform != "bilibili"]
        if bili_items:
            # 直接下载，不弹窗（动态图片走 draw 分支）
            bv_items, draw_items = download_ops.classify_bili(bili_items)
            quality = download_ops.quality_to_qn(self.quality_sel.currentText())
            ck = download_ops.get_bilibili_cookie()
            self._do_bilibili_download(bv_items, draw_items, non_bili, quality, ck)
        else:
            self._download_media(items)

    def _col_download(self, key):
        items = self._selected(key)
        if not items:
            QMessageBox.warning(self, "提示", "该栏暂无勾选条目")
            return
        self._download_items(items)

    def _download_all(self):
        """三平台一次下载：聚合三栏勾选项统一下载。"""
        items = self._all_selected()
        if not items:
            QMessageBox.warning(self, "提示", "三栏均无勾选条目，请先勾选要下载的内容")
            return
        self._download_items(items)

    def _on_quality_change(self, value):
        """清晰度改变时同步到全局（供资产管理/视频下载页复用）。"""
        common.CURRENT_QUALITY = value

    def _download_media(self, items):
        """下载图片/live图/视频（media_downloader，非 B站）。抖音按画质重新解析。"""
        quality = self.quality_sel.currentText() if hasattr(self, "quality_sel") else "最高画质"

        def work():
            download_ops.refresh_douyin_urls(items, quality)
            return download_ops.download_media_items(items)

        def done(result, err):
            if err:
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
                return
            stats = result or {}
            self.app.toast(
                f"图片 {stats.get('images', 0)} | Live图 {stats.get('live', 0)} | 视频 {stats.get('videos', 0)}",
                "success")

        run_bg(work, done)

    def _do_bilibili_download(self, bv_items, draw_items, non_bili, quality, ck):
        quality_label = self.quality_sel.currentText() if hasattr(self, "quality_sel") else "最高画质"

        def work():
            return download_ops.do_bilibili_download(
                bv_items, draw_items, non_bili, quality, ck, quality_label=quality_label)

        def done(result, err):
            if err:
                self.app.toast(f"下载失败: {str(err)[:60]}", "error")
                return
            paths, draw_stats, other_stats, errors = result
            msg = download_ops.summarize_download(paths, draw_stats, other_stats, errors)
            self.app.toast(msg, "success" if not errors else "warning")

        run_bg(work, done)

    def _clear(self):
        if not common.COLLECTED:
            return
        common.COLLECTED.clear()
        self.refresh()
