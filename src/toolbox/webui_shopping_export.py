"""Private, bounded shopping-result snapshots and offline Excel exports (L3).

ShoppingExports is the sole owner of these derived, process-local snapshots:
schema 1, at most three results / 16 MiB in total, expiring after 30 minutes.
No database, credential reader or persistent cache is used. Only models captured
by the server's current parse operation can become a snapshot. Export files are
created below the job workspace, then published atomically as one attachment.
"""

from __future__ import annotations

import io
import json
import os
import re
import secrets
import shutil
import stat
import tempfile
import threading
import time
import warnings
from collections import OrderedDict
from dataclasses import asdict, dataclass
from pathlib import Path

TTL_SECONDS = 30 * 60
MAX_RESULTS = 3
MAX_ROWS = 2000
MAX_MEMORY = 16 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_CELL_TEXT = 32767
_ID = re.compile(r"[A-Za-z0-9_-]{32}")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_PLATFORMS = {"douyin": "抖音商城", "bili": "B站小店"}


class ShoppingExportError(ValueError):
    """A safe, user-facing export failure; never contains a raw path/error."""


@dataclass(frozen=True)
class _Snapshot:
    created: float
    platform: str
    count: int
    models: bytes
    images: tuple[tuple[int, bytes, str], ...]
    size: int


def _cancel(callback):
    if callback and callback():
        raise InterruptedError("已取消商品导出")


def _regular(path: Path):
    state = path.lstat()
    if stat.S_ISLNK(state.st_mode) or getattr(state, "st_file_attributes", 0) & _REPARSE:
        raise OSError("unsupported link")
    return state


def _plain_chain(path: Path):
    """Reject symbolic links and Windows junction/reparse ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise OSError("invalid path")
    for part in (*reversed(path.parents), path):
        _regular(part)


def _local_image(model, root: Path | None, remaining: int):
    """Freeze a validated local picture now, never reopen user paths at export."""
    if root is None or not model.local_dir or not model.image_file:
        return None
    root = Path(root)
    directory, name = Path(model.local_dir), str(model.image_file)
    if (not name or Path(name).name != name or "/" in name or "\\" in name
            or any(char in name for char in '<>:"|?*')
            or any(ord(char) < 32 for char in name)
            or ".." in directory.parts):
        return None
    path = directory / name
    try:
        path.relative_to(root)
        _plain_chain(root)
        _plain_chain(path)
        before = _regular(path)
        if not stat.S_ISREG(before.st_mode) or before.st_size <= 0:
            return None
        if before.st_size > remaining:
            raise ShoppingExportError("本次商品信息及主图超过 16 MB，无法导出，请减少本次解析数量")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino, opened.st_size)
                    != (before.st_dev, before.st_ino, before.st_size)):
                return None
            raw = stream.read(remaining + 1)
            after = os.fstat(stream.fileno())
        _plain_chain(path)
        if len(raw) > remaining:
            raise ShoppingExportError("本次商品信息及主图超过 16 MB，无法导出，请减少本次解析数量")
        if ((after.st_size, after.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns)
                or len(raw) != before.st_size):
            return None
        from PIL import Image
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as picture:
                if (picture.width <= 0 or picture.height <= 0
                        or picture.width * picture.height > MAX_IMAGE_PIXELS):
                    return None
                suffix = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif"}.get(picture.format)
                if suffix is None:
                    return None
                picture.verify()
        return raw, suffix
    except ShoppingExportError:
        raise
    except Exception:
        # Missing, changing, unsupported or untrusted pictures are optional.
        return None


def _snapshot(platform, models, root, created, is_cancelled):
    from platforms import bili_shop as bs, douyin_shop as ds

    if platform == "douyin":
        if type(models) is not list or any(type(item) is not ds.DouyinProduct for item in models):
            raise ShoppingExportError("本次商品信息格式不支持导出，请重新解析")
        count = len(models)
        values = []
        for model in models:
            value = asdict(model)
            value.update(local_dir="", image_file="")
            values.append(value)
    elif platform == "bili":
        if (type(models) is not bs.BiliShop or type(models.items) is not list
                or any(type(item) is not bs.BiliShopItem for item in models.items)):
            raise ShoppingExportError("本次商品信息格式不支持导出，请重新解析")
        count = len(models.items)
        values = asdict(models)
        values["local_dir"] = ""
    else:
        raise ShoppingExportError("该平台暂不支持本次结果导出")
    if count == 0:
        raise ShoppingExportError("本次没有可导出的商品，请重新解析")
    if count > MAX_ROWS:
        raise ShoppingExportError("本次商品超过 2000 件，无法导出，请减少本次解析数量")
    pending = [values]
    while pending:
        value = pending.pop()
        if isinstance(value, str) and len(value) > MAX_CELL_TEXT:
            raise ShoppingExportError("本次商品文本超过 Excel 单元格 32767 字符上限，暂不能完整导出")
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, (list, tuple)):
            pending.extend(value)
    raw = json.dumps(values, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    size = len(raw)
    if size > MAX_MEMORY:
        raise ShoppingExportError("本次商品信息超过 16 MB，无法导出，请减少本次解析数量")
    pictures = []
    if platform == "douyin":
        for index, model in enumerate(models):
            _cancel(is_cancelled)
            picture = _local_image(model, root, MAX_MEMORY - size)
            if picture is not None:
                data, suffix = picture
                size += len(data)
                pictures.append((index, data, suffix))
    return _Snapshot(created, platform, count, raw, tuple(pictures), size)


def _finish_workbook(path: Path, snapshot: _Snapshot):
    """Keep desktop columns/styles/images and force every string to literal text."""
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=False, keep_links=False)
    try:
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
                        cell.hyperlink = None
        metadata = workbook.create_sheet("_export_meta")
        metadata.append(["schema_version", 1])
        metadata.append(["platform", snapshot.platform])
        metadata.append(["count", snapshot.count])
        metadata.sheet_state = "hidden"
        workbook.save(path)
    finally:
        workbook.close()


class ShoppingExports:
    """One ShoppingService owns one cache and one nonblocking export mutex."""

    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._export_lock = threading.Lock()
        self._results = OrderedDict()

    def _expire(self):
        now = self._clock()
        for key in list(self._results):
            if now - self._results[key].created >= TTL_SECONDS:
                del self._results[key]

    @staticmethod
    def validate_payload(payload):
        if (type(payload) is not dict or set(payload) != {"result_id"}
                or type(payload.get("result_id")) is not str
                or _ID.fullmatch(payload["result_id"]) is None):
            raise ShoppingExportError("导出参数无效，请重新解析商品后导出")
        return {"result_id": payload["result_id"]}

    def parse(self, payload, on_log=None, is_cancelled=None, on_taobao_models=None):
        from toolbox import webui_shopping

        _cancel(is_cancelled)
        captured = []
        message = "该平台暂不支持本次结果导出"

        def capture(platform, models, root):
            nonlocal message
            try:
                captured.append(_snapshot(platform, models, root, self._clock(), is_cancelled))
            except InterruptedError:
                raise
            except ShoppingExportError as error:
                message = str(error)
            except Exception:
                message = "本次商品信息暂不能导出，请重新解析后重试"

        result = webui_shopping.parse(payload, on_log=on_log, is_cancelled=is_cancelled,
                                      on_models=capture, on_taobao_models=on_taobao_models)
        _cancel(is_cancelled)
        result.update(export_available=False, export_result_id="",
                      export_message=message, expires_in=TTL_SECONDS)
        if len(captured) != 1:
            return result
        snapshot = captured[0]
        if snapshot.platform != result.get("platform") or snapshot.count != result.get("total"):
            result["export_message"] = "本次商品信息不完整，请重新解析后导出"
            return result
        key = secrets.token_urlsafe(24)
        with self._lock:
            self._expire()
            while self._results and (len(self._results) >= MAX_RESULTS
                    or sum(item.size for item in self._results.values()) + snapshot.size > MAX_MEMORY):
                self._results.popitem(last=False)
            self._results[key] = snapshot
        result.update(export_available=True, export_result_id=key, export_message="")
        return result

    def export(self, payload, output_dir, *, is_cancelled=None, on_progress=None):
        _cancel(is_cancelled)
        body = self.validate_payload(payload)
        with self._lock:
            self._expire()
            snapshot = self._results.get(body["result_id"])
        if snapshot is None:
            raise ShoppingExportError("解析结果已失效或不存在，请重新解析商品后导出")
        if not self._export_lock.acquire(blocking=False):
            raise ShoppingExportError("已有商品导出任务正在执行，请稍后重试")
        staging, published = None, None
        try:
            _cancel(is_cancelled)
            workspace = Path(output_dir)
            _plain_chain(workspace)
            if not stat.S_ISDIR(_regular(workspace).st_mode):
                raise ShoppingExportError("导出任务目录不可用，请重新导出")
            workspace = workspace.resolve(strict=True)
            staging = Path(tempfile.mkdtemp(prefix="shopping-export-", dir=workspace))
            target = staging / "result.xlsx"
            if on_progress:
                on_progress("正在生成完整商品 Excel", 0.1)
            values = json.loads(snapshot.models)
            if snapshot.platform == "douyin":
                from platforms import douyin_shop as ds
                models = [ds.DouyinProduct(**value) for value in values]
                image_dir = staging / "images"
                if snapshot.images:
                    image_dir.mkdir()
                for index, raw, suffix in snapshot.images:
                    _cancel(is_cancelled)
                    image = image_dir / f"{index}{suffix}"
                    image.write_bytes(raw)
                    models[index].local_dir = str(image_dir)
                    models[index].image_file = image.name
                _cancel(is_cancelled)
                generated = ds.export_products_xlsx(models, target)
            else:
                from platforms import bili_shop as bs
                items = [bs.BiliShopItem(**item) for item in values.pop("items")]
                model = bs.BiliShop(**values, items=items)
                _cancel(is_cancelled)
                generated = bs.export_items_xlsx(model, target)
            _cancel(is_cancelled)
            if (Path(generated) != target or not stat.S_ISREG(_regular(target).st_mode)
                    or not 0 < target.stat().st_size <= MAX_OUTPUT_BYTES):
                raise ShoppingExportError("商品 Excel 未完整生成，请重新导出")
            if on_progress:
                on_progress("正在核对 Excel 文本与完整性", 0.75)
            _finish_workbook(target, snapshot)
            _cancel(is_cancelled)
            if not 0 < _regular(target).st_size <= MAX_OUTPUT_BYTES:
                raise ShoppingExportError("商品 Excel 内容过大，请减少本次解析数量")
            destination = workspace / f"购物解析_{_PLATFORMS[snapshot.platform]}.xlsx"
            if destination.exists() or destination.is_symlink():
                raise ShoppingExportError("任务目录已有导出文件，请重新创建导出任务")
            os.replace(target, destination)
            published = destination
            _cancel(is_cancelled)
            outcome = {"schema_version": 1, "count": snapshot.count,
                       "platform": snapshot.platform,
                       "summary": f"已导出本次全部 {snapshot.count} 件{_PLATFORMS[snapshot.platform]}商品，可下载 Excel"}
            if on_progress:
                on_progress("商品 Excel 已生成", 1.0)
            _cancel(is_cancelled)
            return outcome, published
        except Exception as error:
            if published is not None:
                try:
                    published.unlink(missing_ok=True)
                except OSError:
                    pass
            _cancel(is_cancelled)
            if isinstance(error, (InterruptedError, ShoppingExportError)):
                raise
            raise ShoppingExportError("商品 Excel 导出失败，请重新解析后重试") from None
        finally:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            self._export_lock.release()
