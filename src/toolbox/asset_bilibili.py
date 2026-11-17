"""B站资产下载（L3）：动态类型/BV 解析、清晰度映射、视频与封面下载。

不依赖 Qt；进度通过回调交给页面/浮窗。
"""

from __future__ import annotations

import json
import os


def _raw_json(item: dict) -> dict:
    path = (item or {}).get("file_path", "")
    if not path:
        return {}
    raw = path.replace(".json", "_raw.json")
    if not os.path.exists(raw):
        return {}
    try:
        with open(raw, encoding="utf-8") as fp:
            data = json.load(fp)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def dynamic_type(item: dict) -> str:
    """'draw'=图文动态 / 'av'=视频动态 / ''=非动态。"""
    raw = _raw_json(item)
    kind = str(raw.get("type", "") or "").upper()
    if "DRAW" in kind:
        return "draw"
    if "AV" in kind or "DYNAMIC" in kind:
        return "av"
    return ""


def av_bvid(item: dict) -> str:
    """从视频动态 raw.json 提取 archive.bvid。"""
    raw = _raw_json(item)
    major = (((raw.get("modules", {}) or {}).get("module_dynamic", {}) or {})
             .get("major", {}) or {})
    archive = major.get("archive", {}) or {}
    return archive.get("bvid", "") or ""


def quality_to_qn(label: str):
    """全局清晰度 → B站 qn；最高画质返回 None。"""
    return {"8K": 127, "杜比视界": 126, "HDR": 125, "4K": 120,
            "1080P60": 116, "1080P": 80, "720P": 64, "480P": 32}.get(label)


def download_video(item: dict, *, qn, cookie: str, out_dir: str, threads: int = 4,
                   progress_cb=None, seg_progress_cb=None) -> str:
    """下载 B站视频并同步保存同名封面；返回视频路径。"""
    from bilibili_download import download_bilibili_by_bvid
    vid = (item or {}).get("video_id", "")
    if dynamic_type(item) == "av":
        bvid = av_bvid(item)
        if bvid:
            vid = bvid
    if not vid or str(vid).startswith("dyn_"):
        raise ValueError("no_bvid")

    vpath = download_bilibili_by_bvid(
        vid, quality=qn, cookie=cookie or "", out_dir=out_dir,
        title=item.get("title", ""), author=item.get("author", ""),
        publish_time=item.get("publish_time", ""), threads=threads,
        progress_cb=progress_cb, seg_progress_cb=seg_progress_cb,
    )
    cover = str(item.get("cover_url") or "").strip()
    if cover and vpath:
        try:
            from media_downloader import _download_one
            if cover.startswith("//"):
                cover = "https:" + cover
            _download_one(
                cover, os.path.splitext(vpath)[0] + ".jpg",
                {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                               "AppleWebKit/537.36",
                 "Referer": "https://www.bilibili.com/"})
        except Exception:
            pass
    return vpath
