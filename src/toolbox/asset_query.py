"""资产库查询用例（L3）：统计、数据库浏览、条目分页与本地资源扫描缓存。

WebUI 与桌面页面只调用本模块，不直接访问 asset_index / increment_db /
导出目录。只读；返回相对信息，不向界面暴露绝对路径。
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from toolbox import asset_groups, asset_index, increment_db
from toolbox.config_manager import EXPORT_CATEGORIES, get_export_dir

_KNOWN_PLATFORMS = ("douyin", "bilibili", "weibo", "kuaishou",
                    "xiaohongshu", "tieba", "zhihu")
_SORT_COLUMNS = {
    "fetched_at", "kind", "title", "author", "publish_time",
    "play_count", "like_count", "comment_count", "video_url",
}
_DB_COLUMNS = [
    "dedup_key", "fetched_at", "kind", "video_id", "title", "author",
    "publish_time", "play_count", "like_count", "comment_count",
    "share_count", "favorite_count", "danmaku_count", "coin_count",
    "duration", "video_url",
]
_PLATFORM_ORDER = ["douyin", "bilibili", "weibo", "kuaishou",
                   "xiaohongshu", "acfun", "tieba", "zhihu"]
_LOCAL_SCAN_LIMIT = 50000
_DETAIL_MAX_CHARS = 200 * 1024
_DETAIL_FIELDS = (
    ("title", "标题"), ("author", "作者"), ("platform", "平台"),
    ("publish_time", "发布时间"), ("video_id", "视频 ID"),
    ("duration", "时长"), ("play_count", "播放"), ("like_count", "点赞"),
    ("comment_count", "评论"), ("coin_count", "投币"),
    ("danmaku_count", "弹幕"), ("video_url", "链接"),
)
_LOCAL_CACHE_TTL = 300
_LOCAL_GROUP_LIMIT = 200
_PREVIEW_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
_PREVIEW_IMAGE_MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                       ".png": "image/png", ".gif": "image/gif",
                       ".webp": "image/webp", ".bmp": "image/bmp"}
_PREVIEW_VIDEO_EXTS = {".mp4", ".flv", ".mkv", ".ts", ".mov", ".webm"}
_PREVIEW_MAX_BYTES = 20 * 1024 * 1024


class AssetQueryService:
    """资产查询用例。实例持有条目与本地扫描的短时缓存，可跨请求复用。"""

    def __init__(self):
        self._entries_cache: tuple[str, int, list, bool] | None = None
        self._entries_truncated = False
        self._local_cache: dict[str, dict] = {}
        self._local_lock = threading.Lock()

    def summary(self) -> dict:
        try:
            json_rows, media_rows, db_bytes = asset_index.stats()
        except Exception:
            json_rows, media_rows, db_bytes = 0, 0, 0
        root = get_export_dir()
        categories = []
        for name in dict.fromkeys(EXPORT_CATEGORIES.values()):
            if (root / name).is_dir():
                categories.append(name)
        return {
            "index": {"json_rows": int(json_rows), "media_rows": int(media_rows),
                      "db_bytes": int(db_bytes)},
            "categories": categories,
            "export_root_name": root.name,
        }

    def db_tables(self) -> dict:
        raw = [{"name": name, "rows": int(count)}
               for name, count in increment_db.list_tables()]
        rank = {name: index for index, name in enumerate(_PLATFORM_ORDER)}
        raw.sort(key=lambda item: (rank.get(item["name"], len(rank)),
                                   item["name"]))
        return {"tables": raw}

    def db_rows(self, platform: str, page: int, page_size: int,
                keyword: str = "", author: str = "", order_by: str = "",
                ascending: bool = False) -> dict:
        tables = [name for name, _count in increment_db.list_tables()]
        if platform not in tables:
            raise ValueError("平台表不存在")
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        order = order_by if order_by in _SORT_COLUMNS else ""
        rows, total = increment_db.query_rows(
            platform, keyword=keyword, author=author,
            limit=page_size, offset=(page - 1) * page_size,
            order_by=order, ascending=bool(ascending))
        pages = max(1, (int(total) + page_size - 1) // page_size)
        if page > pages:
            page = pages
            if total:
                rows, total = increment_db.query_rows(
                    platform, keyword=keyword, author=author,
                    limit=page_size, offset=(page - 1) * page_size,
                    order_by=order, ascending=bool(ascending))
        columns = [name for name, _kind in increment_db.COLUMNS
                   if name in _DB_COLUMNS]
        indexes = [i for i, (name, _kind) in enumerate(increment_db.COLUMNS)
                   if name in _DB_COLUMNS]
        data = []
        for row in rows:
            item = {}
            for column, index in zip(columns, indexes):
                value = row[index]
                if column == "description" and isinstance(value, str):
                    value = value[:2000]
                item[column] = value
            data.append(item)
        return {"platform": platform, "columns": columns, "rows": data,
                "total": int(total), "page": page, "page_size": page_size,
                "pages": pages}

    def entries(self, page: int, page_size: int, keyword: str = "",
                platform: str = "") -> dict:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        items = self._entry_rows()
        kw = (keyword or "").strip().lower()
        if kw:
            items = [item for item in items
                     if kw in (item.get("title") or "").lower()
                     or kw in (item.get("author") or "").lower()]
        if platform:
            items = [item for item in items if item.get("platform") == platform]
        total = len(items)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        return {"items": items[start:start + page_size], "total": total,
                "page": page, "page_size": page_size, "pages": pages,
                "truncated": bool(self._entries_truncated)}

    def category_root(self, category: str) -> Path:
        root = get_export_dir() / category
        if category not in set(EXPORT_CATEGORIES.values()) or not root.is_dir():
            raise ValueError("分类目录不存在")
        return root

    def scan_local(self, category: str, on_progress=None) -> dict:
        """同步扫描分类目录并写入缓存；on_progress(count) 可抛异常中止。"""
        root = self.category_root(category)
        groups, truncated = asset_groups.load_dir_groups(
            str(root), limit_files=_LOCAL_SCAN_LIMIT, cat=category,
            on_progress=on_progress)
        payload = [_local_group_payload(group) for group in groups]
        files = sum(item["n_img"] + item["n_vid"] + item["n_aud"]
                    + item["n_doc"] for item in payload)
        with self._local_lock:
            self._local_cache[category] = {
                "groups": payload, "truncated": bool(truncated),
                "files": int(files), "stamp": _dir_stamp(root),
                "scanned_at": time.time(),
            }
        summary = f"扫描 {files} 个文件，聚合 {len(payload)} 个条目"
        if truncated:
            summary += f"（超过 {_LOCAL_SCAN_LIMIT} 个文件，结果已截断）"
        return {"category": category, "summary": summary, "files": int(files),
                "groups": len(payload), "truncated": bool(truncated)}

    def entry_detail(self, path: str) -> dict:
        """条目二级详情：读取导出 JSON 概要与原始文本（只读，限制 200KB）。"""
        root = get_export_dir() / EXPORT_CATEGORIES["video_data"]
        rel = str(path or "").replace("\\", "/").strip("/")
        if not rel or ".." in rel.split("/"):
            raise ValueError("条目路径不合法")
        target = root / rel
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("条目不在导出目录内") from exc
        if not target.is_file():
            raise ValueError("导出文件不存在或已被移动")
        if target.suffix.lower() != ".json":
            raise ValueError("只支持查看 JSON 导出文件")
        try:
            stat = target.stat()
        except OSError as exc:
            raise ValueError("导出文件读取失败") from exc
        raw = target.read_text(encoding="utf-8", errors="replace")
        truncated = len(raw) > _DETAIL_MAX_CHARS
        text = raw[:_DETAIL_MAX_CHARS]
        try:
            data = json.loads(raw)
        except ValueError:
            data = {}
        fields = []
        if isinstance(data, dict):
            for key, label in _DETAIL_FIELDS:
                value = data.get(key)
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)[:200]
                if value in (None, ""):
                    continue
                fields.append({"label": label, "value": str(value)[:300]})
        return {"path": rel, "file": target.name,
                "size": int(stat.st_size),
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(stat.st_mtime)),
                "fields": fields, "text": text, "truncated": truncated,
                "note": "只读详情：显示导出 JSON 的字段与原文，最多 200KB。"}

    def local_stat(self, category: str, rel_dir: str, name: str) -> dict:
        """本地分类文件的详情（大小 / 修改时间 / 类型），只读。"""
        root = self.category_root(category)
        rel = str(rel_dir or "").replace("\\", "/").strip("/")
        file_name = str(name or "").strip()
        if not file_name or ".." in file_name or "/" in file_name:
            raise ValueError("文件名不合法")
        if ".." in rel.split("/"):
            raise ValueError("目录不合法")
        target = (root / rel / file_name) if rel else (root / file_name)
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("文件不在分类目录内") from exc
        if not target.is_file():
            raise ValueError("文件不存在或已被移动")
        stat = target.stat()
        ext = target.suffix.lower()
        kind = ("image" if ext in _PREVIEW_IMAGE_EXTS
                else "video" if ext in _PREVIEW_VIDEO_EXTS else "file")
        return {"category": category, "dir": rel, "name": file_name,
                "size": int(stat.st_size),
                "mtime": time.strftime("%Y-%m-%d %H:%M:%S",
                                      time.localtime(stat.st_mtime)),
                "kind": kind,
                "previewable": kind in ("image", "video")}

    def preview_local(self, category: str, rel_dir: str, name: str) -> tuple:
        """预览分类目录内文件：图片返回原字节，视频返回 ffmpeg 首帧。

        只读（首帧走 stdout 管道，不写任何目录）；返回 (bytes, mime)。
        路径越界 / 缺失 / 超大图片均抛 ValueError。
        """
        root = self.category_root(category)
        rel = str(rel_dir or "").replace("\\", "/").strip("/")
        file_name = str(name or "").strip()
        if not file_name or ".." in file_name or "/" in file_name:
            raise ValueError("文件名不合法")
        if ".." in rel.split("/"):
            raise ValueError("目录不合法")
        target = (root / rel / file_name) if rel else (root / file_name)
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("文件不在分类目录内") from exc
        if not target.is_file():
            raise ValueError("文件不存在或已被移动")
        ext = target.suffix.lower()
        if ext in _PREVIEW_IMAGE_EXTS:
            size = target.stat().st_size
            if size > _PREVIEW_MAX_BYTES:
                raise ValueError("图片过大，请在桌面端查看")
            return target.read_bytes(), _PREVIEW_IMAGE_MIME.get(
                ext, "application/octet-stream")
        if ext in _PREVIEW_VIDEO_EXTS:
            return _video_first_frame(target), "image/jpeg"
        raise ValueError("该文件类型不支持预览")

    def thumbnail_local(self, category: str, rel_dir: str, name: str) -> tuple:
        """缩略图：图片或视频首帧经 ffmpeg 缩放到 200px，内存缓存 300 秒。"""
        root = self.category_root(category)
        rel = str(rel_dir or "").replace("\\", "/").strip("/")
        file_name = str(name or "").strip()
        if not file_name or ".." in file_name or "/" in file_name:
            raise ValueError("文件名不合法")
        if ".." in rel.split("/"):
            raise ValueError("目录不合法")
        target = (root / rel / file_name) if rel else (root / file_name)
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("文件不在分类目录内") from exc
        if not target.is_file():
            raise ValueError("文件不存在或已被移动")
        ext = target.suffix.lower()
        if ext not in _PREVIEW_IMAGE_EXTS and ext not in _PREVIEW_VIDEO_EXTS:
            raise ValueError("该文件类型不支持缩略图")
        try:
            stamp = int(target.stat().st_mtime)
        except OSError:
            stamp = 0
        cache_key = (str(target), stamp)
        cached = _thumb_cache.get(cache_key)
        if cached is not None:
            return cached, "image/jpeg"
        data = _scaled_frame(target, is_video=ext in _PREVIEW_VIDEO_EXTS)
        _thumb_cache[cache_key] = data
        if len(_thumb_cache) > 240:
            for key in list(_thumb_cache)[:80]:
                _thumb_cache.pop(key, None)
        return data, "image/jpeg"

    def local_groups(self, category: str, page: int = 1,
                     page_size: int = 40) -> dict:
        page = max(1, int(page))
        page_size = max(1, min(100, int(page_size)))
        root = self.category_root(category)
        with self._local_lock:
            cached = self._local_cache.get(category)
        if cached is None:
            return {"category": category, "status": "idle", "groups": [],
                    "total": 0, "page": 1, "page_size": page_size,
                    "pages": 1, "truncated": False, "files": 0,
                    "scanned_at": 0, "stale": False}
        groups = cached["groups"]
        total = len(groups)
        pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        start = (page - 1) * page_size
        stamp = _dir_stamp(root)
        stale = (bool(stamp and cached["stamp"] and stamp != cached["stamp"])
                 or time.time() - cached["scanned_at"] > _LOCAL_CACHE_TTL)
        return {"category": category, "status": "ready",
                "groups": groups[start:start + page_size], "total": total,
                "page": page, "page_size": page_size, "pages": pages,
                "truncated": bool(cached["truncated"]),
                "files": int(cached["files"]),
                "scanned_at": int(cached["scanned_at"]), "stale": bool(stale)}

    def _entry_rows(self) -> list[dict]:
        root = get_export_dir() / EXPORT_CATEGORIES["video_data"]
        try:
            stamp = int(root.stat().st_mtime)
        except OSError:
            stamp = 0
        cached = self._entries_cache
        if cached and cached[0] == str(root) and cached[1] == stamp:
            self._entries_truncated = cached[3]
            return cached[2]

        rows: list[dict] = []
        self._entries_truncated = False
        if asset_index.db_path().exists():
            view = asset_index.fetch_view(str(root), "entries")
            if view and isinstance(view.get("items"), list):
                rows = [self._from_cached(root, item)
                        for item in view["items"]]
        if not rows:
            rows = self._scan_json(root)
        rows = [row for row in rows if row]
        rows.sort(key=lambda item: item.get("publish_time") or "", reverse=True)
        self._entries_cache = (str(root), stamp, rows, self._entries_truncated)
        return rows

    @staticmethod
    def _from_cached(root: Path, item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        file_path = str(item.get("file_path") or "")
        rel = ""
        try:
            rel = str(Path(file_path).relative_to(root)).replace("\\", "/")
        except ValueError:
            rel = os.path.basename(file_path)
        return {
            "title": item.get("title") or "-",
            "author": item.get("author") or "-",
            "platform": item.get("platform") or "",
            "publish_time": item.get("publish_time") or "",
            "play": _num(item.get("play_count")),
            "like": _num(item.get("like_count")),
            "url": item.get("video_url") or "",
            "file": os.path.basename(file_path),
            "path": rel,
            "video_id": str(item.get("video_id") or ""),
            "cover": item.get("cover_url") or "",
            "images": int(item.get("expected_imgs") or 0),
        }

    def _scan_json(self, root: Path) -> list[dict]:
        rows: list[dict] = []
        if not root.is_dir():
            return rows
        cached = asset_index.fetch_all() if asset_index.db_path().exists() else {}
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for name in sorted(filenames):
                if not name.lower().endswith(".json"):
                    continue
                stem = os.path.splitext(name)[0]
                if stem.endswith(("_raw", "_comments")):
                    continue
                scanned += 1
                if scanned > 2000:
                    self._entries_truncated = True
                    return rows
                path = os.path.join(dirpath, name)
                payload = None
                hit = cached.get(os.path.normcase(os.path.abspath(path)))
                if hit and hit[4]:
                    try:
                        payload = json.loads(hit[4])
                    except (ValueError, TypeError):
                        payload = None
                if not isinstance(payload, dict):
                    try:
                        with open(path, "r", encoding="utf-8") as handle:
                            payload = json.load(handle)
                    except (OSError, ValueError):
                        continue
                if isinstance(payload, list):
                    payload = payload[0] if payload else {}
                if not isinstance(payload, dict):
                    continue
                folder = os.path.basename(dirpath)
                platform = _platform_of(folder) or _platform_of(name)
                rows.append({
                    "title": payload.get("标题") or stem,
                    "author": payload.get("作者") or "-",
                    "platform": platform,
                    "publish_time": payload.get("发布时间") or "",
                    "play": _num(payload.get("播放量") if platform != "weibo"
                                  else payload.get("阅览量")),
                    "like": _num(payload.get("点赞数")),
                    "url": payload.get("原始URL") or payload.get("链接") or "",
                    "file": name,
                    "path": os.path.relpath(path, root).replace("\\", "/"),
                    "video_id": str(payload.get("视频ID") or ""),
                    "cover": payload.get("封面") or "",
                    "images": _num(payload.get("图片数")),
                })
        return rows


def _local_group_payload(group: dict) -> dict:
    files = group.get("files") or []
    cover = next((item for item in files if item.get("type") == "image"), None)
    if cover is None:
        cover = next((item for item in files if item.get("type") == "video"),
                     None)
    return {
        "title": group.get("title") or "",
        "date": group.get("date") or "",
        "mtime": group.get("mtime") or "",
        "size": int(group.get("size") or 0),
        "n_img": int(group.get("n_img") or 0),
        "n_vid": int(group.get("n_vid") or 0),
        "n_aud": int(group.get("n_aud") or 0),
        "n_doc": int(group.get("n_doc") or 0),
        "cover": {"name": cover.get("name") or "",
                  "dir": cover.get("rel_dir") or ""} if cover else None,
        "files": [{
            "name": item.get("name") or "",
            "type": item.get("type") or "",
            "size": int(item.get("size") or 0),
            "rel_dir": item.get("rel_dir") or "",
        } for item in files[:_LOCAL_GROUP_LIMIT]],
    }


def _dir_stamp(root: Path) -> int:
    try:
        return int(root.stat().st_mtime)
    except OSError:
        return 0


def _platform_of(text: str) -> str:
    from toolbox.utils import norm_platform_dir
    found = norm_platform_dir(text or "")
    if found:
        return found
    low = (text or "").lower()
    for key in _KNOWN_PLATFORMS:
        if key in low:
            return key
    return ""


def _num(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0

def _video_first_frame(path: Path) -> bytes:
    """ffmpeg 抽视频首帧（stdout 管道输出 mjpeg，不写任何目录）。"""
    import subprocess

    from toolbox.utils import find_ffmpeg_exe
    ffmpeg = find_ffmpeg_exe()
    if not ffmpeg:
        raise ValueError("未找到 ffmpeg，无法生成视频预览")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error",
               "-ss", "1", "-i", str(path), "-frames:v", "1",
               "-f", "mjpeg", "pipe:1"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("视频预览超时") from exc
    frame = result.stdout
    if not frame:
        raise ValueError("视频首帧提取失败（文件可能损坏）")
    return frame

_thumb_cache: dict[tuple[str, int], bytes] = {}


def _scaled_frame(path: Path, is_video: bool) -> bytes:
    """ffmpeg 缩放生成 200px 缩略图（stdout 管道，不写任何目录）。"""
    import subprocess

    from toolbox.utils import find_ffmpeg_exe
    ffmpeg = find_ffmpeg_exe()
    if not ffmpeg:
        raise ValueError("未找到 ffmpeg，无法生成缩略图")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if is_video:
        command += ["-ss", "1"]
    command += ["-i", str(path), "-vf", "scale=200:-2", "-frames:v", "1",
                "-f", "mjpeg", "-q:v", "6", "pipe:1"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise ValueError("缩略图生成超时") from exc
    if not result.stdout:
        raise ValueError("缩略图生成失败")
    return result.stdout
