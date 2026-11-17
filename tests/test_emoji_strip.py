import sys
import unittest

from PySide6.QtWidgets import (
    QApplication, QLineEdit, QPlainTextEdit, QStatusBar, QTextBrowser,
    QTextEdit, QWidget,
)

from gui_qt import emoji_icons


class EmojiStripTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)
        emoji_icons.install()

    def test_strip_emoji_keeps_text(self):
        cleaned = emoji_icons.strip_emoji("📂 扫描到 2 个日期文件夹")
        self.assertNotIn("📂", cleaned)
        self.assertIn("扫描到 2 个日期文件夹", cleaned)

    def test_readonly_plain_text_log_has_no_emoji(self):
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.appendPlainText("📂 扫描到 2 个日期文件夹")
        log.appendPlainText("⚠️ 已是标准命名: demo.mp4")
        text = log.toPlainText()
        self.assertNotIn("📂", text)
        self.assertNotIn("⚠️", text)
        self.assertIn("已是标准命名", text)

    def test_readonly_set_plain_text_has_no_emoji(self):
        log = QPlainTextEdit()
        log.setReadOnly(True)
        log.setPlainText("✅ 完成")
        self.assertNotIn("✅", log.toPlainText())

    def test_readonly_text_edit_log_has_no_emoji(self):
        log = QTextEdit()
        log.setReadOnly(True)
        log.append("📂 扫描到 2 个日期文件夹")
        log.append("⚠️ 已是标准命名: demo.mp4")
        text = log.toPlainText()
        self.assertNotIn("📂", text)
        self.assertNotIn("⚠️", text)
        self.assertIn("已是标准命名", text)

    def test_editable_input_is_preserved(self):
        edit = QPlainTextEdit()
        edit.setPlainText("📁 用户输入")
        self.assertIn("📁", edit.toPlainText())

    def test_text_browser_user_content_is_preserved(self):
        browser = QTextBrowser()
        browser.append("📁 用户正文")
        self.assertIn("📁", browser.toPlainText())

    def test_tooltip_and_placeholder_have_no_emoji(self):
        widget = QWidget()
        widget.setToolTip("⚠️ 提示")
        self.assertNotIn("⚠️", widget.toolTip())
        edit = QLineEdit()
        edit.setPlaceholderText("📁 输入")
        self.assertNotIn("📁", edit.placeholderText())
        self.assertIn("输入", edit.placeholderText())

    def test_status_bar_has_no_emoji(self):
        bar = QStatusBar()
        bar.showMessage("⚠️ 已是标准命名")
        self.assertNotIn("⚠️", bar.currentMessage())
        self.assertIn("已是标准命名", bar.currentMessage())


if __name__ == "__main__":
    unittest.main()
