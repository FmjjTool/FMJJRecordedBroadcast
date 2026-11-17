import tempfile
import unittest
from pathlib import Path

from toolbox.asset_groups import extract_date, group_stem, load_dir_groups


class AssetGroupsTests(unittest.TestCase):
    def test_extract_date_only_accepts_standalone_valid_dates(self):
        self.assertEqual(extract_date("20260923_标题"), "20260923")
        self.assertEqual(extract_date("2026-09-23 标题"), "20260923")
        self.assertEqual(extract_date("商品20261340"), "")
        self.assertEqual(extract_date("1234567890123"), "")

    def test_group_stem_strips_attachment_suffixes(self):
        self.assertEqual(group_stem("录播_封面.jpg"), "录播")
        self.assertEqual(group_stem("录播_弹幕.xml"), "录播")
        self.assertEqual(group_stem("录播_live2.mp4"), "录播")
        self.assertEqual(group_stem("录播_raw.json"), "录播")

    def test_dir_rule_groups_article_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "公众号文章"
            article = root / "账号A" / "20260101_文章标题"
            article.mkdir(parents=True)
            (article / "正文.md").write_text("正文", encoding="utf-8")
            (article / "封面.jpg").write_bytes(b"img")

            groups, truncated = load_dir_groups(root, cat="公众号文章")
            self.assertFalse(truncated)
            self.assertEqual(len(groups), 1)
            group = groups[0]
            self.assertEqual(group["title"], "20260101_文章标题")
            self.assertEqual(group["date"], "20260101")
            self.assertEqual(len(group["files"]), 2)
            self.assertEqual((group["n_img"], group["n_doc"]), (1, 1))
            self.assertEqual(Path(group["root_dir"]), article)

    def test_stem_rule_keeps_attachments_with_main_product(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "直播录制"
            live = root / "频道A"
            live.mkdir(parents=True)
            for name in ("20260102_直播.mp4", "20260102_直播_封面.jpg",
                         "20260102_直播_弹幕.xml", "20260102_直播_live1.mp4"):
                (live / name).write_bytes(b"x")

            groups, truncated = load_dir_groups(root, cat="直播录制")
            self.assertFalse(truncated)
            self.assertEqual(len(groups), 1)
            group = groups[0]
            self.assertEqual(group["title"], "20260102_直播")
            self.assertEqual(group["date"], "20260102")
            self.assertEqual(len(group["files"]), 4)
            self.assertEqual((group["n_img"], group["n_vid"], group["n_doc"]), (1, 2, 1))

    def test_danmaku_groups_by_room_and_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "弹幕文件"
            root.mkdir(parents=True)
            (root / "123456_20260103.csv").write_text("a", encoding="utf-8")
            (root / "123456_20260103.zip").write_bytes(b"z")

            groups, truncated = load_dir_groups(root, cat="弹幕文件")
            self.assertFalse(truncated)
            self.assertEqual(len(groups), 1)
            group = groups[0]
            self.assertEqual(group["title"], "123456_20260103")
            self.assertEqual(group["date"], "20260103")
            self.assertEqual(len(group["files"]), 2)

    def test_limit_files_marks_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "歌曲文件"
            root.mkdir(parents=True)
            (root / "a.mp3").write_bytes(b"1")
            (root / "b.mp3").write_bytes(b"2")

            groups, truncated = load_dir_groups(root, limit_files=1, cat="歌曲文件")
            self.assertTrue(truncated)
            self.assertEqual(sum(len(g["files"]) for g in groups), 1)

    def test_missing_root_returns_empty(self):
        groups, truncated = load_dir_groups("Z:/不存在的资产目录", cat="歌曲文件")
        self.assertEqual(groups, [])
        self.assertFalse(truncated)


if __name__ == "__main__":
    unittest.main()
