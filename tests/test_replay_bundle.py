import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from toolbox import replay_bundle


def _cast(ts, method="WebcastChatMessage", content="你好", name="观众", gift=None):
    item = {"timestamp": ts, "method": method,
            "user": {"name": name}, "content": content}
    if gift:
        item["gift"] = gift
    return item


class ReplayBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        replay_bundle.cleanup_tmp_dirs()
        self.tmp.cleanup()

    def test_write_zip_contains_expected_files(self):
        casts = [
            _cast(1_700_000_000_000),
            _cast(1_700_000_001_000, "WebcastGiftMessage", "",
                  gift={"name": "玫瑰", "count": 2}),
        ]
        out = self.root / "export.zip"
        replay_bundle.write_replay_zip(
            out, casts, room="123", anchor="主播", title="标题",
            like_count=5, cover_bytes=b"cover-bytes")
        with zipfile.ZipFile(out) as z:
            names = set(z.namelist())
            bundle = json.loads(z.read("data.json").decode("utf-8"))
            all_casts = json.loads(z.read("全量弹幕.json").decode("utf-8"))
        self.assertTrue({"data.json", "全量弹幕.json", "弹幕.txt", "礼物.txt",
                         "cover.jpg"}.issubset(names))
        self.assertNotIn("recording.flv", names)
        self.assertEqual(bundle["roomId"], "123")
        self.assertEqual(bundle["nickname"], "主播")
        self.assertEqual(bundle["likeCount"], 5)
        self.assertEqual(len(all_casts), 2)

    def test_merge_dedups_overlap(self):
        c1 = _cast(1_700_000_000_000, content="一")
        c2 = _cast(1_700_000_001_000, content="二")
        c3 = _cast(1_700_000_002_000, content="三")
        p1 = self.root / "a.zip"
        p2 = self.root / "b.zip"
        replay_bundle.write_replay_zip(p1, [c1, c2], room="1", anchor="主播")
        replay_bundle.write_replay_zip(p2, [c2, c3], room="1", anchor="主播")
        out_dir = self.root / "out"
        result = replay_bundle.merge_replay_zips([str(p1), str(p2)], out_dir)
        self.assertEqual(result["total"], 4)
        self.assertEqual(result["merged"], 3)
        self.assertTrue(Path(result["out"]).is_file())
        with zipfile.ZipFile(result["out"]) as z:
            bundle = json.loads(z.read("data.json").decode("utf-8"))
            merged = json.loads(z.read("全量弹幕.json").decode("utf-8"))
        self.assertEqual(len(merged), 3)
        self.assertEqual(bundle["mergedFrom"], ["a.zip", "b.zip"])

    def test_parse_zip_extracts_video_and_cleanup(self):
        video = self.root / "recording.flv"
        video.write_bytes(b"x" * 2048)
        casts = [_cast(1_700_000_000_000)]
        out = self.root / "with_video.zip"
        replay_bundle.write_replay_zip(
            out, casts, room="1", anchor="主播", cover_bytes=b"cover",
            video_path=str(video))
        parsed = replay_bundle.parse_replay_file(str(out))
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["casts"]), 1)
        self.assertEqual(parsed["cover"], b"cover")
        self.assertTrue(os.path.isfile(parsed["video"]))
        self.assertTrue(os.path.isdir(parsed["zip_dir"]))
        replay_bundle.cleanup_tmp_dirs()
        self.assertFalse(os.path.exists(parsed["zip_dir"]))

    def test_parse_json_bundle(self):
        path = self.root / "bundle.json"
        path.write_text(json.dumps({"_bundle": True, "casts": [_cast(1)]}),
                        encoding="utf-8")
        parsed = replay_bundle.parse_replay_file(str(path))
        self.assertIsNotNone(parsed)
        self.assertEqual(len(parsed["casts"]), 1)


if __name__ == "__main__":
    unittest.main()
