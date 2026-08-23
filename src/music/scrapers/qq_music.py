"""
======================================================================
  QQ音乐 (y.qq.com) 抓取器
  API: c.y.qq.com  |  数据: 搜索+详情+歌词+专辑+播放直链+全字段extra
======================================================================
"""

from __future__ import annotations

import re
from typing import Optional
from datetime import datetime

from ..base_scraper import BaseScraper
from ..models import SongInfo, AlbumInfo, PlaylistInfo, FileInfo, MVInfo, PayInfo
from ..utils import get_client, make_headers, safe_json, parse_duration


def _match_text(text: str, kw: str) -> bool:
    """模糊匹配：大小写不敏感、忽略空格、双向包含判断。"""
    if text is None or kw is None:
        return False
    text_norm = "".join(text.lower().split())
    kw_norm = "".join(kw.lower().split())
    if not text_norm or not kw_norm:
        return False
    return kw_norm in text_norm or text_norm in kw_norm


class QQMusicScraper(BaseScraper):
    platform = "qq"
    platform_name = "QQ音乐"

    SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
    SONG_URL   = "https://c.y.qq.com/v8/fcg-bin/fcg_play_single_song.fcg"
    LYRIC_URL  = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
    ALBUM_URL  = "https://c.y.qq.com/v8/fcg-bin/fcg_v8_album_detail_cp.fcg"
    PLAYLIST_URL = "https://c.y.qq.com/qzone/fcg-bin/fcg_ucc_getcdinfo_byids_cp.fcg"

    # ------------------------------------------------------------------
    def search_song(self, keyword: str, page: int = 1, limit: int = 10) -> list[SongInfo]:
        params = {"w": keyword, "format": "json", "p": page, "n": limit, "type": 0}
        headers = make_headers(referer="https://y.qq.com/")
        with get_client() as client:
            resp = client.get(self.SEARCH_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data:
            return []

        songs = []
        for item in data.get("data", {}).get("song", {}).get("list", []):
            songs.append(self._parse_song_item(item))
        return songs

    def _parse_song_item(self, item: dict) -> SongInfo:
        """搜索/歌单通用的歌曲条目解析"""
        albummid = item.get("albummid", "")
        singer_list = item.get("singer", [])
        singer0 = singer_list[0] if singer_list else {}
        song_name = item.get("songname", "")
        return SongInfo(
            song_id=item.get("songmid", str(item.get("songid", ""))),
            song_name=song_name,
            title=song_name,
            subtitle="",
            alias=[],
            singer=_join(singer_list),
            singer_id=str(singer0.get("id", "")),
            singer_mid=singer0.get("mid", ""),
            album_name=item.get("albumname", ""),
            album_id=albummid,
            track_no=item.get("cdIdx", 0),
            disc_no=str(item.get("belongCD", "")),
            duration=parse_duration(item.get("interval", 0)),
            interval=item.get("interval", 0),
            language=_detect_lang(song_name),
            cover_url=_cover_url(albummid) if albummid else "",
            files=_parse_files(item),
            size128=item.get("size128", 0),
            size320=item.get("size320", 0),
            sizeflac=item.get("sizeflac", 0),
            mv=MVInfo(vid=item.get("vid", "")),
            pay=PayInfo(
                fee=item.get("pay", {}).get("payplay", 0),
                pay_play=item.get("pay", {}).get("payplay", 0),
                pay_down=item.get("pay", {}).get("paydownload", 0),
                price_track=item.get("pay", {}).get("paytrackprice", 0),
                price_album=item.get("pay", {}).get("payalbumprice", 0),
            ),
            publish_time=item.get("pubtime", 0),
            release_date=_ts_to_date(item.get("pubtime", 0)),
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://y.qq.com/n/ryqq/songDetail/{item.get('songmid', '')}",
            extra=_remainder(item, "songmid songid songname albumname albummid albumid singer interval cdIdx belongCD size128 size320 sizeflac vid pay pubtime"),
        )

    # ------------------------------------------------------------------
    def get_song_detail(self, song_id: str) -> Optional[SongInfo]:
        params = {"songmid": song_id, "format": "json"}
        headers = make_headers(referer="https://y.qq.com/")
        with get_client() as client:
            resp = client.get(self.SONG_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("code") != 0:
            return None

        info = data.get("data", {})
        if isinstance(info, list):
            info = info[0] if info else {}

        album = info.get("album") or {}
        albummid = album.get("mid", "")
        singer_list = info.get("singer", [])
        singer0 = singer_list[0] if singer_list else {}
        song_name = info.get("name", "") or info.get("songname", "")
        lyrics = self._get_lyrics(song_id)

        # 文件信息
        fobj = info.get("file") or {}
        files = []
        for qkey, qlabel in [("size_128mp3","128kbps"),("size_192aac","192kbps"),("size_320mp3","320kbps"),("size_flac","FLAC"),("size_ape","APE"),("size_ogg","OGG")]:
            sz = fobj.get(qkey, 0)
            if sz:
                files.append(FileInfo(quality=qlabel, size=sz, extension=qkey.split("_")[-1]))

        mv_info = info.get("mv") or {}

        return SongInfo(
            song_id=song_id,
            song_name=song_name,
            title=info.get("title", song_name),
            subtitle=info.get("subtitle", album.get("subtitle", "")),
            alias=[],
            singer=_join(singer_list),
            singer_id=str(singer0.get("id", "")),
            singer_mid=singer0.get("mid", ""),
            album_name=album.get("name", ""),
            album_id=albummid,
            track_no=info.get("index_album", 0),
            disc_no=str(info.get("index_cd", info.get("cdIdx", ""))),
            duration=parse_duration(info.get("interval", 0)),
            interval=info.get("interval", 0),
            bpm=info.get("bpm", 0),
            genre=_genre_name(info.get("genre", 0)),
            language=_lang_name(info.get("language", 0)) or _detect_lang(song_name),
            lyrics=lyrics,
            cover_url=_cover_url(albummid) if albummid else "",
            mv=MVInfo(
                id=str(mv_info.get("id", "")),
                vid=mv_info.get("vid", ""),
                name=mv_info.get("name", ""),
                title=mv_info.get("title", ""),
            ),
            files=files,
            pay=PayInfo(
                fee=info.get("pay", {}).get("pay_play", 0),
                pay_play=info.get("pay", {}).get("pay_play", 0),
                pay_down=info.get("pay", {}).get("pay_down", 0),
                price_track=info.get("pay", {}).get("price_track", 0),
                price_album=info.get("pay", {}).get("price_album", 0),
            ),
            release_date=info.get("time_public", album.get("time_public", "")),
            publish_time=_date_to_ts(info.get("time_public", "")),
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://y.qq.com/n/ryqq/songDetail/{song_id}",
            extra=_remainder(info, "name title subtitle singer album interval index_album index_cd bpm genre language mv file pay time_public"),
        )

    # ------------------------------------------------------------------
    def _get_lyrics(self, song_id: str) -> str:
        params = {"songmid": song_id, "format": "json", "nobase64": 1}
        headers = make_headers(referer="https://y.qq.com/")
        with get_client() as client:
            resp = client.get(self.LYRIC_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("code") != 0:
            return ""
        return data.get("lyric", "") or ""

    # ------------------------------------------------------------------
    def get_album(self, album_id: str) -> Optional[AlbumInfo]:
        params = {"albummid": album_id, "format": "json"}
        headers = make_headers(referer="https://y.qq.com/")
        with get_client() as client:
            resp = client.get(self.ALBUM_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("code") != 0:
            return None

        dd = data.get("data", {})
        ainfo = dd.get("getAlbumInfo", {}) or dd
        adesc = dd.get("getAlbumDesc", {}) or {}
        sinfo = dd.get("getSingerInfo", {}) or dd.get("singerInfo", [{}])[0] if dd.get("singerInfo") else {}

        album_songs = []
        for item in dd.get("getSongInfo", dd.get("list", dd.get("songlist", []))):
            album_songs.append(SongInfo(
                song_id=item.get("songmid", ""),
                song_name=item.get("songname", ""),
                singer=_join(item.get("singer", [])),
                album_name=ainfo.get("Falbum_name", ainfo.get("name", "")),
                album_id=album_id,
                duration=parse_duration(item.get("interval", 0)),
                platform=self.platform,
                platform_name=self.platform_name,
                cover_url=_cover_url(album_id),
                extra=_remainder(item, "songmid songname singer interval"),
            ))

        album_name = ainfo.get("Falbum_name", ainfo.get("name", ""))
        company_info = dd.get("company", {}) or dd.get("getCompanyInfo", {})

        return AlbumInfo(
            album_id=album_id,
            album_name=album_name,
            title=album_name,
            subtitle=ainfo.get("Falbum_subtitle", ""),
            singer=sinfo.get("Fsinger_name", ainfo.get("Fsingername", "")),
            singer_id=str(sinfo.get("Fsinger_id", "")),
            release_date=ainfo.get("Fpublic_time", dd.get("aDate", "")),
            description=adesc.get("Falbum_desc", dd.get("desc", "")),
            album_type=dd.get("albumtype", ainfo.get("Falbum_type", "")),
            language=dd.get("language", _detect_lang(album_name)),
            genre=dd.get("genre", ""),
            company=company_info.get("name") or company_info.get("Fcompany_name") or "",
            cover_url=_cover_url(album_id),
            song_count=ainfo.get("Fsongcount", len(album_songs)),
            songs=album_songs,
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://y.qq.com/n/ryqq/albumDetail/{album_id}",
            extra=_remainder(dd, "getAlbumInfo getAlbumDesc company getCompanyInfo getSingerInfo getSongInfo singerInfo albumtype genre language"),
        )

    # ------------------------------------------------------------------
    def search_by_album(self, singer: str, album_name: str, limit: int = 20) -> list[SongInfo]:
        # QQ音乐原生专辑搜索(type=8) + 歌曲搜索fallback
        params = {"w": f"{singer} {album_name}", "format": "json", "p": 1, "n": limit, "type": 8}
        headers = make_headers(referer="https://y.qq.com/")
        with get_client() as client:
            resp = client.get(self.SEARCH_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if data:
            for alb in data.get("data", {}).get("album", {}).get("list", []):
                al_name = alb.get("albumname", "")
                al_singer = alb.get("singername", "")
                if _match_text(al_name, album_name) and _match_text(al_singer, singer):
                    amid = alb.get("albummid", "")
                    album = self.get_album(amid)
                    if album and album.songs:
                        return album.songs
        return super().search_by_album(singer, album_name, limit)

    # ------------------------------------------------------------------
    def get_playlist(self, playlist_id: str) -> Optional[PlaylistInfo]:
        """QQ音乐歌单解析：qzone cdinfo 接口（disstid）"""
        params = {"type": 1, "json": 1, "utf8": 1, "onlysong": 0,
                  "disstid": playlist_id, "format": "json", "g_tk": 5381,
                  "loginUin": 0, "hostUin": 0, "inCharset": "utf8",
                  "outCharset": "utf-8", "notice": 0, "platform": "yqq",
                  "needNewCode": 0}
        headers = make_headers(referer="https://y.qq.com/")
        with get_client() as client:
            resp = client.get(self.PLAYLIST_URL, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data:
            return None
        cdlist = data.get("cdlist") or []
        cd = cdlist[0] if cdlist else {}
        if not cd or not cd.get("dissname") and not cd.get("songlist"):
            return None

        songs = [self._parse_song_item(item) for item in (cd.get("songlist") or [])]
        creator = cd.get("nickname", "") or ""
        tags = cd.get("tags") or []
        tag_names = []
        for t in tags:
            name = t.get("name", "") if isinstance(t, dict) else str(t)
            if name:
                tag_names.append(name)
        tag_str = "、".join(tag_names)
        visit = cd.get("visitnum", 0)
        return PlaylistInfo(
            playlist_id=str(cd.get("dissid", playlist_id)),
            name=cd.get("dissname", ""),
            creator=creator,
            cover_url=cd.get("logo", ""),
            description=cd.get("desc", ""),
            song_count=int(cd.get("total_song_num", len(songs)) or len(songs)),
            play_count=int(visit) if isinstance(visit, int) else 0,
            tags=tag_str,
            songs=songs,
            platform=self.platform,
            platform_name=self.platform_name,
            url=f"https://y.qq.com/n/ryqq/playlist/{playlist_id}",
            extra=_remainder(cd, "dissid dissname logo desc total_song_num songnum nickname tags visitnum songlist"),
        )

    # ------------------------------------------------------------------
    def get_play_url(self, song_id: str) -> str:
        """QQ音乐播放直链 - 朋友圈分享格式"""
        # song_id 即 songmid, 格式: http://c6.y.qq.com/rsc/fcgi-bin/fcg_pyq_play.fcg?songid=&songmid={songmid}&songtype=1&fromtag=50&uin=0&code=5F4F8
        return f"http://c6.y.qq.com/rsc/fcgi-bin/fcg_pyq_play.fcg?songid=&songmid={song_id}&songtype=1&fromtag=50&uin=0&code=5F4F8"

    # ------------------------------------------------------------------
    @staticmethod
    def extract_song_id(text: str) -> str:
        """从输入解析歌曲 songmid：songDetail/xxx、songmid=xxx 或纯 ID"""
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"songDetail/([A-Za-z0-9]+)", s) or re.search(r"songmid=([A-Za-z0-9]+)", s)
        if m:
            return m.group(1)
        if re.fullmatch(r"[A-Za-z0-9]{8,}", s):
            return s
        return ""

    @staticmethod
    def extract_playlist_id(text: str) -> str:
        """从输入解析歌单 dissid：playlist/xxx、disstid=xxx 或纯数字"""
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"playlist/(\d+)", s) or re.search(r"disstid=(\d+)", s)
        if m:
            return m.group(1)
        if s.isdigit():
            return s
        return ""


# ------------------------------------------------------------------
# Helpers

def _cover_url(albummid: str, size: int = 800) -> str:
    if not albummid:
        return ""
    return f"https://y.gtimg.cn/music/photo_new/T002R{size}x{size}M000{albummid}.jpg"


def _join(singers: list) -> str:
    if not singers:
        return ""
    return " / ".join(s.get("name", "") for s in singers if isinstance(s, dict) and s.get("name"))


def _detect_lang(name: str) -> str:
    if not name: return ""
    if re.search(r'[\u4e00-\u9fff]', name): return "国语"
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', name): return "日语"
    if re.search(r'[\uac00-\ud7af]', name): return "韩语"
    return "外语"


def _lang_name(lang_id: int) -> str:
    return {0: "国语", 1: "粤语", 2: "英语", 3: "日语", 4: "韩语", 5: "其他"}.get(lang_id, "")


def _genre_name(gid: int) -> str:
    return {0: "流行", 1: "Pop", 2: "摇滚", 3: "民谣", 4: "电子", 5: "舞曲",
            6: "说唱", 7: "轻音乐", 8: "爵士", 9: "乡村", 10: "古典",
            11: "R&B", 12: "古风", 13: "影视原声", 14: "动漫", 15: "游戏",
            16: "网络", 17: "实验", 18: "另类", 19: "世界音乐", 20: "新世纪"}.get(gid, f"流派#{gid}")


def _ts_to_date(ts: int) -> str:
    if not ts or ts <= 0:
        return ""
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
    except Exception:
        return str(ts)


def _date_to_ts(d: str) -> int:
    if not d:
        return 0
    try:
        return int(datetime.strptime(d, "%Y-%m-%d").timestamp())
    except Exception:
        return 0


def _parse_files(item: dict) -> list[FileInfo]:
    """从搜索结果的 size* 字段构建文件列表"""
    files = []
    for key, q in [("size128","128kbps"),("size320","320kbps"),("sizeflac","FLAC"),("sizeape","APE"),("sizeogg","OGG")]:
        sz = item.get(key, 0)
        if sz:
            files.append(FileInfo(quality=q, size=sz, extension=key.replace("size","").lower()))
    return files


def _remainder(obj: dict, exclude: str) -> dict:
    """返回未映射到标准字段的剩余数据"""
    skip = set(exclude.split())
    return {k: v for k, v in obj.items() if k not in skip}
