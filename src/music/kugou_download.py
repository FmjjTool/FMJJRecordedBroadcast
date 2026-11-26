"""酷狗文件音质适配：使用平台提供的各档 hash，保留实际返回音质。

元数据缺少高档文件或平台拒绝授权时不构造 hash、不绕过权限。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .models import SongInfo
from .utils import get_client, make_headers, safe_json


@dataclass
class KugouUrlDetails:
    url: str = ""
    ext: str = ""
    quality: str = ""
    error: str = ""
    is_preview: bool = False


def safe_audio_url(value) -> str:
    if not isinstance(value, str):
        return ""
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower().rstrip(".")
        if (parsed.scheme not in {"https", "http"} or parsed.username or parsed.password
                or parsed.port not in {None, 80, 443}):
            return ""
        if any(host == suffix or host.endswith("." + suffix)
               for suffix in ("kugou.com", "kugou.net", "kgimg.com")):
            return value
    except ValueError:
        pass
    return ""


def quality_hashes(base_hash: str, metadata: dict) -> dict[str, str]:
    keys = {"flac": ("sqhash", "SQFileHash", "SQHash", "sq_hash"),
            "320k": ("320hash", "HQFileHash", "HQHash", "hqhash", "hash_320"),
            "128k": ("hash", "FileHash", "Hash")}
    hashes = {}
    for quality, aliases in keys.items():
        for key in aliases:
            value = metadata.get(key)
            if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{32}", value):
                hashes[quality] = value.lower()
                break
    hashes.setdefault("128k", base_hash.lower())
    return hashes


def stream_details(data: dict) -> KugouUrlDetails:
    """统一主版/概念版响应；不把请求档位当成实际档位。"""
    raw_url = data.get("url") or data.get("backupUrl") or ""
    urls = raw_url if isinstance(raw_url, list) else [raw_url]
    url = next((safe_audio_url(item) for item in urls if safe_audio_url(item)), "")
    if not url:
        return KugouUrlDetails(error="酷狗未授权所选音质（需要登录、会员权限或歌曲不可用）")
    ext = str(data.get("extName") or data.get("extname") or "").lower().lstrip(".")
    if not ext and url:
        ext = urlsplit(url).path.rsplit(".", 1)[-1].lower()
    if ext not in {"flac", "ape", "mp3", "m4a", "ogg", "wma", "wav"}:
        return KugouUrlDetails(error="酷狗返回了不支持的音频格式")
    try:
        rate = float(data.get("bitRate") or data.get("bitrate") or 0)
        rate = rate / 1000 if rate > 10000 else rate
    except (ValueError, TypeError):
        rate = 0
    quality = ext.upper() if ext in {"flac", "ape"} else (f"{round(rate)}kbps" if rate else "")
    preview = data.get("is_free_part") in (1, "1", True) or data.get("isFreePart") in (1, "1", True)
    return KugouUrlDetails(url=url, ext="." + ext, quality=quality, is_preview=preview)


def resolve_kugou_url(song: SongInfo, quality: str = "highest") -> KugouUrlDetails:
    quality = quality or "highest"
    if quality not in {"highest", "flac", "320k", "128k"}:
        return KugouUrlDetails(error="酷狗不支持所选音质")
    if not re.fullmatch(r"[a-fA-F0-9]{32}", song.song_id or ""):
        return KugouUrlDetails(error="酷狗歌曲标识无效，请重新解析歌曲")
    try:
        from auth import get_cookie
        account = get_cookie("kugou")
    except Exception:
        return KugouUrlDetails(error="酷狗无法读取当前登录状态")
    headers = make_headers(referer="https://m.kugou.com/")
    if account:
        headers["Cookie"] = account
    metadata = dict(song.extra or {})
    error = "酷狗未提供所选音质，请选择最高可用或其他档位"
    preview = None
    try:
        with get_client(timeout=15) as client:
            hashes = quality_hashes(song.song_id, metadata)
            if quality != "128k" and (quality == "highest" or quality not in hashes):
                response = client.get("https://mobilecdn.kugou.com/api/v3/song/info",
                                      params={"hash": song.song_id},
                                      headers=headers, follow_redirects=False)
                info = safe_json(response.text) if response.status_code == 200 and len(response.text) < 2_000_000 else {}
                if isinstance(info, dict) and isinstance(info.get("data"), dict):
                    detailed = quality_hashes(song.song_id, info["data"])
                    hashes.update({key: value for key, value in detailed.items() if key != "128k"})
            levels = ["flac", "320k", "128k"] if quality == "highest" else [quality]
            for level in levels:
                file_hash = hashes.get(level)
                if not file_hash:
                    continue
                response = client.get("https://m.kugou.com/app/i/getSongInfo.php",
                                      params={"cmd": "playInfo", "hash": file_hash},
                                      headers=headers, follow_redirects=False)
                if response.status_code == 429:
                    return KugouUrlDetails(error="酷狗请求过于频繁，请稍后再试")
                if response.status_code != 200 or len(response.text) > 2_000_000:
                    continue
                data = safe_json(response.text)
                if not isinstance(data, dict):
                    continue
                got = stream_details(data)
                if got.url:
                    if level == "flac" and got.ext not in {".flac", ".ape"}:
                        continue
                    if quality == "highest" and got.is_preview:
                        preview = preview or got
                        continue
                    return got
                error = got.error
    except Exception:
        return KugouUrlDetails(error="酷狗下载地址请求失败，请稍后重试")
    return preview or KugouUrlDetails(error=error)
