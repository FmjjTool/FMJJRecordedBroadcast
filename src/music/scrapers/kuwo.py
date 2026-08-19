"""
======================================================================
  酷我音乐 (kuwo.cn) 抓取器
  API: search.kuwo.cn + m.kuwo.cn  |  数据: 搜索+歌词(移动端)+专辑+播放直链+全extra
======================================================================
"""

from __future__ import annotations

import re
from typing import Optional

from ..base_scraper import BaseScraper
from ..models import SongInfo, AlbumInfo, FileInfo
from ..utils import get_client, make_headers, safe_json, parse_duration


class KuwoScraper(BaseScraper):
    platform = "kuwo"
    platform_name = "酷我音乐"

    SEARCH_URL = "https://www.kuwo.cn/search/searchMusicBykeyWord"
    SEARCH_PARAMS = "vipver=1&client=kt&ft=music&cluster=0&strategy=2012&encoding=utf8&rformat=json&mobi=1&issubtitle=1&show_copyright_off=1"
    LYRIC_URL  = "http://m.kuwo.cn/newh5/singles/songinfoandlrc"

    # ------------------------------------------------------------------
    def search_song(self, keyword: str, page: int = 1, limit: int = 10) -> list[SongInfo]:
        """双搜策略：1)完整关键词+过滤 2)仅歌手名+歌名匹配 合并去重"""
        songs = []
        seen = set()

        # 第一轮：完整关键词搜索
        songs1 = self._do_search(keyword, page, limit * 2)
        for s in songs1:
            if not _match_any(keyword, s.song_name, s.singer):
                continue
            if s.song_id not in seen:
                seen.add(s.song_id)
                songs.append(s)

        # 第二轮：如果关键词含空格，用歌手名单独搜（扩大覆盖）
        parts = keyword.strip().split()
        if len(parts) >= 2 and len(songs) < limit:
            # 找最长的中文片段作为歌手名
            singer_kw = max(parts, key=len)
            songs2 = self._do_search(singer_kw, 1, limit * 3)
            # 按歌名匹配
            for s in songs2:
                if s.song_id in seen:
                    continue
                if _match_any(keyword, s.song_name, s.singer):
                    seen.add(s.song_id)
                    songs.append(s)

        return songs[:limit]

    def _do_search(self, keyword: str, page: int, limit: int) -> list[SongInfo]:
        """单次搜索"""
        from urllib.parse import quote
        full_url = f"{self.SEARCH_URL}?{self.SEARCH_PARAMS}&pn={(page - 1) * limit}&rn={limit}&all={quote(keyword)}"
        headers = make_headers(referer="https://www.kuwo.cn/")
        with get_client() as client:
            resp = client.get(full_url, headers=headers)
        data = safe_json(resp.text)
        if not data:
            return []
        songs = []
        for item in data.get("abslist", []):
            rid = str(item.get("MUSICRID", "")).replace("MUSIC_", "")
            song_name = (item.get("SONGNAME") or item.get("NAME", "")).replace("&nbsp;", " ").replace("\u0026", "&")
            singer = (item.get("ARTIST") or item.get("SINGER", "")).replace("\u0026", "&")
            songs.append(SongInfo(
                song_id=rid, song_name=song_name, title=song_name, singer=singer,
                album_name=item.get("ALBUM", ""), album_id=str(item.get("ALBUMID", "")),
                duration=parse_duration(item.get("DURATION", 0)),
                interval=int(item.get("DURATION", 0)) if item.get("DURATION", 0) else 0,
                language=_detect_lang(song_name),
                cover_url=item.get("PICPATH", item.get("pic", "")),
                platform=self.platform, platform_name=self.platform_name,
                url=f"http://www.kuwo.cn/play_detail/{rid}",
                extra=_remainder(item, "MUSICRID SONGNAME NAME ARTIST SINGER ALBUM ALBUMID DURATION PICPATH pic"),
            ))
        return songs

    # ------------------------------------------------------------------
    def get_song_detail(self, song_id: str) -> Optional[SongInfo]:
        params = {"musicId": song_id, "httpsStatus": 1}
        headers = make_headers(referer="http://m.kuwo.cn/")
        with get_client() as client:
            resp = client.get(self.LYRIC_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data:
            return None

        wrapper = data.get("data") or {}
        # songinfo 可能是 dict / str / None，确保是 dict
        raw_info = (wrapper or {}).get("songinfo", wrapper) if wrapper else {}
        info = raw_info if isinstance(raw_info, dict) else {}
        if not info:
            info = wrapper if isinstance(wrapper, dict) else {}

        lrc_list = wrapper.get("lrclist", []) if isinstance(wrapper, dict) else []
        lyrics = "\n".join(item.get("lineLyric", "") for item in lrc_list if isinstance(item, dict) and item.get("lineLyric", "").strip()) if lrc_list else ""

        song_name = info.get("songName", "")
        if not song_name:
            return None

        # 文件格式
        formats = info.get("formats", {}) or {}
        files = []
        for qlabel, qkey in [("128kbps","128kbps"),("192kbps","192kbps"),("320kbps","320kbps"),("FLAC","flac"),("APE","ape")]:
            url = formats.get(qkey, "")
            if url:
                files.append(FileInfo(quality=qlabel, url=url, extension=qkey.replace("kbps","mp3") if "kbps" in qkey else qkey.lower()))

        return SongInfo(
            song_id=song_id,
            song_name=song_name,
            title=song_name,
            singer=info.get("artist", ""),
            album_name=info.get("album", ""),
            album_id=str(info.get("albumId", "")),
            duration=parse_duration(info.get("duration", info.get("songTime", 0))),
            interval=info.get("duration", info.get("songTime", 0)),
            language=_detect_lang(song_name),
            lyrics=lyrics,
            cover_url=info.get("pic", info.get("htsPic", "")),
            files=files,
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"http://www.kuwo.cn/play_detail/{song_id}",
            extra=_remainder(info, "songName artist album albumId duration songTime pic htsPic formats lrclist"),
        )

    # ------------------------------------------------------------------
    def get_album(self, album_id: str) -> Optional[AlbumInfo]:
        if not album_id or album_id == "0":
            return None
        params = {"albumid": album_id, "pn": 1, "rn": 100, "httpsStatus": 1}
        headers = make_headers(referer="http://www.kuwo.cn/", extra={"csrf": "", "X-Requested-With": "XMLHttpRequest"})
        with get_client() as client:
            try:
                client.get("http://www.kuwo.cn/", headers=make_headers())
            except Exception:
                pass
            resp = client.get("http://www.kuwo.cn/api/www/album/albumInfo", params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("code") != 200:
            return None

        info = data.get("data", {})
        songs_data = info.get("musicList", [])
        album_name = info.get("name", info.get("album", ""))

        album_songs = [
            SongInfo(
                song_id=str(item.get("rid", "")),
                song_name=item.get("name", ""),
                singer=item.get("artist", ""),
                album_name=album_name, album_id=album_id,
                duration=parse_duration(item.get("duration", 0)),
                platform=self.platform, platform_name=self.platform_name,
                url=f"http://www.kuwo.cn/play_detail/{item.get('rid', '')}",
                extra=_remainder(item, "rid name artist duration"),
            ) for item in songs_data
        ]

        return AlbumInfo(
            album_id=album_id, album_name=album_name, title=album_name,
            singer=info.get("artist", ""),
            release_date=info.get("releaseDate", ""),
            description=info.get("info", info.get("desc", "")),
            language=_detect_lang(album_name),
            cover_url=info.get("pic", info.get("htsPic", "")),
            song_count=len(album_songs), songs=album_songs,
            platform=self.platform, platform_name=self.platform_name,
            url=f"http://www.kuwo.cn/album_detail/{album_id}",
            extra=_remainder(info, "name album artist releaseDate info desc pic htsPic musicList"),
        )

    # ------------------------------------------------------------------
    def get_play_url(self, song_id: str) -> str:
        """酷我播放直链 - antiserver 格式"""
        # song_id 即 rid (不含 MUSIC_ 前缀)
        return f"http://antiserver.kuwo.cn/anti.s?format=mp3&rid=MUSIC_{song_id}&response=res&type=convert_url&br=128kmp3"


# ------------------------------------------------------------------

def _detect_lang(name: str) -> str:
    if not name: return ""
    if re.search(r'[\u4e00-\u9fff]', name): return "国语"
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', name): return "日语"
    if re.search(r'[\uac00-\ud7af]', name): return "韩语"
    return "外语"


def _remainder(obj: dict, exclude: str) -> dict:
    skip = set(exclude.split())
    return {k: v for k, v in obj.items() if k not in skip}


def _match_any(keyword: str, song_name: str, singer: str) -> bool:
    parts = keyword.strip().split()
    if len(parts) <= 1:
        return True
    sn = (song_name or "").lower()
    si = (singer or "").lower()
    # 多词搜索：最长片段（通常是歌手名）必须命中歌手或歌名
    longest = max(parts, key=len).lower()
    if len(longest) >= 2 and longest not in si and longest not in sn:
        return False
    # 其他片段至少命中一个
    for p in parts:
        p = p.lower()
        if len(p) >= 2 and (p in sn or p in si):
            return True
    return False
