"""WebUI 资产下载中间层（L3）：按条目路由下载视频 / 图片 / B站。

与桌面资产页语义一致（复用 `asset_bilibili` / `asset_media` / `asset_video`
/ `asset_images` 引擎）：B站动态分 draw/av，抖音/微博/小红书下载前刷新
时效直链，图文走图片通道（封面优先、同名跳过）。隔离条款见
`layering-contract`「WebUI 中间层隔离」：不 import PySide6 / gui_qt，
不改桌面行为；只写导出目录。

另含媒体完整度（P1.2）：扫描「已获取数据」下已下载媒体，按桌面同款
两级匹配（精确 → 子串；日期+平台轮次）挂到条目，产出「完整 / 部分 /
该下未下 / 纯文本」状态，供 Web 端筛选「该下未下」。B站资源包目录
本轮不参与（独立条目，待办）。
"""

from __future__ import annotations

import json
import os
import re
import time
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
_IMG_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


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

# ==================== 媒体完整度（P1.2，只读） ====================

_MEDIA_CACHE_TTL = 60
_STATUS_LABELS = {
    "ok": "完整", "partial": "部分", "missing": "该下未下",
    "text": "纯文本",
}
_media_cache: dict[str, tuple[float, dict]] = {}


def entries_with_status(page: int = 1, page_size: int = 20,
                        keyword: str = "", platform: str = "",
                        status: str = "") -> dict:
    """条目列表 + 媒体完整度状态（对齐桌面 `_match_media` + `_item_media_complete`）。

    status 筛选：'' 全部 / missing 该下未下 / partial 部分 / ok 完整 / text 纯文本。
    """
    service = asset_query.AssetQueryService()
    base = service.entries(1, 500, keyword=keyword, platform=platform)
    rows = base.get("items") or []
    matched = _match_media(rows)
    for row, media in zip(rows, matched):
        row["status"] = _entry_status(row, media)
        row["n_img"] = sum(1 for m in media if m["type"] == "image")
        row["n_vid"] = sum(1 for m in media if m["type"] == "video")
    if status:
        rows = [row for row in rows if row["status"] == status]
    total = len(rows)
    page = max(1, min(int(page), max(1, (total + page_size - 1) // page_size)))
    start = (page - 1) * page_size
    counts = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"items": rows[start:start + page_size], "total": total,
            "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
            "counts": counts,
            "truncated": bool(base.get("truncated")),
            "labels": _STATUS_LABELS}


def _media_map() -> dict:
    """扫描「已获取数据」下媒体文件（缓存 60 秒），返回桌面同构 media_map。"""
    root = get_export_subdir("video_data")
    key = str(root)
    now = time.time()
    cached = _media_cache.get(key)
    if cached and now - cached[0] < _MEDIA_CACHE_TTL:
        return cached[1]
    from toolbox.utils import norm_platform_dir

    media_map: dict[tuple, list] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in sorted(dirnames)
                       if not name.startswith(".")]
        subdir = ""
        for part in Path(dirpath).parts:
            subdir = norm_platform_dir(part)
            if subdir:
                break
        for name in sorted(filenames):
            ext = os.path.splitext(name)[1].lower()
            if ext not in _IMG_EXTS and ext not in (".mp4", ".flv", ".mkv",
                                                    ".ts", ".mov", ".webm"):
                continue
            date_part, _author, title_key = _parse_media_name(name)
            base = _sanitize(title_key)[:20] if title_key else ""
            mtype = "image" if ext in _IMG_EXTS else "video"
            media_map.setdefault((date_part, base, subdir), []).append({
                "path": os.path.join(dirpath, name), "name": name,
                "type": mtype, "date_part": date_part, "subdir": subdir,
            })
    _media_cache[key] = (now, media_map)
    return media_map


def _match_media(rows: list[dict]) -> list[list[dict]]:
    """桌面对齐的两级匹配（精确 → 子串；日期+平台轮次；同名共享）。"""
    media_map = dict(_media_map())
    for row in rows:
        row["_tn"] = _sanitize(row.get("title") or "") or (row.get("title") or "").strip()
        row["_tn"] = row["_tn"].strip("_")
        row["_jp"] = row.get("platform") or ""
        row["date_part"] = _date_part(row.get("publish_time") or "")
    exact_idx, buckets = {}, {}
    for mkey, mfs in media_map.items():
        if not mfs:
            continue
        m_date, m_title, m_sub = mkey
        t = (m_title or "").strip("_")
        exact_idx.setdefault((m_date, m_sub, t), mkey)
        buckets.setdefault((m_date, m_sub), []).append((t, mkey))
    owner: dict[tuple, dict] = {}
    results = [dict(media_files=[]) for _row in rows]

    def sub_match(tn, t):
        t = t.strip("_") if t else ""
        if not t or not tn or len(t) < 4:
            return False
        return t in tn or tn[:40] in t

    def phase(is_exact, date_same, sub_same):
        for index, row in enumerate(rows):
            if results[index]["media_files"]:
                continue
            tn, jp, dp = row["_tn"], row["_jp"], row["date_part"]
            d = dp if date_same else ""
            s = jp if sub_same else ""
            if is_exact:
                if not tn or len(tn) < 2:
                    continue
                mkey = exact_idx.get((d, s, tn))
                if mkey is not None and mkey in media_map:
                    results[index]["media_files"] = media_map.pop(mkey)
                    owner[(tn, jp, dp)] = row
                continue
            for t, mkey in buckets.get((d, s), ()):
                if mkey not in media_map:
                    continue
                if sub_match(tn, t):
                    results[index]["media_files"] = media_map.pop(mkey)
                    owner[(tn, jp, dp)] = row
                    break

    def share():
        for index, row in enumerate(rows):
            if not results[index]["media_files"] and len(row["_tn"]) >= 4:
                holder = owner.get((row["_tn"], row["_jp"], row["date_part"]))
                if holder is not None:
                    pos = rows.index(holder)
                    results[index]["media_files"] = list(
                        results[pos]["media_files"])

    phase(True, True, True)
    share()
    phase(False, True, True)
    phase(True, True, False)
    phase(False, True, False)
    phase(True, False, False)
    phase(False, False, False)
    share()
    return [item["media_files"] for item in results]


def _entry_status(row: dict, media: list[dict]) -> str:
    """对齐桌面 `_item_media_complete` / `_item_is_text_only` 的判定（只读简化）。"""
    platform = row.get("platform") or ""
    vid = row.get("video_id") or ""
    is_video_item = (
        (platform == "douyin" and bool(vid) and not row.get("images"))
        or (platform == "bilibili" and bool(vid) and not vid.startswith("dyn_"))
        or (platform not in ("douyin", "bilibili")
            and _entry_known_video(row))
    )
    if is_video_item:
        if any(m["type"] == "video" for m in media):
            return "ok"
        return "missing"
    if row.get("images"):
        if sum(1 for m in media if m["type"] == "image") >= int(row["images"]):
            return "ok"
        return "partial" if media else "missing"
    if row.get("url") or row.get("cover"):
        return "ok" if media else "missing"
    return "text"


def _entry_known_video(row: dict) -> bool:
    if str(row.get("url") or "").startswith(("http://", "https://")):
        return ".mp4" in str(row.get("url")) or False
    return False


def _parse_media_name(name: str) -> tuple[str, str, str]:
    """对齐桌面 `_parse_media_name`：{date}_{author}_{title}_{i}.ext。"""
    stem = name.rsplit(".", 1)[0]
    parts = stem.split("_")
    date_part = parts[0] if parts and len(parts[0]) == 8 and parts[0].isdigit() else ""
    author = parts[1] if len(parts) > 1 and date_part else ""
    rest = parts[2:] if date_part else parts
    while rest and (rest[-1].isdigit()
                    or re.fullmatch(r"live\d*", rest[-1].lower())):
        rest = rest[:-1]
    while rest and rest[-1] == "封面":
        rest = rest[:-1]
    title = re.sub(r"~[0-9a-f]{6}\d*$", "", "_".join(rest))
    return date_part, author, title


def _sanitize(title: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]", "", title or "")


def _date_part(publish_time: str) -> str:
    digits = re.sub(r"\D", "", publish_time or "")[:8]
    return digits if len(digits) == 8 else ""