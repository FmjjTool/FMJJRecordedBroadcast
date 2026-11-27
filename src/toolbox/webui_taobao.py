"""Bounded Taobao detail batches for the WebUI (L3, without Qt).

This owner keeps at most three process-local shop snapshots (schema 1, 4 MiB,
30 minutes). Selection uses only their server-captured numeric product IDs.
The existing shopping engine remains the owner of per-product directories and
item.json sidecars. Completed products are retained in the shared export tree;
only an atomic ZIP of completed Excel files is published in the job workspace.
No credential, persistent cache, second settings file or scheduled task is kept.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import stat
import threading
import time
import zipfile
from collections import OrderedDict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from platforms.taobao_media import (TaobaoImageDownloader, TaobaoMediaError,
                                    below as _below, cdn_url as _cdn_url,
                                    ensure_directory as _mkdir,
                                    plain_chain as _plain_chain, regular as _regular,
                                    validate_picture)

TTL_SECONDS = 30 * 60
MAX_RESULTS = 3
MAX_ROWS = 2000
MAX_MEMORY = 4 * 1024 * 1024
MAX_SELECTED = 30
MAX_DETAIL_BYTES = 2 * 1024 * 1024
MAX_CELL_TEXT = 32767
MAX_IMAGES = 120
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_MEDIA_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
REQUEST_INTERVAL = 3.0
_RESULT_ID = re.compile(r"[A-Za-z0-9_-]{32}")
_ITEM_ID = re.compile(r"[0-9]{1,32}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SHEETS = ("商品信息", "参数信息(全部)", "选购参数", "主图", "图文详情")
_BEIJING = timezone(timedelta(hours=8))


class TaobaoExportError(ValueError):
    """A safe message, never a raw platform exception or filesystem path."""

    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result


class TaobaoCancelled(InterruptedError):
    def __init__(self, result):
        super().__init__("已取消淘宝商品详情导出，已完成的文件保留在导出目录")
        self.result = result


@dataclass(frozen=True)
class Prepared:
    result_id: str
    item_ids: tuple[str, ...]


@dataclass(frozen=True)
class _Snapshot:
    created: float
    models: bytes
    item_ids: tuple[str, ...]


def _text(value):
    if type(value) is not str or len(value) > MAX_CELL_TEXT or _CONTROL.search(value):
        raise TaobaoExportError("商品文本格式不支持完整导出，本项已跳过")


def _texts(value, max_bytes):
    pending = [value]
    size = 0
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            _text(item)
            size += len(item.encode("utf-8"))
            if size > max_bytes:
                raise TaobaoExportError("商品信息超过大小上限，暂不能完整导出")
        elif isinstance(item, dict):
            pending.extend(item.values())
        elif isinstance(item, (list, tuple)):
            pending.extend(item)
        else:
            raise TaobaoExportError("商品信息格式无效，本项已跳过")


def _detail(value, item_id):
    import shopping

    if type(value) is not shopping.TaobaoItemDetail or value.item_id != item_id:
        raise TaobaoExportError("商品详情与所选商品不一致，本项已停止")
    if value.local_main or value.local_sku or value.local_desc or value.image_dir:
        raise TaobaoExportError("商品详情包含无效本地文件，本项已停止")
    if (type(value.skus) is not list
            or any(type(sku) is not shopping.ItemSku for sku in value.skus)
            or type(value.sku_props) is not list or type(value.params) is not list
            or type(value.main_images) is not list or type(value.desc_images) is not list
            or type(value.desc_text) is not list):
        raise TaobaoExportError("商品详情格式无效，本项已停止")
    raw = asdict(value)
    _texts(raw, MAX_DETAIL_BYTES)
    if len(json.dumps(raw, ensure_ascii=False).encode("utf-8")) > MAX_DETAIL_BYTES:
        raise TaobaoExportError("商品详情超过大小上限，本项已停止")
    for pair in value.params:
        if type(pair) not in (list, tuple) or len(pair) != 2:
            raise TaobaoExportError("商品参数格式无效，本项已停止")
    count = len(value.main_images) + len(value.desc_images)
    value.main_images = [_cdn_url(url) for url in value.main_images]
    value.desc_images = [_cdn_url(url) for url in value.desc_images]
    for group in value.sku_props:
        if type(group) is not dict or type(group.get("values", [])) is not list:
            raise TaobaoExportError("商品规格格式无效，本项已停止")
        names = []
        for option in group.get("values") or []:
            if type(option) is not dict or type(option.get("name", "")) is not str:
                raise TaobaoExportError("商品规格格式无效，本项已停止")
            names.append(option.get("name") or "")
            if option.get("image"):
                option["image"] = _cdn_url(option["image"])
                count += 1
        _text(" / ".join(names))
    if count > MAX_IMAGES:
        raise TaobaoExportError("商品图片超过 120 张上限，本项已停止")
    return value


def _finish_workbook(path, detail, downloaded):
    from openpyxl import load_workbook

    workbook = load_workbook(path, data_only=False, keep_links=False)
    try:
        if tuple(workbook.sheetnames) != _SHEETS:
            raise TaobaoExportError("商品 Excel 结构校验失败，本项已停止")
        for name, urls in (("主图", detail.main_images), ("图文详情", detail.desc_images)):
            sheet = workbook[name]
            # The desktop exporter lists URLs only when every image failed.
            # Keep partial failures visible as well, without changing its columns.
            if any(url in downloaded for url in urls):
                for url in urls:
                    if url not in downloaded:
                        sheet.append(["图片未下载", url])
        for sheet in workbook.worksheets:
            if sheet.sheet_state != "visible":
                raise TaobaoExportError("商品 Excel 结构校验失败，本项已停止")
            for row in sheet.iter_rows():
                for cell in row:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"
                        cell.hyperlink = None
        workbook.save(path)
    finally:
        workbook.close()


def _credential():
    from auth import get_cookie
    return get_cookie("taobao") or ""


def _export_root():
    from toolbox.config_manager import get_export_dir_readonly
    return Path(get_export_dir_readonly()) / "淘宝商品"


def _summary(item_ids, rows, *, cancelled=False, stopped=False, reason=""):
    known = {row["item_id"] for row in rows}
    rows = list(rows) + [{"item_id": item_id, "status": "skipped",
                         "message": reason or ("任务已取消" if cancelled else "本次未执行")}
                        for item_id in item_ids if item_id not in known]
    succeeded = sum(row["status"] == "completed" for row in rows)
    failed = sum(row["status"] == "failed" for row in rows)
    skipped = len(rows) - succeeded - failed
    if cancelled:
        message = f"任务已取消：完成 {succeeded} 件，失败 {failed} 件，未执行 {skipped} 件；已完成的文件保留在导出目录"
    elif stopped:
        message = f"任务已停止：完成 {succeeded} 件，失败 {failed} 件，未执行 {skipped} 件。{reason}"
    elif failed:
        state = "部分完成" if succeeded else "失败"
        message = f"商品详情导出{state}：完成 {succeeded} 件，失败 {failed} 件"
    else:
        message = f"商品详情导出完成：共 {succeeded} 件"
    return {"platform": "taobao", "requested": len(item_ids), "succeeded": succeeded,
            "failed": failed, "skipped": skipped, "cancelled": cancelled,
            "stopped": stopped, "message": message, "rows": rows}


class TaobaoExports:
    def __init__(self, *, clock=time.monotonic, credential_reader=None, export_root=None, waiter=None):
        self._clock = clock
        self._credential_reader = credential_reader or _credential
        self._export_root = export_root or _export_root
        self._waiter = waiter or threading.Event().wait
        self._lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._results = OrderedDict()

    def _expire(self):
        now = self._clock()
        for key in list(self._results):
            if now - self._results[key].created >= TTL_SECONDS:
                del self._results[key]

    def capture(self, shop, products, is_cancelled=None):
        import shopping

        try:
            if is_cancelled and is_cancelled():
                raise InterruptedError("已取消商品解析")
            if (type(shop) is not shopping.ShopInfo or type(products) is not list
                    or not products or len(products) > MAX_ROWS
                    or any(type(product) is not shopping.ShopProduct for product in products)):
                raise TaobaoExportError("本次商品列表暂不支持详情导出，请重新解析")
            values = {"schema_version": "1", "shop": asdict(shop),
                      "products": [asdict(product) for product in products]}
            _texts(values, MAX_MEMORY)
            ids = tuple(dict.fromkeys(product.item_id for product in products))
            if any(_ITEM_ID.fullmatch(item_id) is None for item_id in ids):
                raise TaobaoExportError("本次商品编号无效，请重新解析")
            raw = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(raw) > MAX_MEMORY:
                raise TaobaoExportError("本次商品列表超过大小上限，请减少解析数量")
            with self._lock:
                self._expire()
                while self._results and (len(self._results) >= MAX_RESULTS
                        or sum(len(value.models) for value in self._results.values()) + len(raw) > MAX_MEMORY):
                    self._results.popitem(last=False)
                result_id = secrets.token_urlsafe(24)
                self._results[result_id] = _Snapshot(self._clock(), raw, ids)
            return {"taobao_available": True, "taobao_result_id": result_id,
                    "taobao_count": len(ids), "taobao_expires_in": TTL_SECONDS,
                    "taobao_message": "勾选商品后抓取详情与图片，每件商品生成一个 Excel"}
        except InterruptedError:
            raise
        except Exception as error:
            message = str(error) if isinstance(error, TaobaoExportError) else "本次商品列表暂不能导出，请重新解析"
            return {"taobao_available": False, "taobao_message": message}

    def prepare(self, payload):
        if (type(payload) is not dict or set(payload) != {"result_id", "item_ids"}
                or type(payload.get("result_id")) is not str
                or _RESULT_ID.fullmatch(payload["result_id"]) is None
                or type(payload.get("item_ids")) is not list
                or not 1 <= len(payload["item_ids"]) <= MAX_SELECTED
                or any(type(value) is not str or _ITEM_ID.fullmatch(value) is None
                       for value in payload["item_ids"])
                or len(set(payload["item_ids"])) != len(payload["item_ids"])):
            raise TaobaoExportError("请选择本次解析结果中的 1 至 30 件商品")
        prepared = Prepared(payload["result_id"], tuple(payload["item_ids"]))
        self._snapshot(prepared)
        return prepared

    def _snapshot(self, prepared):
        if (type(prepared) is not Prepared or type(prepared.result_id) is not str
                or _RESULT_ID.fullmatch(prepared.result_id) is None
                or type(prepared.item_ids) is not tuple
                or not 1 <= len(prepared.item_ids) <= MAX_SELECTED
                or any(type(value) is not str or _ITEM_ID.fullmatch(value) is None for value in prepared.item_ids)
                or len(set(prepared.item_ids)) != len(prepared.item_ids)):
            raise TaobaoExportError("商品导出参数无效，请重新选择")
        with self._lock:
            self._expire()
            snapshot = self._results.get(prepared.result_id)
            if snapshot is None:
                raise TaobaoExportError("商品结果已过期，请重新解析后选择商品")
            if not set(prepared.item_ids).issubset(snapshot.item_ids):
                raise TaobaoExportError("所选商品不属于本次解析结果，请重新选择")
            return snapshot

    def run(self, prepared, workspace, on_log=None, on_progress=None, is_cancelled=None):
        if not self._run_lock.acquire(blocking=False):
            raise TaobaoExportError("已有淘宝商品详情任务正在执行，请等待完成")
        rows, completed = [], []
        stopped, reason = False, ""
        archive_temp = None
        archive_created = False
        try:
            self._snapshot(prepared)
            ids = prepared.item_ids

            def checkpoint():
                if is_cancelled and is_cancelled():
                    raise TaobaoCancelled(_summary(ids, rows, cancelled=True))

            checkpoint()
            credential = self._credential_reader()
            if not credential:
                raise TaobaoExportError("抓取淘宝商品详情需要登录")

            def identity():
                checkpoint()
                if self._credential_reader() != credential:
                    raise TaobaoExportError("登录状态已变化，本次任务已停止，请重新解析后重试")

            output = _mkdir(Path(self._export_root()).absolute())
            batch = output / ("网页批次_" + datetime.now(_BEIJING).strftime("%Y%m%d_%H%M%S_") + secrets.token_hex(4))
            batch.mkdir(exist_ok=False)
            _plain_chain(batch)
            workspace = _mkdir(Path(workspace).absolute())
            import shopping

            total_bytes = 0
            for index, item_id in enumerate(ids):
                checkpoint()
                try:
                    identity()
                    if index:
                        remaining = REQUEST_INTERVAL
                        while remaining > 0:
                            identity()
                            step = min(0.2, remaining)
                            self._waiter(step)
                            remaining -= step
                    if on_log:
                        on_log(f"正在抓取商品 {item_id} 的详情（{index + 1}/{len(ids)}）")
                    if on_progress:
                        on_progress(f"抓取商品详情 {index + 1}/{len(ids)}", index / len(ids))
                    detail = _detail(shopping.fetch_taobao_item_detail(item_id, credential, log=None), item_id)
                    identity()
                    download = TaobaoImageDownloader(
                        batch, checkpoint=identity, max_file_bytes=MAX_IMAGE_BYTES,
                        max_total_bytes=MAX_MEDIA_BYTES, max_pixels=MAX_IMAGE_PIXELS)
                    detail = shopping.download_item_images(detail, batch, log=None, threads=1, downloader=download)
                    identity()
                    directory = _below(Path(detail.image_dir), batch)
                    if not directory.is_dir():
                        raise TaobaoExportError("商品输出目录无效，本项已停止")
                    for name in ("local_main", "local_sku", "local_desc"):
                        paths = getattr(detail, name)
                        if type(paths) is not list:
                            raise TaobaoExportError("商品图片结果格式无效，本项已停止")
                        for path in paths:
                            validate_picture(_below(Path(path), directory),
                                             max_bytes=MAX_IMAGE_BYTES, max_pixels=MAX_IMAGE_PIXELS)
                    target = _below(Path(shopping.default_item_xlsx_path(detail, batch)), directory)
                    if target.exists():
                        raise TaobaoExportError("商品 Excel 已存在，本项未覆盖")
                    temporary = target.with_name("." + secrets.token_hex(8) + ".xlsx")
                    try:
                        shopping.export_item_xlsx(detail, str(temporary))
                        identity()
                        _finish_workbook(temporary, detail, download.downloaded)
                        identity()
                        state = _regular(temporary)
                        if not stat.S_ISREG(state.st_mode) or not 0 < state.st_size <= MAX_OUTPUT_BYTES:
                            raise TaobaoExportError("商品 Excel 超过 32 MB 上限，本项已停止")
                        if total_bytes + state.st_size > MAX_ARCHIVE_BYTES - 65536:
                            raise TaobaoExportError("本批 Excel 超过 64 MB 附件上限，请减少选择数量")
                        os.replace(temporary, target)
                    finally:
                        temporary.unlink(missing_ok=True)
                    total_bytes += state.st_size
                    filename = f"商品_{item_id}.xlsx"
                    completed.append((target, filename))
                    expected = set(detail.main_images + detail.desc_images)
                    expected.update(option["image"] for group in detail.sku_props
                                    for option in group.get("values") or [] if option.get("image"))
                    missing = len(expected - download.downloaded)
                    message = "详情与 Excel 已保存"
                    if missing:
                        message += f"；{missing} 张图片未下载，可稍后重试"
                    rows.append({"item_id": item_id, "status": "completed",
                                 "message": message, "filename": filename, "missing_images": missing})
                    if on_log:
                        on_log(f"商品 {item_id} 导出完成")
                except TaobaoCancelled:
                    raise
                except Exception as error:
                    message = str(error) if isinstance(error, (TaobaoExportError, TaobaoMediaError)) else "商品详情抓取或导出失败，请稍后重试"
                    rows.append({"item_id": item_id, "status": "failed", "message": message})
                    # Never retry a risk challenge or silently switch identities.
                    raw = str(error).lower()
                    if (isinstance(error, TaobaoExportError) and ("登录状态" in message or "附件上限" in message)
                            or any(word in raw for word in ("cookie", "token", "登录", "captcha", "rgv", "风控", "验证", "429", "403"))):
                        stopped = True
                        reason = message if isinstance(error, TaobaoExportError) else "平台暂未允许继续抓取详情，请稍后重试"
                    if on_log:
                        on_log(f"商品 {item_id}：{message}")
                    if stopped:
                        break
            checkpoint()
            result = _summary(ids, rows, stopped=stopped, reason=reason)
            if not completed:
                raise TaobaoExportError(result["message"] + "；没有生成可下载文件", result=result)
            archive = workspace / ("淘宝商品详情_" + secrets.token_hex(6) + ".zip")
            archive_temp = archive.with_suffix(".partial")
            descriptor = os.open(archive_temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
            archive_created = True
            with os.fdopen(descriptor, "wb") as output_stream:
                with zipfile.ZipFile(output_stream, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=False) as bundle:
                    for path, filename in completed:
                        checkpoint()
                        _plain_chain(path)
                        bundle.write(path, filename)
                    bundle.writestr("结果清单.json", json.dumps({"schema_version": 1, **result}, ensure_ascii=False, indent=2))
            if _regular(archive_temp).st_size > MAX_ARCHIVE_BYTES:
                raise TaobaoExportError("本批附件超过 64 MB 上限，请减少选择数量；已完成的 Excel 保留在导出目录")
            checkpoint()
            os.replace(archive_temp, archive)
            archive_temp = None
            if on_progress:
                on_progress(result["message"], 1.0)
            return result, archive
        except (TaobaoCancelled, TaobaoExportError):
            raise
        except TaobaoMediaError as error:
            raise TaobaoExportError(str(error)) from None
        except Exception:
            raise TaobaoExportError("淘宝商品详情导出失败，请稍后重试；已完成的文件保留在导出目录") from None
        finally:
            try:
                if archive_created and archive_temp is not None:
                    try:
                        archive_temp.unlink(missing_ok=True)
                    except OSError:
                        if on_log:
                            on_log("部分临时文件未能清理，已完成的本地文件仍保留")
            finally:
                self._run_lock.release()
