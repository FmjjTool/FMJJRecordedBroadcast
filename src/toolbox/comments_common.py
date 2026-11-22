"""三大视频平台评论区共用层：筛选 / 保存 / 长图导出。

被 toolbox/{douyin,bili,kuaishou}_comments 复用（duck-typing 后端），
后端需提供字段提取器与常量：
  image_urls / avatar_url / comment_text / comment_user / comment_likes /
  comment_time / comment_ts / comment_extra / IMG_HEADERS
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import requests


def _http_img(be, url: str):
    """下载并打开图片；失败返回 None。"""
    try:
        r = requests.get(url, headers=be.IMG_HEADERS, timeout=20)
        if r.status_code == 200 and len(r.content) > 500:
            from PIL import Image, ImageOps
            import io
            return ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert("RGB")
    except Exception:
        pass
    return None


# ==================== 搜索 / 筛选 ====================

def filter_comments(be, comments: list, keyword: str = "", user: str = "",
                    image_mode: str = "all", min_likes: int = 0,
                    sort_by: str = "default") -> list:
    """搜索筛选评论。

    keyword：评论文本包含（不区分大小写）；user：昵称包含；
    image_mode：all 全部 / has 带图 / text 纯文字；
    min_likes：最低点赞；sort_by：default 原序 / likes 点赞 / time 时间。
    """
    kw = (keyword or "").strip().lower()
    us = (user or "").strip().lower()
    out = []
    for c in comments:
        if kw and kw not in be.comment_text(c).lower():
            continue
        if us and us not in be.comment_user(c).lower():
            continue
        n_img = len(be.image_urls(c))
        if image_mode == "has" and n_img == 0:
            continue
        if image_mode == "text" and n_img > 0:
            continue
        if min_likes and be.comment_likes(c) < min_likes:
            continue
        out.append(c)
    if sort_by == "likes":
        out.sort(key=be.comment_likes, reverse=True)
    elif sort_by == "time":
        out.sort(key=be.comment_ts, reverse=True)
    return out


# ==================== 保存原始数据 ====================

def save_raw(be, comments: list, out_dir: Path, progress=None,
             task_event=None, threads: int = 4) -> dict:
    """保存原始数据：comments.json / comments.txt / images/。返回统计。

    评论图片并发下载（threads 控制并发数）；task_event 可选 → 下载浮窗
    进度（plan/start/done 协议，与 DownloadPanel 一致，任意线程可调）。
    """
    (out_dir / "images").mkdir(parents=True, exist_ok=True)
    (out_dir / "comments.json").write_text(
        json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")

    lines, jobs = [], []  # jobs: (url, 落盘路径)
    for c in comments:
        user = be.comment_user(c)
        text = be.comment_text(c).replace("\n", " ")
        lines.append(f"[{user}] {text}")
        for u in be.image_urls(c):
            ext = ".jpg"
            m = re.search(r"\.(jpe?g|png|webp|gif)(?:[?~]|$)", u, re.I)
            if m:
                ext = "." + m.group(1).lower()
            path = out_dir / "images" / f"img_{len(jobs) + 1:03d}{ext}"
            jobs.append((u, path))
    n_img = len(jobs)

    def _evt(*event):
        if task_event is not None:
            try:
                task_event(*event)
            except Exception:
                pass

    _evt("plan", "parallel", n_img, threads if n_img > 1 else 1)

    def _fetch(job):
        u, path = job
        _evt("start", path.name)
        ok = False
        try:
            r = requests.get(u, headers=be.IMG_HEADERS, timeout=30)
            if r.status_code == 200 and len(r.content) > 1000:
                path.write_bytes(r.content)
                ok = True
        except Exception:
            pass
        _evt("done", path.name, ok)
        return ok

    n_dl = 0
    if threads > 1 and n_img > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=threads) as ex:
            futs = [ex.submit(_fetch, j) for j in jobs]
            for i, fut in enumerate(futs):
                if fut.result():
                    n_dl += 1
                if progress and (i + 1) % 5 == 0:
                    progress(f"图片 {n_dl}/{i + 1}", i + 1, n_img)
    else:
        for i, job in enumerate(jobs):
            if _fetch(job):
                n_dl += 1
            if progress and (i + 1) % 5 == 0:
                progress(f"图片 {n_dl}/{i + 1}", i + 1, n_img)

    (out_dir / "comments.txt").write_text(
        "\n".join(lines) + f"\n\n共 {len(comments)} 条评论，{n_img} 张图片，成功下载 {n_dl} 张",
        encoding="utf-8")
    return {"comments": len(comments), "images": n_img, "downloaded": n_dl}


# ==================== 导出长图（PIL） ====================

def export_comments_png(be, comments: list, meta: dict, out_path: Path,
                        progress=None) -> Path:
    """评论列表导出为 1080 px 长图；绘制统一走 toolbox.image_render。

    meta: {platform, video_desc, filter_desc, stats}（页头信息）。
    评论图片与头像在这里下载；渲染阶段不再发网络请求。超过 20000 px 自动分页。
    """
    from toolbox import image_render

    items = []
    for idx, c in enumerate(comments):
        images = [im for u in be.image_urls(c)[:9]
                  if (im := _http_img(be, u)) is not None]
        items.append({
            "avatar": _http_img(be, be.avatar_url(c)),
            "user": be.comment_user(c),
            "likes": be.comment_likes(c),
            "text": be.comment_text(c),
            "extra": be.comment_extra(c),
            "images": images,
        })
        if progress and (idx + 1) % 5 == 0:
            progress(f"渲染 {idx + 1}/{len(comments)}", idx + 1, len(comments))

    platform = meta.get("platform") or ""
    title = f"{platform}评论区" if platform else "评论区"
    stats = meta.get("stats") or {}
    parts = [f"视频：{(meta.get('video_desc') or '未知')[:60]}"]
    count = f"共导出 {len(comments)} 条评论"
    if stats.get("total"):
        count += f"（原始 {stats['total']} 条）"
    parts.append(count)
    if meta.get("filter_desc"):
        parts.append(f"筛选：{meta['filter_desc']}")
    subtitle = " · ".join(parts)

    path = image_render.render_comment_feed(
        title, subtitle, items, out_path, source=title)
    if progress:
        progress("导出完成", 1, 1)
    return Path(path)
