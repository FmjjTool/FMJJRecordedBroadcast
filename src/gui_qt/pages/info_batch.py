"""批量导入页（PySide6 版）：每行一个 URL，自动按平台分流解析。"""

import threading

from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QTextEdit, QVBoxLayout,
)

from gui_qt.common import _collect, post_to_main, refresh_results_count, run_bg
from gui_qt.pages.base import BasePage


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
        hint = QLabel("每行一个 URL，自动按平台分流解析（Ctrl+Enter 触发）")
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

    def _parse(self):
        urls = [ln.strip() for ln in self.text.toPlainText().splitlines() if ln.strip()]
        if not urls:
            QMessageBox.warning(self, "提示", "请输入 URL")
            return
        self.batch_btn.setEnabled(False)
        self.pause_btn.setEnabled(True)
        self.result_text.setPlainText(f"⏳ 批量解析 {len(urls)} 个...\n")

        def work():
            from platforms import URLRecognizer, get_parser
            ok, fail = 0, 0
            details = []
            for idx, u in enumerate(urls, 1):
                # 暂停门闩：clear 时在 URL 边界停住
                self._pause_evt.wait()
                prefix = f"[{idx}/{len(urls)}]"
                platform = URLRecognizer.recognize(u)
                if not platform:
                    fail += 1
                    details.append(f"  {prefix} [无法识别] {u[:70]}")
                    continue
                parser = get_parser(platform)
                if not parser:
                    fail += 1
                    details.append(f"  {prefix} [未实现] {u[:70]}")
                    continue
                try:
                    info = parser.parse(u)
                except Exception as e:
                    fail += 1
                    details.append(f"  {prefix} [失败] {u[:70]} ({e})")
                    continue
                if info:
                    _collect([info])
                    ok += 1
                    details.append(f"  {prefix} [成功] [{platform}] {info.title[:40] if info.title else u[:40]}")
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
            self.result_text.setPlainText(
                f"✅ 完成: 成功 {ok} / 失败 {fail} / 共 {len(urls)}\n" + "=" * 40 + "\n" + "\n".join(details))
            refresh_results_count(self.app)

        run_bg(work, done)
