import tempfile
import unittest
from pathlib import Path

from toolbox import asset_scan


class AssetScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "a.jpg").write_bytes(b"a")
        (self.root / "b.mp4").write_bytes(b"b")
        (self.root / "c.txt").write_text("c", encoding="utf-8")
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "d.mp3").write_bytes(b"d")
        skip = self.root / "__pycache__"
        skip.mkdir()
        (skip / "e.jpg").write_bytes(b"e")

    def tearDown(self):
        self.tmp.cleanup()

    def test_classify(self):
        self.assertEqual(asset_scan.classify("a.JPG"), "image")
        self.assertEqual(asset_scan.classify("b.mp4"), "video")
        self.assertEqual(asset_scan.classify("d.mp3"), "audio")
        self.assertEqual(asset_scan.classify("c.txt"), "")

    def test_depth_zero_keeps_current_level_only(self):
        files = asset_scan.scan_assets(str(self.root), depth=0)
        self.assertEqual([f["name"] for f in files], ["a.jpg", "b.mp4"])
        self.assertTrue(all(f["rel_dir"] == "" for f in files))

    def test_depth_one_recurses_and_skips_cache_dirs(self):
        files = asset_scan.scan_assets(str(self.root), depth=1)
        names = [f["name"] for f in files]
        self.assertIn("d.mp3", names)
        self.assertNotIn("e.jpg", names)
        sub_item = next(f for f in files if f["name"] == "d.mp3")
        self.assertEqual(sub_item["rel_dir"], "sub")

    def test_limit(self):
        files = asset_scan.scan_assets(str(self.root), limit=1, depth=1)
        self.assertEqual(len(files), 1)


if __name__ == "__main__":
    unittest.main()
