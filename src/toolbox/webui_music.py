"""音乐搜索与下载 WebUI 适配（L3，Qt-free）。

桌面「音乐信息获取 / 音乐下载」合并页的核心动作在此复现：
搜索（单平台或五平台对比）与勾选下载（失败/试听自动换源）。
歌单创建与标签写入不在本模块范围内。
"""

from __future__ import annotations

from typing import Callable, Iterable

MERGE = "all"
MERGE_LABEL = "五平台对比"

_DOWNLOAD_META_KEYS = ("media_mid", "strMediaMid", "sqhash", "320hash", "HQFileHash",
                       "SQFileHash", "HQHash", "SQHash", "album_audio_id", "AlbumAudioID")


def download_quality_options() -> dict:
    """桌面与 Web 共用平台音质声明，不在前端手抄参数映射。"""
    from music.downloader import quality_options
    catalog = {pid: [{"value": value, "label": label} for value, label in quality_options(pid)]
               for pid in ("qq", "kugou", "kuwo", "5sing", "kugou_lite")}
    catalog["all"] = [{"value": "highest", "label": "各平台最高可用"},
                      {"value": "flac", "label": "无损 FLAC / APE"},
                      {"value": "320k", "label": "320k 高音质"},
                      {"value": "192k", "label": "192k 高音质"},
                      {"value": "128k", "label": "128k 标准"}]
    catalog["netease"] = [{"value": "jymaster", "label": "超清母带"},
                          {"value": "lossless", "label": "无损"},
                          {"value": "exhigh", "label": "320k"},
                          {"value": "higher", "label": "192k"},
                          {"value": "standard", "label": "标准"}]
    return {"platforms": catalog}


def _platforms() -> list[tuple[str, str, type]]:
    from music import ALL_PLATFORMS
    return list(ALL_PLATFORMS)


def _label_map() -> dict[str, str]:
    return {pid: name for pid, name, _ in _platforms()}


def _duration_text(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _row(song) -> dict:
    return {
        "platform": song.platform,
        "platform_name": _label_map().get(song.platform, song.platform),
        "song_id": song.song_id,
        "name": song.song_name or song.title,
        "singer": song.singer or "",
        "album": song.album_name or "",
        "album_id": song.album_id or "",
        "duration": int(song.duration or 0),
        "duration_text": _duration_text(song.duration or 0),
        "cover": song.cover_url or "",
        "download_meta": {key: song.extra[key] for key in _DOWNLOAD_META_KEYS
                          if isinstance(song.extra.get(key), (str, int))},
    }


def _song_from_row(row: dict):
    from music.models import SongInfo
    metadata = row.get("download_meta")
    metadata = metadata if isinstance(metadata, dict) else {}
    return SongInfo(
        platform=row.get("platform") or "",
        song_id=str(row.get("song_id") or ""),
        song_name=row.get("name") or "",
        singer=row.get("singer") or "",
        album_name=row.get("album") or "",
        album_id=str(row.get("album_id") or ""),
        duration=int(row.get("duration") or 0),
        cover_url=row.get("cover") or "",
        extra={key: str(metadata[key])[:80] for key in _DOWNLOAD_META_KEYS
               if isinstance(metadata.get(key), (str, int))},
    )


def search(payload: dict, on_log: Callable[[str], None] | None = None,
           is_cancelled: Callable[[], bool] | None = None) -> dict:
    """按关键词搜索歌曲；platform=all 时五平台依次搜并合并。"""
    log = on_log or (lambda _msg: None)
    keyword = str(payload.get("keyword") or "").strip()
    if not keyword:
        raise ValueError("请输入歌曲关键词")
    platform = str(payload.get("platform") or "")
    limit = max(1, min(int(payload.get("limit") or 20), 50))
    known = _label_map()
    if platform != MERGE and platform not in known:
        raise ValueError("未知音乐平台")

    targets = [(MERGE, MERGE_LABEL)] if platform == MERGE else [
        (platform, known[platform])]
    if platform == MERGE:
        targets = [(pid, name) for pid, name, _ in _platforms()]

    items: list[dict] = []
    failures: list[dict] = []
    for pid, name in targets:
        if is_cancelled and is_cancelled():
            break
        log(f"搜索 {name}：{keyword}")
        scraper_cls = next(cls for p, _, cls in _platforms() if p == pid)
        try:
            songs = scraper_cls().search_song(keyword, 1, limit) or []
        except Exception as exc:  # noqa: BLE001 - 单平台失败不影响整体
            failures.append({"platform": pid, "name": name,
                             "error": str(exc)[:200]})
            log(f"{name} 失败：{exc}")
            continue
        for song in songs:
            items.append(_row(song))
        log(f"{name} 命中 {len(songs)} 首")

    if not items and failures:
        first = failures[0]
        raise ValueError(f"{first['name']}搜索失败：{first['error']}")
    summary = f"{keyword} · {len(items)} 首"
    if failures:
        summary += f"（{len(failures)} 个平台失败）"
    return {"keyword": keyword, "items": items, "total": len(items),
            "failures": failures, "summary": summary}


def download(payload: dict, on_log: Callable[[str], None] | None = None,
             is_cancelled: Callable[[], bool] | None = None) -> dict:
    """下载勾选歌曲到音乐下载目录；失败或仅试听时自动换源。"""
    from music.downloader import DownloadResult, _file_is_preview, download_song_smart
    from toolbox.config_manager import get_music_download_dir

    log = on_log or (lambda _msg: None)
    rows = payload.get("items") or []
    if not isinstance(rows, list) or not rows:
        raise ValueError("请先勾选要下载的歌曲")
    if len(rows) > 100:
        raise ValueError("一次最多下载 100 首")
    quality = str(payload.get("quality") or "")
    fallback = bool(payload.get("fallback", True))
    include_cover = payload.get("include_cover", True)
    if type(include_cover) is not bool:
        raise ValueError("封面选项无效，请重新选择")
    out_dir = str(get_music_download_dir())
    log(f"下载目录：{out_dir}")

    files: list[dict] = []
    failures: list[dict] = []
    preview = 0
    for index, row in enumerate(rows, 1):
        if is_cancelled and is_cancelled():
            log("已取消")
            break
        song = _song_from_row(row if isinstance(row, dict) else {})
        title = f"{song.singer} - {song.song_name}".strip(" -")
        log(f"[{index}/{len(rows)}] {title or song.song_id}")
        if not song.song_id or not song.platform:
            failures.append({"title": title, "error": "缺少歌曲 ID 或平台"})
            continue
        try:
            if song.platform == "netease":
                from auth import get_netease_cookie
                from netease import MusicDownloader
                level = {"": "jymaster", "default": "jymaster", "highest": "jymaster",
                         "flac": "lossless", "320k": "exhigh", "192k": "higher",
                         "128k": "standard"}.get(quality, quality)
                if level not in {"jymaster", "jyeffect", "sky", "hires", "lossless",
                                 "exhigh", "higher", "standard"}:
                    raise ValueError("网易云不支持所选音质")
                client = MusicDownloader(download_dir=out_dir,
                                         cookie_string=get_netease_cookie())
                raw = client.download_music_file(
                    int(song.song_id), level, include_cover=include_cover)
                info = getattr(raw, "music_info", None)
                actual = getattr(info, "quality", "") or ""
                labels = {item["value"]: item["label"] for item in
                          download_quality_options()["platforms"]["netease"]}
                path = getattr(raw, "file_path", "") or ""
                result = DownloadResult(
                    success=bool(raw and raw.success), file_path=path,
                    error_message=getattr(raw, "error_message", "") or "下载失败",
                    quality=labels.get(actual, actual),
                    is_preview=bool(path and _file_is_preview(path, song.duration)))
            else:
                result = download_song_smart(song, out_dir, quality,
                                             fallback=fallback,
                                             include_cover=include_cover)
        except Exception as exc:  # noqa: BLE001
            failures.append({"title": title, "error": str(exc)[:200]})
            continue
        if result.success and result.file_path:
            if result.is_preview:
                preview += 1
            files.append({
                "title": title,
                "file": result.file_path,
                "quality": result.quality or "",
                "preview": bool(result.is_preview),
                "via": result.via_platform or song.platform,
            })
        else:
            failures.append({"title": title,
                             "error": result.error_message or "下载失败"})

    total = len(files) + len(failures)
    parts = [f"成功 {len(files)}"]
    if preview:
        parts.append(f"试听 {preview}")
    if failures:
        parts.append(f"失败 {len(failures)}")
    return {"files": files, "failures": failures, "total": total,
            "preview": preview, "out_dir": out_dir,
            "summary": " / ".join(parts)}
