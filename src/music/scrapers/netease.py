"""
======================================================================
  网易云音乐 (music.163.com) 抓取器
  API: music.163.com/api  |  数据: 搜索+46字段详情+歌词+专辑+播放直链+全extra
======================================================================
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from ..base_scraper import BaseScraper
from ..models import SongInfo, AlbumInfo, FileInfo, MVInfo, PayInfo
from ..utils import get_client, make_headers, safe_json, parse_duration


class NeteaseScraper(BaseScraper):
    platform = "netease"
    platform_name = "网易云音乐"

    BASE = "https://music.163.com"
    SEARCH_URL = f"{BASE}/api/cloudsearch/pc"
    SONG_URL   = f"{BASE}/api/song/detail"
    LYRIC_URL  = f"{BASE}/api/song/lyric"
    ALBUM_URL  = f"{BASE}/api/album"

    # ------------------------------------------------------------------
    def _init_cookies(self, client) -> None:
        try:
            client.get(self.BASE, headers=make_headers())
        except Exception:
            pass

    # ------------------------------------------------------------------
    def search_song(self, keyword: str, page: int = 1, limit: int = 10) -> list[SongInfo]:
        """使用 /api/cloudsearch/pc 获取更丰富的搜索数据"""
        offset = (page - 1) * limit
        params = {"s": keyword, "type": 1, "offset": offset, "limit": limit}
        headers = make_headers(referer=f"{self.BASE}/search/", extra={"X-Requested-With": "XMLHttpRequest"})
        with get_client() as client:
            self._init_cookies(client)
            resp = client.post(f"{self.BASE}/api/search/get", data=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("code") != 200:
            return []

        songs = []
        for item in data.get("result", {}).get("songs", []):
            al = item.get("album", {}) or item.get("al", {}) or {}
            ar = item.get("artists", []) or item.get("ar", [])
            ar0 = ar[0] if ar else {}
            song_name = item.get("name", "")

            songs.append(SongInfo(
                song_id=str(item.get("id", "")),
                song_name=song_name,
                title=song_name,
                alias=item.get("alias", []),
                singer=_join_ar(ar),
                singer_id=str(ar0.get("id", "")),
                album_name=al.get("name", ""),
                album_id=str(al.get("id", "")),
                duration=parse_duration(item.get("duration", 0)),
                interval=item.get("duration", 0),
                language=_detect_lang(song_name),
                cover_url=al.get("picUrl", "") or _cover_url(al.get("id")),
                mv=MVInfo(id=str(item.get("mvid", ""))),
                pay=PayInfo(fee=item.get("fee", 0)),
                popularity=float(item.get("pop", item.get("popularity", 0))),
                publish_time=al.get("publishTime", 0),
                release_date=_ts2date(al.get("publishTime", 0)),
                platform=self.platform,
                platform_name=self.platform_name,
                url=f"https://music.163.com/song?id={item.get('id', '')}",
                extra=_remainder(item, "id name alias duration artists album mvid fee pop"),
            ))
        return songs

    # ------------------------------------------------------------------
    def get_song_detail(self, song_id: str) -> Optional[SongInfo]:
        params = {"ids": f"[{song_id}]"}
        headers = make_headers(referer=f"{self.BASE}/", extra={"X-Requested-With": "XMLHttpRequest"})
        with get_client() as client:
            self._init_cookies(client)
            resp = client.get(self.SONG_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("code") != 200:
            return None

        songs = data.get("songs", [])
        if not songs:
            return None
        item = songs[0]

        al = item.get("al", {}) or item.get("album", {}) or {}
        ar = item.get("ar", []) or item.get("artists", [])
        ar0 = ar[0] if ar else {}
        song_name = item.get("name", "")
        lyrics = self._get_lyrics(song_id)

        # 各音质文件
        files = []
        for qkey, qlabel in [("lMusic","128kbps"),("mMusic","192kbps"),("hMusic","320kbps"),("sqMusic","FLAC"),("hrMusic","Hi-Res")]:
            mobj = item.get(qkey)
            if mobj and mobj.get("size"):
                files.append(FileInfo(
                    quality=qlabel, size=mobj.get("size", 0),
                    extension=mobj.get("extension", ""),
                    bitrate=mobj.get("bitrate", 0), sr=mobj.get("sr", 0),
                ))


        return SongInfo(
            song_id=str(item.get("id", song_id)),
            song_name=song_name,
            title=song_name,
            alias=item.get("alias", []),
            singer=_join_ar(ar),
            singer_id=str(ar0.get("id", "")),
            album_name=al.get("name", ""),
            album_id=str(al.get("id", "")),
            track_no=item.get("no", item.get("position", 0)),
            disc_no=str(item.get("disc", item.get("cd", ""))),
            duration=parse_duration(item.get("dt", item.get("duration", 0))),
            interval=item.get("dt", item.get("duration", 0)),
            lyrics=lyrics,
            cover_url=al.get("picUrl", "") or _cover_url(al.get("id")),
            mv=MVInfo(id=str(item.get("mvid", "")), vid=item.get("mv", {}).get("vid", "") if isinstance(item.get("mv"), dict) else ""),
            files=files,
            pay=PayInfo(fee=item.get("fee", 0)),
            release_date=_ts2date(item.get("publishTime", al.get("publishTime", 0))),
            publish_time=item.get("publishTime", al.get("publishTime", 0)),
            popularity=item.get("pop", item.get("popularity", 0.0)),
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://music.163.com/song?id={item.get('id', '')}",
            extra=_remainder(item, "id name alias dt duration no position disc cd ar artists al album fee mvid mv pop popularity hMusic mMusic lMusic sqMusic hrMusic bMusic publishTime"),
        )

    # ------------------------------------------------------------------
    def _get_lyrics(self, song_id: str) -> str:
        params = {"id": song_id, "lv": -1}
        headers = make_headers(referer=f"{self.BASE}/", extra={"X-Requested-With": "XMLHttpRequest"})
        with get_client() as client:
            self._init_cookies(client)
            resp = client.get(self.LYRIC_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("code") != 200:
            return ""
        return (data.get("lrc") or {}).get("lyric", "")

    # ------------------------------------------------------------------
    def get_album(self, album_id: str) -> Optional[AlbumInfo]:
        headers = make_headers(referer=f"{self.BASE}/", extra={"X-Requested-With": "XMLHttpRequest"})
        with get_client() as client:
            self._init_cookies(client)
            resp = client.get(f"{self.ALBUM_URL}/{album_id}", headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("code") != 200:
            return None

        ad = data.get("album", {})
        songs_data = ad.get("songs", [])
        ar = ad.get("artists", []) or ad.get("ar", [])
        ar0 = ar[0] if ar else {}
        album_name = ad.get("name", "")

        album_songs = []
        for item in songs_data:
            item_ar = item.get("ar", []) or item.get("artists", [])
            album_songs.append(SongInfo(
                song_id=str(item.get("id", "")),
                song_name=item.get("name", ""),
                singer=_join_ar(item_ar),
                album_name=album_name,
                album_id=album_id,
                duration=parse_duration(item.get("dt", 0)),
                track_no=item.get("no", item.get("position", 0)),
                platform=self.platform,
                platform_name=self.platform_name,
                cover_url=_cover_url(album_id),
                extra=_remainder(item, "id name dt no position ar artists"),
            ))

        pub_time = ad.get("publishTime", 0)
        album_type = ad.get("type", ad.get("subType", ""))
        type_map = {"专辑": "录音室专辑", "EP": "EP", "Single": "单曲", "Live": "现场专辑"}
        album_type = type_map.get(album_type, album_type)

        return AlbumInfo(
            album_id=album_id,
            album_name=album_name,
            title=album_name,
            singer=_join_ar(ar),
            singer_id=str(ar0.get("id", "")),
            release_date=_ts2date(pub_time),
            publish_time=pub_time,
            description=ad.get("description", ad.get("desc", "")),
            album_type=album_type,
            language=_detect_lang(album_name),
            company=ad.get("company") or "",
            cover_url=ad.get("picUrl", "") or _cover_url(album_id),
            song_count=ad.get("size", len(album_songs)),
            songs=album_songs,
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://music.163.com/album?id={album_id}",
            extra=_remainder(ad, "name id type subType size picUrl publishTime description company artists ar songs desc"),
        )

    # ------------------------------------------------------------------
    def get_play_url(self, song_id: str) -> str:
        return f"https://music.163.com/song/media/outer/url?id={song_id}.mp3"

    # ------------------------------------------------------------------
    def search_by_album(self, singer: str, album_name: str, limit: int = 20) -> list[SongInfo]:
        """网易云专辑搜索：先用原生专辑API(type=10)，再用歌曲搜索+筛选"""
        # 方式1: 原生专辑搜索
        params = {"s": f"{singer} {album_name}", "type": 10, "offset": 0, "limit": limit, "sub": "false"}
        headers = make_headers(referer=f"{self.BASE}/search/", extra={"X-Requested-With": "XMLHttpRequest"})
        with get_client() as client:
            self._init_cookies(client)
            resp = client.post(self.SEARCH_URL, data=params, headers=headers)
        data = safe_json(resp.text)
        if data and data.get("code") == 200:
            for alb in data.get("result", {}).get("albums", []):
                al_name = alb.get("name", "")
                al_artist = (alb.get("artist") or {}).get("name", "")
                if _match_album(al_name, album_name) and _match_artist(al_artist, singer):
                    # 获取专辑歌曲
                    al_id = str(alb.get("id", ""))
                    album = self.get_album(al_id)
                    if album and album.songs:
                        return album.songs
        # 方式2: 歌曲搜索+筛选（fallback）
        return super().search_by_album(singer, album_name, limit)


# ------------------------------------------------------------------

def _join_ar(artists: list) -> str:
    if not artists: return ""
    return " / ".join(a.get("name", "") for a in artists if isinstance(a, dict) and a.get("name"))


def _cover_url(album_id) -> str:
    return f"https://p2.music.126.net/{album_id}.jpg" if album_id else ""


def _detect_lang(name: str) -> str:
    if not name: return ""
    if re.search(r'[\u4e00-\u9fff]', name): return "国语"
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', name): return "日语"
    if re.search(r'[\uac00-\ud7af]', name): return "韩语"
    return "外语"


def _ts2date(ts: int) -> str:
    if not ts or ts <= 0: return ""
    try: return datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d")
    except: return str(ts)


def _remainder(obj: dict, exclude: str) -> dict:
    skip = set(exclude.split())
    return {k: v for k, v in obj.items() if k not in skip}


def _match_album(found: str, target: str) -> bool:
    f, t = found.strip().lower(), target.strip().lower()
    if not f or not t:
        return False
    return t == f or t in f or (len(f) >= max(3, len(t) * 0.6) and f in t)


def _match_artist(found: str, target: str) -> bool:
    f, t = found.strip().lower(), target.strip().lower()
    if not f or not t:
        return True
    return t == f or t in f or f in t
