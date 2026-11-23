"""资产库 WebUI 服务：统计、数据库浏览、本地资源与导出 JSON 概览。

只读；不返回绝对路径，不触发扫描写索引以外的业务动作。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from toolbox import asset_groups, asset_index, increment_db
from toolbox.config_manager import EXPORT_CATEGORIES, get_export_dir, get_export_subdir

_KNOWN_PLATFORMS = ("douyin", "bilibili", "weibo", "kuaishou",
                    "xiaohongshu", "tieba", "zhihu")
_SORT_COLUMNS = {
    "fetched_at", "kind", "title", "author", "publish_time",
    "play_count", "like_count", "comment_count", "video_url",
}
_SKIP_COLUMNS = {"raw_json", "content_key"}
_DB_COLUMNS = [
    "dedup_key", "fetched_at", "kind", "video_id", "title", "author",
    "publish_time", "play_count", "like_count", "comment_count",
    "share_count", "favorite_count", "danmaku_count", "coin_count",
    "duration", "video_url",
]
_PLATFORM_ORDER = ["douyin", "bilibili", "weibo", "kuaishou",
                   "xiaohongshu", "acfun", "tieba", "zhihu"]


class AssetService:
    def __init__(self):
        self._entries_cache: tuple[str, int, list, bool] | None = None
        self._entries_truncated = False
        self._groups_cache: dict[tuple[str, int], tuple[float, list, bool]] = {}

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
        pages = max(1, (int(total) + page_size - 1) // page_size)
        return {"platform": platform, "columns": columns, "rows": data,
                "total": int(total), "page": page, "page_size": page_size,
                "pages": pages}

    def local_groups(self, category: str) -> dict:
        root = get_export_dir() / category
        if category not in set(EXPORT_CATEGORIES.values()) or not root.is_dir():
            raise ValueError("分类目录不存在")
        try:
            stamp = int(root.stat().st_mtime)
        except OSError:
            stamp = 0
        cache_key = (category, stamp)
        cached = self._groups_cache.get(cache_key)
        now = time.time()
        if cached and now - cached[0] < 300:
            groups, truncated = cached[1], cached[2]
        else:
            groups, truncated = asset_groups.load_dir_groups(
                str(root), limit_files=8000, cat=category)
            self._groups_cache = {cache_key: (now, groups, truncated)}
        payload = []
        for group in groups[:500]:
            payload.append({
                "title": group.get("title") or "",
                "date": group.get("date") or "",
                "mtime": group.get("mtime") or "",
                "size": int(group.get("size") or 0),
                "n_img": int(group.get("n_img") or 0),
                "n_vid": int(group.get("n_vid") or 0),
                "n_aud": int(group.get("n_aud") or 0),
                "n_doc": int(group.get("n_doc") or 0),
                "files": [{
                    "name": item.get("name") or "",
                    "type": item.get("type") or "",
                    "size": int(item.get("size") or 0),
                    "rel_dir": item.get("rel_dir") or "",
                } for item in (group.get("files") or [])[:200]],
            })
        return {"category": category, "groups": payload,
                "truncated": bool(truncated)}

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
        start = (page - 1) * page_size
        pages = max(1, (total + page_size - 1) // page_size)
        return {"items": items[start:start + page_size], "total": total,
                "page": page, "page_size": page_size, "pages": pages,
                "truncated": bool(self._entries_truncated)}

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
                rows = [self._from_cached(item) for item in view["items"]]
        if not rows:
            rows = self._scan_json(root)
        rows = [row for row in rows if row]
        rows.sort(key=lambda item: item.get("publish_time") or "", reverse=True)
        self._entries_cache = (str(root), stamp, rows, self._entries_truncated)
        return rows

    @staticmethod
    def _from_cached(item: dict) -> dict | None:
        if not isinstance(item, dict):
            return None
        file_path = str(item.get("file_path") or "")
        return {
            "title": item.get("title") or "-",
            "author": item.get("author") or "-",
            "platform": item.get("platform") or "",
            "publish_time": item.get("publish_time") or "",
            "play": _num(item.get("play_count")),
            "like": _num(item.get("like_count")),
            "url": item.get("video_url") or "",
            "file": os.path.basename(file_path),
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
                    "images": 0,
                })
        return rows


def _platform_of(text: str) -> str:
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
