import importlib.util
import tempfile
import unittest
import zipfile
from pathlib import Path

from toolbox import asset_content


class AssetContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_fmt_size(self):
        self.assertEqual(asset_content.fmt_size(512), "512 B")
        self.assertEqual(asset_content.fmt_size(2048), "2.0 KB")
        self.assertEqual(asset_content.fmt_size(2 * 1024 * 1024), "2.0 MB")
        self.assertEqual(asset_content.fmt_size(2 * 1024 ** 3), "2.00 GB")

    def test_read_text_file_fallbacks(self):
        utf8 = self.root / "utf8.txt"
        utf8.write_text("你好", encoding="utf-8")
        gb = self.root / "gb.txt"
        gb.write_bytes("中文".encode("gb18030"))
        self.assertEqual(asset_content.read_text_file(str(utf8)), "你好")
        self.assertEqual(asset_content.read_text_file(str(gb)), "中文")

    def test_md_to_html_basic_blocks(self):
        md = "# 标题\n**粗体** 和 *斜体*\n> 引用\n![图](img/a.png)"
        html = asset_content.md_to_html(md, str(self.root))
        self.assertIn("<h2>标题</h2>", html)
        self.assertIn("<b>粗体</b>", html)
        self.assertIn("<i>斜体</i>", html)
        self.assertIn("<blockquote><p>引用</p></blockquote>", html)
        self.assertIn("file:", html)
        self.assertIn("a.png", html)

    def test_docx_to_html(self):
        path = self.root / "sample.docx"
        xml = ('<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
               '<w:body><w:p><w:r><w:t>你好</w:t></w:r></w:p></w:body></w:document>')
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("word/document.xml", xml)
        self.assertIn("<p>你好</p>", asset_content.docx_to_html(str(path)))

    def test_text_and_files_summary_escape(self):
        self.assertIn("&lt;x&gt;", asset_content.text_preview_html("<x>"))
        html = asset_content.files_summary_html([{"name": "a.txt", "size": 0}])
        self.assertIn("a.txt", html)
        self.assertIn("0 B", html)
        self.assertIn("没有可预览", asset_content.files_summary_html([]))

    @unittest.skipUnless(importlib.util.find_spec("bs4"), "bs4 未安装")
    def test_html_to_content(self):
        html = ('<html><head><title>标题</title></head><body>'
                '<div class="content"><p>正文</p><script>x()</script></div>'
                '</body></html>')
        title, body = asset_content.html_to_content(html)
        self.assertEqual(title, "标题")
        self.assertIn("<p>正文</p>", body)
        self.assertNotIn("script", body)


if __name__ == "__main__":
    unittest.main()
