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
    SEARCH_ALBUM_URL = "http://mobilecdn.kugou.com/api/v3/search/album"
    LYRIC_URL   = "http://m.kugou.com/app/i/krc.php"
    ALBUM_URL   = "http://mobilecdn.kugou.com/api/v3/album/info"
    ALBUM_SONG_URL = "http://mobilecdn.kugou.com/api/v3/album/song"
    SINGER_SEARCH_URL = "http://mobilecdn.kugou.com/api/v3/search/singer"
    SINGER_INFO_URL = "http://mobilecdn.kugou.com/api/v3/singer/info"
    SINGER_SONG_URL = "http://mobilecdn.kugou.com/api/v3/singer/song"
    SINGER_ALBUM_URL = "http://mobilecdn.kugou.com/api/v3/singer/album"
    SINGER_MOBILE_URL = "https://m.kugou.com/singer/info/{token}"
    MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                 "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148")
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
    def resolve_song_id(self, text: str) -> str:
        """完整歌曲常存 mixsong 短链；先解析短链页面里的 32 位 hash。"""
        song_id = self.extract_song_id(text)
        if song_id:
            return song_id
        match = re.search(r"/mixsong/([A-Za-z0-9]+)\.html", text or "")
        if not match:
            return ""
        url = f"https://m.kugou.com/mixsong/{match.group(1)}.html"
        try:
            with get_client() as client:
                resp = client.get(url, headers=make_headers(referer="http://m.kugou.com/"))
            found = re.search(r"hash[\"']?\s*[:=]\s*[\"']([a-fA-F0-9]{32})", resp.text)
            return found.group(1) if found else ""
        except Exception:
            return ""

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
    def get_album(self, album_id: str, *, max_songs: int | None = None) -> Optional[AlbumInfo]:
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
            songs=self._album_songs(album_id, max_songs=max_songs),
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://www.kugou.com/album/{album_id}.html",
            extra=_remainder(info, "albumname singername singerid publishtime intro imgurl songcount"),
        )

    def _album_songs(self, album_id: str, *, max_songs: int | None = None) -> list[SongInfo]:
        """酷狗专辑曲目（album/song 接口只给 filename，需要拆歌手/歌名）"""
        params = {"albumid": album_id, "page": 1, "pagesize": 100, "format": "json"}
        headers = make_headers(referer="http://m.kugou.com/")
        songs: list[SongInfo] = []
        page = 1
        if max_songs is not None:
            max_songs = max(1, min(int(max_songs), 3000))
        with get_client() as client:
            while page <= 30:  # 防御性上限 3000 首
                page_size = min(100, max_songs - len(songs)) if max_songs is not None else 100
                if page_size <= 0:
                    break
                params["page"] = page
                params["pagesize"] = page_size
                resp = client.get(self.ALBUM_SONG_URL, params=params, headers=headers)
                data = safe_json(resp.text)
                batch = ((data or {}).get("data") or {}).get("info") or []
                if not batch:
                    break
                songs.extend(self._parse_filename_song(item) for item in batch[:page_size])
                if max_songs is not None and len(songs) >= max_songs:
                    break
                if len(batch) < page_size:
                    break
                page += 1
        return songs

    def _parse_filename_song(self, item: dict) -> SongInfo:
        """album/song、singer/song 等只给 filename 的条目 → SongInfo"""
        filename = item.get("filename", "") or ""
        song_name = item.get("songname") or ""
        singer = item.get("singername") or ""
        if not song_name:
            if " - " in filename:
                singer_part, song_name = filename.split(" - ", 1)
                singer = singer or singer_part
            else:
                song_name = filename
        audio_id = str(item.get("album_audio_id", "") or "")
        song_hash = item.get("hash", "")
        return SongInfo(
            song_id=song_hash,
            song_name=song_name,
            title=item.get("songname") or song_name,
            singer=singer,
            album_name=item.get("album_name", "") or "",
            album_id=str(item.get("album_id", "") or ""),
            duration=parse_duration(item.get("duration", 0)),
            interval=item.get("duration", 0),
            language=_detect_lang(song_name),
            cover_url=f"http://imge.kugou.com/stdmusic/240/{audio_id}.jpg" if audio_id else "",
            size128=item.get("filesize", 0),
            files=[FileInfo(quality="128kbps", size=item.get("filesize", 0), extension="mp3")] if item.get("filesize") else [],
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://www.kugou.com/song/#hash={song_hash}",
            extra=_remainder(item, "hash filename songname singername album_id album_name album_audio_id duration filesize"),
        )

    # ------------------------------------------------------------------
    def get_playlist(self, playlist_id: str, *, max_songs: int | None = None) -> Optional[PlaylistInfo]:
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
        if max_songs is not None:
            max_songs = max(1, min(int(max_songs), 5000))
        page = 1
        with get_client() as client:
            while True:
                page_size = min(100, max_songs - len(songs)) if max_songs is not None else 100
                if page_size <= 0:
                    break
                resp2 = client.get(self.SPECIAL_SONG_URL,
                                   params={"specialid": playlist_id, "page": page, "pagesize": page_size},
                                   headers=headers)
                d2 = safe_json(resp2.text)
                if not d2 or d2.get("status") != 1:
                    break
                batch = (d2.get("data") or {}).get("info") or []
                if not batch:
                    break
                songs.extend(self._parse_special_song(item) for item in batch[:page_size])
                if max_songs is not None and len(songs) >= max_songs:
                    break
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
    # 专辑搜索 / ID 解析
    def search_album(self, keyword: str, limit: int = 30) -> list[AlbumInfo]:
        params = {"keyword": keyword, "page": 1, "pagesize": limit, "format": "json"}
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.SEARCH_ALBUM_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("status") != 1:
            return []
        albums = []
        for item in ((data.get("data") or {}).get("info") or []):
            album_id = str(item.get("albumid") or "")
            name = item.get("albumname") or ""
            if not album_id or not name:
                continue
            albums.append(AlbumInfo(
                album_id=album_id,
                album_name=name,
                title=name,
                singer=item.get("singername") or "",
                singer_id=str(item.get("singerid") or ""),
                release_date=item.get("publishtime") or "",
                description=item.get("intro") or "",
                cover_url=(item.get("imgurl") or "").replace("{size}", "480"),
                song_count=int(item.get("songcount") or 0),
                platform=self.platform,
                platform_name=self.platform_name,
                url=f"https://www.kugou.com/album/{album_id}.html",
                extra=_remainder(item, "albumid albumname singername singerid publishtime intro imgurl songcount"),
            ))
        return albums

    @staticmethod
    def extract_album_id(text: str) -> str:
        """album/{id}.html、albumid={id} 或纯数字"""
        s = (text or "").strip()
        if not s:
            return ""
        m = (re.search(r"/album/(\d+)", s)
             or re.search(r"albumid=(\d+)", s)
             or re.search(r"album_id=(\d+)", s))
        if m:
            return m.group(1)
        if s.isdigit():
            return s
        return ""

    # ------------------------------------------------------------------
    # 歌手主页
    def search_artist(self, keyword: str, limit: int = 10) -> list[dict]:
        params = {"keyword": keyword, "page": 1, "pagesize": limit, "format": "json"}
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.SINGER_SEARCH_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("status") != 1:
            return []
        artists = []
        for item in data.get("data") or []:
            sid = str(item.get("singerid") or "")
            name = item.get("singername") or ""
            if sid and name:
                artists.append({"id": sid, "name": name, "pic": "",
                                "song_count": 0, "album_count": 0, "desc": ""})
        return artists

    def get_artist(self, artist_id: str, name: str = "", *, max_songs: int | None = None) -> Optional[dict]:
        artist_id = self.resolve_singer_id(artist_id)
        if not artist_id:
            return None
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.SINGER_INFO_URL,
                              params={"singerid": artist_id}, headers=headers)
        data = safe_json(resp.text) or {}
        info = data.get("data") or {}
        artist_name = name or info.get("singername") or ""
        if not artist_name:
            return None
        pic = (info.get("imgurl") or "").replace("{size}", "480")

        songs, seen = [], set()
        song_limit = max(1, min(int(max_songs), 50)) if max_songs is not None else 50
        with get_client() as client:
            resp = client.get(self.SINGER_SONG_URL,
                              params={"singerid": artist_id, "page": 1,
                                      "pagesize": song_limit, "format": "json"},
                              headers=headers)
        song_data = safe_json(resp.text) or {}
        for item in ((song_data.get("data") or {}).get("info") or [])[:song_limit]:
            song = self._parse_filename_song(item)
            if song.song_id and song.song_id not in seen:
                seen.add(song.song_id)
                songs.append(song)

        albums = []
        with get_client() as client:
            resp = client.get(self.SINGER_ALBUM_URL,
                              params={"singerid": artist_id, "page": 1,
                                      "pagesize": 30, "format": "json"},
                              headers=headers)
        album_data = safe_json(resp.text) or {}
        for item in ((album_data.get("data") or {}).get("info") or []):
            album_id = str(item.get("albumid") or "")
            album_name = item.get("albumname") or ""
            if not album_id or not album_name:
                continue
            albums.append(AlbumInfo(
                album_id=album_id,
                album_name=album_name,
                title=album_name,
                singer=item.get("singername") or artist_name,
                release_date=item.get("publishtime") or "",
                cover_url=(item.get("imgurl") or "").replace("{size}", "480"),
                song_count=int(item.get("songcount") or 0),
                platform=self.platform,
                platform_name=self.platform_name,
                url=f"https://www.kugou.com/album/{album_id}.html",
                extra=_remainder(item, "albumid albumname singername imgurl songcount publishtime"),
            ))

        return {
            "id": artist_id,
            "name": artist_name,
            "pic": pic,
            "desc": info.get("intro") or "",
            "song_count": int(info.get("songcount") or len(songs)),
            "album_count": int(info.get("albumcount") or len(albums)),
            "url": f"https://www.kugou.com/singer/{artist_id}.html",
            "songs": songs,
            "albums": albums,
        }

    @classmethod
    def resolve_singer_id(cls, text: str) -> str:
        """把主页链接/哈希 ID 归一成数字 singerid。

        新式主页链接 `www.kugou.com/singer/info/<哈希>`（如 5NQUK0F36CC0A）
        里的哈希不是数字 ID，截取前几位会指向另一位歌手，必须查移动端
        H5 页面内嵌 JSON 里的 singerid。
        """
        token = (text or "").strip()
        token = cls.extract_artist_id(token) or token
        if not token or token.isdigit():
            return token
        # 只有移动端 UA 才返回带歌手 JSON 的 H5 页面，桌面 UA 是空壳 SPA
        headers = make_headers(referer="https://m.kugou.com/",
                               extra={"User-Agent": cls.MOBILE_UA})
        with get_client() as client:
            resp = client.get(cls.SINGER_MOBILE_URL.format(token=token),
                              headers=headers)
        m = re.search(r'"singerid"\s*:\s*(\d+)', resp.text or "")
        return m.group(1) if m else ""

    @staticmethod
    def extract_artist_id(text: str) -> str:
        """singer/{id}.html、singer/home/{id}.html、singer/info/{id|哈希}、singerid={id} 或纯数字"""
        s = (text or "").strip()
        if not s:
            return ""
        m = (re.search(r"/singer/info/([0-9A-Za-z]+)", s)
             or re.search(r"/singer/home/(\d+)", s)
             or re.search(r"/singer/(\d+)", s)
             or re.search(r"singerid=(\d+)", s))
        if m:
            return m.group(1)
        if s.isdigit():
            return s
        return ""

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

    def resolve_link_ids(self, link: str) -> dict:
        """酷狗歌曲页链接 → {song_id, extra}；解析不出返回 {}。

        「完整歌曲」表里存的是 `m.kugou.com/mixsong/<key>.html`，key 是 8 位
        短码而不是 hash，`extract_song_id` 只能返回空。这里取一次页面，
        从中读出 hash / audio_id / album_audio_id（云歌单加歌要靠
        album_audio_id 定位音频）。
        """
        m = re.search(r"mixsong/([A-Za-z0-9]+)\.html", link or "")
        if not m:
            return {}
        key = m.group(1)
        if re.fullmatch(r"[a-fA-F0-9]{32}", key):
            return {"song_id": key, "extra": {}}
        url = f"https://m.kugou.com/mixsong/{key}.html"
        try:
            with get_client() as client:
                resp = client.get(url, headers=make_headers(referer="https://m.kugou.com/"))
            text = resp.text or ""
        except Exception:  # noqa: BLE001 - 解析失败退回搜索匹配
            return {}
        found = re.search(r'"hash"\s*:\s*"([A-Fa-f0-9]{32})"', text)
        if not found:
            return {}
        extra = {}
        for field, pattern in (
            ("mixsongid", r'"mixsongid"\s*:\s*"?(\d+)"?'),
            ("album_audio_id", r'"album_audio_id"\s*:\s*"?(\d+)"?'),
            ("audio_id", r'"audio_id"\s*:\s*"?(\d+)"?'),
            ("album_id", r'"albumid"\s*:\s*"?(\d+)"?'),
        ):
            hit = re.search(pattern, text)
            if hit:
                extra[field] = hit.group(1)
        # 页面上的 mixsongid 就是云歌单加歌要的 album_audio_id（同一首歌实测一致）
        if "mixsongid" in extra and "album_audio_id" not in extra:
            extra["album_audio_id"] = extra["mixsongid"]
        return {"song_id": found.group(1), "extra": extra}

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
