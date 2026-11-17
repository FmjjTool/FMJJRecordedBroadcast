import tempfile
import unittest
from pathlib import Path

from PIL import Image

from toolbox import image_render as render
from toolbox.asset_preview import render_asset_preview


class AssetPreviewTests(unittest.TestCase):
    def _item(self, media):
        return {
            "platform": "douyin",
            "title": "测试资产标题，用于检查换行与页头",
            "author": "测试作者",
            "play_count": 123456,
            "like_count": 23456,
            "comment_count": 345,
            "publish_time": "2026-11-16 20:00",
            "fetched_at": "2026-11-17 20:00",
            "date_part": "20261116",
            "description": "这是测试正文，包含 emoji 🎵 与较长描述。" * 4,
            "media_files": media,
        }

    def test_preview_with_images_light_and_dark(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = []
            for index, color in enumerate(((200, 120, 80), (90, 150, 210))):
                path = root / f"media_{index}.png"
                Image.new("RGB", (640, 480), color).save(path)
                media.append({"path": str(path), "type": "image"})
            item = self._item(media)
            for theme in ("light", "dark"):
                out = root / f"preview_{theme}.png"
                result = Path(render_asset_preview(item, out, theme=theme))
                self.assertEqual(result, out)
                with Image.open(result) as image:
                    self.assertEqual(image.size[0], render.WIDTH)
                    self.assertEqual(image.mode, "RGB")

    def test_preview_without_media_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = self._item([
                {"path": str(root / "missing.png"), "type": "image"},
                {"path": str(root / "missing.mp4"), "type": "video"},
            ])
            out = root / "preview.png"
            result = render_asset_preview(item, out)
            self.assertTrue(Path(result).exists())
            with Image.open(result) as image:
                self.assertEqual(image.size[0], render.WIDTH)
            self.assertEqual(list(root.glob(".image_render_*")), [])


if __name__ == "__main__":
    unittest.main()
