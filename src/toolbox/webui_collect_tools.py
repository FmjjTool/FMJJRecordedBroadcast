"""WebUI 采集工具执行：评论区抓取（抖音 / B站 / 微博）与公众号解析导出。

复用桌面端同一套 L4 引擎（`toolbox.bili_comments` / `douyin_comments` /
`weibo_comments` / `platforms.wechat_article` / `exporters.wechat_writer`），
输出目录与桌面一致（`<导出目录>/评论区/<平台>/<视频ID>/`、公众号分类目录）。
评论区长图与原始数据按勾选生成；PDF 公众号导出依赖桌面 Qt 渲染，Web 端不提供。
本模块 Qt-free。
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path

_COMMENT_BACKENDS = {
    "douyin": ("toolbox.douyin_comments", "抖音"),
    "bili": ("toolbox.bili_comments", "B站"),
    "weibo": ("toolbox.weibo_comments", "微博"),
}
_PLATFORM_DIRS = {"douyin": "抖音", "bili": "B站", "weibo": "微博"}
_IMAGE_MODES = ("all", "has", "text")
_SORTS = ("default", "likes", "time")
_WECHAT_FORMATS = {"html": "HTML", "docx": "Word", "md": "Markdown"}


class CollectToolCancelled(Exception):
    """抓取任务被取消。"""


def _comments_mode(payload: dict, platform: str) -> str:
    """兼容旧客户端自动识别；显式模式只接受当前平台支持的值。"""
    mode = str(payload.get("mode") or "").strip()
    if mode not in ("", "video", "author"):
        raise ValueError("不支持的抓取模式，请选择视频或作者")
    if mode == "author" and platform != "douyin":
        raise ValueError("当前平台仅支持视频模式")
    return mode


def ensure_comments(payload: dict) -> None:
    """抓取前校验（请求线程执行，失败直接 400）。"""
    platform = str(payload.get("platform") or "douyin").strip()
    if platform not in _COMMENT_BACKENDS:
        raise ValueError("未知平台，只支持抖音 / B站 / 微博")
    mode = _comments_mode(payload, platform)
    if not str(payload.get("url") or "").strip():
        raise ValueError("请输入作者主页链接或 sec_uid" if mode == "author"
                         else "请输入视频链接或 ID")
    if not payload.get("png", True) and not payload.get("raw", True):
        raise ValueError("请至少选择一种导出内容（长图 / 原始数据）")


def ensure_wechat(payload: dict) -> list[str]:
    """公众号解析前校验：识别链接与导出格式。"""
    from platforms.wechat_article import extract_urls

    urls = extract_urls(str(payload.get("text") or ""))
    if not urls:
        raise ValueError("未识别到公众号文章链接（需 mp.weixin.qq.com 链接）")
    if len(urls) > 30:
        raise ValueError("单次最多 30 篇文章，请分批")
    formats = [str(item) for item in (payload.get("formats") or [])
               if str(item) in _WECHAT_FORMATS]
    if not formats:
        raise ValueError("请至少选择一种导出格式（HTML / Word / Markdown）")
    return urls


def comments_run(payload: dict, on_progress=None, on_log=None,
                 is_cancelled=None) -> dict:
    """抓取评论区并按勾选导出长图 / 原始数据。"""
    from toolbox.config_manager import get_export_dir, get_export_subdir

    platform = str(payload.get("platform") or "douyin").strip()
    if platform not in _COMMENT_BACKENDS:
        raise ValueError("未知平台，只支持抖音 / B站 / 微博")
    mode = _comments_mode(payload, platform)
    text = str(payload.get("url") or "").strip()
    if not text:
        raise ValueError("请输入作者主页链接或 sec_uid" if mode == "author"
                         else "请输入视频链接或 ID")
    try:
        pages = int(payload.get("pages") or 5)
    except (TypeError, ValueError):
        pages = 5
    pages = max(1, min(pages, 50))
    save_png = bool(payload.get("png", True))
    save_raw = bool(payload.get("raw", True))
    if not (save_png or save_raw):
        raise ValueError("请至少选择一种导出内容（长图 / 原始数据）")
    keyword = str(payload.get("keyword") or "").strip()
    user = str(payload.get("user") or "").strip()
    image_mode = str(payload.get("image_mode") or "all")
    if image_mode not in _IMAGE_MODES:
        image_mode = "all"
    sort_by = str(payload.get("sort_by") or "default")
    if sort_by not in _SORTS:
        sort_by = "default"
    try:
        min_likes = int(payload.get("min_likes") or 0)
    except (TypeError, ValueError):
        min_likes = 0
    min_likes = max(0, min(min_likes, 10 ** 8))

    module_name, platform_name = _COMMENT_BACKENDS[platform]
    be = importlib.import_module(module_name)
    kind, vid = be.parse_input(text)
    if not kind or not vid:
        raise ValueError("无法识别输入（需要视频链接或 ID）")
    if mode == "author" and kind != "sec_uid":
        raise ValueError("作者模式需要抖音作者主页链接或 sec_uid")
    if mode == "video" and kind == "sec_uid":
        raise ValueError("视频模式需要视频链接或 ID；作者主页请切换为作者模式")
    _log(on_log, f"{platform_name} · 识别到 {kind}：{vid}")

    plan = []
    if kind == "sec_uid" and hasattr(be, "fetch_author_videos"):
        try:
            count = int(payload.get("count") or 3)
        except (TypeError, ValueError):
            count = 3
        count = max(1, min(count, 20))
        _log(on_log, f"获取作者视频列表（最多 {count} 个）…")
        targets = be.fetch_author_videos(vid, count)
        if not targets:
            raise RuntimeError("未获取到作者视频列表（链接无效或被风控拦截）")
        plan = [(item[0], item[1], None) for item in targets]
    elif kind == "dynamic" and hasattr(be, "fetch_dynamic_info"):
        _log(on_log, "校验动态…")
        info = be.fetch_dynamic_info(vid)
        if not info.get("exists") and not info.get("unknown"):
            raise RuntimeError(f"动态不存在（ID {vid}）")
        plan = [(info.get("comment_id") or vid, info.get("desc") or "",
                 info.get("comment_type") or 17)]
    else:
        _log(on_log, "校验视频…")
        info = be.fetch_video_info(vid) if hasattr(be, "fetch_video_info") else {}
        if isinstance(info, dict) and not info.get("exists", True) \
                and not info.get("unknown"):
            raise RuntimeError(f"视频不存在（ID {vid}）")
        desc = info.get("desc") if isinstance(info, dict) else ""
        plan = [(vid, desc or "", None)]

    total = len(plan)
    root = get_export_subdir("video_comments")
    export_root = get_export_dir()
    videos = []
    files = []
    for index, (fetch_id, desc, ctype) in enumerate(plan, 1):
        _check_cancelled(is_cancelled)
        _progress(on_progress, index - 1, total,
                  f"视频 {index}/{total}：{desc or fetch_id}")
        _log(on_log, f"[{index}/{total}] 抓取评论：{desc or fetch_id}")
        display_id = str(vid if kind == "dynamic" else fetch_id)
        kwargs = {"pages": pages, "progress": lambda msg, cur=0, tot=0:
                  _log(on_log, str(msg))}
        if ctype is not None:
            kwargs["ctype"] = ctype
        comments = list(be.fetch_comments(fetch_id, **kwargs) or [])
        filtered = comments
        if keyword or user or image_mode != "all" or min_likes \
                or sort_by != "default":
            filtered = list(be.filter_comments(
                comments, keyword=keyword, user=user, image_mode=image_mode,
                min_likes=min_likes, sort_by=sort_by) or [])
        image_count = sum(len(be.image_urls(item)) for item in comments) \
            if hasattr(be, "image_urls") else 0
        out_dir = root / _PLATFORM_DIRS.get(platform, platform) / display_id
        produced = []
        if save_raw and filtered:
            try:
                be.save_raw(filtered, out_dir,
                            progress=lambda msg, cur=0, tot=0: None)
                produced.append("原始数据")
            except Exception as exc:  # noqa: BLE001 - 导出失败不阻断其它视频
                _log(on_log, f"原始数据导出失败：{exc}")
        if save_png and filtered:
            meta = {
                "platform": platform_name,
                "video_desc": desc or display_id,
                "filter_desc": _filter_desc(keyword, user, image_mode,
                                            min_likes, sort_by),
                "stats": {"total": len(comments)},
            }
            try:
                out_png = out_dir / f"评论区_{display_id}.png"
                be.export_comments_png(filtered, meta, out_png)
                produced.append(out_png.name)
            except Exception as exc:  # noqa: BLE001
                _log(on_log, f"评论区长图导出失败：{exc}")
        if produced:
            files.append({"video": display_id,
                          "dir": _rel(out_dir, export_root),
                          "items": produced})
        videos.append({"id": display_id, "desc": desc or "",
                       "total": len(comments), "exported": len(filtered),
                       "images": image_count})
        _log(on_log, f"  共 {len(comments)} 条评论，导出 {len(filtered)} 条")
    _progress(on_progress, total, total, "抓取完成")
    comment_total = sum(item["total"] for item in videos)
    exported_total = sum(item["exported"] for item in videos)
    return {
        "platform": platform,
        "platform_name": platform_name,
        "videos": videos,
        "files": files,
        "total": comment_total,
        "exported": exported_total,
        "summary": (f"{platform_name} · {len(videos)} 个视频 / {comment_total} 条评论，"
                    f"导出 {exported_total} 条"
                    + ("（可在「产物浏览」查看）" if files else "")),
        "note": "抓取依赖登录态与网络；长图与原始数据写入评论区导出目录。",
    }


def douyin_emoji_fetch(payload: dict) -> dict:
    """拉取抖音直播间「会员表情栏」（只读，需要抖音登录 Cookie）。"""
    from toolbox import douyin_emoji as DE

    room = str(payload.get("room") or "").strip()
    if not room:
        raise ValueError("请填写直播间号或直播间链接")
    result = DE.fetch_vip_emojis(room) or {}
    error = str(result.get("error") or "")
    items = result.get("items") or []
    if error and not items:
        raise ValueError(f"拉取失败：{error}")
    rows = [{"name": str(item.get("name") or "表情"),
             "url": str(item.get("url") or "")} for item in items
            if item.get("url")]
    return {"items": rows, "total": len(rows), "error": error,
            "describe": str(result.get("describe") or ""),
            "member": bool(result.get("member")),
            "note": "只读拉取；下载后写入「抖音表情包」导出目录。"}


def douyin_emoji_download(payload: dict, on_progress=None, on_log=None,
                          is_cancelled=None) -> dict:
    """拉取并下载抖音直播间会员表情。"""
    from toolbox import douyin_emoji as DE
    from toolbox.config_manager import get_export_subdir

    room = str(payload.get("room") or "").strip()
    if not room:
        raise ValueError("请填写直播间号或直播间链接")
    data = douyin_emoji_fetch({"room": room})
    items = data.get("items") or []
    if not items:
        raise ValueError("没有可下载的会员表情")
    out_dir = Path(get_export_subdir("douyin_emoji"))
    _log(on_log, f"共 {len(items)} 个表情，开始下载到 {out_dir}")

    def progress(done, total, name, ok) -> None:
        if is_cancelled and is_cancelled():
            return
        _progress(on_progress, done, total, f"{'已下载' if ok else '失败'}：{name}")

    result = DE.download_items(items, out_dir, on_progress=progress,
                               stop=is_cancelled or (lambda: False))
    saved = len(result.get("saved") or [])
    failed = len(result.get("failed") or [])
    return {"saved": saved, "failed": failed,
            "summary": f"抖音表情下载完成：成功 {saved} 个"
                       + (f"，失败 {failed} 个" if failed else ""),
            "note": "输出目录：抖音表情包；可在下方产物列表查看。"}


def _filter_desc(keyword, user, image_mode, min_likes, sort_by) -> str:
    parts = []
    if keyword:
        parts.append(f"关键词「{keyword}」")
    if user:
        parts.append(f"昵称「{user}」")
    if image_mode != "all":
        parts.append("仅带图" if image_mode == "has" else "仅纯文字")
    if min_likes:
        parts.append(f"点赞≥{min_likes}")
    if sort_by != "default":
        parts.append("按点赞排序" if sort_by == "likes" else "按时间排序")
    return "、".join(parts) or "未筛选"


def wechat_run(payload: dict, on_progress=None, on_log=None,
               is_cancelled=None) -> dict:
    """公众号文章批量解析并导出 HTML / Word / Markdown。"""
    import os

    from platforms.wechat_article import download_images, extract_urls, fetch_article
    from exporters import wechat_writer
    from toolbox.config_manager import get_export_subdir, load_convert_config
    from toolbox.config_manager import get_export_dir

    text = str(payload.get("text") or "")
    urls = extract_urls(text)
    if not urls:
        raise ValueError("未识别到公众号文章链接（需 mp.weixin.qq.com 链接）")
    if len(urls) > 30:
        raise ValueError("单次最多 30 篇文章，请分批")
    formats = [str(item) for item in (payload.get("formats") or [])
               if str(item) in _WECHAT_FORMATS]
    if not formats:
        raise ValueError("请至少选择一种导出格式（HTML / Word / Markdown）")
    cfg = load_convert_config()
    if cfg.get("wechat_use_custom_dir") and cfg.get("wechat_custom_dir"):
        out_dir = Path(str(cfg["wechat_custom_dir"]))
    else:
        out_dir = get_export_subdir("wechat")
    export_root = get_export_dir()
    ok = 0
    failed = []
    outputs = []
    total = len(urls)
    for index, url in enumerate(urls, 1):
        _check_cancelled(is_cancelled)
        _progress(on_progress, index - 1, total, f"解析第 {index}/{total} 篇")
        try:
            art = fetch_article(url)
            _log(on_log, f"[{index}/{total}] {art.title}")
            _log(on_log, f"  公众号 {art.account or '-'} · 图片 "
                         f"{len(art.images)} 张")
            download_images(
                art, progress=lambda n, m: _log(on_log, f"  图片 {n}/{m}"),
                should_cancel=is_cancelled or (lambda: False), threads=6)
            rel_dir, stem = wechat_writer.build_article_path(art, str(out_dir))
            art_dir = out_dir / rel_dir
            art_dir.mkdir(parents=True, exist_ok=True)
            if "html" in formats:
                wechat_writer.export_html(art, str(art_dir / (stem + ".html")))
            if "docx" in formats:
                wechat_writer.export_docx(art, str(art_dir / (stem + ".docx")))
            if "md" in formats:
                wechat_writer.export_markdown(art, str(art_dir))
            outputs.append({"title": art.title or url,
                            "dir": _rel(art_dir, export_root),
                            "formats": list(formats)})
            ok += 1
            _log(on_log, "  导出完成 → " + _rel(art_dir, export_root))
        except CollectToolCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - 单篇失败不影响其它
            failed.append({"url": url, "error": str(exc)[:200]})
            _log(on_log, f"[{index}/{total}] 失败：{exc}")
        if index < total and not (is_cancelled and is_cancelled()):
            time.sleep(1.2)  # 与桌面一致：批量限速，降低风控
    _progress(on_progress, total, total, "解析完成")
    return {
        "ok": ok,
        "failed": len(failed),
        "outputs": outputs,
        "failures": failed,
        "summary": (f"公众号解析完成：成功 {ok} 篇"
                    + (f"，失败 {len(failed)} 篇" if failed else "")),
        "note": ("导出到公众号分类目录（或桌面设置的自定义目录）；"
                 "PDF 导出需要桌面端 Qt 渲染，Web 端不提供。"),
    }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix() + "/"
    except ValueError:
        return path.name + "/"


def _check_cancelled(is_cancelled) -> None:
    if is_cancelled and is_cancelled():
        raise CollectToolCancelled("任务已取消")


def _log(on_log, text) -> None:
    if on_log and text:
        on_log(str(text))


def _progress(on_progress, done, total, text) -> None:
    if on_progress:
        on_progress(done, total, text)
