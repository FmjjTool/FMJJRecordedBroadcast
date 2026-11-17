import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from music.models import SongInfo
from music.scrapers.netease import NeteaseScraper
from music.scrapers.qq_music import QQMusicScraper
from toolbox import music_complete_songs as complete


class _FakeScraper:
    platform = "fake"
    platform_name = "虚构平台"

    @staticmethod
    def extract_song_id(text):
        return "fake-id" if text else ""

    def get_song_detail(self, song_id):
        return SongInfo(song_id=song_id)

    def search(self, keyword, limit=8):
        return [SongInfo(song_name=keyword, singer="", platform=self.platform,
                         platform_name=self.platform_name)]


class CompleteSongTests(unittest.TestCase):
    def test_load_complete_songs_from_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tables.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE rows (doc_key TEXT, tab_id TEXT, row_no INTEGER, data_json TEXT)")
            conn.execute(
                "INSERT INTO rows VALUES (?, ?, ?, ?)",
                (complete.DOC_KEY, complete.TAB_ID, 1, json.dumps({
                    "歌曲名称": "虚构歌曲", "演唱者": "虚构歌手", "歌曲时长": "03:20",
                    "首发日期": "2026-01-02", "所属专辑": "虚构专辑",
                    "QQ音乐": "https://y.qq.com/n/ryqq/songDetail/abc123",
                    "网易云音乐": "https://music.163.com/song?id=123",
                }, ensure_ascii=False)))
            conn.execute(
                "INSERT INTO rows VALUES (?, ?, ?, ?)",
                (complete.DOC_KEY, complete.TAB_ID, 2, json.dumps({"歌曲名称": ""})))
            conn.commit()
            conn.close()

            songs = complete.load_complete_songs(path)
            self.assertEqual(len(songs), 1)
            self.assertEqual(songs[0]["title"], "虚构歌曲")
            self.assertEqual(songs[0]["album"], "虚构专辑")
            self.assertEqual(songs[0]["links"]["qq"],
                             "https://y.qq.com/n/ryqq/songDetail/abc123")

    def test_missing_snapshot_returns_empty(self):
        self.assertEqual(complete.load_complete_songs(Path(tempfile.gettempdir()) / "不存在.db"), [])

    def test_link_first_and_search_fallback(self):
        specs = (("fake", "虚构平台", _FakeScraper, "虚构平台"),)
        linked = complete.fetch_platform_songs(
            {"title": "虚构歌曲", "singer": "虚构歌手",
             "links": {"fake": "https://example.invalid/song/fake-id"}}, specs)
        self.assertEqual(linked["fake"][0].song_id, "fake-id")
        self.assertEqual(linked["fake"][0].song_name, "虚构歌曲")

        fallback = complete.fetch_platform_songs(
            {"title": "虚构歌曲", "singer": "虚构歌手", "links": {}}, specs)
        self.assertEqual(fallback["fake"][0].song_name, "虚构歌手 虚构歌曲")

    def test_netease_extract_song_id(self):
        self.assertEqual(NeteaseScraper.extract_song_id(
            "https://music.163.com/song?id=3456967747"), "3456967747")
        self.assertEqual(NeteaseScraper.extract_song_id("3456967747"), "3456967747")
        self.assertEqual(NeteaseScraper.extract_song_id("https://music.163.com/album?id=1"), "")

    def test_qq_extract_song_id_supports_old_and_new_links(self):
        self.assertEqual(QQMusicScraper.extract_song_id(
            "https://y.qq.com/n/ryqq_v2/songDetail/002bAKZU1TKoXh"), "002bAKZU1TKoXh")
        self.assertEqual(QQMusicScraper.extract_song_id(
            "https://y.qq.com/n/yqq/song/003bUJAd3zIsYc.html"), "003bUJAd3zIsYc")
        self.assertEqual(QQMusicScraper.extract_song_id("002bAKZU1TKoXh"), "002bAKZU1TKoXh")

    def test_build_profile_from_album_and_lyrics(self):
        item = SongInfo(
            song_name="虚构歌曲", singer="甲、乙", album_name="虚构专辑",
            duration=160, release_date="2026-05-01", language="国语",
            track_no=2, cover_url="https://example.invalid/cover.jpg",
            lyrics="[00:00]作词Lyricist：词人甲\n[00:01]作曲Composer：曲人乙\n"
                   "[00:02]出品Presented By：虚构厂牌\n[00:03]正文")
        item.extra["_album"] = {
            "album_name": "虚构专辑", "description": "专辑简介正文",
            "company": "虚构厂牌", "language": "国语",
            "release_date": "2026-05-01",
        }
        cover_item = SongInfo(
            song_name="虚构歌曲", singer="原唱甲、原唱乙", platform="netease",
            platform_name="网易云音乐")
        cover_item.extra["originSongSimpleData"] = {
            "artists": [{"name": "原唱甲"}, {"name": "原唱乙"}]}
        accompaniment = SongInfo(song_name="虚构歌曲 (伴奏)", album_name="虚构专辑")

        profile = complete.build_song_profile(
            {"title": "虚构歌曲"},
            {"qq": [item], "netease": [cover_item], "kuwo": [accompaniment]})

        self.assertEqual(profile["歌曲简介"], "专辑简介正文")
        self.assertEqual(profile["语言"], "汉语（CN）")
        self.assertEqual(profile["首发日期"], "2026-05-01")
        self.assertEqual(profile["原唱艺人"], "原唱甲、原唱乙")
        self.assertEqual(profile["合唱形式"], "双人合唱")
        self.assertEqual(profile["可用伴奏形式"], "纯伴奏")
        self.assertEqual(profile["作词人"], "词人甲")
        self.assertEqual(profile["作曲人"], "曲人乙")
        self.assertEqual(profile["出品方"], "虚构厂牌")


if __name__ == "__main__":
    unittest.main()
