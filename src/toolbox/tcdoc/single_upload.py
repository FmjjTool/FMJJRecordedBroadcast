"""资产页单条作品 → 腾讯文档「官方动态」上传。

与中间层批量上传共用同一条写回链路（记录构造 / 计划 / 图片 / 读回校验），
差别只有三点：
1. 条目直接来自资产管理（本地 json + 同目录媒体），不经过中间层待同步清单；
2. 上传前自动跑一遍「同步前刷新」（采集 + 拉云端快照），不需要用户手动点；
3. 一次只写一条，不受中间层 50 条批量上限影响。
"""

from __future__ import annotations

import re
from pathlib import Path

from toolbox import sync_refresh
from toolbox.tcdoc import bridge
from toolbox.tcdoc import import_pending as pending
from toolbox.tcdoc.import_local import (
    ImportErrorDetail, LocalImage, LocalImportClient, ManagedAuth,
)

MEDIA_ROOT = pending.ROOT / "download/已获取数据"
# 写回白名单里的「官方动态」表（与中间层批量上传同一个目标）
OFFICIAL_DOC = "DRnd3bkhkQUR1aEJr"
OFFICIAL_POSTS_TAB = "ss_h2uh2s"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
                  ".tif", ".tiff", ".avif", ".heic", ".heif"}
MAX_IMAGES = 20


def _item_from_entry(entry: dict) -> dict:
    """资产管理条目 → bridge 的本地条目结构（供 _row_from_group 生成表格行）。"""
    platform = str(entry.get("platform") or "")
    video_id = str(entry.get("video_id") or "")
    url = str(entry.get("video_url") or "")
    if platform == "bilibili":
        kind = "dynamic" if (video_id.startswith("dyn_") or "t.bilibili.com" in url) else "video"
    elif platform == "weibo":
        kind = "dynamic"
    elif entry.get("duration"):
        kind = "video"
    else:
        kind = "image"
    return {
        "platform": platform,
        "kind": kind,
        "video_id": video_id,
        "title": entry.get("title") or "",
        "description": entry.get("description") or "",
        "author": entry.get("author") or "",
        "publish_time": entry.get("publish_time") or "",
        "video_url": url,
        "raw_url": entry.get("raw_url") or "",
        "cover_url": entry.get("cover_url") or "",
        "duration": entry.get("duration") or 0,
        "images": [],
    }


def _trailing_number(path: Path) -> int:
    match = re.search(r"_(\d+)$", path.stem)
    return int(match.group(1)) if match else 0


def pick_local_images(entry: dict) -> list[Path]:
    """取这条作品的本地图片：优先多图 `_1/_2…`，没有则用视频封面 `_封面`。"""
    json_path = Path(str(entry.get("file_path") or ""))
    folder = json_path.parent
    if not json_path.name or not folder.is_dir():
        return []
    files = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES
             and p.name.startswith(json_path.stem)]
    numbers = sorted((p for p in files if _trailing_number(p)), key=_trailing_number)
    covers = sorted(p for p in files if "_封面" in p.name)
    picked = numbers or covers
    if not picked:
        picked = sorted(p for p in files if p not in covers)
    return picked[:MAX_IMAGES]


def _fill_type_defaults(row: dict, item: dict, has_images: bool) -> None:
    """表里投票算不出「动态类型 / 发布位置」时按内容兜底，避免整条被卡住。"""
    platform, kind = item["platform"], item["kind"]
    if not row.get("动态类型"):
        if platform == "weibo":
            row["动态类型"] = "图文" if has_images else "文字"
        elif kind == "video":
            row["动态类型"] = "视频投稿"
        else:
            row["动态类型"] = "图文" if has_images else "动态"
    if not row.get("发布位置"):
        row["发布位置"] = "动态" if (kind == "dynamic" or platform == "weibo") else "主页"


def _read_cookie() -> str:
    import auth as auth_module
    return auth_module.get_cookie("tcdoc_openapi") or ""


def upload_entry(entry: dict, *, doc_key: str, tab_id: str, refresh: bool = True,
                 emit=lambda _event: None) -> dict:
    """单条上传：自动刷新 → 构造记录 → 写入 → 读回校验。返回 receipt。"""
    entry = dict(entry or {})
    if refresh:
        emit({"phase": "refresh"})
        state = sync_refresh.run(on_status=lambda text: emit(
            {"phase": "refresh_status", "text": text}))
        if not state.get("ok"):
            detail = "；".join(f"{key}:{value.get('msg')}"
                               for key, value in (state.get("steps") or {}).items())
            raise ImportErrorDetail(f"自动刷新失败，未上传（{state.get('message')}｜{detail}）")

    remote = bridge.load_remote(doc_key, tab_id)
    index = bridge._index_of(remote)
    row = bridge._row_from_group([_item_from_entry(entry)], index)
    link = row.get("作品链接") or row.get("动态链接(b站)")
    if not link:
        fallback = bridge._norm_url(str(entry.get("raw_url") or ""))
        if fallback:
            row["作品链接"] = fallback
            link = fallback
    key = pending.link_key(link) if link else None
    if not key:
        raise ImportErrorDetail(
            "这条作品缺少可确定身份的原帖链接（需要原始链接或视频 ID），无法保证不重复写入")

    paths = pick_local_images(entry)
    _fill_type_defaults(row, _item_from_entry(entry), bool(paths))
    record = {
        "key": key,
        "columns": {name: str(row.get(name) or "") for name in bridge.COLUMNS},
        "images": [],
        "expected_images": len(paths),
        "missing_slots": [],
        "_images": [LocalImage.inspect(path, MEDIA_ROOT) for path in paths],
    }
    client = LocalImportClient(ManagedAuth(_read_cookie), doc_key, tab_id)
    client.connect()
    receipt = pending.run(client, [record], apply=True, available_only=False,
                          verify_each=True, emit=emit, profile="posts",
                          allow_new_options=True)
    if receipt.get("added"):
        sync_refresh.invalidate("单条上传完成")
    return receipt