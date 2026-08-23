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
from ..models import SongInfo, AlbumInfo, PlaylistInfo
from ..utils import get_client, make_headers, safe_json, parse_duration


FIVESING_MODES = {
    "yc": {"key": "yc", "name": "原唱", "type_id": 1},
    "fc": {"key": "fc", "name": "翻唱", "type_id": 2},
    "bz": {"key": "bz", "name": "伴奏", "type_id": 3},
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
    def get_album(self, album_id: str) -> Optional[AlbumInfo]:
        """酷狗 v3 专辑接口（共享数据库）→ 取封面等专辑信息。"""
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
            songs=[],
            platform=self.platform, platform_name=self.platform_name,
            url=f"https://5sing.kugou.com/album/{album_id}.html",
        )

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
