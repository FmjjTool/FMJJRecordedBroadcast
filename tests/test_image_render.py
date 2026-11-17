import tempfile
import unittest
from pathlib import Path

from PIL import Image

from toolbox import image_render as render
from music_account.export_png import export_diff_png as music_diff_png
from tracker.export_png import NUM_METRICS as TRACKER_METRICS
from tracker.export_png import export_diff_png as tracker_diff_png


class ImageRenderTests(unittest.TestCase):
    def test_number_and_delta_formats(self):
        self.assertEqual(render.format_count(12345), "1.2万")
        self.assertEqual(render.format_count(123), "123")
        self.assertEqual(render.format_count(100_000_000), "1.0亿")
        self.assertEqual(render.format_count(None), "—")
        self.assertEqual(render.format_delta(3), ("↑ +3", "success"))
        self.assertEqual(render.format_delta(-3), ("↓ -3", "danger"))
        self.assertEqual(render.format_delta(0), ("—", "flat"))

    def test_output_name_and_atomic_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            path = render.build_output_path(out, "tracker", "测试账号")
            image = Image.new("RGB", (render.WIDTH, 200), "#ffffff")
            render.save_png(image, path)
            self.assertTrue(path.exists())
            self.assertEqual(path.suffix, ".png")
            with Image.open(path) as saved:
                self.assertEqual(saved.size, (render.WIDTH, 200))
            self.assertEqual(list(out.glob(".image_render_*")), [])
            second = render.build_output_path(out, "tracker", "测试账号")
            self.assertNotEqual(second, path)

    def test_report_light_and_dark(self):
        sections = [{
            "title": "测试账号",
            "subtitle": "抖音",
            "rows": [
                ("粉丝数", ("1.2万", "#263449", False),
                 ("1.3万", "#263449", True), ("↑ +345", "#18754e", True)),
            ],
            "notes": ["上次采集：2026-11-16 10:00"],
        }]
        with tempfile.TemporaryDirectory() as tmp:
            for theme in ("light", "dark"):
                path = Path(tmp) / f"{theme}.png"
                result = render.render_report("测试报表", "副标题", sections, path,
                                              theme=theme)
                self.assertEqual(result, str(path))
                with Image.open(path) as image:
                    self.assertEqual(image.size[0], render.WIDTH)
                    self.assertEqual(image.mode, "RGB")

    def test_migrated_tracker_exports_use_1080_canvas(self):
        account = {"name": "测试账号", "platform": "douyin"}
        history = [
            {"fans": 12345, "following": 100, "likes": 200000,
             "video_count": 50, "_time": "2026-11-17 10:00:00"},
            {"fans": 12000, "following": 99, "likes": 190000,
             "video_count": 49, "_time": "2026-11-16 10:00:00"},
        ]
        self.assertGreaterEqual(len(TRACKER_METRICS), 1)
        with tempfile.TemporaryDirectory() as tmp:
            tracker_path = Path(tracker_diff_png(account, history, output_dir=tmp))
            music_path = Path(music_diff_png(account, history, output_dir=tmp))
            for path in (tracker_path, music_path):
                self.assertTrue(path.exists())
                with Image.open(path) as image:
                    self.assertEqual(image.size[0], render.WIDTH)


if __name__ == "__main__":
    unittest.main()
