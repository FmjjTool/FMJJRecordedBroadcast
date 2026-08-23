"""
四平台音乐下载器（QQ/酷狗/酷我/5sing）。

直链策略（2026-08 实测）：
- 酷狗: m.kugou.com getSongInfo playInfo → sharefs 直链（免费歌 128k 全曲，游客可用）
- 酷我: antiserver convert_url3 wma → 免费歌全曲，VIP 歌 11.3s 试听（下载后按时长判别）
- 5sing: mobileapi getSongUrl → hqurl 192k / lqurl 128k 全曲，游客可用
- QQ: vkey 接口游客+普通 Cookie 均被拒（860100005），仅支持解析，不支持下载

download_song_smart：失败/仅试听时自动换源（酷我→酷狗→网易云），跨平台找同名免费曲。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .models import SongInfo
from .utils import get_client, make_headers, safe_json


@dataclass
class DownloadResult:
    success: bool = False
    file_path: str = ""
    error_message: str = ""
    is_preview: bool = False
    quality: str = ""
    via_platform: str = ""  # 换源成功时记录实际提供文件的平台


@dataclass
class _UrlResult:
    url: str = ""
    ext: str = "mp3"
    error: str = ""
    is_preview: bool = False
    quality: str = ""
    referer: str = ""


_INVALID_FS = re.compile(r'[\\/:*?"<>|\r\n\t]')
_PREVIEW_SECONDS = 15  # 短于此且原曲更长 → 视为试听片段


def _safe_name(text: str, maxlen: int = 80) -> str:
    text = _INVALID_FS.sub(" ", (text or "").strip()).strip()
    return (text[:maxlen].strip() or "未知") + " "


def _detect_preview(content: bytes, expected_seconds: int) -> bool:
    if expected_seconds <= 30:
        return False
    try:
        import io
        from mutagen.mp3 import MP3
        real = MP3(io.BytesIO(content)).info.length
    except Exception:
        # 无法解析时长时，按大小粗判：128k 试听约 180KB
        return expected_seconds > 60 and len(content) < 400_000
    return real < _PREVIEW_SECONDS


def _save(content: bytes, base_name: str, ext: str, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{base_name}{ext}")
    i = 1
    while os.path.exists(path):
        path = os.path.join(out_dir, f"{base_name}({i}){ext}")
        i += 1
    with open(path, "wb") as f:
        f.write(content)
    return path


def _download_to(url: str, referer: str, base_name: str, ext: str, out_dir: str,
                 expected_seconds: int = 0) -> DownloadResult:
    with get_client(timeout=60) as client:
        resp = client.get(url, headers=make_headers(referer=referer))
    if resp.status_code != 200 or len(resp.content) < 1024:
        return DownloadResult(error_message=f"直链获取失败(HTTP {resp.status_code})")
    preview = _detect_preview(resp.content, expected_seconds)
    path = _save(resp.content, base_name, ext, out_dir)
    if preview:
        return DownloadResult(success=True, file_path=path, is_preview=True)
    return DownloadResult(success=True, file_path=path)


# ---------------------------------------------------------------------------
# 各平台直链解析


def _url_kugou(song: SongInfo, quality: str) -> _UrlResult:
    with get_client() as client:
        resp = client.get(
            "http://m.kugou.com/app/i/getSongInfo.php",
            params={"cmd": "playInfo", "hash": song.song_id},
            headers=make_headers(referer="http://m.kugou.com/"))
    data = safe_json(resp.text) or {}
    url = data.get("url") or ""
    if not url:
        if data.get("songName") is None and data.get("singerName") is None:
            return _UrlResult(error="酷狗未找到该歌曲（可能已下架）")
        priv = data.get("privilege") or data.get("128privilege")
        if priv == 10 or data.get("pay_type") in (1, 3):
            return _UrlResult(error="酷狗 VIP 付费歌曲，游客无法获取直链")
        return _UrlResult(error="酷狗未返回播放直链（歌曲可能已下架）")
    ext = (data.get("extName") or "mp3").lower()
    return _UrlResult(url=url, ext=ext if ext.startswith(".") else f".{ext}",
                      quality=f"{data.get('bitRate', 128)}kbps")


def _url_kuwo(song: SongInfo, quality: str) -> _UrlResult:
    with get_client() as client:
        resp = client.get(
            "https://antiserver.kuwo.cn/anti.s",
            params={"type": "convert_url3", "rid": song.song_id, "format": "wma"},
            headers=make_headers(referer="https://kuwo.cn/"))
    data = safe_json(resp.text) or {}
    url = data.get("url") or ""
    if not url:
        return _UrlResult(error="酷我拒绝该歌曲的直链请求（VIP/版权限制）")
    return _UrlResult(url=url, ext=".wma", quality="128kbps")


def _url_fivesing(song: SongInfo, quality: str) -> _UrlResult:
    song_id = song.song_id
    if "/" not in song_id:
        # 搜索结果带的是酷狗 hash（共享数据库）→ 走酷狗通道兜底
        got = _url_kugou(song, quality)
        got.referer = _REFERERS["kugou"]
        return got
    mode, num = song_id.split("/", 1)
    with get_client() as client:
        resp = client.get(
            "http://mobileapi.5sing.kugou.com/song/getSongUrl",
            params={"songtype": mode, "songid": num, "version": "6.6.72"},
            headers=make_headers(referer="https://5sing.kugou.com/"))
    data = safe_json(resp.text) or {}
    payload = data.get("data") or {}
    want_hq = quality != "lq"
    url = (payload.get("hqurl") if want_hq else "") or payload.get("lqurl") or ""
    if not url:
        return _UrlResult(error="5sing 未返回播放直链")
    ext = payload.get("hqext") or payload.get("lqext") or "mp3"
    ext = (ext or "mp3").lower().lstrip(".")
    tag = "192kbps" if url == payload.get("hqurl") else "128kbps"
    return _UrlResult(url=url, ext=f".{ext}", quality=tag)


def _url_qq(song: SongInfo, quality: str) -> _UrlResult:
    return _UrlResult(error="QQ音乐直链接口已收紧（需 VIP 登录态），当前仅支持解析，暂不支持下载")


_RESOLVERS = {
    "kugou": _url_kugou,
    "kuwo": _url_kuwo,
    "5sing": _url_fivesing,
    "qq": _url_qq,
}

_REFERERS = {
    "kugou": "https://www.kugou.com/",
    "kuwo": "https://kuwo.cn/",
    "5sing": "https://5sing.kugou.com/",
    "qq": "https://y.qq.com/",
}


# ---------------------------------------------------------------------------
# 对外入口


def download_song(song: SongInfo, out_dir: str, quality: str = "") -> DownloadResult:
    """下载一首歌。song.platform 决定解析通道。"""
    resolver = _RESOLVERS.get(song.platform)
    if not resolver:
        return DownloadResult(error_message=f"平台 {song.platform or '?'} 暂不支持下载")
    if not song.song_id:
        return DownloadResult(error_message="缺少歌曲 ID")

    got = resolver(song, quality)
    if got.error:
        return DownloadResult(error_message=got.error)

    base = f"{_safe_name(song.singer)} - {_safe_name(song.song_name)}".strip()
    base = re.sub(r"\s+", " ", base)
    referer = got.referer or _REFERERS[song.platform]
    return _download_to(got.url, referer, base, got.ext, out_dir, song.duration)


def platform_downloadable(platform: str) -> bool:
    return platform in ("kugou", "kuwo", "5sing")


def quality_options(platform: str) -> list[tuple[str, str]]:
    """(值, 显示名) 列表。"""
    if platform == "5sing":
        return [("hq", "192k 高音质"), ("lq", "128k 标准")]
    if platform == "kugou":
        return [("128k", "128k")]
    if platform == "kuwo":
        return [("128k", "128k")]
    return [("default", "默认")]


# ---------------------------------------------------------------------------
# 跨平台智能换源：失败/仅试听时，去其他平台找同名曲

_FALLBACK_ORDER = ["kuwo", "kugou"]
_FALLBACK_NAMES = {"kuwo": "酷我", "kugou": "酷狗", "netease": "网易云"}


def _norm_text(s: str) -> str:
    return re.sub(r"\s+", "", (s or "")).lower()


def _match_score(cand_name, cand_singer, cand_duration, song: SongInfo) -> int:
    """候选与原曲相似度：歌名必须沾边，歌手/时长加权。"""
    n, cn = _norm_text(song.song_name), _norm_text(cand_name)
    s, cs = _norm_text(song.singer), _norm_text(cand_singer)
    if not n or not cn:
        return -100
    if cn == n:
        score = 50
    elif n in cn or cn in n:
        score = 35
    else:
        return -100
    if s and cs:
        if s == cs:
            score += 30
        elif s in cs or cs in s:
            score += 20
        else:
            score -= 20
    dur = song.duration or 0
    if dur and cand_duration:
        diff = abs(dur - cand_duration)
        if diff <= 3:
            score += 20
        elif diff <= 10:
            score += 5
        elif diff > 30:
            score -= 25
    return score


def _find_on(platform: str, song: SongInfo):
    """在指定平台搜索同名曲，返回最佳候选 SongInfo（无则 None）。"""
    from .scrapers.kuwo import KuwoScraper
    from .scrapers.kugou import KugouScraper
    keyword = f"{song.song_name} {song.singer}".strip()
    try:
        if platform == "kuwo":
            cands = KuwoScraper().search_song(keyword, 1, 20) or []
        else:
            cands = KugouScraper().search_song(keyword, 1, 20) or []
    except Exception:
        return None
    scored = []
    for c in cands:
        sc = _match_score(c.song_name, c.singer, c.duration, song)
        if sc >= 45:
            scored.append((sc, c))
    if not scored:
        return None
    scored.sort(key=lambda t: -t[0])
    return scored[0][1]


def _file_is_preview(path: str, expected_seconds: int) -> bool:
    """网易云游客对 VIP 歌只给 30s 试听：实际时长不足原曲一半即视为试听。"""
    if expected_seconds <= 30:
        return False
    try:
        import mutagen
        real = mutagen.File(path).info.length
        return real + 5 < expected_seconds * 0.5
    except Exception:
        return False


def _try_netease(song: SongInfo, out_dir: str) -> Optional[DownloadResult]:
    """网易云兜底：用已存 Cookie 搜索下载（Cookie 在则可用，无则跳过）。"""
    try:
        from auth import get_netease_cookie, sync_netease_to_legacy
        from netease import CookieManager, COOKIE_FILE, MusicDownloader, search_music
        content = get_netease_cookie()
        cookies = CookieManager(COOKIE_FILE).parse_cookie_string(content) if content else {}
        keyword = f"{song.song_name} {song.singer}".strip()
        results = search_music(keyword, cookies, limit=20) or []
    except Exception:
        return None
    best, best_score = None, 45
    for it in results:
        if not isinstance(it, dict):
            continue
        sc = _match_score(it.get("name", ""), it.get("artists", ""),
                          (it.get("duration") or 0) // 1000, song)
        if sc >= best_score:
            best, best_score = it, sc
    if not best:
        return None
    try:
        sync_netease_to_legacy()
        dl = MusicDownloader(download_dir=out_dir, cookie_file=COOKIE_FILE)
        r = dl.download_music_file(int(best["id"]), "exhigh")
    except Exception:
        return None
    if r and r.success and r.file_path:
        return DownloadResult(success=True, file_path=r.file_path,
                              quality="exhigh", via_platform="网易云",
                              is_preview=_file_is_preview(r.file_path, song.duration or 0))
    return None


def download_song_smart(song: SongInfo, out_dir: str, quality: str = "",
                        fallback: bool = True) -> DownloadResult:
    """下载一首歌，失败或仅试听时自动换源：酷我→酷狗→网易云。"""
    result = download_song(song, out_dir, quality)
    if result.success and not result.is_preview:
        return result
    if not fallback:
        return result

    # 保底：至少拿到试听片段也比纯失败强
    best = result if result.success else None
    for pf in _FALLBACK_ORDER:
        if pf == song.platform:
            continue
        cand = _find_on(pf, song)
        if cand is None:
            continue
        try:
            r = download_song(cand, out_dir, quality)
        except Exception:
            continue
        if r.success and not r.is_preview:
            r.via_platform = _FALLBACK_NAMES.get(pf, pf)
            return r
        if r.success and best is None:
            best = r

    nr = _try_netease(song, out_dir)
    if nr is not None:
        if nr.success and not nr.is_preview:
            return nr
        if nr.success and best is None:
            best = nr

    if best is not None:
        return best
    return result
