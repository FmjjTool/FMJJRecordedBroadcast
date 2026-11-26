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
import threading
import time
import weakref
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
_NO_DL_FILE = "no_download_keys.json"


class DownloadCancelled(Exception):
    """下载任务被取消。"""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = result or {}


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
                     is_cancelled=None, *, query=None) -> dict:
    """批量下载条目媒体（对齐桌面 `_download_for_item` 路由）。"""
    from media_downloader import _is_direct_media_url

    raw_paths = [str(item or "").strip() for item in (paths or [])]
    raw_paths = [item for item in raw_paths if item][:_MAX_TASK_ITEMS]
    if not raw_paths:
        raise ValueError("请选择要下载的条目")
    if quality not in _QUALITY_LABELS:
        quality = "最高画质"
    query = query or _default_query()
    root = get_export_subdir("video_data")
    results = []
    ok = 0
    flagged = _flagged_no_dl()
    for index, rel in enumerate(raw_paths, 1):
        if is_cancelled and is_cancelled():
            raise DownloadCancelled("任务已取消", _download_result(results, ok, True))
        title = rel
        try:
            entry = _load_entry(Path(root), rel)
            title = entry["title"] or title
            if _entry_key(entry) in flagged:
                results.append({"file": rel, "title": title, "ok": False,
                                "error": "已标记不可下载；如需下载请先解除标记"})
            else:
                stats = _download_one(entry, quality, _is_direct_media_url)
                output_paths = stats.pop("paths", [])
                public_paths = _remember_download(root, rel, entry, output_paths, query)
                results.append({"file": rel, "title": title, "ok": True,
                                **stats, "paths": public_paths})
                ok += 1
        except DownloadCancelled:
            raise DownloadCancelled("任务已取消", _download_result(results, ok, True))
        except Exception as exc:
            results.append({"file": rel, "title": title, "ok": False,
                            "error": str(exc).replace(str(root), "导出目录")[:300]})
        if on_progress:
            on_progress(index, len(raw_paths), title)
    return _download_result(results, ok)


def _download_result(results: list[dict], ok: int, cancelled: bool = False) -> dict:
    summary = f"完成 {ok}/{len(results)} 个条目"
    if cancelled:
        summary = f"已取消（完成 {len(results)} 条）"
    return {"summary": summary, "results": results, "ok": ok,
            "failed": len(results) - ok}


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
            return {"kind": "video", "videos": 1, "path": os.path.basename(path),
                    "paths": [(path, "视频")]}
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
                "covers": int(stats.get("covers", 0) or 0),
                "paths": stats.get("paths") or []}
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
            "skipped": int(stats.get("skipped", 0) or 0),
            "paths": stats.get("paths") or []}

# ==================== 媒体完整度（P1.2，只读） ====================

_MEDIA_CACHE_TTL = 60
_STATUS_LABELS = {
    "ok": "完整", "partial": "部分", "missing": "该下未下",
    "text": "纯文本", "nodl": "不可下载",
}
_media_cache: dict[str, tuple[float, dict]] = {}
_status_cache = weakref.WeakKeyDictionary()
_cache_lock = threading.RLock()
_query_instance = None
_target_outputs: dict[str, dict[str, list[dict]]] = {}


def _default_query():
    global _query_instance
    with _cache_lock:
        if _query_instance is None:
            _query_instance = asset_query.AssetQueryService()
        return _query_instance


def _no_dl_store_path() -> Path:
    from toolbox.config_manager import get_config_dir
    return get_config_dir() / _NO_DL_FILE


def _load_no_dl() -> dict:
    try:
        data = json.loads(_no_dl_store_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "auto": set(data.get("auto") or []),
                "manual": set(data.get("manual") or []),
                "allow": set(data.get("allow") or []),
            }
    except Exception:
        pass
    return {"auto": set(), "manual": set(), "allow": set()}


def _save_no_dl(store) -> None:
    path = _no_dl_store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {key: sorted(values) for key, values in store.items()},
        ensure_ascii=False, indent=1), encoding="utf-8")


def _flagged_no_dl() -> set[str]:
    store = _load_no_dl()
    return set(store["auto"]) | set(store["manual"])


def _entry_key(row_or_entry: dict) -> str:
    """条目标识：与桌面 `_dl_key` 一致（绝对 file_path → video_id → title）。"""
    file_path = str(row_or_entry.get("file_path") or "")
    if not file_path and row_or_entry.get("path"):
        root = get_export_subdir("video_data")
        try:
            candidate = (root / str(row_or_entry["path"])).resolve()
            candidate.relative_to(root.resolve())
            file_path = str(candidate)
        except (ValueError, OSError):
            file_path = ""
    return file_path or str(row_or_entry.get("video_id") or
                            row_or_entry.get("title") or "")


def set_entry_no_dl(rel_path: str, marked: bool) -> dict:
    """标记 / 解除条目的「不可下载」；与桌面共用 no_download_keys.json。"""
    root = get_export_subdir("video_data")
    entry = _load_entry(root, rel_path)
    key = _entry_key(entry)
    if not key:
        raise ValueError("条目缺少可识别标识，无法标记")
    store = _load_no_dl()
    if marked:
        store["manual"].add(key)
        store["auto"].discard(key)
        store["allow"].discard(key)
    else:
        store["auto"].discard(key)
        store["manual"].discard(key)
        store["allow"].add(key)
    _save_no_dl(store)
    return {"ok": True, "marked": bool(marked),
            "message": "已标记为不可下载" if marked else "已解除不可下载标记"}


def entries_with_status(page: int = 1, page_size: int = 20,
                        keyword: str = "", platform: str = "",
                        status: str = "", *, query=None) -> dict:
    """条目列表 + 媒体完整度状态（对齐桌面 `_match_media` + `_item_media_complete`）。

    status 筛选：'' 全部 / missing 该下未下 / partial 部分 / ok 完整 /
    text 纯文本 / nodl 不可下载。
    """
    query = query or _default_query()
    with _cache_lock:
        snapshot = _status_snapshot(query)
        rows = [dict(row) for row in snapshot["rows"].values()]
        truncated = snapshot["truncated"]
    kw = (keyword or "").strip().lower()
    if kw:
        rows = [row for row in rows if kw in str(row.get("title") or "").lower()
                or kw in str(row.get("author") or "").lower()]
    if platform:
        rows = [row for row in rows if row.get("platform") == platform]
    if status:
        rows = [row for row in rows if row["status"] == status]
    rows.sort(key=lambda row: row.get("publish_time") or "", reverse=True)
    page_size = max(1, min(100, int(page_size)))
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
            "truncated": bool(truncated),
            "labels": _STATUS_LABELS}


def _status_row(row: dict, media: list[dict], flagged: set) -> dict:
    result = {key: value for key, value in row.items() if not key.startswith("_")}
    no_dl = _entry_key(row) in flagged
    result.update(no_dl=bool(no_dl), status="nodl" if no_dl else _entry_status(row, media),
                  n_img=sum(1 for item in media if item["type"] == "image"),
                  n_vid=sum(1 for item in media if item["type"] == "video"))
    return result


def _status_snapshot(query) -> dict:
    rows = query.entry_rows()
    root = str(get_export_subdir("video_data").resolve())
    snapshot = _status_cache.get(query)
    flagged = _flagged_no_dl()
    if (snapshot and snapshot["root"] == root
            and snapshot["revision"] == query._entries_revision
            and time.monotonic() - snapshot["updated"] < _MEDIA_CACHE_TTL):
        for row in snapshot["rows"].values():
            row.update(_status_row(row, snapshot["media"].get(row["path"], []), flagged))
        return snapshot
    seeded = query._entry_media
    if rows and all(row["path"] in seeded for row in rows):
        matched = [seeded[row["path"]] for row in rows]
    else:
        matched = _match_media(rows)
    media = {row["path"]: list(files) for row, files in zip(rows, matched)}
    for rel, files in _target_outputs.get(root, {}).items():
        if rel in media:
            media[rel] = _merge_media(media[rel], files)
    snapshot = {"root": root, "revision": query._entries_revision,
                "rows": {row["path"]: _status_row(row, media[row["path"]], flagged)
                         for row in rows}, "media": media,
                "updated": time.monotonic(), "truncated": query._entries_truncated}
    _status_cache[query] = snapshot
    return snapshot


def refresh_entries(paths, *, query=None) -> dict:
    """仅重读指定条目并修补状态快照；不重新扫描 JSON 或媒体根目录。"""
    if (not isinstance(paths, list) or not 1 <= len(paths) <= 200
            or any(not isinstance(path, str) or not path.strip() for path in paths)):
        raise ValueError("请选择 1–200 条要刷新的记录")
    query = query or _default_query()
    with _cache_lock:
        result = query.refresh_entry_paths(list(dict.fromkeys(paths)))
        root = get_export_subdir("video_data").resolve()
        snapshot = _status_cache.get(query)
        if snapshot and snapshot["root"] != str(root):
            snapshot = None
        flagged = _flagged_no_dl()
        items = []
        for row in result["items"]:
            rel = row["path"]
            media = (snapshot["media"].get(rel) if snapshot else None)
            if media is None:
                media = query._entry_media.get(rel)
            if media is None:
                media = _target_media_files(root, row)
            media = _merge_media(media, _target_outputs.get(str(root), {}).get(rel, []))
            media = [item for item in media if Path(item["path"]).is_file()]
            fresh = _status_row(row, media, flagged)
            items.append(fresh)
            if snapshot is not None:
                snapshot["rows"][rel] = fresh
                snapshot["media"][rel] = media
        if snapshot is not None:
            for rel in result["removed"]:
                snapshot["rows"].pop(rel, None)
                snapshot["media"].pop(rel, None)
            snapshot["revision"] = query._entries_revision
        return {"items": items, "removed": result["removed"]}


def _merge_media(existing: list[dict], additions: list[dict]) -> list[dict]:
    merged = {item["path"]: item for item in existing}
    merged.update({item["path"]: item for item in additions})
    return list(merged.values())


def _target_media_files(root: Path, row: dict) -> list[dict]:
    """冷态单条刷新仅查看该平台作者目录及旧版平铺文件，不递归整库。"""
    from toolbox.utils import norm_platform_dir
    folders = [root]
    try:
        for folder in root.iterdir():
            if folder.is_dir() and norm_platform_dir(folder.name) == row.get("platform"):
                folders.append(folder)
                author_dir = (folder / str(row.get("author") or "")).resolve()
                try:
                    author_dir.relative_to(root)
                except ValueError:
                    continue
                if author_dir != folder and author_dir.is_dir():
                    folders.append(author_dir)
    except OSError:
        pass
    found = []
    title = _sanitize(row.get("title") or "")
    for folder in dict.fromkeys(folders):
        try:
            for path in folder.iterdir():
                if not path.is_file() or path.suffix.lower() not in (_IMG_EXTS | {".mp4", ".flv", ".mkv", ".ts", ".mov", ".webm"}):
                    continue
                date, _author, name = _parse_media_name(path.name)
                name = _sanitize(name)
                if title and (name == title or (len(name) >= 4 and name in title)):
                    found.append({"path": str(path), "name": path.name,
                                  "type": "image" if path.suffix.lower() in _IMG_EXTS else "video",
                                  "date_part": date, "subdir": row.get("platform") or ""})
        except OSError:
            continue
    return found


def _remember_download(root: Path, rel: str, entry: dict, paths, query) -> list:
    """消费下载器已知产出，直接合并内存媒体与目标状态，不清空后全盘扫描。"""
    root = root.resolve()
    additions, public = [], []
    for value in paths or []:
        if not isinstance(value, (list, tuple)) or not value:
            continue
        path = Path(value[0])
        path = (path if path.is_absolute() else root / path).resolve()
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        if not path.is_file():
            continue
        kind = "image" if path.suffix.lower() in _IMG_EXTS else "video"
        date, _author, _title = _parse_media_name(path.name)
        additions.append({"path": str(path), "name": path.name, "type": kind,
                          "date_part": date, "subdir": entry.get("platform") or ""})
        public.append([relative, str(value[1]) if len(value) > 1 else kind])
    with _cache_lock:
        targets = _target_outputs.setdefault(str(root), {})
        targets[rel] = _merge_media(targets.get(rel, []), additions)
        cached = _media_cache.get(str(root))
        if cached is not None:
            for media in additions:
                date, _author, title = _parse_media_name(media["name"])
                key = (date, _sanitize(title)[:20], media["subdir"])
                cached[1][key] = _merge_media(cached[1].get(key, []), [media])
        refresh_entries([rel], query=query)
    return public


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
