import json
import tempfile
import unittest
from pathlib import Path

from toolbox import asset_records


def _row(video_id="v1", url="https://cdn.example/video.mp4"):
    row = [""] * 22
    row[0] = "key"
    row[1] = "2026-11-17 12:00:00"
    row[2] = "video"
    row[3] = video_id
    row[4] = "标题"
    row[5] = "作者"
    row[6] = "2026-11-17"
    row[7] = 10
    row[8] = 2
    row[9] = 3
    row[10] = 4
    row[11] = 5
    row[12] = 6
    row[13] = 7
    row[14] = 60
    row[15] = "正文"
    row[16] = "https://cover"
    row[17] = url
    row[18] = "tag"
    row[19] = "分类"
    row[20] = "音乐"
    row[21] = json.dumps({"raw": 1}, ensure_ascii=False)
    return row


class AssetRecordsTests(unittest.TestCase):
    def test_norm_dy_source_link(self):
        self.assertEqual(
            asset_records.norm_dy_source_link("douyin", "v1", "https://cdn/x.mp4"),
            "https://www.douyin.com/video/v1")
        self.assertEqual(
            asset_records.norm_dy_source_link("douyin", "v1", "https://www.douyin.com/video/v1"),
            "https://www.douyin.com/video/v1")
        self.assertEqual(
            asset_records.norm_dy_source_link("weibo", "v1", "https://x"), "https://x")

    def test_rows_to_records_keeps_raw_and_overrides_core(self):
        records = asset_records.rows_to_records([_row()], "douyin")
        self.assertEqual(records[0]["raw"], 1)
        self.assertEqual(records[0]["平台"], "douyin")
        self.assertEqual(records[0]["标题"], "标题")
        self.assertEqual(records[0]["链接"], "https://www.douyin.com/video/v1")

    def test_export_rows_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            path = asset_records.export_rows_json([_row()], "douyin", out_dir)
            self.assertTrue(Path(path).is_file())
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["视频ID"], "v1")
            self.assertTrue(Path(path).name.startswith("数据库导出_douyin_1条_"))


if __name__ == "__main__":
    unittest.main()
