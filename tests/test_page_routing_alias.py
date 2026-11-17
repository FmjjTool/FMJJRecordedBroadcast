import unittest

from gui_qt.main_window import PAGE_ALIASES, PAGE_CLASSES, _resolve_page_key


class PageRoutingAliasTests(unittest.TestCase):
    def test_legacy_test_page_route_maps_to_douyin_danmaku(self):
        self.assertEqual(_resolve_page_key("test_page"), "douyin_danmaku")
        self.assertEqual(_resolve_page_key("douyin_danmaku"), "douyin_danmaku")
        self.assertIn("douyin_danmaku", PAGE_CLASSES)
        self.assertNotIn("test_page", PAGE_CLASSES)
        self.assertEqual(PAGE_ALIASES["test_page"], "douyin_danmaku")


if __name__ == "__main__":
    unittest.main()
