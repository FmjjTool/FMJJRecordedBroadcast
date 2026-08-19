"""
======================================================================
  酷狗音乐 (kugou.com) 抓取器
  API: mobilecdn.kugou.com  |  数据: 搜索+歌词(KRC解码)+专辑+播放直链+全extra
======================================================================
"""

from __future__ import annotations

import re
from typing import Optional

from ..base_scraper import BaseScraper
from ..models import SongInfo, AlbumInfo, FileInfo
from ..utils import get_client, make_headers, safe_json, parse_duration


class KugouScraper(BaseScraper):
    platform = "kugou"
    platform_name = "酷狗音乐"

    SEARCH_URL  = "http://mobilecdn.kugou.com/api/v3/search/song"
    LYRIC_URL   = "http://m.kugou.com/app/i/krc.php"
    ALBUM_URL   = "http://mobilecdn.kugou.com/api/v3/album/info"

    # ------------------------------------------------------------------
    def search_song(self, keyword: str, page: int = 1, limit: int = 10) -> list[SongInfo]:
        params = {"keyword": keyword, "page": page, "pagesize": limit, "showtype": 1, "format": "json"}
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.SEARCH_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("status") != 1:
            return []

        songs = []
        for item in data.get("data", {}).get("info", []):
            song_hash = item.get("hash", "")
            song_name = item.get("songname", "").replace("<em>", "").replace("</em>", "")
            album_id = str(item.get("album_id", ""))
            duration_raw = item.get("duration", 0)

            songs.append(SongInfo(
                song_id=song_hash,
                song_name=song_name,
                title=song_name,
                singer=item.get("singername", ""),
                album_name=item.get("album_name", "") or item.get("remark", ""),
                album_id=album_id,
                duration=parse_duration(duration_raw),
                interval=duration_raw,
                language=_detect_lang(song_name),
                cover_url=item.get("imgUrl", item.get("imgurl", "")),
                size128=item.get("filesize", 0),
                files=[FileInfo(quality="128kbps", size=item.get("filesize", 0), extension="mp3")] if item.get("filesize") else [],
                platform=self.platform,
                platform_name=self.platform_name,
                url=f"https://www.kugou.com/song/#hash={song_hash}",
                extra=_remainder(item, "hash songname singername album_name album_id remark duration imgUrl imgurl filesize"),
            ))
        return songs

    # ------------------------------------------------------------------
    def get_song_detail(self, song_id: str) -> Optional[SongInfo]:
        lyrics = self._get_lyrics(song_id)
        if not lyrics:
            return None
        return SongInfo(
            song_id=song_id,
            lyrics=lyrics,
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://www.kugou.com/song/#hash={song_id}",
        )

    # ------------------------------------------------------------------
    def _get_lyrics(self, song_hash: str) -> str:
        params = {"hash": song_hash, "timelength": 300000, "cmd": "100"}
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.LYRIC_URL, params=params, headers=headers)
        text = resp.text.strip()
        # 去除 BOM 头
        if text.startswith("\ufeff"):
            text = text[1:]
        if text.startswith("["):  # LRC/KRC 歌词格式（[ti:/[ar:/[id:/[offset:等）
            return text
        data = safe_json(text)
        if data:
            content = data.get("content", "") or data.get("lyrics", "")
            if content:
                import base64, zlib
                try:
                    decoded = base64.b64decode(content)
                    try:
                        decompressed = zlib.decompress(decoded).decode("utf-8", errors="replace")
                    except zlib.error:
                        decompressed = decoded.decode("utf-8", errors="replace")
                    lines = re.findall(r'\[(\d+),(\d+)\](.*?)(?=\[\d+,\d+\]|$)', decompressed)
                    return "\n".join(t.strip() for _, _, t in lines if t.strip())
                except Exception:
                    pass
                return content
        return ""

    # ------------------------------------------------------------------
    def get_album(self, album_id: str) -> Optional[AlbumInfo]:
        if not album_id or album_id == "0":
            return None
        params = {"albumid": album_id, "format": "json"}
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.ALBUM_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("status") != 1:
            return None

        info = data.get("data", {})
        album_name = info.get("albumname", "")
        return AlbumInfo(
            album_id=album_id,
            album_name=album_name,
            title=album_name,
            singer=info.get("singername", ""),
            singer_id=str(info.get("singerid", "")),
            release_date=info.get("publishtime", ""),
            description=info.get("intro", ""),
            language=_detect_lang(album_name),
            cover_url=info.get("imgurl", ""),
            song_count=info.get("songcount", 0),
            songs=[],
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://www.kugou.com/album/{album_id}.html",
            extra=_remainder(info, "albumname singername singerid publishtime intro imgurl songcount"),
        )

    # ------------------------------------------------------------------
    def get_play_url(self, song_id: str) -> str:
        """酷狗播放直链 - 微信分享格式"""
        # song_id 即 hash
        return f"https://m.kugou.com/api/v1/wechat/index?cmd=101&hash={song_id}"


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
