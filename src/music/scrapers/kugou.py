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
from ..models import SongInfo, AlbumInfo, PlaylistInfo, FileInfo, MVInfo
from ..utils import get_client, make_headers, safe_json, parse_duration


class KugouScraper(BaseScraper):
    platform = "kugou"
    platform_name = "酷狗音乐"

    SEARCH_URL  = "http://mobilecdn.kugou.com/api/v3/search/song"
    LYRIC_URL   = "http://m.kugou.com/app/i/krc.php"
    ALBUM_URL   = "http://mobilecdn.kugou.com/api/v3/album/info"
    SONG_INFO_URL = "http://mobilecdn.kugou.com/api/v3/song/info"
    SPECIAL_INFO_URL = "http://mobilecdn.kugou.com/api/v3/special/info"
    SPECIAL_SONG_URL = "http://mobilecdn.kugou.com/api/v3/special/song"

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
                cover_url=(item.get("imgUrl", "") or item.get("imgurl", "")).replace("{size}", "480"),
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
        info = self._get_song_info(song_id)
        lyrics = self._get_lyrics(song_id)
        if not info and not lyrics:
            return None
        if not info:
            return SongInfo(
                song_id=song_id, lyrics=lyrics,
                platform=self.platform, platform_name=self.platform_name,
                url=f"https://www.kugou.com/song/#hash={song_id}",
            )
        filename = info.get("filename", "") or ""
        song_name = info.get("songname", "") or ""
        singer = info.get("singername", "") or ""
        if not song_name and " - " in filename:
            singer, song_name = filename.split(" - ", 1)
        elif not song_name:
            song_name = filename
        album = info.get("album") or {}
        if isinstance(album, list):
            album = album[0] if album and isinstance(album[0], dict) else {}
        if not isinstance(album, dict):
            album = {}
        album_name = album.get("albumname", "") or album.get("name", "")
        album_id = str(album.get("album_id", "") or album.get("id", "") or "")
        audio_id = str(album.get("album_audio_id", "") or "")
        # imge.kugou.com 专辑封面优于歌手头像
        cover = f"http://imge.kugou.com/stdmusic/240/{audio_id}.jpg" if audio_id \
            else (info.get("imgurl", "") or "").replace("{size}", "240")
        return SongInfo(
            song_id=song_id,
            song_name=song_name,
            title=song_name,
            singer=singer,
            singer_id=str(info.get("singerid", "")),
            album_name=album_name,
            album_id=album_id,
            duration=parse_duration(info.get("duration", 0)),
            interval=info.get("duration", 0),
            language=_detect_lang(song_name),
            lyrics=lyrics,
            cover_url=cover,
            size128=info.get("filesize", 0),
            files=[FileInfo(quality="128kbps", size=info.get("filesize", 0), extension="mp3")] if info.get("filesize") else [],
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://www.kugou.com/song/#hash={song_id}",
            extra=_remainder(info, "filename songname singername singerid duration album imgurl filesize intro extra"),
        )

    # ------------------------------------------------------------------
    def _get_song_info(self, song_hash: str) -> dict:
        """酷狗单曲信息（song/info 接口，游客可用）"""
        params = {"hash": song_hash}
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.SONG_INFO_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("status") != 1:
            return {}
        return data.get("data") or {}

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
            cover_url=(info.get("imgurl", "") or "").replace("{size}", "480"),
            song_count=info.get("songcount", 0),
            songs=[],
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://www.kugou.com/album/{album_id}.html",
            extra=_remainder(info, "albumname singername singerid publishtime intro imgurl songcount"),
        )

    # ------------------------------------------------------------------
    def get_playlist(self, playlist_id: str) -> Optional[PlaylistInfo]:
        """酷狗歌单解析：special/info（信息）+ special/song（曲目，分页）"""
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.SPECIAL_INFO_URL,
                              params={"specialid": playlist_id, "format": "json"},
                              headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("status") != 1:
            return None
        info = data.get("data") or {}
        if not info.get("specialname") and not info.get("songcount"):
            return None

        # 分页取全部曲目
        songs: list[SongInfo] = []
        total = int(info.get("songcount", 0) or 0)
        page = 1
        with get_client() as client:
            while True:
                resp2 = client.get(self.SPECIAL_SONG_URL,
                                   params={"specialid": playlist_id, "page": page, "pagesize": 100},
                                   headers=headers)
                d2 = safe_json(resp2.text)
                if not d2 or d2.get("status") != 1:
                    break
                batch = (d2.get("data") or {}).get("info") or []
                if not batch:
                    break
                songs.extend(self._parse_special_song(item) for item in batch)
                if total and len(songs) >= total:
                    break
                if len(batch) < 100:
                    break
                page += 1
                if page > 50:  # 防御性上限 5000 首
                    break

        return PlaylistInfo(
            playlist_id=str(playlist_id),
            name=info.get("specialname", ""),
            creator=info.get("nickname", ""),
            cover_url=(info.get("imgurl", "") or "").replace("{size}", "480"),
            description=info.get("intro", ""),
            song_count=total or len(songs),
            play_count=int(info.get("playcount", 0) or 0),
            songs=songs,
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://www.kugou.com/yy/special/single/{playlist_id}.html",
            extra=_remainder(info, "specialid specialname nickname intro songcount playcount collectcount publishtime imgurl"),
        )

    def _parse_special_song(self, item: dict) -> SongInfo:
        """歌单曲目解析：条目仅有 filename（歌手 - 歌名）等字段"""
        filename = item.get("filename", "") or ""
        singer, song_name = "", filename
        if " - " in filename:
            singer, song_name = filename.split(" - ", 1)
        audio_id = str(item.get("album_audio_id", "") or "")
        return SongInfo(
            song_id=item.get("hash", ""),
            song_name=song_name,
            title=song_name,
            singer=singer,
            album_id=str(item.get("album_id", "") or ""),
            duration=parse_duration(item.get("duration", 0)),
            interval=item.get("duration", 0),
            language=_detect_lang(song_name),
            cover_url=f"http://imge.kugou.com/stdmusic/240/{audio_id}.jpg" if audio_id else "",
            size128=item.get("filesize", 0),
            size320=item.get("320filesize", 0),
            sizeflac=item.get("sqfilesize", 0),
            files=[FileInfo(quality="128kbps", size=item.get("filesize", 0), extension="mp3")] if item.get("filesize") else [],
            mv=MVInfo(vid=item.get("mvhash", "")) if item.get("mvhash") else MVInfo(),
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://www.kugou.com/song/#hash={item.get('hash', '')}",
            extra=_remainder(item, "hash filename album_id album_audio_id duration filesize 320filesize sqfilesize mvhash"),
        )

    # ------------------------------------------------------------------
    def get_play_url(self, song_id: str) -> str:
        """酷狗播放直链 - 微信分享格式"""
        # song_id 即 hash
        return f"https://m.kugou.com/api/v1/wechat/index?cmd=101&hash={song_id}"

    # ------------------------------------------------------------------
    @staticmethod
    def extract_song_id(text: str) -> str:
        """从输入解析歌曲 hash：hash=xxx、mixsong/xxx 或 32 位十六进制"""
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"hash=([a-fA-F0-9]{32})", s) or re.search(r"mixsong/([a-fA-F0-9]{32})", s)
        if m:
            return m.group(1)
        if re.fullmatch(r"[a-fA-F0-9]{32}", s):
            return s
        return ""

    @staticmethod
    def extract_playlist_id(text: str) -> str:
        """从输入解析歌单 specialid：special/single/xxx、specialid=xxx 或纯数字"""
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"special/single/(\d+)", s) or re.search(r"specialid=(\d+)", s) \
            or re.search(r"/plist/(\d+)", s)
        if m:
            return m.group(1)
        if s.isdigit():
            return s
        return ""


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
