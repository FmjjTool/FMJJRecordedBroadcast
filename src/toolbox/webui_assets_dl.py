"""WebUI 资产下载中间层（L3）：按条目路由下载视频 / 图片 / B站。

与桌面资产页语义一致（复用 `asset_bilibili` / `asset_media` / `asset_video`
/ `asset_images` 引擎）：B站动态分 draw/av，抖音/微博/小红书下载前刷新
时效直链，图文走图片通道（封面优先、同名跳过）。隔离条款见
`layering-contract`「WebUI 中间层隔离」：不 import PySide6 / gui_qt，
不改桌面行为；只写导出目录。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from toolbox import asset_query
from toolbox.asset_bilibili import (av_bvid, download_video, dynamic_type,
                                    quality_to_qn)
from toolbox.asset_images import extract_pic_urls
from toolbox.asset_media import download_item_images, download_item_media
from toolbox.asset_video import resolve_video_source
from toolbox.config_manager import get_export_subdir

_MAX_TASK_ITEMS = 50
_QUALITY_LABELS = ("最高画质", "8K", "杜比视界", "HDR", "4K",
                   "1080P60", "1080P", "720P", "480P")


class DownloadCancelled(Exception):
    """下载任务被取消。"""


def _raw_has_video(raw) -> bool:
    if not isinstance(raw, dict):
        return False
    mi = (raw.get("page_info") or {}).get("media_info") or {}
    if isinstance(mi, dict) and (mi.get("stream_url") or mi.get("stream_url_hd")):
        return True
    if isinstance(raw.get("urls"), dict) and raw.get("urls"):
        return True
    return bool(raw.get("stream_url"))


def _load_entry(root: Path, rel: str) -> dict:
    """按相对路径加载条目 JSON（含原始数据），返回桌面条目同构 dict。"""
    path = (root / rel).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("条目不在已获取数据目录内") from exc
    if not path.is_file():
        raise ValueError("条目文件不存在或已被移动")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"条目文件读取失败: {exc}") from exc
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        raise ValueError("条目文件格式不正确")
    raw = payload.get("原始数据")
    raw = raw if isinstance(raw, dict) else {}
    return {
        "platform": (payload.get("平台") or payload.get("platform") or "").lower(),
        "title": payload.get("标题") or path.stem,
        "author": payload.get("作者") or "",
        "video_id": str(payload.get("视频ID") or payload.get("video_id") or ""),
        "video_url": payload.get("视频直链") or payload.get("video_url") or "",
        "raw_url": payload.get("原始URL") or payload.get("链接") or "",
        "cover_url": payload.get("封面") or payload.get("cover_url") or "",
        "publish_time": payload.get("发布时间") or "",
        "file_path": str(path),
        "raw_data": raw,
    }


def download_entries(paths, quality: str = "最高画质", on_progress=None,
                     is_cancelled=None) -> dict:
    """批量下载条目媒体（对齐桌面 `_download_for_item` 路由）。"""
    from media_downloader import _is_direct_media_url

    raw_paths = [str(item or "").strip() for item in (paths or [])]
    raw_paths = [item for item in raw_paths if item][:_MAX_TASK_ITEMS]
    if not raw_paths:
        raise ValueError("请选择要下载的条目")
    if quality not in _QUALITY_LABELS:
        quality = "最高画质"
    service = asset_query.AssetQueryService()
    rows = service.entries(1, 500)
    known = {row.get("path"): row for row in rows.get("items") or []}
    root = get_export_subdir("video_data")
    base_dir = str(root)
    results = []
    ok = 0
    for index, rel in enumerate(raw_paths, 1):
        if is_cancelled and is_cancelled():
            raise DownloadCancelled("任务已取消")
        title = rel
        try:
            entry = _load_entry(Path(root), rel)
            title = entry["title"] or title
            stats = _download_one(entry, quality, _is_direct_media_url)
            results.append({"file": rel, "title": title, "ok": True,
                            **stats})
            ok += 1
        except DownloadCancelled:
            raise
        except Exception as exc:
            results.append({"file": rel, "title": title, "ok": False,
                            "error": str(exc)[:300]})
        if on_progress:
            on_progress(index, len(raw_paths), title)
    summary = f"完成 {ok}/{len(raw_paths)} 个条目"
    return {"summary": summary, "results": results, "ok": ok,
            "failed": len(raw_paths) - ok}


def _download_one(entry: dict, quality: str, is_direct) -> dict:
    """单条目路由下载：B站 / 视频 / 图片（对齐桌面三条通道）。"""
    cancel = None
    platform = entry.get("platform", "")
    if platform == "bilibili":
        dtype = dynamic_type(entry)
        vid = entry.get("video_id", "")
        if dtype == "av":
            bvid = av_bvid(entry)
            if bvid:
                vid = bvid
        if vid and not vid.startswith("dyn_"):
            from auth import get_cookie
            path = download_video(
                entry, qn=quality_to_qn(quality),
                cookie=get_cookie("bilibili") or "",
                out_dir=str(get_export_subdir("video_data")))
            return {"kind": "video", "videos": 1, "path": os.path.basename(path)}
        return _download_images(entry)

    has_video = is_direct(entry.get("video_url") or "")
    if not has_video and platform == "xiaohongshu":
        url = entry.get("video_url") or entry.get("raw_url") or ""
        has_video = "xiaohongshu.com" in url
    if not has_video:
        has_video = _raw_has_video(entry.get("raw_data"))
    if has_video:
        resolved = resolve_video_source(
            entry, video_url=entry.get("video_url", ""),
            video_id=entry.get("video_id", ""),
            raw_data=entry.get("raw_data"), quality=quality)
        stats = download_item_media(
            entry, resolved, base_dir=str(get_export_subdir("video_data")),
            threads=4, cancel_event=cancel)
        return {"kind": "video",
                "videos": int(stats.get("videos", 0) or 0),
                "skipped": int(stats.get("skipped", 0) or 0),
                "covers": int(stats.get("covers", 0) or 0)}
    return _download_images(entry)


def _download_images(entry: dict) -> dict:
    pics, lives = extract_pic_urls(entry)
    cover = entry.get("cover_url", "")
    if cover and cover not in pics:
        if entry.get("platform") != "weibo" or not pics:
            pics.insert(0, cover)
    if not pics and not lives:
        raise ValueError("此条目无可下载媒体（无图片且无视频源）")
    stats = download_item_images(
        entry, pics=pics, lives=lives, cover=cover,
        base_dir=str(get_export_subdir("video_data")), threads=4,
        skip_existing=True) or {}
    return {"kind": "images",
            "images": int(stats.get("images", 0) or 0),
            "covers": int(stats.get("covers", 0) or 0),
            "live": int(stats.get("live", 0) or 0),
            "skipped": int(stats.get("skipped", 0) or 0)}