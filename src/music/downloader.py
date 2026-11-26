"""
音乐下载用例：统一选择最高可用档，并保留平台实际返回的音质与试听状态。

平台授权与文件定位由适配器处理；指定档位不静默降低，自动换源保留音质要求。
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .models import SongInfo
from .utils import get_client, make_headers, safe_json, kuwo_secret, kuwo_web_token, KUWO_COOKIE_KEY


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


_PREVIEW_SECONDS = 15  # 短于此且原曲更长 → 视为试听片段


def _safe_name(text: str, maxlen: int = 80) -> str:
    from toolbox.utils import sanitize_filename
    return sanitize_filename(text, maxlen, "未知", collapse_ws=False)


def _detect_preview(content: bytes, expected_seconds: int) -> bool:
    if expected_seconds <= 30:
        return False
    try:
        import io
        import mutagen
        audio = mutagen.File(io.BytesIO(content))
        real = audio.info.length
    except Exception:
        # 无法解析时长时，按大小粗判：128k 试听约 180KB
        return expected_seconds > 60 and len(content) < 400_000
    return real < _PREVIEW_SECONDS or real + 5 < expected_seconds * 0.5


def normalize_quality(quality: str = "") -> str:
    """兼容桌面旧值及 Web 音质值；空值统一为最高可用。"""
    value = str(quality or "").strip().lower()
    value = {"": "highest", "default": "highest", "lossless": "flac",
             "exhigh": "320k", "higher": "192k", "standard": "128k",
             "sq": "320k", "hq": "192k", "lq": "128k",
             "128": "128k", "320": "320k"}.get(value, value)
    if value not in {"highest", "flac", "320k", "192k", "128k"}:
        raise ValueError("不支持该音质，请重新选择")
    return value


def _quality_matches(requested: str, ext: str, actual: str) -> bool:
    if requested == "highest":
        return True
    lossless = ext.lower() in (".flac", ".ape") and actual.upper() == ext[1:].upper()
    if requested == "flac":
        return lossless
    if lossless:
        return True
    rate = re.search(r"(\d+(?:\.\d+)?)\s*k", actual, re.I)
    return bool(rate and float(rate.group(1)) >= int(requested[:-1]) * 0.95)


def _audio_details(content: bytes, ext: str) -> tuple[str, str]:
    """从媒体头确认格式/码率，不以所选音质冒充实际音质。"""
    import io
    import mutagen
    if content.startswith(b"fLaC"):
        ext = ".flac"
    elif content.startswith(b"MAC "):
        ext = ".ape"
    elif content.startswith(b"OggS"):
        ext = ".ogg"
    elif content.startswith(b"RIFF") and content[8:12] == b"WAVE":
        ext = ".wav"
    elif len(content) > 1 and content[0] == 255 and content[1] & 246 == 240:
        ext = ".aac"
    elif content.startswith(b"ID3") or (len(content) > 1 and content[0] == 255 and content[1] & 224 == 224):
        ext = ".mp3"
    elif content[4:8] == b"ftyp":
        ext = ".m4a"
    try:
        if ext == ".aac":
            from mutagen.aac import AAC
            audio = AAC(io.BytesIO(content))
        else:
            audio = mutagen.File(io.BytesIO(content))
        if audio is not None:
            kinds = {"FLAC": ".flac", "MonkeysAudio": ".ape", "MP3": ".mp3",
                     "MP4": ".m4a", "AAC": ".aac", "ASF": ".wma", "WAVE": ".wav"}
            ext = kinds.get(type(audio).__name__, ext)
            if ext in (".flac", ".ape"):
                return ext, ext[1:].upper()
            rate = getattr(audio.info, "bitrate", 0) or 0
            if rate:
                return ext, f"{round(rate / 1000)}kbps"
            return ext, ext[1:].upper()
    except Exception:
        pass
    return ext, ""


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


_COVER_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")


def _download_cover(cover_url: str, referer: str, audio_path: str) -> str:
    """下载封面存为与音频同名的图片文件；失败静默，不影响歌曲下载。"""
    if not cover_url:
        return ""
    from urllib.parse import urlparse
    ext = os.path.splitext(urlparse(cover_url).path)[1].lower()
    if ext not in _COVER_EXTS:
        ext = ".jpg"
    path = os.path.splitext(audio_path)[0] + ext
    if os.path.exists(path):
        return path
    try:
        with get_client(timeout=15) as client:
            resp = client.get(cover_url, headers=make_headers(referer=referer))
        if resp.status_code != 200 or len(resp.content) < 100:
            return ""
        with open(path, "wb") as f:
            f.write(resp.content)
        return path
    except Exception:
        return ""


def _download_to(url: str, referer: str, base_name: str, ext: str, out_dir: str,
                 expected_seconds: int = 0, requested_quality: str = "highest") -> DownloadResult:
    try:
        with get_client(timeout=60) as client:
            resp = client.get(url, headers=make_headers(referer=referer))
    except Exception:
        return DownloadResult(error_message="音频下载失败：网络连接不可用，请稍后重试")
    if resp.status_code != 200 or len(resp.content) < 1024:
        return DownloadResult(error_message=f"直链获取失败(HTTP {resp.status_code})")
    if resp.content.lstrip()[:1] in (b"<", b"{", b"["):
        return DownloadResult(error_message="下载失败：平台返回了错误页面，请稍后重试")
    ext, actual = _audio_details(resp.content, ext)
    if not actual:
        return DownloadResult(error_message="下载失败：无法识别平台返回的音频文件，请稍后重试")
    if not _quality_matches(requested_quality, ext, actual):
        return DownloadResult(error_message="下载失败：实际文件未达到所选音质，请选择最高可用或其他档位")
    preview = _detect_preview(resp.content, expected_seconds)
    path = _save(resp.content, base_name, ext, out_dir)
    return DownloadResult(success=True, file_path=path, is_preview=preview, quality=actual)


# ---------------------------------------------------------------------------
# 各平台直链解析


def _url_kugou(song: SongInfo, quality: str) -> _UrlResult:
    from .kugou_download import resolve_kugou_url
    return resolve_kugou_url(song, quality)


def _url_kuwo_web(song: SongInfo) -> _UrlResult:
    """酷我 Web playUrl（Secret 头）→ 免费歌 320k MP3 全曲。

    VIP 歌返回 code=-1（付费提示），由调用方回退 antiserver 拿试听。
    """
    with get_client() as client:
        token = kuwo_web_token(client)
        if not token:
            return _UrlResult(error="")
        headers = make_headers(referer=f"https://www.kuwo.cn/play_detail/{song.song_id}",
                               extra={"Secret": kuwo_secret(token),
                                      "Cookie": f"{KUWO_COOKIE_KEY}={token}"})
        resp = client.get(
            "https://www.kuwo.cn/api/v1/www/music/playUrl",
            params={"mid": song.song_id, "type": "music", "httpsStatus": "1",
                    "reqId": str(uuid.uuid4()), "plat": "web_www", "from": ""},
            headers=headers)
    data = safe_json(resp.text)
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        return _UrlResult(error="酷我下载接口可能已变化，请稍后重试")
    if data.get("code") != 200:
        return _UrlResult(error="")
    from .kuwo_download import _safe_audio_url
    url = _safe_audio_url(data["data"].get("url"))
    if not url:
        return _UrlResult(error="")
    return _UrlResult(url=url, ext=".mp3")


def _url_kuwo(song: SongInfo, quality: str) -> _UrlResult:
    """优先平台已提供的授权音质文件；兼容旧播放通道并检测实际媒体。"""
    from .kuwo_download import resolve_url
    resolved = resolve_url(song, quality)
    if resolved.url or quality == "flac":
        return resolved
    if quality != "128k":
        got = _url_kuwo_web(song)
        if got.url:
            return got
        if quality != "highest":
            return resolved
    with get_client() as client:
        resp = client.get(
            "https://antiserver.kuwo.cn/anti.s",
            params={"type": "convert_url3", "rid": song.song_id, "format": "wma"},
            headers=make_headers(referer="https://kuwo.cn/"))
    data = safe_json(resp.text)
    if not isinstance(data, dict):
        return _UrlResult(error="酷我下载接口可能已变化，请稍后重试")
    from .kuwo_download import _safe_audio_url
    url = _safe_audio_url(data.get("url"))
    if not url:
        return _UrlResult(error="酷我拒绝该歌曲的直链请求（VIP/版权限制）")
    return _UrlResult(url=url, ext=".wma", quality="128kbps")


def _url_fivesing(song: SongInfo, quality: str) -> _UrlResult:
    song_id = song.song_id
    if "/" not in song_id:
        # 搜索结果带的是酷狗 hash（共享数据库）→ 走酷狗通道兜底
        quality = normalize_quality(quality)
        got = _url_kugou(song, "320k" if quality == "192k" else quality)
        got.referer = _REFERERS["kugou"]
        got.via_platform = "酷狗"
        return got
    mode, num = song_id.split("/", 1)
    if mode not in {"yc", "fc", "bz"} or not num.isdigit():
        return _UrlResult(error="5sing 歌曲标识无效，请重新解析歌曲")
    with get_client() as client:
        resp = client.get(
            "http://mobileapi.5sing.kugou.com/song/getSongUrl",
            params={"songtype": mode, "songid": num, "version": "6.6.72"},
            headers=make_headers(referer="https://5sing.kugou.com/"))
    data = safe_json(resp.text)
    if not isinstance(data, dict) or not isinstance(data.get("data"), dict):
        return _UrlResult(error="5sing 下载接口可能已变化，请稍后重试")
    payload = data["data"]
    quality = normalize_quality(quality)
    levels = {"320k": ("sq", "320kbps"), "192k": ("hq", "192kbps"),
              "128k": ("lq", "128kbps")}
    candidates = list(levels.values()) if quality == "highest" else [levels.get(quality)]
    for candidate in candidates:
        if candidate is None:
            continue
        prefix, tag = candidate
        from .kugou_download import safe_audio_url
        url = safe_audio_url(payload.get(f"{prefix}url"))
        if url:
            ext = str(payload.get(f"{prefix}ext") or "mp3").lower().lstrip(".")
            return _UrlResult(url=url, ext=f".{ext}", quality=tag)
    return _UrlResult(error="5sing 未提供所选音质，请选择最高可用或其他档位")


def _url_qq(song: SongInfo, quality: str) -> _UrlResult:
    from .qq_download import resolve_url
    return resolve_url(song, quality)


def _url_kugou_lite(song: SongInfo, quality: str) -> _UrlResult:
    """酷狗概念版直链：v5/url（登录态 + 概念版免费 VIP 可下高音质/无损）。"""
    from .scrapers.kugou_lite import KuGouLiteClient
    try:
        return KuGouLiteClient().song_url_details(
            song.song_id, quality, album_id=song.album_id,
            album_audio_id=song.extra.get("album_audio_id") or song.extra.get("AlbumAudioID") or 0,
            metadata=song.extra)
    except Exception:
        return _UrlResult(error="酷狗概念版下载地址请求失败，请稍后重试")


_RESOLVERS = {
    "kugou": _url_kugou,
    "kuwo": _url_kuwo,
    "5sing": _url_fivesing,
    "qq": _url_qq,
    "kugou_lite": _url_kugou_lite,
}

_REFERERS = {
    "kugou": "https://www.kugou.com/",
    "kuwo": "https://kuwo.cn/",
    "5sing": "https://5sing.kugou.com/",
    "qq": "https://y.qq.com/",
    "kugou_lite": "https://www.kugou.com/",
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

    try:
        quality = normalize_quality(quality)
    except ValueError as exc:
        return DownloadResult(error_message=str(exc))
    got = resolver(song, quality)
    if got.error:
        return DownloadResult(error_message=got.error)
    if not got.url:
        return DownloadResult(error_message="平台未返回可下载的音频，请稍后重试")
    if got.quality and not _quality_matches(quality, got.ext, got.quality):
        return DownloadResult(error_message="平台未提供所选音质，请选择最高可用或其他档位")

    base = f"{_safe_name(song.singer)} - {_safe_name(song.song_name)}".strip()
    base = re.sub(r"\s+", " ", base)
    referer = getattr(got, "referer", "") or _REFERERS[song.platform]
    result = _download_to(got.url, referer, base, got.ext, out_dir, song.duration, quality)
    result.quality = result.quality or got.quality
    result.is_preview = result.is_preview or got.is_preview
    result.via_platform = getattr(got, "via_platform", "")
    if result.success and song.cover_url:
        _download_cover(song.cover_url, referer, result.file_path)
    return result


def quality_options(platform: str) -> list[tuple[str, str]]:
    """(值, 显示名) 列表。"""
    highest = [("highest", "最高可用")]
    if platform == "5sing":
        return highest + [("320k", "320k SQ"), ("192k", "192k HQ"), ("128k", "128k 标准")]
    if platform in ("kugou", "kuwo", "kugou_lite", "qq"):
        return highest + [("flac", "无损 FLAC / APE"), ("320k", "320k 高音质"), ("128k", "128k 标准")]
    return [("default", "默认")]


def default_quality(platform: str) -> str:
    """平台默认音质（换源下载到其他平台时使用该平台最优档）。"""
    return "highest" if platform in _RESOLVERS else ""


# ---------------------------------------------------------------------------
# 跨平台智能换源：失败/仅试听时，去其他平台找同名曲

_FALLBACK_ORDER = ["kuwo", "kugou", "kugou_lite"]
_FALLBACK_NAMES = {"kuwo": "酷我", "kugou": "酷狗", "kugou_lite": "酷狗概念版",
                   "netease": "网易云"}


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
    from .scrapers.kugou_lite import KuGouLiteScraper
    keyword = f"{song.song_name} {song.singer}".strip()
    try:
        if platform == "kuwo":
            cands = KuwoScraper().search_song(keyword, 1, 20) or []
        elif platform == "kugou_lite":
            # 概念版搜索需登录，未登录抛异常 → 跳过
            cands = KuGouLiteScraper().search_song(keyword, 1, 20) or []
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


def _try_netease(song: SongInfo, out_dir: str, quality: str = "highest") -> Optional[DownloadResult]:
    """网易云兜底：用已存 Cookie 搜索下载（Cookie 在则可用，无则跳过）。"""
    try:
        from auth import get_netease_cookie
        from netease import MusicDownloader, parse_cookie_string, search_music
        content = get_netease_cookie()
        cookies = parse_cookie_string(content) if content else {}
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
        dl = MusicDownloader(download_dir=out_dir, cookie_string=content)
        level = {"highest": "jymaster", "flac": "lossless", "320k": "exhigh",
                 "192k": "higher", "128k": "standard"}[quality]
        r = dl.download_music_file(int(best["id"]), level)
    except Exception:
        return None
    if r and r.success and r.file_path:
        with open(r.file_path, "rb") as stream:
            ext, actual = _audio_details(stream.read(), os.path.splitext(r.file_path)[1])
        if not _quality_matches(quality, ext, actual):
            return None
        return DownloadResult(success=True, file_path=r.file_path,
                              quality=actual, via_platform="网易云",
                              is_preview=_file_is_preview(r.file_path, song.duration or 0))
    return None


def download_song_smart(song: SongInfo, out_dir: str, quality: str = "",
                        fallback: bool = True) -> DownloadResult:
    """下载一首歌，失败或仅试听时自动换源：酷我→酷狗→网易云。"""
    try:
        quality = normalize_quality(quality)
    except ValueError as exc:
        return DownloadResult(error_message=str(exc))
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
            target_quality = "320k" if quality == "192k" and pf != "5sing" else quality
            r = download_song(cand, out_dir, target_quality)
        except Exception:
            continue
        if r.success and not r.is_preview:
            r.via_platform = _FALLBACK_NAMES.get(pf, pf)
            return r
        if r.success and best is None:
            best = r

    nr = _try_netease(song, out_dir, quality)
    if nr is not None:
        if nr.success and not nr.is_preview:
            return nr
        if nr.success and best is None:
            best = nr

    if best is not None:
        return best
    return result
