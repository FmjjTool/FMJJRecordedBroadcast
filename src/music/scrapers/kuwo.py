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
from ..models import SongInfo, AlbumInfo, PlaylistInfo, FileInfo, PayInfo
from ..utils import get_client, make_headers, safe_json, parse_duration


class KuwoScraper(BaseScraper):
    platform = "kuwo"
    platform_name = "酷我音乐"

    SEARCH_URL = "https://www.kuwo.cn/search/searchMusicBykeyWord"
    SEARCH_PARAMS = "vipver=1&client=kt&ft=music&cluster=0&strategy=2012&encoding=utf8&rformat=json&mobi=1&issubtitle=1&show_copyright_off=1"
    LYRIC_URL  = "http://m.kuwo.cn/newh5/singles/songinfoandlrc"
    NPL_URL = "http://nplserver.kuwo.cn/pl.svc"

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
            # www 详情/专辑接口有反爬；封面用搜索自带 web_albumpic_short（120px）升级 700 大图
            pic_short = item.get("web_albumpic_short", "") or ""
            pic_short = re.sub(r"^\d+/", "", pic_short)
            cover = item.get("PICPATH", "") or item.get("pic", "") or (
                f"https://img1.kuwo.cn/star/albumcover/700/{pic_short}" if pic_short else "")
            songs.append(SongInfo(
                song_id=rid, song_name=song_name, title=song_name, singer=singer,
                album_name=item.get("ALBUM", ""), album_id=str(item.get("ALBUMID", "")),
                duration=parse_duration(item.get("DURATION", 0)),
                interval=int(item.get("DURATION", 0)) if item.get("DURATION", 0) else 0,
                language=_detect_lang(song_name),
                cover_url=cover,
                platform=self.platform, platform_name=self.platform_name,
                url=f"http://www.kuwo.cn/play_detail/{rid}",
                extra=_remainder(item, "MUSICRID SONGNAME NAME ARTIST SINGER ALBUM ALBUMID DURATION PICPATH pic web_albumpic_short"),
            ))
        return songs

    # ------------------------------------------------------------------
    def get_song_detail(self, song_id: str) -> Optional[SongInfo]:
        params = {"musicId": song_id, "httpsStatus": 1}
        headers = make_headers(referer="http://m.kuwo.cn/")
        data = None
        for _ in range(2):  # 接口偶发返回空，重试一次
            with get_client() as client:
                resp = client.get(self.LYRIC_URL, params=params, headers=headers)
            data = safe_json(resp.text)
            wrapper0 = (data or {}).get("data") or {}
            if isinstance(wrapper0.get("songinfo"), dict) and wrapper0.get("songinfo", {}).get("songName"):
                break
            data = None
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
        singer = info.get("artist", "")
        album_name = info.get("album", "")
        cover = info.get("pic", info.get("htsPic", ""))
        cover = re.sub(r"/(\d+)/", "/700/", cover, count=1) if cover else ""
        # 接口偶发漏 artist/专辑，用搜索结果回填
        if not singer or not album_name:
            try:
                for cand in self._do_search(song_name, 1, 10):
                    if cand.song_id == str(song_id):
                        singer = singer or cand.singer
                        album_name = album_name or cand.album_name
                        cover = cover or cand.cover_url
                        break
            except Exception:
                pass

        # 文件格式：接口两种形态——dict(含直链) 或 "MP3128|OGG192|ALFLAC|..." 字符串
        formats = info.get("formats", {}) or {}
        files = []
        if isinstance(formats, str):
            for code in formats.split("|"):
                m = re.fullmatch(r"(MP3|OGG|WMA|AAC)(\d+)", code)
                if m:
                    files.append(FileInfo(quality=f"{m.group(2)}kbps",
                                          extension=m.group(1).lower(),
                                          bitrate=int(m.group(2))))
                elif code in ("ALFLAC", "FLAC"):
                    files.append(FileInfo(quality="FLAC", extension="flac"))
                elif code == "APE":
                    files.append(FileInfo(quality="APE", extension="ape"))
        else:
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
    def get_playlist(self, playlist_id: str) -> Optional[PlaylistInfo]:
        """酷我歌单解析：nplserver pl.svc 老接口（游客可用，分页，偶发抽风重试一次）"""
        result = self._get_playlist_once(playlist_id)
        if result is None:
            result = self._get_playlist_once(playlist_id)
        return result

    def _get_playlist_once(self, playlist_id: str) -> Optional[PlaylistInfo]:
        headers = make_headers(referer="http://www.kuwo.cn/")
        songs: list[SongInfo] = []
        total, title, creator, cover, desc, playnum = 0, "", "", "", "", 0
        pn = 0
        with get_client() as client:
            while True:
                resp = client.get(self.NPL_URL,
                                  params={"op": "getlistinfo", "pid": playlist_id, "pn": pn, "rn": 100,
                                          "encode": "utf8", "keyset": "pl2012", "identity": "kuwo",
                                          "pcmp4": 1, "vipver": "MUSIC_9.0.5.0_W1", "newver": 1},
                                  headers=headers)
                data = safe_json(resp.text)
                if not data:
                    break
                if pn == 0:
                    title = data.get("title", "") or ""
                    creator = data.get("uname", "") or ""
                    cover = data.get("pic", "") or ""
                    desc = data.get("info", "") or ""
                    playnum = int(data.get("playnum", 0) or 0)
                    total = int(data.get("total", 0) or 0)
                batch = data.get("musiclist") or []
                if not batch:
                    break
                songs.extend(self._parse_npl_song(item) for item in batch)
                if total and len(songs) >= total:
                    break
                if len(batch) < 100:
                    break
                pn += 1
                if pn > 50:  # 防御性上限 5000 首
                    break
        if not title and not songs:
            return None

        return PlaylistInfo(
            playlist_id=str(playlist_id),
            name=title,
            creator=creator,
            cover_url=cover,
            description=desc,
            song_count=total or len(songs),
            play_count=playnum,
            songs=songs,
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"http://www.kuwo.cn/playlist_detail/{playlist_id}",
            extra={},
        )

    def _parse_npl_song(self, item: dict) -> SongInfo:
        """nplserver 歌单曲目解析"""
        song_name = item.get("name", "") or item.get("displaysongname", "")
        singer = item.get("artist", "") or item.get("displayartistname", "") or item.get("FARTIST", "")
        album_name = item.get("album", "") or item.get("displayalbumname", "")
        pic = item.get("albumpic", "") or item.get("musicPic", "")
        pic = re.sub(r"/(\d+)/", "/700/", pic, count=1) if pic else ""
        return SongInfo(
            song_id=str(item.get("id", "")),
            song_name=song_name,
            title=song_name,
            singer=singer,
            album_name=album_name,
            album_id=str(item.get("albumid", "") or ""),
            duration=parse_duration(item.get("duration", 0)),
            interval=int(item.get("duration", 0)) if str(item.get("duration", "")).isdigit() else 0,
            language=_detect_lang(song_name),
            cover_url=pic,
            files=_parse_minfo(item.get("MINFO", "")),
            pay=_parse_payinfo(item.get("payInfo")),
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"http://www.kuwo.cn/play_detail/{item.get('id', '')}",
            extra=_remainder(item, "id name artist album albumid albumpic musicPic duration MINFO N_MINFO payInfo displaysongname displayartistname displayalbumname FARTIST FSONGNAME FALBUM AARTIST"),
        )

    # ------------------------------------------------------------------
    def get_play_url(self, song_id: str) -> str:
        """酷我播放直链 - antiserver 格式"""
        # song_id 即 rid (不含 MUSIC_ 前缀)
        return f"http://antiserver.kuwo.cn/anti.s?format=mp3&rid=MUSIC_{song_id}&response=res&type=convert_url&br=128kmp3"

    # ------------------------------------------------------------------
    @staticmethod
    def extract_song_id(text: str) -> str:
        """从输入解析歌曲 rid：play_detail/xxx、yinyue/xxx、rid=xxx 或纯数字"""
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"play_detail/(\d+)", s) or re.search(r"yinyue/(\d+)", s) \
            or re.search(r"[?&]rid=(\d+)", s)
        if m:
            return m.group(1)
        if s.isdigit():
            return s
        return ""

    @staticmethod
    def extract_playlist_id(text: str) -> str:
        """从输入解析歌单 pid：playlist_detail/xxx、pid=xxx 或纯数字"""
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"playlist_detail/(\d+)", s) or re.search(r"[?&]pid=(\d+)", s) \
            or re.search(r"/playlists/(\d+)", s)
        if m:
            return m.group(1)
        if s.isdigit():
            return s
        return ""


# ------------------------------------------------------------------

def _parse_minfo(minfo: str) -> list[FileInfo]:
    """解析 nplserver MINFO 音质串：level:p,bitrate:300,format:ogg,size:6.18Mb;..."""
    files = []
    if not minfo:
        return files
    for seg in minfo.split(";"):
        kv = {}
        for part in seg.split(","):
            if ":" in part:
                k, v = part.split(":", 1)
                kv[k] = v
        fmt = kv.get("format", "")
        if not fmt or fmt in ("mgg", "mflac"):  # 跳过加密格式
            continue
        size = 0
        m = re.match(r"^([\d.]+)([KM]?)b$", kv.get("size", ""), re.I)
        if m:
            size = int(float(m.group(1)) * (1024 if m.group(2).upper() == "K" else 1024 * 1024))
        files.append(FileInfo(quality=f"{kv.get('bitrate', '')}kbps" if fmt == "mp3" else fmt.upper(),
                              size=size, extension=fmt.lower(),
                              bitrate=int(kv.get("bitrate", 0) or 0)))
    return files


def _parse_payinfo(pay: dict) -> PayInfo:
    if not isinstance(pay, dict):
        return PayInfo()
    fee_type = pay.get("feeType") or {}
    fee = 0
    if isinstance(fee_type, dict):
        for v in fee_type.values():
            try:
                if str(v) not in ("0", ""):
                    fee = max(fee, int(v))
            except (TypeError, ValueError):
                pass
    return PayInfo(
        fee=fee,
        pay_play=1 if str(pay.get("cannotOnlinePlay", "0")) == "1" else 0,
        pay_down=1 if str(pay.get("cannotDownload", "0")) == "1" else 0,
    )


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
