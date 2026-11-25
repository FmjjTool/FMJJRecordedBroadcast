"""命名投稿 · B站投稿上传（WebUI L3）：预检与二次确认上传。

复用桌面端 `toolbox.bili_upload`（元数据模板、命名校验、UPOS 上传、上传历史与
幂等拦截）。预检只读取本地文件与 B站账号信息，不创建稿件；上传必须带
`confirmed=true`，且会重新预检一次，避免选中文件或模板在确认后被改动。
本模块 Qt-free；真实上传需要 B站登录 Cookie（SESSDATA + bili_jct）。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

_SETTING_KEYS = ("title", "desc", "tags", "tid", "creation_statement", "season",
                 "source", "cover", "date", "auto_publish", "platform", "threads")
_MAX_FILES = 20


class PublishCancelled(Exception):
    """上传任务被取消。"""


def _bu():
    from toolbox import bili_upload
    return bili_upload


def _work_root() -> Path | None:
    from toolbox.config_manager import load_convert_config
    text = str(load_convert_config().get("work_dir") or "").strip()
    if not text:
        return None
    root = Path(text)
    return root if root.is_dir() else None


def _resolve(paths) -> list[Path]:
    if not isinstance(paths, list) or not paths:
        raise ValueError("请先勾选要投稿的视频")
    if len(paths) > _MAX_FILES:
        raise ValueError(f"单次最多 {_MAX_FILES} 个视频（分P）")
    root = _work_root()
    if root is None:
        raise ValueError("尚未设置工作目录，请先在全局设置中配置")
    resolved = []
    for item in paths:
        rel = str(item or "").replace("\\", "/").strip().lstrip("/")
        if not rel:
            raise ValueError("视频路径不正确")
        path = (root / rel)
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise ValueError("视频不在工作目录内") from exc
        if not path.is_file():
            raise ValueError(f"视频不存在：{rel}")
        resolved.append(path)
    return resolved


def _settings(payload: dict) -> dict:
    bu = _bu()
    raw = payload.get("settings") or {}
    if not isinstance(raw, dict):
        raise ValueError("投稿设置格式不正确")
    settings = {key: raw[key] for key in _SETTING_KEYS if key in raw}
    merged = {**bu.DEFAULTS, **settings}
    return merged


def overview() -> dict:
    bu = _bu()
    from auth import get_cookie
    cookie = ""
    try:
        cookie = get_cookie("bilibili") or ""
    except Exception:  # noqa: BLE001 - 未登录时只提示
        cookie = ""
    logged_in = "SESSDATA" in cookie and "bili_jct" in cookie
    history = []
    try:
        for key, state, data in bu.UploadHistory().recent()[:20]:
            history.append({"task": key[:12], "state": state,
                            "title": str(data.get("title") or ""),
                            "account": str((data.get("account") or {}).get("name") or "")})
    except Exception:  # noqa: BLE001 - 历史不可读不影响页面
        history = []
    return {
        "settings": {key: bu.DEFAULTS.get(key) for key in _SETTING_KEYS},
        "statements": [{"value": key, "label": label}
                       for key, label, *_ in bu.CREATION_STATEMENTS],
        "threads_max": int(bu.MAX_UPLOAD_THREADS),
        "logged_in": logged_in,
        "recent": history,
        "note": ("上传会真实发布到当前登录的 B站账号，需要二次确认；"
                 "同一视频重复投稿会被上传历史拦截。"),
    }


def precheck(payload: dict) -> dict:
    """投稿预检：命名、日期、分P、标题 / 标签 / 分区、文件状态与账号登录。"""
    bu = _bu()
    paths = _resolve(payload.get("paths"))
    settings = _settings(payload)
    checks = []

    def add(key, label, ok, detail="", fatal=True):
        checks.append({"key": key, "label": label, "ok": bool(ok),
                       "detail": str(detail)[:300], "fatal": fatal})
        return ok

    try:
        bu.validate_renamed_videos(paths)
        add("renamed", "标准命名", True,
            "全部符合 日期_时间_前缀… 规则" if len(paths) == 1 else f"{len(paths)} 个视频均已命名")
    except ValueError as exc:
        add("renamed", "标准命名", False, str(exc))
        return _result(paths, None, checks, "请先按标准命名后再投稿", settings)

    metadata = None
    try:
        metadata = bu.build_metadata(paths, settings)
        add("metadata", "标题 / 简介 / 标签 / 分区", True,
            f"标题：{metadata['title']}；标签：{metadata['tag']}；分区 {metadata['tid']}")
    except (ValueError, TypeError) as exc:
        add("metadata", "标题 / 简介 / 标签 / 分区", False, str(exc))

    problems = []
    total_bytes = 0
    now = time.time()
    for path in paths:
        try:
            stat = path.stat()
        except OSError as exc:
            problems.append(f"{path.name}：读取失败（{exc}）")
            continue
        if path.is_symlink():
            problems.append(f"{path.name}：不支持符号链接")
            continue
        if path.suffix.lower() not in bu.VIDEO_EXTS:
            problems.append(f"{path.name}：不是视频文件")
            continue
        if not stat.st_size:
            problems.append(f"{path.name}：空文件")
            continue
        if now - stat.st_mtime < 30:
            problems.append(f"{path.name}：最近 30 秒内仍有写入，请等待录制结束")
            continue
        total_bytes += stat.st_size
    add("files", "视频文件", not problems,
        "；".join(problems) or f"{len(paths)} 个文件，共 {total_bytes / 1024 / 1024:.1f} MB")

    account = None
    seasons = []
    try:
        uploader = bu.BiliUploader()
        try:
            account = uploader.check_account()
            add("account", "B站登录", True, f"{account['name']}（UID {account['mid']}）")
            try:
                seasons = uploader.list_seasons()
            except Exception:  # noqa: BLE001 - 合集列表不可读不阻断
                seasons = []
        finally:
            uploader.close()
    except Exception as exc:  # noqa: BLE001 - 登录 / 网络失败统一提示
        add("account", "B站登录", False, str(exc))

    wanted_season = str(settings.get("season") or "").strip()
    if wanted_season and seasons:
        titles = [item.get("title") for item in seasons]
        add("season", "加入合集", wanted_season in titles,
            f"合集「{wanted_season}」" + ("" if wanted_season in titles
                                          else "不存在，请在设置中改选"), fatal=False)
    return _result(paths, metadata, checks, "", settings, account,
                   [item.get("title") for item in seasons], total_bytes)


def _result(paths, metadata, checks, blocking, settings, account=None,
            seasons=None, total_bytes=0) -> dict:
    fatal_failed = [item for item in checks if item["fatal"] and not item["ok"]]
    ok = not fatal_failed and metadata is not None
    return {
        "ok": ok,
        "blocking": blocking or (fatal_failed[0]["detail"] if fatal_failed else ""),
        "checks": checks,
        "account": account or {},
        "metadata": {
            "title": (metadata or {}).get("title", ""),
            "desc": (metadata or {}).get("desc", ""),
            "tag": (metadata or {}).get("tag", ""),
            "tid": (metadata or {}).get("tid", 0),
            "copyright": (metadata or {}).get("copyright", 1),
            "source": (metadata or {}).get("source", ""),
        } if metadata else {},
        "parts": len(paths),
        "total_bytes": int(total_bytes),
        "names": [path.name for path in paths],
        "seasons": seasons or [],
        "auto_publish": bool(settings.get("auto_publish", True)),
        "season": str(settings.get("season") or ""),
        "note": ("预检只读：未创建稿件。确认无误后点「确认投稿」才会真实上传。"
                 if ok else "预检未通过：请按上方提示处理后重试。"),
    }


def upload(payload: dict, on_progress=None, on_log=None, is_cancelled=None) -> dict:
    """确认后真实上传（会重新预检一次）。"""
    bu = _bu()
    if payload.get("confirmed") is not True:
        raise ValueError("缺少投稿确认")
    check = precheck(payload)
    if not check["ok"]:
        raise ValueError(check["blocking"] or "预检未通过，请先处理提示")
    paths = _resolve(payload.get("paths"))
    settings = _settings(payload)
    cancel = threading.Event()

    def progress(done, total, text) -> None:
        if is_cancelled and is_cancelled():
            cancel.set()
        if on_progress:
            fraction = (float(done) / total) if total else None
            on_progress(fraction, str(text))

    def log(text) -> None:
        if on_log:
            on_log(str(text))

    log(f"开始上传 {len(paths)} 个视频：{check['metadata'].get('title', '')}")
    uploader = bu.BiliUploader(cancel=cancel, on_progress=progress)
    try:
        result = uploader.run(paths, settings)
    except bu.UploadCancelled as exc:
        raise PublishCancelled(str(exc)) from exc
    except bu.UploadError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        uploader.close()
    state = str(result.get("state") or "")
    text = {
        "published": "已投稿，等待 B站审核",
        "uploaded": "已上传，未提交投稿",
        "uncertain": "提交结果不明，请到 B站投稿管理核对",
    }.get(state, f"任务状态：{state or '未知'}")
    ref = result.get("bvid") or result.get("aid") or result.get("task") or ""
    return {
        "state": state,
        "bvid": str(result.get("bvid") or ""),
        "aid": str(result.get("aid") or ""),
        "task": str(result.get("task") or ""),
        "season": str(result.get("season") or ""),
        "season_note": str(result.get("season_note") or ""),
        "summary": text + (f"：{ref}" if ref else ""),
        "note": ("同一视频重复投稿会被上传历史拦截；如需重试请先在桌面端"
                 "「投稿管理」核对状态。"),
    }
