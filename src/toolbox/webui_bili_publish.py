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


def _flatten_categories(data) -> list[dict]:
    """创作中心 typelist → [{id, name}]；与桌面弹窗的扁平化规则一致。"""
    items = []

    def visit(nodes, prefix="") -> None:
        if isinstance(nodes, dict):
            nodes = list(nodes.values())
        if not isinstance(nodes, list):
            return
        for node in nodes:
            if not isinstance(node, dict):
                continue
            name = str(node.get("name") or node.get("typename") or "")
            children = node.get("children") or node.get("sub")
            if children:
                visit(children, prefix + name + "/")
                continue
            value = node.get("id") or node.get("tid") or node.get("typeid")
            if not value:
                continue
            try:
                category_id = int(value)
            except (TypeError, ValueError):
                continue
            items.append({"id": category_id, "name": prefix + name})

    visit(data.get("typelist") or data.get("type_list") or [])
    return items


def categories() -> dict:
    """当前账号可用分区（创作中心 typelist）。"""
    bu = _bu()
    try:
        uploader = bu.BiliUploader()
        try:
            data = uploader.creator_info()
        finally:
            uploader.close()
    except Exception as exc:  # noqa: BLE001 - 未登录 / 网络 / 风控统一提示
        raise ValueError(f"读取分区失败：{exc}") from exc
    items = _flatten_categories(data)
    if not items:
        raise ValueError("账号没有可用分区（可能未开通投稿权限或接口变更）")
    return {"items": items, "total": len(items),
            "note": "来自 B站创作中心「当前账号可用分区」；可在下拉里选，也可手填 ID。"}


def templates() -> dict:
    """投稿模板列表（与桌面共用 `BU.load_settings()['upload_templates']`）。"""
    bu = _bu()
    try:
        settings = bu.load_settings()
    except Exception:  # noqa: BLE001
        settings = {}
    raw = settings.get("upload_templates") or {}
    items = []
    if isinstance(raw, dict):
        for name, config in raw.items():
            if not isinstance(config, dict):
                continue
            items.append({"name": str(name),
                          "settings": {key: config.get(key)
                                       for key in _SETTING_KEYS
                                       if key in config}})
    return {"items": items,
            "active": str(settings.get("active_upload_template") or "")}


def save_template(payload: dict) -> dict:
    """保存 / 覆盖 / 删除投稿模板。"""
    bu = _bu()
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 30:
        raise ValueError("模板名称需为 1～30 个字符")
    action = str(payload.get("action") or "save").strip()
    try:
        settings = bu.load_settings()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"读取投稿设置失败：{exc}") from exc
    raw = settings.get("upload_templates")
    items = dict(raw) if isinstance(raw, dict) else {}
    if action == "delete":
        if name not in items:
            raise ValueError("模板不存在，可能已被删除")
        items.pop(name, None)
        settings["upload_templates"] = items
        settings["active_upload_template"] = next(iter(items), "")
    elif action == "save":
        config = _settings(payload)
        config["date"] = ""
        items[name] = {key: config.get(key) for key in _SETTING_KEYS}
        settings["upload_templates"] = items
        settings["active_upload_template"] = name
    else:
        raise ValueError("未知模板操作")
    try:
        bu.save_settings(settings)
    except OSError as exc:
        raise ValueError(f"模板保存失败：{exc}") from exc
    data = templates()
    data["ok"] = True
    data["summary"] = ("已删除模板：" if action == "delete" else "已保存模板：") + name
    return data


def history() -> dict:
    """最近投稿任务（桌面上传历史）。"""
    bu = _bu()
    try:
        rows = bu.UploadHistory().recent()
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"读取投稿历史失败：{exc}") from exc
    items = []
    for key, state, data in rows:
        items.append({
            "task": str(key)[:12],
            "state": str(state),
            "title": str((data or {}).get("title") or ""),
            "account": str(((data or {}).get("account") or {}).get("name") or ""),
        })
    return {"items": items, "total": len(items),
            "note": ("已投稿 / 结果不明的任务不能再次提交；"
                     "确认官方投稿管理里没有相同稿件后，可「允许重试」。")}


def allow_retry(payload: dict) -> dict:
    """把失败任务标记为可重试（需确认；已投稿任务拒绝）。"""
    bu = _bu()
    if payload.get("confirmed") is not True:
        raise ValueError("请确认后再允许重试")
    task = str(payload.get("task") or "").strip()
    if not task:
        raise ValueError("缺少任务标识")
    store = bu.UploadHistory()
    target = None
    for key, state, data in store.recent():
        if str(key).startswith(task):
            target = (key, state, data)
            break
    if target is None:
        raise ValueError("任务不存在，可能已被清理")
    key, state, data = target
    if state == "published":
        raise ValueError("已成功投稿的任务不能从这里重试")
    if state == "uploading":
        raise ValueError("任务仍在上传中，请先确认已停止")
    store.save(key, "failed", data)
    return {"ok": True, "task": str(key)[:12],
            "summary": "已标记为可重试；请确认没有重复稿件后再上传"}


def _cover_check(cover: str, checks_add) -> None:
    """封面检查：留空时要求有 FFmpeg 提取首帧；填写时校验图片可读。"""
    text = str(cover or "").strip()
    if not text:
        try:
            from toolbox.live_record import find_ffmpeg
            exe = find_ffmpeg()
        except Exception:  # noqa: BLE001
            exe = None
        checks_add("cover", "封面", bool(exe),
                   "未选择封面，将自动提取视频首帧" if exe
                   else "未选择封面且未找到 FFmpeg，无法自动提取首帧")
        return
    path = Path(text)
    if not path.is_absolute():
        checks_add("cover", "封面", False, "封面请填写完整路径")
        return
    if not path.is_file():
        checks_add("cover", "封面", False, "封面文件不存在")
        return
    if path.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp", ".bmp"):
        checks_add("cover", "封面", False, "封面仅支持 JPG / PNG / WEBP / BMP")
        return
    try:
        if path.stat().st_size > 20 * 1024 * 1024:
            checks_add("cover", "封面", False, "封面超过 20MB")
            return
        from PIL import Image
        with Image.open(path) as image:
            image.verify()
        checks_add("cover", "封面", True, f"{path.name}（上传时会转 JPG）")
    except Exception as exc:  # noqa: BLE001 - 打不开就当作不可用
        checks_add("cover", "封面", False, f"封面无法读取：{exc}")


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

    _cover_check(settings.get("cover") or "", add)

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
    category_list = []
    category_error = ""
    try:
        uploader = bu.BiliUploader()
        try:
            account = uploader.check_account()
            add("account", "B站登录", True, f"{account['name']}（UID {account['mid']}）")
            try:
                seasons = uploader.list_seasons()
            except Exception:  # noqa: BLE001 - 合集列表不可读不阻断
                seasons = []
            try:
                category_list = _flatten_categories(uploader.creator_info())
            except Exception as exc:  # noqa: BLE001 - 分区接口失败降级为手填
                category_list = []
                category_error = str(exc)[:120]
        finally:
            uploader.close()
    except Exception as exc:  # noqa: BLE001 - 登录 / 网络失败统一提示
        add("account", "B站登录", False, str(exc))

    tid = int((metadata or {}).get("tid") or 0)
    tid_name = ""
    if metadata and category_list:
        found = next((item for item in category_list if item["id"] == tid), None)
        tid_name = found["name"] if found else ""
        add("category", "分区可用", found is not None,
            (f"分区 {tid}「{found['name']}」" if found
             else f"分区 {tid} 不在当前账号可用分区内，请从下拉中重新选择"))
    elif metadata:
        add("category", "分区可用", True,
            "未能读取分区列表，按手填 ID 继续" + (f"（{category_error}）"
                                          if category_error else ""),
            fatal=False)

    wanted_season = str(settings.get("season") or "").strip()
    if wanted_season and seasons:
        titles = [item.get("title") for item in seasons]
        add("season", "加入合集", wanted_season in titles,
            f"合集「{wanted_season}」" + ("" if wanted_season in titles
                                          else "不存在，请在设置中改选"), fatal=False)
    return _result(paths, metadata, checks, "", settings, account,
                   [item.get("title") for item in seasons], total_bytes,
                   tid_name=tid_name, categories=category_list)


def _result(paths, metadata, checks, blocking, settings, account=None,
            seasons=None, total_bytes=0, tid_name="", categories=None) -> dict:
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
            "tid_name": tid_name,
            "copyright": (metadata or {}).get("copyright", 1),
            "source": (metadata or {}).get("source", ""),
        } if metadata else {},
        "parts": len(paths),
        "total_bytes": int(total_bytes),
        "names": [path.name for path in paths],
        "seasons": seasons or [],
        "categories": categories or [],
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
