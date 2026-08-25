"""批量导入页（PySide6 版）：每行一个 URL 批量解析（多平台）+ 勾选条目批量下载。

- 解析：B站/抖音/微博 完整解析器 + 小红书/快手/好看/微视/梨视频/皮皮搞笑（multi_downloader）
- 解析结果为可勾选列表（默认全勾选，支持全选/反选/清除），双击行打开原链接
- 下载：仅下载勾选的条目（B站按画质走 playurl，其余走 media_downloader）
"""

import threading
import webbrowser

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QListWidgetItem, QMessageBox,
    QPushButton, QTextEdit, QVBoxLayout,
)

from gui_qt import common, download_ops
from gui_qt.common import _collect, post_to_main, refresh_results_count, run_bg
from gui_qt.pages.base import BasePage
from gui_qt.row_widgets import RowCheckList

# 平台英文键 → 中文名（列表行显示用）
PLAT_CN = {
    "douyin": "抖音",
    "bilibili": "B站",
    "weibo": "微博",
    "xiaohongshu": "小红书",
    "kuaishou": "快手",
    "haokan": "好看视频",
    "weishi": "微视",
    "pear": "梨视频",
    "pipigx": "皮皮搞笑",
}


class InfoBatchPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        # 暂停门闩：set=放行 / clear=暂停（解析循环在 URL 边界等待）
        self._pause_evt = threading.Event()
        self._pause_evt.set()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("📋 批量导入")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("每行一个 URL，自动按平台分流解析（Ctrl+Enter 触发）；"
                      "解析完成后勾选需要下载的条目，点「下载勾选」批量下载")
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        self.text = QTextEdit()
        self.text.setPlaceholderText("每行一个 URL...")
        self.text.setFixedHeight(150)
        layout.addWidget(self.text)
        # QTextEdit 回车会换行，用 Ctrl+Enter 触发批量解析（对应原版 <Control-Return>）
        shortcut = QShortcut(QKeySequence("Ctrl+Return"), self.text)
        shortcut.activated.connect(self._parse)

        btn_row = QHBoxLayout()
        self.batch_btn = QPushButton("批量解析")
        self.batch_btn.clicked.connect(self._parse)
        btn_row.addWidget(self.batch_btn)
        self.pause_btn = QPushButton("⏸ 暂停")
        self.pause_btn.setEnabled(False)
        self.pause_btn.setToolTip("暂停后当前 URL 解析完即停住，点「继续」恢复")
        self.pause_btn.clicked.connect(self._toggle_pause)
        btn_row.addWidget(self.pause_btn)
        import_btn = QPushButton("从文件导入")
        import_btn.clicked.connect(self._import_file)
        btn_row.addWidget(import_btn)
        btn_row.addStretch(1)
        self.dl_btn = QPushButton("⬇ 下载勾选")
        self.dl_btn.setEnabled(False)
        self.dl_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.dl_btn.setToolTip("仅下载下方列表中勾选的条目（按所选画质）")
        self.dl_btn.clicked.connect(self._download_checked)
        btn_row.addWidget(self.dl_btn)
        btn_row.addWidget(QLabel("画质:"))
        self.quality_sel = QComboBox()
        self.quality_sel.addItems(
            ["最高画质", "8K", "杜比视界", "HDR", "4K", "1080P60", "1080P", "720P", "480P"])
        self.quality_sel.setFixedWidth(110)
        self.quality_sel.setCurrentText(common.CURRENT_QUALITY)
        self.quality_sel.currentTextChanged.connect(self._on_quality_change)
        btn_row.addWidget(self.quality_sel)
        layout.addLayout(btn_row)

        # 解析结果列表头：计数 + 全选/反选/清除
        list_head = QHBoxLayout()
        self.count_label = QLabel("已解析 0 条 · 勾选 0 条")
        self.count_label.setStyleSheet("color: #1976d2;")
        list_head.addWidget(self.count_label)
        list_head.addStretch(1)
        for text, mode in (("全选", "all"), ("反选", "invert"), ("清除", "none")):
            b = QPushButton(text)
            b.setFixedSize(40, 22)
            b.setStyleSheet("font-size: 10px;")
            b.clicked.connect(lambda _=False, m=mode: self._select_rows(m))
            list_head.addWidget(b)
        layout.addLayout(list_head)

        # 解析结果：可勾选列表（点整行任意位置切换勾选，双击打开原链接）
        self.parsed_list = RowCheckList()
        self.parsed_list.itemChanged.connect(lambda _item: self._update_counts())
        self.parsed_list.itemDoubleClicked.connect(self._open_row_url)
        layout.addWidget(self.parsed_list, 1)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setFixedHeight(110)
        self.log_text.setPlaceholderText("解析 / 下载日志")
        layout.addWidget(self.log_text)

    # ==================== 文件导入 / 暂停 / 画质 ====================

    def _import_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 URL 列表文件", "", "文本文件 (*.txt);;所有文件 (*.*)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                urls = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
        except Exception as e:
            QMessageBox.critical(self, "错误", f"读取失败: {e}")
            return
        self.text.setPlainText("\n".join(urls))

    def _toggle_pause(self):
        if self._pause_evt.is_set():
            self._pause_evt.clear()
            self.pause_btn.setText("▶ 继续")
            self.pause_btn.setStyleSheet("background: #eab308; color: white;")
            self._log("⏸ 已暂停（当前 URL 解析完成后停住）")
        else:
            self._pause_evt.set()
            self.pause_btn.setText("⏸ 暂停")
            self.pause_btn.setStyleSheet("")
            self._log("▶ 已继续解析")

    def _on_quality_change(self, value):
        """清晰度改变时同步到全局（已获取/单个解析/资产管理页共用）。"""
        common.CURRENT_QUALITY = value

    # ==================== 解析 ====================

    def _parse(self):
        urls = [ln.strip() for ln in self.text.toPlainText().splitlines() if ln.strip()]
        if not urls:
            QMessageBox.warning(self, "提示", "请输入 URL")
            return
        self.batch_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.dl_btn.setEnabled(False)
        self._set_parsed_rows([])
        self.log_text.setPlainText(f"⏳ 批量解析 {len(urls)} 个...\n")

        def work():
            ok, fail = 0, 0
            for idx, u in enumerate(urls, 1):
                # 暂停门闩：clear 时在 URL 边界停住
                self._pause_evt.wait()
                prefix = f"[{idx}/{len(urls)}]"
                try:
                    info = download_ops.parse_url_info(u)
                except Exception as e:
                    fail += 1
                    self._log(f"{prefix} ❌ {u[:70]} ({e})")
                    continue
                if info:
                    _collect([info])
                    ok += 1
                    title = info.title[:40] if info.title else u[:40]
                    self._append_row(info)
                    self._log(f"{prefix} ✅ [{info.platform}] {title}")
                else:
                    fail += 1
                    self._log(f"{prefix} ❌ {u[:70]}")
            return ok, fail, urls

        def done(result, err):
            self.batch_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self._pause_evt.set()
            self.pause_btn.setText("⏸ 暂停")
            self.pause_btn.setStyleSheet("")
            if err:
                self._log(f"❌ {err}")
                return
            ok, fail, urls = result
            self._log(f"✅ 解析完成: 成功 {ok} / 失败 {fail} / 共 {len(urls)}"
                      + ("，请勾选需要下载的条目" if ok else ""))
            refresh_results_count(self.app)

        run_bg(work, done)

    # ==================== 勾选列表 ====================

    def _row_text(self, info) -> str:
        plat = PLAT_CN.get(info.platform, info.platform or "未知")
        kind_tag = ""
        if info.platform == "bilibili":
            kind_tag = "[动态] " if info.kind == "draw" else "[视频] "
        title = info.title or info.video_id or ""
        author = f" @{info.author}" if info.author else ""
        return f"{kind_tag}[{plat}] {title}{author}"

    def _set_parsed_rows(self, infos):
        """重建解析结果列表（默认全勾选）。"""
        lst = self.parsed_list
        lst.blockSignals(True)
        lst.clear()
        for info in infos:
            it = QListWidgetItem(self._row_text(info))
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, info)
            it.setToolTip((info.raw_url or "") + (f"\n{info.publish_time}" if info.publish_time else ""))
            lst.addItem(it)
        lst.blockSignals(False)
        self._update_counts()

    def _append_row(self, info):
        """解析成功一条即追加一行（后台线程经 post_to_main 回主线程调用）。"""
        def apply():
            lst = self.parsed_list
            lst.blockSignals(True)
            it = QListWidgetItem(self._row_text(info))
            it.setCheckState(Qt.Checked)
            it.setData(Qt.UserRole, info)
            it.setToolTip((info.raw_url or "") + (f"\n{info.publish_time}" if info.publish_time else ""))
            lst.addItem(it)
            lst.blockSignals(False)
            self._update_counts()
        post_to_main(apply)

    def _select_rows(self, mode):
        lst = self.parsed_list
        if lst.count() == 0:
            return
        for i in range(lst.count()):
            it = lst.item(i)
            if mode == "all":
                it.setCheckState(Qt.Checked)
            elif mode == "invert":
                it.setCheckState(Qt.Unchecked if it.checkState() == Qt.Checked else Qt.Checked)
            else:
                it.setCheckState(Qt.Unchecked)
        self._update_counts()

    def _checked_infos(self) -> list:
        infos = []
        for i in range(self.parsed_list.count()):
            it = self.parsed_list.item(i)
            if it.checkState() == Qt.Checked:
                info = it.data(Qt.UserRole)
                if info is not None:
                    infos.append(info)
        return infos

    def _update_counts(self):
        total = self.parsed_list.count()
        checked = len(self._checked_infos())
        self.count_label.setText(f"已解析 {total} 条 · 勾选 {checked} 条")
        self.dl_btn.setEnabled(checked > 0)

    def _open_row_url(self, item):
        info = item.data(Qt.UserRole)
        url = getattr(info, "raw_url", "") or ""
        if url:
            webbrowser.open(url)

    # ==================== 下载（勾选条目） ====================

    def _download_checked(self):
        items = self._checked_infos()
        if not items:
            QMessageBox.warning(self, "提示", "请先勾选需要下载的条目")
            return
        self.dl_btn.setEnabled(False)
        self._log(f"⏳ 开始下载勾选的 {len(items)} 个条目（画质: {self.quality_sel.currentText()}）...")
        bili_items, non_bili = download_ops.resolve_items(items)
        if bili_items:
            self._download_bilibili(bili_items, non_bili)
        else:
            self._download_media(items)

    def _download_media(self, items):
        quality_label = self.quality_sel.currentText()

        def work():
            # 抖音：按所选画质重新解析拿新直链（存量直链有时效性）
            download_ops.refresh_douyin_urls(items, quality_label)
            return download_ops.download_media_items(items, log=self._log)

        def done(result, err):
            if err:
                self._log(f"❌ 下载失败: {err}")
            else:
                stats = result or {}
                msg = (f"✅ 下载完成: 图片 {stats.get('images', 0)} | "
                       f"Live图 {stats.get('live', 0)} | 视频 {stats.get('videos', 0)}")
                if stats.get("skipped", 0):
                    msg += f" | 跳过已下载 {stats['skipped']}"
                self._log(msg)
            self._update_counts()

        run_bg(work, done)

    def _download_bilibili(self, bili_items, non_bili):
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
            else:
                paths, draw_stats, other_stats, errors = result
                msg = download_ops.summarize_download(paths, draw_stats, other_stats, errors)
                self._log(f"✅ {msg}")
                if errors:
                    self._log("失败列表:\n" + "\n".join(errors[:5]))
            self._update_counts()

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
