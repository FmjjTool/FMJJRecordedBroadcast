import unittest

from toolbox import music_tag_match as m


class MusicTagMatchTests(unittest.TestCase):
    def test_auto_map_field(self):
        self.assertEqual(m.auto_map_field("歌名"), "title")
        self.assertEqual(m.auto_map_field("文件"), "filename")
        self.assertEqual(m.auto_map_field("专辑"), "album")
        self.assertEqual(m.auto_map_field(""), "")
        self.assertEqual(m.auto_map_field("无关列"), "")

    def test_cell_and_norm_helpers(self):
        self.assertEqual(m.cell_str(None), "")
        self.assertEqual(m.cell_str(3.0), "3")
        self.assertEqual(m.cell_str(" x "), "x")
        self.assertEqual(m.norm_key("A - B_·C"), "abc")
        self.assertEqual(m.pair_score("abc", "abc"), 50)
        self.assertEqual(m.pair_score("ab", "abc"), 35)
        self.assertEqual(m.pair_score("ab", "xy"), 0)

    def test_filename_match_is_highest(self):
        row = {
            "path": "C:/Music/Artist - Title.mp3",
            "tags": {"title": "Title", "artist": "Artist", "album": "Album"},
        }
        self.assertEqual(m.xlsx_match_score(row, {"filename": "Artist - Title.mp3"}), 100)
        self.assertEqual(m.xlsx_match_score(row, {"filename": "Other.mp3"}), 0)

    def test_tag_match_scores_artist_and_album(self):
        row = {
            "path": "C:/Music/Artist - Title.mp3",
            "tags": {"title": "Title", "artist": "Artist", "album": "Album"},
        }
        score = m.xlsx_match_score(row, {
            "title": "Title", "artist": "Artist", "album": "Album"})
        self.assertEqual(score, 90)
        low = m.xlsx_match_score(row, {"title": "Other"})
        self.assertLess(low, m.XLSX_MATCH_MIN)


if __name__ == "__main__":
    unittest.main()
