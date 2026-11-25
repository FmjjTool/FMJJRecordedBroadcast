"""WebUI B站资源：表情包搜索 / 下载、装扮下载、UP 主空间资源下载。

复用 `bili.api` 与 `bili.download.DownloadTask`，清单映射与桌面端共用
`toolbox.bili_assets`；输出到 `<导出目录>/B站资源/<表情包|装扮|空间资源>/`。
本模块 Qt-free；真实请求需要 B站 登录 Cookie。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

_MAX_IDS = 30
_MAX_FILES = 3000


class BiliToolCancelled(Exception):
    """下载任务被取消。"""


def _api():
    from bili import api
    return api


def _export_sub(kind: str) -> Path:
    from toolbox.config_manager import get_export_dir
    return get_export_dir() / "B站资源" / kind


def _ts(value) -> str:
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def emote_search(payload: dict) -> dict:
    """搜索 B站表情包（只读，返回全量包名与 ID）。"""
    api = _api()
    keyword = str(payload.get("keyword") or "").strip().lower()
    try:
        limit = int(payload.get("limit") or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 500))
    try:
        panel = api.emote_panel()
    except Exception as exc:  # noqa: BLE001 - bili.api 异常类型较多
        raise ValueError(f"读取表情包列表失败：{exc}") from exc
    rows = []
    for item in panel or []:
        name = str(item.get("text") or item.get("id") or "")
        rows.append({"id": str(item.get("id") or ""), "name": name,
                     "mtime": _ts(item.get("mtime"))})
    if keyword:
        rows = [row for row in rows if keyword in row["name"].lower()]
    total = len(rows)
    return {"items": rows[:limit], "total": total,
            "truncated": total > limit, "panel_total": len(panel or []),
            "note": "只读搜索；勾选后下载到「B站资源/表情包」。"
                    "长 ID（>8 位）按装扮表情包处理。"}


def emote_download(payload: dict, on_progress=None, on_log=None,
                   is_cancelled=None) -> dict:
    """按勾选的包 ID 下载表情图。"""
    from bili.download import DownloadTask

    ids = [str(item).strip() for item in (payload.get("ids") or [])
           if str(item).strip()]
    if not ids:
        raise ValueError("请先勾选要下载的表情包")
    if len(ids) > _MAX_IDS:
        raise ValueError(f"单次最多下载 {_MAX_IDS} 个表情包")
    api = _api()
    base = _export_sub("表情包")
    saved = failed = 0
    hints = []
    titles = []
    for index, idx in enumerate(ids, 1):
        _check_cancelled(is_cancelled)
        _log(on_log, f"[{index}/{len(ids)}] 解析表情包 {idx}…")
        files, title = _emote_files(api, idx, hints)
        if not files:
            failed += 1
            _log(on_log, f"  {idx}：没有可下载的表情（可能已下架或为收藏集）")
            continue
        title = api.sanitize(title or idx)
        titles.append(title)
        _log(on_log, f"  {title}：{len(files)} 个文件")
        task = DownloadTask(title, files[: _MAX_FILES], str(base),
                            on_progress=lambda done, total: _progress(
                                on_progress, index - 1 + (done / total if total else 0),
                                len(ids), f"{title} {done}/{total}"))
        result = task.run(5)
        saved += int(result.get("done") or 0)
        failed += len(result.get("failed") or [])
    _progress(on_progress, len(ids), len(ids), "下载完成")
    return {"saved": saved, "failed": failed, "packages": titles,
            "hints": hints[:20],
            "summary": f"表情包下载完成：成功 {saved} 个文件"
                       + (f"，失败 {failed} 个" if failed else ""),
            "note": "输出目录：B站资源/表情包；可在下方产物列表查看。"}


def _emote_files(api, idx: str, hints: list) -> tuple[list, str]:
    """单个包 ID → (文件清单, 标题)；与桌面端 ID 下载逻辑一致。"""
    from toolbox.bili_assets import _emoji_key, _emoji_urls

    files = []
    title = ""
    if len(idx) > 8:  # 装扮表情包（长 ID）
        detail = api.garb_item(idx)
        title = detail.get("name") or idx
        packages = (detail.get("suit_items", {}) or {}).get("emoji_package") or []
        if not packages:
            packages = [{"name": detail.get("name", ""),
                         "properties": detail.get("properties", {}) or {}}]
        for pkg in packages:
            raw = (pkg.get("properties", {}) or {}).get("item_emoji_list")
            items = []
            if isinstance(raw, str) and raw.startswith("["):
                try:
                    items = json.loads(raw)
                except ValueError:
                    items = []
            elif isinstance(raw, list):
                items = raw
            if not items:
                items = [{"name": item.get("name", ""),
                          "properties": item.get("properties", {}) or {}}
                         for item in (detail.get("suit_items", {}) or {}).get(
                             "emoji", []) or []]
            pkg_dir = api.sanitize(pkg.get("name") or title or "表情包")
            for emoji in items:
                name = api.sanitize(_emoji_key(emoji.get("name", "表情")))
                for url in _emoji_urls(emoji.get("properties", {}) or {}):
                    files.append({"path": f"{pkg_dir}/{name}", "url": url})
        return files, title
    packages = api.emote_package([idx])
    if not packages:
        hints.append(f"{idx}：普通表情包接口无数据（可能是收藏集或已下架）")
        return [], idx
    for pkg in packages:
        title = pkg.get("text") or idx
        pkg_dir = api.sanitize(title)
        for emote in pkg.get("emote") or []:
            url = (emote.get("gif_url") or emote.get("webp_url")
                   or emote.get("url") or emote.get("image"))
            if url:
                files.append({"path": f"{pkg_dir}/{api.sanitize(emote.get('text', '表情'))}",
                              "url": url})
    return files, title


def _bili_id(text: str) -> str:
    import re

    raw = str(text or "").strip()
    match = re.search(r"(?:space\.bilibili\.com/|/suit/|item_id=)(\d+)", raw)
    if match:
        return match.group(1)
    match = re.fullmatch(r"\d{2,20}", raw)
    if match:
        return raw
    raise ValueError("无法识别 ID，请填写数字 ID 或对应链接")


def garb_download(payload: dict, on_progress=None, on_log=None,
                  is_cancelled=None) -> dict:
    """按装扮 item_id 下载全部素材。"""
    from bili.download import DownloadTask
    from toolbox.bili_assets import suit_files

    item_id = _bili_id(payload.get("item_id"))
    api = _api()
    _log(on_log, f"读取装扮 {item_id}…")
    try:
        detail = api.garb_item(item_id)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"装扮读取失败：{exc}") from exc
    files = suit_files(detail)
    if not files:
        raise ValueError("该装扮没有可下载的资源")
    title = api.sanitize(detail.get("name") or f"装扮{item_id}")
    _log(on_log, f"{title}：{len(files)} 个文件")
    _check_cancelled(is_cancelled)
    task = DownloadTask(
        title, files[: _MAX_FILES], str(_export_sub("装扮")),
        on_progress=lambda done, total: _progress(
            on_progress, done, total, f"{title} {done}/{total}"))
    result = task.run(5)
    saved = int(result.get("done") or 0)
    failed = len(result.get("failed") or [])
    return {"saved": saved, "failed": failed, "title": title,
            "summary": f"装扮下载完成：{title}，成功 {saved} 个文件"
                       + (f"，失败 {failed} 个" if failed else ""),
            "note": "输出目录：B站资源/装扮/<装扮名>；下载需要 B站 登录。"}


def up_download(payload: dict, on_progress=None, on_log=None,
                is_cancelled=None) -> dict:
    """按 UID 下载 UP 主空间资源（头像 / 横幅 / 表情等）。"""
    from bili.download import DownloadTask
    from toolbox.bili_assets import space_files

    uid = _bili_id(payload.get("uid"))
    api = _api()
    _log(on_log, f"读取 UP 主 {uid} 空间…")
    try:
        card = api.user_card(uid)
        try:
            appinfo = api.app_space(uid)
        except Exception:  # noqa: BLE001 - App 接口可选
            appinfo = {}
        emotes = api.upower_emote(uid)
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"空间读取失败：{exc}") from exc
    files = space_files(card, appinfo, emotes)
    if not files:
        raise ValueError("该用户空间没有可下载的资源")
    name = (card.get("card", {}) or {}).get("name") or uid
    title = f"{api.sanitize(name)} 空间"
    _log(on_log, f"{title}：{len(files)} 个文件")
    _check_cancelled(is_cancelled)
    task = DownloadTask(
        title, files[: _MAX_FILES], str(_export_sub("空间资源")),
        on_progress=lambda done, total: _progress(
            on_progress, done, total, f"{title} {done}/{total}"))
    result = task.run(5)
    saved = int(result.get("done") or 0)
    failed = len(result.get("failed") or [])
    return {"saved": saved, "failed": failed, "title": title,
            "summary": f"空间资源下载完成：{title}，成功 {saved} 个文件"
                       + (f"，失败 {failed} 个" if failed else ""),
            "note": "输出目录：B站资源/空间资源/<昵称> 空间。"}


def _check_cancelled(is_cancelled) -> None:
    if is_cancelled and is_cancelled():
        raise BiliToolCancelled("任务已取消")


def _log(on_log, text) -> None:
    if on_log and text:
        on_log(str(text))


def _progress(on_progress, done, total, text) -> None:
    if on_progress:
        on_progress(done, total, text)
