"""采集结果 WebUI 服务：分平台查询、筛选与导出。"""

from __future__ import annotations

import os
import time
import zipfile
from pathlib import Path

from exporters import export_per_video_flat
from toolbox import collected_store, download_ops
from toolbox.collected_urls import openable_url
from toolbox.config_manager import get_export_subdir, load_convert_config

PLATFORMS = (("douyin", "抖音"), ("bilibili", "B站"),
             ("weibo", "微博"), ("other", "其他"))
_FORMATS = ("json", "xlsx", "md")
_MAX_EXPORT = 20000


class CollectedService:
    def overview(self) -> dict:
        platforms = []
        for key, name in PLATFORMS:
            _items, total = collected_store.query_cards(key, limit=1)
            platforms.append({"key": key, "name": name, "total": int(total)})
        cfg = load_convert_config()
        formats = [fmt for fmt in cfg.get("export_formats") or []
                   if fmt in _FORMATS]
        try:
            revision = Path(collected_store.db_path()).stat().st_mtime_ns
        except OSError:
            revision = 0
        return {
            "platforms": platforms,
            "formats": formats or ["json", "xlsx", "md"],
            "comment_pages": int(cfg.get("weibo_comment_pages", 2) or 0),
            "revision": revision,
        }

    def items(self, platform: str, page: int, page_size: int,
              keyword: str = "") -> dict:
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        raw, total = collected_store.query_cards(
            platform, keyword, limit=page_size, offset=(page - 1) * page_size)
        items = []
        for row in raw:
            url = openable_url(
                row["platform"], row["video_id"], row["video_url"],
                row["raw_url"], row["kind"])
            items.append({
                "key": row["key"],
                "title": row["title"] or "-",
                "author": row["author"] or "-",
                "publish_time": row["publish_time"] or "",
                "kind": row["kind"] or "",
                "play": _num(row["play_count"]),
                "like": _num(row["like_count"]),
                "url": url,
            })
        pages = max(1, (total + page_size - 1) // page_size)
        return {"platform": platform, "page": page, "page_size": page_size,
                "pages": pages, "total": int(total), "items": items}

    def keys(self, platform: str, keyword: str = "") -> dict:
        """整栏条目键（用于全选 / 反选 / 全不选，与桌面端一致）。"""
        keys = collected_store.query_keys(
            str(platform or ""), str(keyword or ""), limit=_MAX_EXPORT)
        return {"platform": platform, "keys": keys, "total": len(keys)}

    def export_job(self, job, payload: dict) -> dict:
        formats = [fmt for fmt in payload.get("formats") or [] if fmt in _FORMATS]
        if not formats:
            raise ValueError("请至少选择一种导出格式")
        keys = [str(key) for key in payload.get("keys") or [] if str(key)]
        platform = str(payload.get("platform") or "")
        keyword = str(payload.get("keyword") or "")
        if not keys:
            keys = collected_store.query_keys(platform, keyword, limit=_MAX_EXPORT)
        if not keys:
            raise ValueError("没有可导出的条目")
        if len(keys) > _MAX_EXPORT:
            raise ValueError(f"一次最多导出 {_MAX_EXPORT} 条")
        items_map = collected_store.read_by_keys(keys, include_raw=True)
        items = [items_map[key] for key in keys if key in items_map]
        if not items:
            raise ValueError("条目已不存在，请刷新后重试")
        comments = payload.get("comment_pages")
        try:
            comments = max(0, min(50, int(comments if comments is not None else 2)))
        except (TypeError, ValueError):
            comments = 0
        job.update(f"正在导出 {len(items)} 条…", 0.05)
        out_dir = str(get_export_subdir("video_data"))
        paths, skipped = export_per_video_flat(
            items, output_dir=out_dir, formats=formats, skip_existing=True,
            comments_pages=comments, progress=lambda msg: job.log(str(msg)))
        files = [Path(path) for path in paths if Path(path).is_file()]
        result = {"exported": len(files), "skipped": len(skipped),
                  "formats": formats}
        if files:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            archive = job.workspace / f"采集结果_{stamp}.zip"
            job.update("正在打包导出文件…", 0.9)
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                used: set[str] = set()
                for path in files:
                    try:
                        arc = os.path.relpath(path, out_dir).replace("\\", "/")
                    except ValueError:
                        arc = path.name
                    if arc.startswith("../") or arc in used:
                        arc = path.name
                    used.add(arc)
                    zf.write(path, arc)
            job.add_file(archive)
            result["archive"] = archive.name
        job.update("导出完成", 1.0)
        return result

    def download_job(self, job, payload: dict) -> dict:
        keys = [str(key) for key in payload.get("keys") or [] if str(key)]
        if not keys:
            raise ValueError("请先勾选要下载的条目")
        if len(keys) > 500:
            raise ValueError("一次最多下载 500 条")
        items_map = collected_store.read_by_keys(keys, include_raw=True)
        items = [items_map[key] for key in keys if key in items_map]
        if not items:
            raise ValueError("条目已不存在，请刷新后重试")
        quality = str(payload.get("quality") or "最高画质")
        try:
            threads = max(1, min(16, int(payload.get("threads") or 4)))
        except (TypeError, ValueError):
            threads = 4
        job.update(f"正在下载 {len(items)} 条到服务端导出目录…")
        summary = download_ops.download_items_mixed(
            items, quality_label=quality, threads=threads,
            log=lambda line: job.log(str(line)))
        job.update("下载完成", 1.0)
        return {"count": len(items), "summary": summary or "下载完成"}


def _num(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
