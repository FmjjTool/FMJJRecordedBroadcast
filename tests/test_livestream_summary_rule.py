# -*- coding: utf-8 -*-
"""纯规则直播总结的清洗、纠错、日期、归类与输出回归；不联网、不依赖模型。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from toolbox import livestream_summary_rule as rule


SAMPLE = """蜂蜜酒精 2026-08-17 直播
大家好，我是蜂蜜酒精。
下周我要发布一首新歌，叫《夏天的风》。
我今天用了新的双眼皮贴，眼妆也换了。
我觉得恋爱最重要的是沟通。
大学的时候我经常逃课。
四姐今天也在直播间帮忙。
下面给大家唱一首《月老今天不营业》。
我身高168厘米，体重98斤，今年25岁，生日是8月17日。"""


class TranscriptTest(unittest.TestCase):
    def test_strip_srt(self):
        text = ("1\n00:00:01,000 --> 00:00:03,000\n大家好\n\n"
                "2\n00:00:03,500 --> 00:00:05,000\n我是蜂蜜酒精\n")
        self.assertEqual(rule.strip_srt(text), "大家好\n我是蜂蜜酒精")

    def test_plain_text_kept(self):
        self.assertEqual(rule.strip_srt("第一句。\n第二句。"), "第一句。\n第二句。")

    def test_read_gb18030(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "直播.txt"
            path.write_bytes("蜂蜜酒精直播".encode("gb18030"))
            self.assertEqual(rule.read_transcript(path), "蜂蜜酒精直播")


class DateTest(unittest.TestCase):
    def test_filename_first(self):
        value, source = rule.extract_date("封茗囧菌_2026-08-17_直播.txt",
                                          "正文说2026年9月1日")
        self.assertEqual((value, source), ("20260817", "文件名"))

    def test_compact_filename(self):
        self.assertEqual(rule.extract_date("20260817直播.srt", "")[0], "20260817")

    def test_text_date(self):
        value, source = rule.extract_date("直播.txt", "今天是2026年8月17日，欢迎。")
        self.assertEqual((value, source), ("20260817", "正文"))

    def test_invalid_date_ignored(self):
        self.assertEqual(rule.extract_date("2026-02-30.txt", "")[0], None)

    def test_unknown_date_title(self):
        self.assertEqual(rule.build_title(None),
                         "封茗囧菌暂未公开（直播日期）直播总结")


class CorrectionTest(unittest.TestCase):
    def test_map_contains_core_name(self):
        self.assertEqual(rule.load_corrections().get("蜂蜜酒精"), "封茗囧菌")

    def test_standard_term_not_nested(self):
        pairs = rule.load_corrections()
        fixed = rule.apply_corrections("蜂蜜酒精和封茗囧菌说话", pairs)
        self.assertEqual(fixed, "封茗囧菌和封茗囧菌说话")

    def test_site_name_completed(self):
        pairs = rule.load_corrections()
        self.assertEqual(rule.apply_corrections("封茗囧菌咨询站", pairs), "封茗囧菌咨讯站")

    def test_split_name_merged(self):
        pairs = rule.load_corrections()
        self.assertEqual(rule.apply_corrections("封 茗囧菌", pairs), "封茗囧菌")


class ExtractTest(unittest.TestCase):
    def test_sentences(self):
        items = rule.split_sentences("第一句话。第二句话！第三句？")
        self.assertEqual(items, ["第一句话。", "第二句话！", "第三句？"])

    def test_songs(self):
        songs = rule._song_lines(["下面唱一首《月老今天不营业》。",
                                  "再唱一段《润》的片段。"])
        self.assertEqual(songs, ["《月老今天不营业》", "《润》（片段）"])

    def test_profile(self):
        data = rule._profile_lines(
            "我身高168厘米，体重98斤，今年25岁，生日是8月17日，"
            "星座是狮子座，MBTI是INFP，民族是汉族。")
        self.assertEqual(data["身高"], "168厘米")
        self.assertEqual(data["体重"], "98斤")
        self.assertEqual(data["年龄"], "25岁")
        self.assertEqual(data["生日"], "8月17日")
        self.assertEqual(data["星座"], "狮子座")
        self.assertEqual(data["MBTI"], "INFP")
        self.assertEqual(data["民族"], "汉族")

    def test_profile_defaults(self):
        data = rule._profile_lines("今天没有提这些。")
        self.assertTrue(all(value == "未公开" for value in data.values()))


class SummarizeTest(unittest.TestCase):
    def test_full_output(self):
        output, stats = rule.summarize_text(SAMPLE, "封茗囧菌_2026-08-17_直播.txt")
        lines = output.splitlines()
        self.assertEqual(lines[0], "封茗囧菌 20260817 直播总结")
        self.assertEqual(lines[1], rule.FIXED_LINE)
        self.assertEqual(lines[2], rule.FIXED_LINK)
        self.assertEqual(lines[-2], rule.FIXED_LINE)
        self.assertEqual(lines[-1], rule.FIXED_LINK)
        for level in range(1, 8):
            self.assertIn(rule.DIMENSION_TITLES[level], output)
        self.assertNotIn("蜂蜜酒精", output)
        self.assertIn("[新歌发布]", output)
        self.assertIn("[妆造分享]", output)
        self.assertIn("[情感态度]", output)
        self.assertIn("4 级：直播中提及的「学校相关事件」", output)
        self.assertIn("[工作人员互动]", output)
        self.assertIn("6 级：直播中演唱的歌曲\n《月老今天不营业》\n", output)
        self.assertIn("[身高]：168厘米", output)
        self.assertIn("[年龄]：25岁", output)
        self.assertEqual(stats["date"], "20260817")
        self.assertEqual(stats["date_source"], "文件名")

    def test_empty_dimensions(self):
        output, _ = rule.summarize_text("大家好。", "无日期.txt")
        self.assertIn("封茗囧菌暂未公开（直播日期）直播总结", output)
        self.assertIn("1 级：待发布内容详情（未提及）", output)
        self.assertIn("未演唱歌曲", output)
        self.assertIn("[身高]：未公开", output)

    def test_max_per_dim(self):
        text = "。".join(["下周我要发布新歌"] * 5) + "。"
        output, _ = rule.summarize_text(text, "直播.txt", max_per_dim=2)
        self.assertEqual(output.count("[新歌发布]"), 2)

    def test_safe_filename(self):
        self.assertEqual(rule._safe_filename("封茗囧菌 20260817 直播总结"),
                         "封茗囧菌 20260817 直播总结.txt")
        self.assertEqual(rule._safe_filename('a/b:c*d?'), "a_b_c_d_.txt")

    def test_main_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "20260817_直播.txt"
            source.write_text(SAMPLE, encoding="utf-8")
            code = rule.main([str(source)])
            self.assertEqual(code, 0)
            target = Path(tmp) / "封茗囧菌 20260817 直播总结.txt"
            self.assertTrue(target.is_file())
            self.assertIn(rule.FIXED_LINK, target.read_text(encoding="utf-8"))

    def test_main_missing_file(self):
        self.assertEqual(rule.main([str(Path(tempfile.gettempdir()) / "不存在_xyz.txt")]), 1)


if __name__ == "__main__":
    unittest.main()
