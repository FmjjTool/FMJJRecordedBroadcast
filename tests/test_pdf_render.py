import sys
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from gui_qt.pdf_render import render_pdf


class PdfRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_render_pdf_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "article.pdf"
            render_pdf("<html><body><h1>标题</h1><p>正文</p></body></html>",
                       str(out))
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 0)
            self.assertTrue(out.read_bytes().startswith(b"%PDF"))


if __name__ == "__main__":
    unittest.main()
