"""批量导入页（PySide6 版）：每行一个 URL 批量解析（多平台）+ 批量下载（画质可选）。

- 解析：B站/抖音/微博 完整解析器 + 小红书/快手/好看/微视/梨视频/皮皮搞笑（multi_downloader）
- 下载：本次解析成功的条目一键批量下载（B站按画质走 playurl，其余走 media_downloader）
"""

import threading

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout,
)

from gui_qt import common, download_ops
from gui_qt.common import _collect, post_to_main, refresh_results_count, run_bg
from gui_qt.pages.base import BasePage


class InfoBatchPage(BasePage):
    def __init__(self, app):
        super().__init__(app)
        # 暂停门闩：set=放行 / clear=暂停（解析循环在 URL 边界等待）
        self._pause_evt = threading.Event()
        self._pause_evt.set()
        # 本次批量解析成功的条目（供「下载全部」直接用）
        self._parsed = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("📋 批量导入")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("每行一个 URL，自动按平台分流解析（Ctrl+Enter 触发）；"
                      "解析完成后可一键批量下载（画质可选）")
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
        self.dl_btn = QPushButton("⬇ 下载全部")
        self.dl_btn.setEnabled(False)
        self.dl_btn.setStyleSheet("background: #2e7d32; color: white;")
        self.dl_btn.setToolTip("批量下载本次解析成功的条目（按下方所选画质）")
        self.dl_btn.clicked.connect(self._download_parsed)
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

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        layout.addWidget(self.result_text, 1)

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
            self.result_text.append("⏸ 已暂停（当前 URL 解析完成后停住）")
        else:
            self._pause_evt.set()
            self.pause_btn.setText("⏸ 暂停")
            self.pause_btn.setStyleSheet("")
            self.result_text.append("▶ 已继续解析")

    def _on_quality_change(self, value):
        """清晰度改变时同步到全局（已获取/单个解析/资产管理页共用）。"""
        common.CURRENT_QUALITY = value

    def _parse(self):
        urls = [ln.strip() for ln in self.text.toPlainText().splitlines() if ln.strip()]
        if not urls:
            QMessageBox.warning(self, "提示", "请输入 URL")
            return
        self.batch_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.dl_btn.setEnabled(False)
        self._parsed = []
        self.result_text.setPlainText(f"⏳ 批量解析 {len(urls)} 个...\n")

        def work():
            ok, fail = 0, 0
            details = []
            for idx, u in enumerate(urls, 1):
                # 暂停门闩：clear 时在 URL 边界停住
                self._pause_evt.wait()
                prefix = f"[{idx}/{len(urls)}]"
                try:
                    info = download_ops.parse_url_info(u)
                except ValueError as e:
                    fail += 1
                    details.append(f"  {prefix} [无法识别] {u[:70]} ({e})")
                    continue
                except Exception as e:
                    fail += 1
                    details.append(f"  {prefix} [失败] {u[:70]} ({e})")
                    continue
                if info:
                    _collect([info])
                    self._parsed.append(info)
                    ok += 1
                    details.append(
                        f"  {prefix} [成功] [{info.platform}] "
                        f"{info.title[:40] if info.title else u[:40]}")
                else:
                    fail += 1
                    details.append(f"  {prefix} [失败] {u[:70]}")
                post_to_main(lambda d="\n".join(details), o=ok, f=fail: self.result_text.setPlainText(
                    f"⏳ 批量解析中... 成功 {o} / 失败 {f} / 共 {len(urls)}\n" + "=" * 40 + "\n" + d))
            return ok, fail, details

        def done(result, err):
            self.batch_btn.setEnabled(True)
            self.pause_btn.setEnabled(False)
            self._pause_evt.set()
            self.pause_btn.setText("⏸ 暂停")
            self.pause_btn.setStyleSheet("")
            if err:
                self.result_text.setPlainText(f"❌ {err}")
                return
            ok, fail, details = result
            self.dl_btn.setEnabled(bool(self._parsed))
            self.result_text.setPlainText(
                f"✅ 完成: 成功 {ok} / 失败 {fail} / 共 {len(urls)}\n" + "=" * 40 + "\n" + "\n".join(details)
                + (f"\n\n💡 可点击「⬇ 下载全部」批量下载本次解析成功的 {len(self._parsed)} 个条目"
                   if self._parsed else ""))
            refresh_results_count(self.app)

        run_bg(work, done)

    # ==================== 批量下载 ====================

    def _download_parsed(self):
        items = list(self._parsed)
        if not items:
            QMessageBox.warning(self, "提示", "本次尚未解析成功任何条目，请先批量解析")
            return
        self.dl_btn.setEnabled(False)
        self.result_text.append(f"\n⏳ 开始下载 {len(items)} 个条目（画质: {self.quality_sel.currentText()}）...")
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
            self.dl_btn.setEnabled(True)
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
        ck = download_ops.get_bilibili_cookie()
        bv_items, draw_items = download_ops.classify_bili(bili_items)
        quality = download_ops.quality_to_qn(self.quality_sel.currentText())
        quality_label = self.quality_sel.currentText()

        def work():
            return download_ops.do_bilibili_download(
                bv_items, draw_items, non_bili, quality, ck,
                quality_label=quality_label, log=self._log)

        def done(result, err):
            self.dl_btn.setEnabled(True)
            if err:
                self._log(f"❌ 下载失败: {err}")
                return
            paths, draw_stats, other_stats, errors = result
            msg = download_ops.summarize_download(paths, draw_stats, other_stats, errors)
            self._log(f"✅ {msg}")
            if errors:
                self._log("失败列表:\n" + "\n".join(errors[:5]))

        run_bg(work, done)

    # ==================== 日志 ====================

    def _log(self, text):
        """线程安全地往结果框追加一行（后台线程经 post_to_main 回主线程）。"""
        def apply():
            try:
                self.result_text.append(text)
                self.result_text.ensureCursorVisible()
            except Exception:
                pass
        post_to_main(apply)
