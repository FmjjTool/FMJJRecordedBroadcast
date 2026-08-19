"""单个解析页（PySide6 版）：输入 B站/抖音/微博 链接，自动识别平台解析。"""

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QTextEdit, QVBoxLayout,
)

from gui_qt.common import _collect, _info_text, refresh_results_count, run_bg
from gui_qt.pages.base import BasePage


class InfoParsePage(BasePage):
    def __init__(self, app):
        super().__init__(app)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("🔍 单个解析")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)
        hint = QLabel("输入 B站 / 抖音 / 微博 的视频、动态或帖子链接，自动识别平台并解析")
        hint.setStyleSheet("color: #888;")
        layout.addWidget(hint)

        row = QHBoxLayout()
        self.url_entry = QLineEdit()
        self.url_entry.setPlaceholderText("https://www.bilibili.com/video/BV...")
        self.url_entry.returnPressed.connect(self._parse)
        row.addWidget(self.url_entry, 1)
        self.parse_btn = QPushButton("解析")
        self.parse_btn.clicked.connect(self._parse)
        row.addWidget(self.parse_btn)
        layout.addLayout(row)

        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        self.result_text.setPlaceholderText("解析结果将显示在这里")
        layout.addWidget(self.result_text, 1)

    def _parse(self):
        url = self.url_entry.text().strip()
        if not url:
            QMessageBox.warning(self, "提示", "请输入 URL")
            return
        self.parse_btn.setEnabled(False)
        self.result_text.setPlainText("⏳ 解析中...\n")

        def work():
            from platforms import URLRecognizer, get_parser
            platform = URLRecognizer.recognize(url)
            if not platform:
                raise ValueError("无法识别 URL 平台")
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
                self.result_text.setPlainText(f"❌ 失败: {err}")
                return
            self.result_text.setPlainText("✅ 获取成功\n" + "=" * 40 + "\n" + _info_text(result))
            refresh_results_count(self.app)

        run_bg(work, done)
