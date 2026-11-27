"""闲鱼购物用例与本地商品资料库的唯一写入方（Qt-free）。

存储：统一导出目录/闲鱼商品/<稳定商品ID>/item.json + images/；schema=1。
primary 为平台详情的本地资料快照；assets 与 Web 产物列表只是扫描派生视图。
去重键为 item_id，已有完整资料默认跳过；失败图片允许重试补全，不迁移旧库。
不写 collected.db / increment.db，不触碰腾讯文档，不保存任何会话或原始响应。
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx

from platforms import xianyu
from toolbox.durable_io import atomic_write_json

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _credential():
    # get_cookie 的读取链无保存/迁移/刷新调用；请求前选定当前有效登录快照。
    import auth
    return auth.get_cookie("xianyu") or ""


class _TaskCredential:
    """任务一旦使用某登录态，账号变化/关闭后停止，不能切换身份继续。"""
    def __init__(self):
        self.snapshot = None

    def __call__(self):
        current = _credential()
        if self.snapshot is not None and current != self.snapshot:
            raise xianyu.AuthRequired("闲鱼登录状态已变化，请重新开始本次任务")
        self.snapshot = current
        return current

    def check(self):
        if self.snapshot is not None:
            self()


def _row(item):
    return {key: str(item.get(key) or "") for key in
            ("item_id", "title", "price", "sales", "seller", "location", "url")} | {
                "image": bool(item.get("images")),
                "image_count": len(item.get("images") or [])}


def search(text, limit=30, on_log=None, is_cancelled=None):
    try:
        limit = int(limit)
    except (ValueError, TypeError) as exc:
        raise ValueError("搜索数量须为 1–30") from exc
    if not 1 <= limit <= xianyu.MAX_ITEMS:
        raise ValueError("搜索数量须为 1–30")
    items = xianyu.search(text, limit, credential=_TaskCredential(),
                          on_log=on_log, is_cancelled=is_cancelled)
    return {"platform": "xianyu", "kind": "search", "items": [_row(item) for item in items],
            "total": len(items), "summary": f"闲鱼搜索已完成：{len(items)} 件商品（首批最多 30 件）"}


def parse(text, on_log=None, is_cancelled=None):
    item = xianyu.fetch_item(text, credential=_TaskCredential(),
                             on_log=on_log, is_cancelled=is_cancelled)
    return {"platform": "xianyu", "kind": "item", "items": [_row(item)], "total": 1,
            "summary": "闲鱼商品已解析，勾选后可入库并下载图片"}


def is_item_reference(text: str) -> bool:
    """判断输入是否为规范化商品 ID / 官方商品链接，供 UI 入口路由使用。"""
    try:
        xianyu.item_id(text)
    except (TypeError, ValueError):
        return False
    return True


def normalize_items(items):
    """只接受 ID/官方链接，忽略客户端图片地址和其他业务字段。"""
    if not isinstance(items, list) or not 1 <= len(items) <= xianyu.MAX_ITEMS:
        raise ValueError("请勾选 1–30 件闲鱼商品")
    result = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("商品格式无效，请重新搜索后勾选")
        value = xianyu.item_id(item.get("item_id") or item.get("url") or "")
        if value not in result:
            result.append(value)
    return result


def _root():
    from toolbox.config_manager import get_export_dir
    export = Path(get_export_dir()).resolve()
    root = export / "闲鱼商品"
    if root.resolve().parent != export:
        raise ValueError("闲鱼保存目录指向其他位置，请检查导出目录")
    return root


def _inside(path, root):
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("商品保存位置不在闲鱼目录内")
    return path


@contextmanager
def _save_lock(folder):
    """桌面与 Web 进程共享 OS 文件锁；崩溃退出后锁自动释放。"""
    path = _inside(folder / ".save.lock", folder)
    with path.open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise ValueError("该商品正在保存，请稍后重试") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _image_bytes(url, cancel):
    """无账户头的独立客户端；每次重定向重新检查 CDN 白名单。"""
    with httpx.Client(timeout=httpx.Timeout(20, connect=8), follow_redirects=False) as client:
        for _ in range(4):
            xianyu.check_cancel(cancel)
            url = xianyu.image_url(url)
            if not url:
                raise ValueError("商品图片来源不受支持，已停止下载")
            with client.stream("GET", url, headers={"User-Agent": xianyu.UA}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                if response.status_code != 200:
                    raise ValueError("商品图片暂时无法下载，请稍后重试")
                if not response.headers.get("content-type", "").lower().startswith("image/"):
                    raise ValueError("商品图片返回了其他内容，已停止保存")
                body = bytearray()
                for block in response.iter_bytes(65536):
                    xianyu.check_cancel(cancel)
                    body.extend(block)
                    if len(body) > MAX_IMAGE_BYTES:
                        raise ValueError("商品图片超过 20 MB，已停止下载")
                return bytes(body)
    raise ValueError("商品图片跳转过多，请稍后重试")


def _download_image(url, folder, cancel):
    from PIL import Image
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24] + ".jpg"
    path = _inside(folder / name, folder)
    if path.is_file():
        return "images/" + name
    raw = _image_bytes(url, cancel)
    # 统一转 RGB JPEG，去除元数据；显式像素上限避免解压炸弹。
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("商品图片像素过大，已停止保存")
            source.load()
            converted = source.convert("RGB")
            try:
                output = io.BytesIO()
                converted.save(output, "JPEG", quality=95)
                data = output.getvalue()
            finally:
                converted.close()
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError("商品图片格式损坏，已停止保存") from exc
    xianyu.check_cancel(cancel)
    fd, temporary = tempfile.mkstemp(prefix=".image-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return "images/" + name


def _existing(folder):
    path = _inside(folder / "item.json", folder)
    if not path.is_file() or path.stat().st_size > xianyu.MAX_BODY:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or data.get("kind") != "xianyu_item" or not data.get("download_complete")):
            return None
        if any(not isinstance(rel, str) or not _inside(folder / rel, folder).is_file()
               for rel in data.get("local_images", [])):
            return None
        return data
    except (OSError, ValueError, TypeError):
        return None


def list_saved(limit=200):
    """本地资料只读列表，不创建闲鱼目录、不发平台请求。"""
    root = _root()
    rows = []
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if (folder.is_dir() and folder.name.isdigit()
                    and folder.resolve().is_relative_to(root.resolve())):
                if record := _existing(folder):
                    rows.append(_row(record) | {"saved": True})
                    if len(rows) >= max(1, min(500, int(limit))):
                        break
    return {"platform": "xianyu", "items": rows, "total": len(rows),
            "summary": f"已读取 {len(rows)} 件本地闲鱼商品资料"}


def save_items(items, on_log=None, is_cancelled=None):
    ids = normalize_items(items)
    root = _root()
    saved, skipped, failures, results = 0, 0, [], []
    cancelled = False
    credential = _TaskCredential()
    for index, value in enumerate(ids, 1):
        try:
            xianyu.check_cancel(is_cancelled)
            credential.check()
            if on_log:
                on_log(f"保存闲鱼商品 {index}/{len(ids)}")
            folder = _inside(root / value, root)
            folder.mkdir(parents=True, exist_ok=True)
            with _save_lock(folder):
                if existing := _existing(folder):
                    results.append(_row(existing) | {"saved": True, "skipped": True})
                    skipped += 1
                    continue
                detail = xianyu.fetch_item(value, credential=credential,
                                           on_log=on_log, is_cancelled=is_cancelled)
                image_folder = _inside(folder / "images", folder)
                image_folder.mkdir(exist_ok=True)
                local, image_failures = [], 0
                for url in detail.get("images", [])[:30]:
                    xianyu.check_cancel(is_cancelled)
                    try:
                        local.append(_download_image(url, image_folder, is_cancelled))
                    except xianyu.Cancelled:
                        raise
                    except (ValueError, OSError, httpx.HTTPError):
                        image_failures += 1
                # 仅白名单字段落盘，服务器按 ID 回取，客户端不能注入路径/正文。
                record = {key: detail.get(key, "") for key in (
                    "item_id", "title", "price", "sales", "seller", "location", "status", "url", "description")}
                record.update({"kind": "xianyu_item", "schema_version": 1, "platform": "xianyu",
                               "images": detail.get("images", [])[:30], "local_images": local,
                               "download_complete": image_failures == 0,
                               "saved_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")})
                atomic_write_json(_inside(folder / "item.json", folder), record)
                results.append(_row(record) | {"saved": not image_failures})
                if image_failures:
                    failures.append({"item_id": value, "error": f"资料已保存，{image_failures} 张图片下载失败，可重试补全"})
                else:
                    saved += 1
        except xianyu.Cancelled:
            cancelled = True
            break
        except (xianyu.AuthRequired, xianyu.Blocked, xianyu.RateLimited) as exc:
            failures.append({"item_id": value, "error": str(exc)})
            break  # 认证/风控不继续逐件请求。
        except (ValueError, OSError, httpx.HTTPError):
            failures.append({"item_id": value, "error": "商品保存失败，请检查网络与保存目录后重试"})
    remaining = max(0, len(ids) - saved - skipped - len(failures))
    summary = f"{'已取消' if cancelled else '已完成'}：闲鱼商品入库 {saved} 件，已存在 {skipped} 件，失败 {len(failures)} 件"
    if remaining:
        summary += f"，未处理 {remaining} 件"
    return {"platform": "xianyu", "saved": saved, "skipped": skipped, "failed": len(failures),
            "failures": failures, "items": results, "cancelled": cancelled,
            "remaining": remaining,
            "summary": summary + "；资料与图片位于「闲鱼商品」目录"}
