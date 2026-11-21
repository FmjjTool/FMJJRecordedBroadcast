"""
======================================================================
  5sing音乐 (5sing.kugou.com) 抓取器 - 三种模式: 原唱/翻唱/伴奏
  策略: 5sing主站受CloudFlare保护，改用酷狗搜索API（共享数据库）
======================================================================
"""

from __future__ import annotations

import re
from typing import Optional

from ..base_scraper import BaseScraper
from ..models import SongInfo, PlaylistInfo
from ..utils import get_client, make_headers, safe_json, parse_duration


FIVESING_MODES = {
    "yc": {"key": "yc", "name": "原唱", "type_id": 1},
    "fc": {"key": "fc", "name": "翻唱", "type_id": 2},
    "bz": {"key": "bz", "name": "伴奏", "type_id": 3},
}

# 5sing 的一级路径里这些不是「用户名」，解析空间链接时要排除
_NON_USER_PATHS = {
    "m", "my", "album", "topic", "search", "index.html", "yc", "fc", "bz",
    "gd", "singer", "list", "static", "dj", "fans", "friend", "zj", "api",
}


class FiveSingScraper(BaseScraper):
    platform = "5sing"
    platform_name = "5sing音乐"
    BASE = "https://5sing.kugou.com"

    # 使用酷狗搜索API（5sing共享数据库）
    KUGOU_SEARCH = "http://mobilecdn.kugou.com/api/v3/search/song"
    KUGOU_LYRIC  = "http://m.kugou.com/app/i/krc.php"

    # ------------------------------------------------------------------
    def search_by_mode(self, keyword: str, mode: str, page: int = 1, limit: int = 10) -> list[SongInfo]:
        cfg = FIVESING_MODES.get(mode)
        if not cfg:
            return []
        songs = self._search_kugou(keyword, cfg, page, limit)
        for s in songs:
            s.mode = cfg["name"]
        return songs

    def search_all_modes(self, keyword: str, page: int = 1, limit: int = 10) -> dict[str, list[SongInfo]]:
        return {mc["name"]: self.search_by_mode(keyword, mk, page, limit) for mk, mc in FIVESING_MODES.items()}

    def search_song(self, keyword: str, page: int = 1, limit: int = 10) -> list[SongInfo]:
        merged = []
        for s in self.search_all_modes(keyword, page, limit).values():
            merged.extend(s)
        return merged

    # ------------------------------------------------------------------
    def get_song_detail(self, song_id: str) -> Optional[SongInfo]:
        """song_id = hash (from Kugou) 或 yc/123456（5sing 歌曲页形式）"""
        if "/" in song_id:
            return self._parse_song_page(song_id)
        lyrics = self._get_lyrics(song_id)
        if not lyrics:
            return None
        return SongInfo(
            song_id=song_id, lyrics=lyrics,
            platform=self.platform, platform_name=self.platform_name,
            url=f"https://5sing.kugou.com/m/detail/{song_id}.html",
        )

    def _parse_song_page(self, song_id: str) -> Optional[SongInfo]:
        """5sing 歌曲页 HTML 解析：标题「歌名 - 歌手 - 5sing音乐」+ 封面"""
        url = f"https://5sing.kugou.com/{song_id}.html"
        with get_client() as client:
            resp = client.get(url, headers=make_headers(referer="https://5sing.kugou.com/"))
        body = resp.text
        tm = re.search(r"<title>([^<]+)</title>", body)
        song_name, singer = "", ""
        if tm:
            parts = [p.strip() for p in tm.group(1).split(" - ")]
            if parts:
                song_name = parts[0]
            if len(parts) >= 2:
                singer = parts[1]
        if not song_name:
            return None
        mode_key = song_id.split("/", 1)[0]
        return SongInfo(
            song_id=song_id,
            song_name=song_name,
            title=song_name,
            singer=singer,
            language=_detect_lang(song_name),
            cover_url=_first_cover(body),
            mode=FIVESING_MODES.get(mode_key, {}).get("name", ""),
            platform=self.platform,
            platform_name=self.platform_name,
            url=url,
        )

    # ------------------------------------------------------------------
    def get_playlist(self, playlist_id: str) -> Optional[PlaylistInfo]:
        """5sing 歌单解析：HTML 页面解析（playlist_id 为完整 URL 或 uid/dj/hex 路径）"""
        url = self._normalize_playlist_url(playlist_id)
        if not url:
            return None
        with get_client() as client:
            resp = client.get(url, headers=make_headers(referer="https://5sing.kugou.com/"))
        body = resp.text

        name, creator = "", ""
        tm = re.search(r"<title>([^<]+)</title>", body)
        if tm:
            parts = [p.strip() for p in tm.group(1).split(" - ")]
            if parts:
                name = parts[0]
            if len(parts) >= 2:
                creator = parts[1].replace("的歌单", "").strip()

        songs, seen = [], set()
        for m in re.finditer(r'href="(?:https?://5sing\.kugou\.com)?/?((?:yc|fc|bz)/\d+)\.html"[^>]*>\s*([^<]{1,60}?)\s*</a>', body):
            sid, sname = m.group(1), m.group(2).strip()
            if not sname or sid in seen:
                continue
            seen.add(sid)
            mode_key = sid.split("/", 1)[0]
            songs.append(SongInfo(
                song_id=sid,
                song_name=sname,
                title=sname,
                language=_detect_lang(sname),
                mode=FIVESING_MODES.get(mode_key, {}).get("name", ""),
                platform=self.platform,
                platform_name=self.platform_name,
                url=f"https://5sing.kugou.com/{sid}.html",
            ))
        if not name and not songs:
            return None

        count_m = re.search(r"(\d+)\s*首", body)
        return PlaylistInfo(
            playlist_id=url,
            name=name,
            creator=creator,
            cover_url=_first_cover(body),
            description="",
            song_count=int(count_m.group(1)) if count_m else len(songs),
            songs=songs,
            platform=self.platform,
            platform_name=self.platform_name,
            url=url,
            extra={},
        )

    @staticmethod
    def _normalize_playlist_url(text: str) -> str:
        """把用户输入规范成 5sing 歌单 URL（https://5sing.kugou.com/{uid}/dj/{hex}.html）"""
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"(?:https?://5sing\.kugou\.com)?/?(\d+)/dj/([0-9a-fA-F]+)\.html", s)
        if m:
            return f"https://5sing.kugou.com/{m.group(1)}/dj/{m.group(2)}.html"
        return ""

    # ------------------------------------------------------------------
    def get_play_url(self, song_id: str) -> str:
        return f"https://m.kugou.com/api/v1/wechat/index?cmd=101&hash={song_id}"

    # ------------------------------------------------------------------
    @staticmethod
    def extract_song_id(text: str) -> str:
        """从输入解析歌曲 ID：yc/123456.html 链接或 yc/123456 形式或酷狗 hash"""
        s = (text or "").strip()
        if not s:
            return ""
        m = re.search(r"/(yc|fc|bz)/(\d+)\.html", s) or re.search(r"/m/(yc|fc|bz)/(\d+)", s)
        if m:
            return f"{m.group(1)}/{m.group(2)}"
        if re.fullmatch(r"(yc|fc|bz)/\d+", s):
            return s
        if re.fullmatch(r"[a-fA-F0-9]{32}", s):
            return s
        return ""

    @staticmethod
    def extract_playlist_id(text: str) -> str:
        """从输入解析歌单：返回规范化的歌单 URL（uid/dj/hex 形式）"""
        return FiveSingScraper._normalize_playlist_url(text or "")

    # ------------------------------------------------------------------
    # 专辑：5sing 音乐空间里的「专辑」页（/{user}/zj/{hex}.html）曲目需登录、
    # 没有公开接口；这里复用与 5sing 共享曲库的酷狗专辑接口，保证专辑解析、
    # 整张下载与其它平台一致（页面上标注「与酷狗同库」）。
    def get_album(self, album_id: str) -> Optional[AlbumInfo]:
        if not album_id or album_id == "0":
            return None
        with get_client() as client:
            resp = client.get(
                "http://mobilecdn.kugou.com/api/v3/album/info",
                params={"albumid": album_id},
                headers=make_headers(referer="http://m.kugou.com/"))
        data = safe_json(resp.text)
        if not data or data.get("status") != 1:
            return None
        info = data.get("data", {}) or {}
        name = info.get("albumname", "")
        if not name:
            return None
        return AlbumInfo(
            album_id=album_id, album_name=name, title=name,
            singer=info.get("singername", ""),
            release_date=info.get("publishtime", ""),
            description=info.get("intro", ""),
            cover_url=(info.get("imgurl", "") or "").replace("{size}", "480"),
            song_count=info.get("songcount", 0),
            songs=self._album_songs(album_id),
            platform=self.platform, platform_name=self.platform_name,
            url=f"https://5sing.kugou.com/album/{album_id}.html",
        )

    def _album_songs(self, album_id: str) -> list[SongInfo]:
        """专辑曲目走酷狗 album/song（只给 filename，拆歌手/歌名）"""
        params = {"albumid": album_id, "page": 1, "pagesize": 100}
        headers = make_headers(referer="http://m.kugou.com/")
        rows: list[dict] = []
        page = 1
        with get_client() as client:
            while page <= 30:  # 防御性上限 3000 首
                params["page"] = page
                resp = client.get(
                    "http://mobilecdn.kugou.com/api/v3/album/song",
                    params=params, headers=headers)
                data = safe_json(resp.text) or {}
                batch = ((data.get("data") or {}).get("info") or [])
                if not batch:
                    break
                rows.extend(batch)
                if len(batch) < params["pagesize"]:
                    break
                page += 1
        songs = []
        for item in rows:
            filename = item.get("filename", "") or ""
            song_name, singer = filename, ""
            if " - " in filename:
                singer, song_name = filename.split(" - ", 1)
            if not song_name:
                continue
            audio_id = str(item.get("album_audio_id", "") or "")
            song_hash = item.get("hash", "")
            songs.append(SongInfo(
                song_id=song_hash, song_name=song_name, title=song_name,
                singer=singer,
                album_id=str(item.get("album_id", "") or ""),
                duration=parse_duration(item.get("duration", 0)),
                language=_detect_lang(song_name),
                cover_url=f"http://imge.kugou.com/stdmusic/240/{audio_id}.jpg" if audio_id else "",
                platform=self.platform, platform_name=self.platform_name,
                url=f"https://5sing.kugou.com/m/detail/{song_hash}.html",
                extra=_remainder(item, "hash filename album_id album_audio_id duration filesize"),
            ))
        return songs

    # ---- 专辑搜索 / ID 解析（与酷狗共享库） ----
    def search_album(self, keyword: str, limit: int = 30) -> list[AlbumInfo]:
        from .kugou import KugouScraper
        albums = KugouScraper().search_album(keyword, limit)
        for album in albums:
            album.platform = self.platform
            album.platform_name = self.platform_name
            album.url = f"https://5sing.kugou.com/album/{album.album_id}.html"
        return albums

    @staticmethod
    def extract_album_id(text: str) -> str:
        """album/{id}.html、albumid={id} 或纯数字（与酷狗同库）"""
        s = (text or "").strip()
        if not s:
            return ""
        m = (re.search(r"/album/(\d+)", s)
             or re.search(r"albumid=(\d+)", s))
        if m:
            return m.group(1)
        if s.isdigit():
            return s
        return ""

    # ------------------------------------------------------------------
    # 歌手主页（5sing 音乐人为「音乐空间」，歌曲走共享搜索）
    def search_artist(self, keyword: str, limit: int = 10) -> list[dict]:
        key = (keyword or "").strip()
        if not key:
            return []
        artists, seen = [], set()
        for song in self.search_song(key, 1, 40):
            singer = (song.singer or "").strip()
            if not singer or singer in seen:
                continue
            if key.lower() not in singer.lower():
                continue
            seen.add(singer)
            artists.append({
                "id": singer,
                "name": singer,
                "pic": song.cover_url or "",
                "song_count": 0,
                "album_count": 0,
                "desc": "",
            })
            if len(artists) >= limit:
                break
        return artists

    def get_artist(self, artist_id: str, name: str = "") -> Optional[dict]:
        slug = (artist_id or "").strip()
        if not slug:
            return None
        # 只有数字 uid / 英文自定义域名才当空间地址去抓；中文是歌手名，走共享库搜索
        is_space = bool(re.fullmatch(r"[A-Za-z0-9_\-]{3,40}", slug)) and \
            slug.lower() not in _NON_USER_PATHS
        uid, nick, fans, head = self._space_info(slug) if is_space else ("", "", 0, "")
        artist_name = name or nick
        if not artist_name and uid:
            artist_name = f"5sing用户{uid}"
        if not artist_name:
            artist_name = "" if is_space else slug
        if not artist_name:
            return None

        songs = self._space_songs(slug, artist_name, head) if is_space else []
        if not songs:
            # 空间页偶尔取不到（自定义域名/私密曲目/按名搜索），退回共享曲库按名搜索
            songs, seen = [], set()
            for song in self.search_song(artist_name, 1, 60):
                singer = (song.singer or "").strip()
                if artist_name not in singer and singer not in artist_name:
                    continue
                key = (song.song_name, song.mode)
                if key in seen:
                    continue
                seen.add(key)
                songs.append(song)

        # 5sing 空间的「专辑」页曲目需登录且无公开接口，歌手主页不摆专辑区块，
        # 避免拿共享库里的酷狗专辑冒充（专辑解析页签仍可单独用专辑链接/名称）
        return {
            "id": artist_id,
            "name": artist_name,
            "pic": head,
            "desc": "",
            "song_count": len(songs),
            "album_count": 0,
            "fans": fans or 0,
            "url": f"https://5sing.kugou.com/{slug}/default.html",
            "songs": songs,
            "albums": [],
        }

    # ---- 5sing 音乐空间：页面 SSR 解析（昵称/头像/粉丝 + 原唱/翻唱/伴奏曲目） ----
    def _space_page(self, path: str) -> str:
        url = f"{self.BASE}/{str(path).lstrip('/')}"
        with get_client() as client:
            resp = client.get(url, headers=make_headers(referer="https://5sing.kugou.com/"))
        raw = resp.content
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("gbk", errors="replace")

    def _space_info(self, slug: str) -> tuple[str, str, int, str]:
        """返回 (uid, 昵称, 粉丝数, 头像)"""
        try:
            html = self._space_page(f"{slug}/default.html")
        except Exception:
            return "", "", 0, ""
        uid_m = re.search(r"OwnerUserID\s*=\s*(\d+)", html)
        nick_m = re.search(r"OwnerNickName\s*=\s*'([^']*)'", html)
        fans_m = re.search(r'id="totalfans">\s*<a[^>]*>\s*([\d]+)\s*</a>', html)
        head_m = re.search(r'<span><img src="(https://wsingbssdl[^"]+)"', html)
        fans = int(fans_m.group(1)) if fans_m else 0
        return (uid_m.group(1) if uid_m else "",
                nick_m.group(1) if nick_m else "",
                fans,
                head_m.group(1) if head_m else "")

    def _space_songs(self, slug: str, artist_name: str, cover: str = "") -> list[SongInfo]:
        """音乐空间曲目：/{slug}/{yc|fc|bz}/{page}.html 的 SSR 列表。

        列表页只给歌名/上传时间/播放量，没有封面和时长：
        - 封面用空间头像占位（5sing 列表不暴露单曲封面）
        - 时长逐首用 getSongUrl 的文件大小按码率折算
        """
        import html as html_lib
        import math
        from concurrent.futures import ThreadPoolExecutor

        def parse_entries(body: str):
            return re.findall(
                r'<strong class="lt list_name"><a[^>]*href="[^"]*?/((?:yc|fc|bz)/(\d+))\.html"'
                r'[^>]*title="([^"]*)"', body)

        def add_entries(entries, cfg):
            for song_id, _num, title in entries:
                if song_id in seen:
                    continue
                seen.add(song_id)
                song_name = html_lib.unescape(title).strip()
                # 部分上传把歌手写进标题（「封茗囧菌 - 翩翩」），统一去掉前缀
                prefix = f"{artist_name} - "
                if song_name.startswith(prefix):
                    song_name = song_name[len(prefix):].strip()
                if not song_name:
                    continue
                songs.append(SongInfo(
                    song_id=song_id,
                    song_name=song_name,
                    title=song_name,
                    singer=artist_name,
                    language=_detect_lang(song_name),
                    cover_url=cover,
                    mode=cfg["name"],
                    platform=self.platform,
                    platform_name=self.platform_name,
                    url=f"https://5sing.kugou.com/{song_id}.html",
                ))

        songs, seen = [], set()
        tasks = []  # 第 2 页起并行抓，避免 27 页顺序等 15 秒以上
        for mode_key, cfg in FIVESING_MODES.items():
            try:
                first = self._space_page(f"{slug}/{mode_key}/1.html")
            except Exception:
                continue
            entries = parse_entries(first)
            if not entries:
                continue
            add_entries(entries, cfg)
            total_m = re.search(r"song_tit_total[^>]*>\((\d+)首\)", first)
            pages = 1
            if total_m:
                pages = min(50, max(1, math.ceil(int(total_m.group(1)) / 10)))
            elif len(entries) >= 10:
                pages = 20
            for page in range(2, pages + 1):
                tasks.append((mode_key, cfg, page))

        if tasks:
            try:
                with get_client(timeout=15) as client, \
                        ThreadPoolExecutor(max_workers=8) as ex:
                    def fetch(task):
                        mode_key, cfg, page = task
                        try:
                            body = client.get(
                                f"{self.BASE}/{slug}/{mode_key}/{page}.html",
                                headers=make_headers(referer="https://5sing.kugou.com/")
                            ).content.decode("utf-8", errors="replace")
                        except Exception:
                            return cfg, []
                        return cfg, parse_entries(body)

                    for cfg, entries in ex.map(fetch, tasks):
                        add_entries(entries, cfg)
            except Exception:
                pass

        self._fill_durations(songs)
        return songs

    def _fill_durations(self, songs: list[SongInfo], workers: int = 12) -> None:
        """5sing 没有时长接口，用播放地址接口返回的文件大小按码率折算（lq 128k 最稳）。

        260 首要发 260 次小请求：复用同一个 httpx 连接池（每首新建连接会多花 TLS 握手）。
        """
        from concurrent.futures import ThreadPoolExecutor

        def one(client, song: SongInfo) -> None:
            try:
                mode, num = song.song_id.split("/", 1)
                resp = client.get(
                    "http://mobileapi.5sing.kugou.com/song/getSongUrl",
                    params={"songtype": mode, "songid": num, "version": "6.6.72"},
                    headers=make_headers(referer="https://5sing.kugou.com/"))
                payload = (safe_json(resp.text) or {}).get("data") or {}
            except Exception:
                return
            size, rate = 0, 0
            for field, bitrate in (("lqsize", 128000), ("hqsize", 192000), ("sqsize", 320000)):
                try:
                    value = int(payload.get(field) or 0)
                except (TypeError, ValueError):
                    value = 0
                if value:
                    size, rate = value, bitrate
                    break
            if size and rate:
                song.duration = max(1, int(round(size * 8 / rate)))

        if not songs:
            return
        with get_client(timeout=10) as client, \
                ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            list(ex.map(lambda s: one(client, s), songs))

    @staticmethod
    def extract_artist_id(text: str) -> str:
        """5sing 空间链接 /{uid或自定义域名}/default.html、userid={uid} 或纯数字/英文域名"""
        s = (text or "").strip()
        if not s:
            return ""
        m = (re.search(r"5sing\.kugou\.com/([A-Za-z0-9_\-]+)(?:/|$)", s)
             or re.search(r"userid=([A-Za-z0-9_\-]+)", s, re.I))
        if m:
            slug = m.group(1)
            if slug.lower() not in _NON_USER_PATHS:
                return slug
        if re.fullmatch(r"[A-Za-z0-9_\-]{3,40}", s):
            return s
        return ""

    # ------------------------------------------------------------------
    def _search_kugou(self, keyword: str, cfg: dict, page: int, limit: int) -> list[SongInfo]:
        """通过酷狗搜索获取5sing歌曲，用 isoriginal / Accompany / album_name 区分模式"""
        params = {"keyword": keyword, "page": page, "pagesize": limit * 3, "showtype": 1, "format": "json"}
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.KUGOU_SEARCH, params=params, headers=headers)
        data = safe_json(resp.text)
        if not data or data.get("status") != 1:
            return []

        songs = []
        for item in data.get("data", {}).get("info", []):
            song_hash = item.get("hash", "")
            song_name = item.get("songname", "").replace("<em>", "").replace("</em>", "")
            album_name = item.get("album_name", "") or ""
            accompany = item.get("Accompany", 0)
            is_original = item.get("isoriginal", 0)
            othername = item.get("othername_original", "") or ""

            # ---- 模式判定 ----
            mode_key = cfg["key"]
            matched = False

            if mode_key == "yc":  # 原唱
                # isoriginal=1 或 (非伴奏 且 非翻唱 且 非DJ)
                if is_original == 1:
                    matched = True
                elif not accompany and "翻唱" not in album_name and "DJ" not in othername and "伴奏" not in song_name:
                    matched = True

            elif mode_key == "bz":  # 伴奏
                # Accompany=1 或 歌名/专辑含"伴奏"
                if accompany == 1:
                    matched = True
                elif "伴奏" in song_name or "伴奏" in album_name:
                    matched = True

            elif mode_key == "fc":  # 翻唱
                # 非原唱 且 非伴奏 且 (含"翻唱"或默认)
                if is_original != 1 and accompany != 1:
                    if "翻唱" in album_name or "cover" in album_name.lower():
                        matched = True
                    elif not ("伴奏" in song_name or "DJ" in othername):
                        matched = True

            if not matched:
                continue

            songs.append(SongInfo(
                song_id=song_hash,
                song_name=song_name,
                singer=item.get("singername", ""),
                language=_detect_lang(song_name),
                album_name=album_name,
                album_id=str(item.get("album_id", "")),
                duration=parse_duration(item.get("duration", 0)),
                cover_url=(item.get("imgUrl", "") or item.get("imgurl", "")).replace("{size}", "480"),
                platform=self.platform,
                platform_name=self.platform_name,
                url=f"https://5sing.kugou.com/m/detail/{song_hash}.html",
                release_date=item.get("release_date", ""),
                extra=_remainder(item, "hash songname singername album_name album_id remark duration imgUrl imgurl Accompany sourceid isoriginal"),
            ))
            if len(songs) >= limit:
                break
        return songs

    # ------------------------------------------------------------------
    def _get_lyrics(self, song_hash: str) -> str:
        params = {"hash": song_hash, "timelength": 300000, "cmd": "100"}
        headers = make_headers(referer="http://m.kugou.com/")
        with get_client() as client:
            resp = client.get(self.KUGOU_LYRIC, params=params, headers=headers)
        text = resp.text.strip()
        # 去除 BOM 头
        if text.startswith("\ufeff"):
            text = text[1:]
        if text.startswith("["):  # LRC/KRC 歌词格式
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
    def get_play_url(self, song_id: str) -> str:
        return f"https://m.kugou.com/api/v1/wechat/index?cmd=101&hash={song_id}"


# ------------------------------------------------------------------

def _first_cover(body: str) -> str:
    """取页面第一张无缩略后缀的 wsingbssdl 大图作为封面"""
    for m in re.finditer(r'(?:src|data-src|href)="(https://wsingbssdl\.kugou\.com/[^"]+?\.(?:jpg|jpeg|png))"', body):
        u = m.group(1)
        fname = u.rsplit("/", 1)[-1]
        if "_" not in fname.split(".")[0]:
            return u
    return ""


def _detect_lang(name: str) -> str:
    if not name: return ""
    if re.search(r'[\u4e00-\u9fff]', name): return "国语"
    if re.search(r'[\u3040-\u309f\u30a0-\u30ff]', name): return "日语"
    if re.search(r'[\uac00-\ud7af]', name): return "韩语"
    return "外语"


def _remainder(obj: dict, exclude: str) -> dict:
    skip = set(exclude.split())
    return {k: v for k, v in obj.items() if k not in skip}
