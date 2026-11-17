import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from toolbox.asset_images import extract_pic_urls


class AssetImagesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _item(self, data):
        item_json = self.root / "post.json"
        item_json.write_text("{}", encoding="utf-8")
        (self.root / "post_raw.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"file_path": str(item_json)}

    def test_weibo_pics_and_live(self):
        item = self._item({
            "pics": [{"original": {"url": "https://img/1.jpg"},
                      "videoSrc": "https://player/1"}],
            "live_photo": "https://player/2",
        })
        with patch("platforms.weibo.WeiboParser._extract_livephoto_url",
                   side_effect=lambda url: url.replace("player", "mov")):
            urls, lives = extract_pic_urls(item)
        self.assertEqual(urls, ["https://img/1.jpg"])
        # live_photo 只在 pics 未提取到 live 视频时兜底（保持原行为）
        self.assertEqual(lives, ["https://mov/1"])

    def test_weibo_pic_ids_fallback(self):
        urls, lives = extract_pic_urls(self._item({"pic_ids": ["abc"]}))
        self.assertEqual(urls, ["https://wx1.sinaimg.cn/large/abc.jpg"])
        self.assertEqual(lives, [])

    def test_bilibili_opus_and_generic_images(self):
        item = self._item({
            "modules": {"module_dynamic": {"major": {"opus": {"pics": [
                {"url": "https://i0/b.jpg", "live_url": "https://i0/live.mov"}]}}}}
        })
        urls, lives = extract_pic_urls(item)
        self.assertEqual(urls, ["https://i0/b.jpg"])
        self.assertEqual(lives, ["https://i0/live.mov"])

        item = self._item({"images": [{"url_list": ["https://d/1.jpg"]},
                                      {"url": "https://d/2.jpg"}, "https://d/3.jpg"]})
        urls, _ = extract_pic_urls(item)
        self.assertEqual(urls, ["https://d/1.jpg", "https://d/2.jpg", "https://d/3.jpg"])

    def test_missing_raw_returns_empty(self):
        item_json = self.root / "none.json"
        item_json.write_text("{}", encoding="utf-8")
        self.assertEqual(extract_pic_urls({"file_path": str(item_json)}), ([], []))


if __name__ == "__main__":
    unittest.main()
