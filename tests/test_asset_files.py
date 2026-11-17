import tempfile
import unittest
from pathlib import Path

from toolbox.asset_files import collect_asset_files


class AssetFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_collects_existing_files_by_category(self):
        base = self.root / "post.json"
        base.write_text("{}", encoding="utf-8")
        (self.root / "post.md").write_text("md", encoding="utf-8")
        (self.root / "post_raw.json").write_text("{}", encoding="utf-8")
        image = self.root / "a.jpg"
        image.write_bytes(b"img")
        video = self.root / "b.mp4"
        video.write_bytes(b"vid")
        missing = self.root / "missing.jpg"

        files = collect_asset_files({
            "file_path": str(base),
            "media_files": [
                {"path": str(image), "type": "image"},
                {"path": str(video), "type": "video"},
                {"path": str(missing), "type": "image"},
            ],
        })
        self.assertEqual(set(files["docs"]), {
            str(base), str(self.root / "post.md"), str(self.root / "post_raw.json")})
        self.assertEqual(files["images"], [str(image)])
        self.assertEqual(files["videos"], [str(video)])

    def test_media_file_path_is_not_counted_as_doc(self):
        media = self.root / "standalone.mp4"
        media.write_bytes(b"vid")
        files = collect_asset_files({
            "file_path": str(media),
            "media_files": [{"path": str(media), "type": "video"}],
        })
        self.assertEqual(files["docs"], [])
        self.assertEqual(files["videos"], [str(media)])


if __name__ == "__main__":
    unittest.main()
